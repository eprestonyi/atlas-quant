/** Isolated loopback SYNTHETIC source queue. No provider configuration or credentials. */
import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { parseArgs } from "node:util";
import { Miniflare } from "miniflare";
import {
  buildWorkerSource,
  loadWebAssets,
  repositoryRoot,
} from "./worker-source.mjs";
import { canonical } from "../edge/market-preparation/common.mjs";

const { values: args } = parseArgs({
  options: {
    port: { type: "string", default: "8938" },
    symbols: { type: "string", default: "2" },
    estimator: { type: "string", default: "ridge" },
    output: { type: "string" },
  },
});
const port = Number(args.port),
  count = Number(args.symbols);
if (
  !Number.isInteger(port) ||
  port < 1024 ||
  port > 65535 ||
  !Number.isInteger(count) ||
  count < 1 ||
  count > 1000 ||
  !["ridge", "auto"].includes(args.estimator)
)
  throw Error("Invalid preview parameters");
const directory = path.resolve(
  args.output || `private/market-http-${Date.now()}`,
);
if (!directory.startsWith(path.join(repositoryRoot, "private") + path.sep))
  throw Error("Private repository output required");
await fs.mkdir(directory, { recursive: true, mode: 0o700 });
await fs.chmod(directory, 0o700);
try {
  await fs.access(path.join(directory, "session.json"));
  throw Error("Existing session: choose a new output directory");
} catch (e) {
  if (e.code !== "ENOENT") throw e;
}
const fixture = spawnSync(
  process.env.PYTHON || ".venv/bin/python",
  [
    "scripts/fixtures/market_source.py",
    "--symbols",
    String(count),
    "--estimator",
    args.estimator,
  ],
  {
    cwd: repositoryRoot,
    env: { ...process.env, PYTHONPATH: "engine" },
    encoding: "utf8",
    maxBuffer: 8 * 1024 * 1024,
  },
);
if (fixture.status !== 0) throw Error(fixture.stderr);
const { scope, plan, strategy } = JSON.parse(fixture.stdout),
  secret = randomBytes(32).toString("hex");
const buildId = "SYNTHETIC-market-http-" + Date.now();
const mf = new Miniflare({
  host: "127.0.0.1",
  port,
  modules: true,
  script: await buildWorkerSource({ assets: await loadWebAssets(), buildId }),
  compatibilityDate: "2026-08-01",
  d1Databases: ["DB"],
  r2Buckets: ["ARTIFACTS"],
  d1Persist: path.join(directory, "d1"),
  r2Persist: path.join(directory, "r2"),
  bindings: {
    RUNNER_SECRET: secret,
    MARKET_ACQUISITION_ENABLED: "true",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    MARKET_ACQUISITION_AUTH_SCOPE: plan.authorizationScope,
    ALLOW_MARKET_FIXTURES: "true",
    MARKET_RESEARCH_ENABLED: "true",
    BUNDLE_SNAPSHOT_SORTED_V1: "true",
  },
});
await mf.ready;
const db = await mf.getD1Database("DB");
await db.exec(
  (
    await fs.readFile(path.join(repositoryRoot, "edge/schema.sql"), "utf8")
  ).replaceAll("\n", " "),
);
const baseUrl = `http://localhost:${port}/quant/api`,
  response = await fetch(baseUrl + "/session");
if (!response.ok) throw Error("Session bootstrap failed");
const cookie = response.headers.get("set-cookie").split(";")[0],
  owner = (await response.json()).workspace.id;
const planId = randomUUID(),
  stamp = new Date().toISOString();
await db
  .prepare(
    "INSERT INTO quant_universe_scopes(id,owner,scope_root,spec,created_at) VALUES(?,?,?,?,?)",
  )
  .bind(
    plan.universeScopeRef.scopeId,
    owner,
    plan.universeScopeRef.scopeRoot,
    canonical(scope),
    stamp,
  )
  .run();
await db
  .prepare(
    "INSERT INTO quant_market_plans(id,owner,scope_id,scope_root,plan_root,profile,spec,created_at) VALUES(?,?,?,?,?,?,?,?)",
  )
  .bind(
    planId,
    owner,
    plan.universeScopeRef.scopeId,
    plan.universeScopeRef.scopeRoot,
    plan.planRoot,
    plan.profile,
    canonical(plan),
    stamp,
  )
  .run();
await fs.writeFile(
  path.join(directory, "session.json"),
  JSON.stringify(
    {
      baseUrl,
      cookie,
      runnerSecret: secret,
      owner,
      planId,
      planRoot: plan.planRoot,
      universeScopeRef: plan.universeScopeRef,
      scope,
      strategy,
      authorizationScope: plan.authorizationScope,
      buildId,
      synthetic: true,
      providerCalls: 0,
    },
    null,
    2,
  ),
  { mode: 0o600, flag: "wx" },
);
console.log(
  JSON.stringify({
    status: "ready",
    port,
    sourceKind: "fixture",
    symbols: count,
    estimator: args.estimator,
    providerCalls: 0,
    sessionPath: path.join(directory, "session.json"),
  }),
);
let closing = false;
for (const sig of ["SIGINT", "SIGTERM"])
  process.on(sig, async () => {
    if (closing) return;
    closing = true;
    await mf.dispose();
    process.exit(0);
  });
