/** Isolated loopback SYNTHETIC source queue. No provider configuration or credentials. */
import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes, randomUUID, createHash } from "node:crypto";
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
    resume: { type: "boolean", default: false },
    "enable-trend-auto": { type: "boolean", default: false },
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
const sessionPath = path.join(directory, "session.json");
let previous = null;
try {
  const info = await fs.lstat(sessionPath);
  if (!args.resume) throw Error("Existing session: choose a new output directory or explicitly resume");
  if (!info.isFile() || info.isSymbolicLink() || (info.mode & 0o077))
    throw Error("Resume requires a private regular session file");
  previous = JSON.parse(await fs.readFile(sessionPath, "utf8"));
  if (previous.synthetic !== true || previous.providerCalls !== 0 ||
      previous.baseUrl !== `http://localhost:${port}/quant/api` ||
      previous.scope?.symbolCount !== count || previous.strategy?.model?.estimator !== args.estimator ||
      typeof previous.runnerSecret !== "string" || !/^[a-f0-9]{64}$/.test(previous.runnerSecret))
    throw Error("Resume parameters must match the original synthetic session");
  for (const name of ["d1", "r2"]) {
    const saved = await fs.lstat(path.join(directory, name));
    if (!saved.isDirectory() || saved.isSymbolicLink()) throw Error("Saved private storage required");
  }
} catch (e) {
  if (e.code !== "ENOENT" || args.resume) throw e;
}
const fixture = previous ? null : spawnSync(
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
if (fixture && fixture.status !== 0) throw Error(fixture.stderr);
const { scope, plan, strategy } = previous || JSON.parse(fixture.stdout),
  secret = previous?.runnerSecret || randomBytes(32).toString("hex");
const buildId = "SYNTHETIC-market-http-" + Date.now();
const workerSource = await buildWorkerSource({ assets: await loadWebAssets(), buildId });
const mf = new Miniflare({
  host: "127.0.0.1",
  port,
  modules: true,
  script: workerSource,
  compatibilityDate: "2026-08-01",
  d1Databases: ["DB"],
  r2Buckets: ["ARTIFACTS"],
  d1Persist: path.join(directory, "d1"),
  r2Persist: path.join(directory, "r2"),
  bindings: {
    RUNNER_SECRET: secret,
    MARKET_ACQUISITION_ENABLED: "true",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    MARKET_ACQUISITION_AUTH_SCOPE: previous?.authorizationScope || plan.authorizationScope,
    ALLOW_MARKET_FIXTURES: "true",
    MARKET_RESEARCH_ENABLED: "true",
    MARKET_TREND_AUTO_ENABLED: args["enable-trend-auto"] ? "true" : "false",
    BUNDLE_SNAPSHOT_SORTED_V1: "true",
  },
});
await mf.ready;
const db = await mf.getD1Database("DB");
if (previous) {
  const existing = await db.prepare("SELECT id,owner,plan_root FROM quant_market_plans WHERE id=?").bind(previous.planId).first();
  if (!existing || existing.owner !== previous.owner || existing.plan_root !== previous.planRoot) {
    await mf.dispose();
    throw Error("Saved session and persistent plan identity differ");
  }
} else {
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
}
await fs.writeFile(path.join(directory, buildId + ".json"), JSON.stringify({
  buildId, workerSha256: createHash("sha256").update(workerSource).digest("hex"),
  resumed: !!previous, port, synthetic: true, providerCalls: 0,
  trendAutoEnabled: args["enable-trend-auto"],
}), { mode: 0o600, flag: "wx" });
console.log(
  JSON.stringify({
    status: "ready",
    port,
    sourceKind: "fixture",
    symbols: count,
    estimator: args.estimator,
    providerCalls: 0,
    resumed: !!previous,
    trendAutoEnabled: args["enable-trend-auto"],
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
