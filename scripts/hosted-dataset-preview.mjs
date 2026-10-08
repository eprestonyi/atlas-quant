/** Loopback-only hosted dataset integration service, no provider client/secret. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { randomBytes, createHash } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { buildWorkerSource, loadWebAssets } from './worker-source.mjs';
import { seedDatasetSources } from './hosted-dataset-fixture.mjs';
const root = path.resolve(import.meta.dirname, '..');
const resumed =
  process.env.DATASET_PREVIEW_RESUME === 'true'
    ? JSON.parse(
        await fs.readFile(
          path.join(root, 'private/hosted-dataset-preview-session.json'),
          'utf8',
        ),
      )
    : null;
const port = Number(process.env.PORT || 8932),
  runnerSecret = resumed?.runnerSecret || randomBytes(32).toString('hex'),
  fixtureDirectory = process.env.DATASET_FIXTURE_DIRECTORY || null;
const options = {
  modules: true,
  script: await buildWorkerSource({
    assets: await loadWebAssets(),
    buildId: 'dataset-v09-local-unreleased',
  }),
  compatibilityDate: '2026-08-01',
  host: '127.0.0.1',
  port,
  d1Databases: ['DB'],
  r2Buckets: ['ARTIFACTS'],
  d1Persist: path.join(root, 'private/hosted-dataset-runtime/d1'),
  r2Persist: path.join(root, 'private/hosted-dataset-runtime/r2'),
  bindings: {
    RUNNER_SECRET: runnerSecret,
    RESEARCH_DATASETS_ENABLED: 'true',
    FINANCIAL_DATASET_RESEARCH_ENABLED:
      process.env.FINANCIAL_DATASET_RESEARCH_ENABLED === 'true'
        ? 'true'
        : 'false',
    TUSHARE_PUBLIC_AUTHORIZED: 'false',
    FINANCIAL_WORKSPACE_ENABLED: 'true',
  },
};
const mf = new Miniflare(options),
  db = await mf.getD1Database('DB'),
  bucket = await mf.getR2Bucket('ARTIFACTS');
await db.exec(
  (await fs.readFile(path.join(root, 'edge/schema.sql'), 'utf8')).replaceAll(
    '\n',
    ' ',
  ),
);
await mf.ready;
const baseUrl = `http://dataset.localhost:${port}`,
  response = await mf.dispatchFetch(
    baseUrl + '/quant/api/session',
    resumed ? { headers: { cookie: resumed.cookie } } : undefined,
  ),
  session = await response.json(),
  sources =
    resumed ||
    (await seedDatasetSources(
      db,
      bucket,
      session.workspace.id,
      fixtureDirectory,
    )),
  config = {
    baseUrl,
    runnerSecret,
    fixtureDirectory,
    cookie: resumed?.cookie || response.headers.get('set-cookie').split(';')[0],
    ...sources,
    seedRequest: path.join(root, 'private/dataset-preview-seed-request.json'),
    seedResponse: path.join(root, 'private/dataset-preview-seed-response.json'),
  };
await fs.mkdir(path.join(root, 'private'), { recursive: true });
const configPath = path.join(
  root,
  'private/hosted-dataset-preview-session.json',
);
await fs.writeFile(configPath, JSON.stringify(config, null, 2), {
  mode: 0o600,
});
console.log(`Dataset preview: ${baseUrl}/quant/#quant/studio/datasets/source`);
console.log('Private bootstrap: ' + configPath);
let busy = false,
  lastSeed = '',
  lastReload = await fs
    .stat(path.join(root, 'private/hosted-dataset-reload'))
    .then((x) => x.mtimeMs)
    .catch(() => 0);
const priorRequest = await fs
  .readFile(config.seedRequest, 'utf8')
  .catch(() => null);
const priorResponse = await fs
  .readFile(config.seedResponse, 'utf8')
  .then(JSON.parse)
  .catch(() => null);
if (
  priorRequest &&
  priorResponse?._controlRequestSha256 ===
    createHash('sha256').update(priorRequest).digest('hex')
)
  lastSeed = priorRequest;
const timer = setInterval(async () => {
  if (busy) return;
  busy = true;
  try {
    const request = await fs
      .readFile(config.seedRequest, 'utf8')
      .catch(() => null);
    if (request && request !== lastSeed) {
      const v = JSON.parse(request);
      const controlDb = await mf.getD1Database('DB'),
        controlBucket = await mf.getR2Bucket('ARTIFACTS');
      const exists = await controlDb
        .prepare('SELECT id FROM workspaces WHERE id=?')
        .bind(v.owner)
        .first();
      if (!exists) throw Error('Requested exact workspace absent');
      const result = await seedDatasetSources(
        controlDb,
        controlBucket,
        v.owner,
        v.fixtureDirectory || fixtureDirectory,
      );
      await fs.writeFile(
        config.seedResponse,
        JSON.stringify(
          {
            ...result,
            _controlRequestSha256: createHash('sha256')
              .update(request)
              .digest('hex'),
          },
          null,
          2,
        ),
        {
          mode: 0o600,
        },
      );
      lastSeed = request;
    }
    const signal = await fs
      .stat(path.join(root, 'private/hosted-dataset-reload'))
      .then((s) => s.mtimeMs)
      .catch(() => 0);
    if (signal > lastReload) {
      lastReload = signal;
      options.script = await buildWorkerSource({
        assets: await loadWebAssets(),
        buildId: 'dataset-v09-local-unreleased',
      });
      await mf.setOptions(options);
      console.log('Source reloaded; D1/R2 retained.');
    }
  } catch (error) {
    console.error('Private local control: ' + error.message);
  } finally {
    busy = false;
  }
}, 1000);
const stop = async () => {
  clearInterval(timer);
  await mf.dispose();
  process.exit(0);
};
process.once('SIGINT', stop);
process.once('SIGTERM', stop);
