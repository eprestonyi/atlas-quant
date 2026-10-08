/** Isolated loopback UI/HTTP acceptance. No provider credentials or network client.
 * The explicit fixture flag is local-only and never set by production config. */
import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { Miniflare } from "miniflare";
import { buildWorkerSource, loadWebAssets } from "./worker-source.mjs";
const root = path.resolve(import.meta.dirname, "..");
const port = Number(process.env.PORT || 8928);
if (!Number.isInteger(port) || port < 1024 || port > 65535)
  throw Error("Invalid local port");
const runnerSecret = randomBytes(32).toString("hex");
const options = {
  modules: true,
  script: await buildWorkerSource({
    assets: await loadWebAssets(),
    buildId: "financial-acquisition-local-unreleased",
  }),
  compatibilityDate: "2026-08-01",
  host: "127.0.0.1",
  port,
  d1Databases: ["DB"],
  r2Buckets: ["ARTIFACTS"],
  bindings: {
    RUNNER_SECRET: runnerSecret,
    FINANCIAL_WORKSPACE_ENABLED: "true",
    FINANCIAL_ACQUISITION_ENABLED: "true",
    FINANCIAL_ACQUISITION_AUDIENCE: "public",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    FINANCIAL_ACQUISITION_AUTH_SCOPE: "local-synthetic-acquisition-v1",
    ALLOW_ACQUISITION_FIXTURES: "true",
  },
};
const mf = new Miniflare(options),
  db = await mf.getD1Database("DB");
await db.exec(
  (await fs.readFile(path.join(root, "edge/schema.sql"), "utf8")).replaceAll(
    "\n",
    " ",
  ),
);
await mf.ready;
const baseUrl = `http://localhost:${port}`,
  session = await mf.dispatchFetch(baseUrl + "/quant/api/session");
const configPath = path.join(
  root,
  "private/financial-acquisition-preview-session.json",
);
await fs.mkdir(path.dirname(configPath), { recursive: true });
await fs.writeFile(
  configPath,
  JSON.stringify(
    {
      baseUrl,
      runnerSecret,
      cookie: session.headers.get("set-cookie").split(";")[0],
      authorizationScope: options.bindings.FINANCIAL_ACQUISITION_AUTH_SCOPE,
      synthetic: true,
      providerCalls: 0,
    },
    null,
    2,
  ),
  { mode: 0o600 },
);
await fs.chmod(configPath, 0o600);
console.log(
  `Isolated acquisition preview: ${baseUrl}/quant/#quant/studio/financial/acquire/source`,
);
console.log(`Private bootstrap: ${configPath}`);
let busy = false,
  last = 0;
const timer = setInterval(async () => {
  if (busy) return;
  busy = true;
  try {
    const signal = path.join(root, "private/financial-acquisition-reload");
    const modified = await fs
      .stat(signal)
      .then((x) => x.mtimeMs)
      .catch(() => 0);
    if (modified > last) {
      last = modified;
      options.script = await buildWorkerSource({
        assets: await loadWebAssets(),
        buildId: "financial-acquisition-local-unreleased",
      });
      await mf.setOptions(options);
      console.log("Reloaded local source; D1/R2 retained.");
    }
  } catch (error) {
    console.error(error.message);
  } finally {
    busy = false;
  }
}, 1000);
async function stop() {
  clearInterval(timer);
  await mf.dispose();
  process.exit(0);
}
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
