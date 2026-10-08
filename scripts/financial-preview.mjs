/** Local-only financial QA service. Registry injection is deliberately not an
 * HTTP route; only explicitly synthetic, frozen private fixtures are accepted. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { randomBytes, randomUUID, createHash } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { exportFinancialPreview } from './financial-preview-export.mjs';
const root = path.resolve(import.meta.dirname, '..');
const directory = path.resolve(
  root,
  process.env.FINANCIAL_PREVIEW_FIXTURE_DIR || 'private/financial-http-synthetic-canary'
);
const raw = await fs.readFile(path.join(directory, 'calendar-registry.json'));
const calendar = JSON.parse(raw);
if (
  calendar.kind !== 'calendar' ||
  calendar.payload?.kind !== 'fixture' ||
  !calendar.evidenceLevel?.includes('SYNTHETIC')
)
  throw Error('Only an explicitly synthetic local registry fixture is admitted.');
const port = Number(process.env.PORT || 8924),
  runnerSecret = randomBytes(32).toString('hex'),
  calendarRef = randomUUID();
const options = {
  modules: true,
  scriptPath: path.join(root, 'dist/worker.mjs'),
  compatibilityDate: '2026-08-01',
  host: '127.0.0.1',
  port,
  d1Databases: ['DB'],
  r2Buckets: ['ARTIFACTS'],
  bindings: {
    RUNNER_SECRET: runnerSecret,
    FINANCIAL_WORKSPACE_ENABLED: 'true',
    TUSHARE_PUBLIC_AUTHORIZED: 'false',
  },
};
const mf = new Miniflare(options),
  db = await mf.getD1Database('DB'),
  bucket = await mf.getR2Bucket('ARTIFACTS');
await db.exec(
  (await fs.readFile(path.join(root, 'edge/schema.sql'), 'utf8')).replaceAll('\n', ' ')
);
const key = 'local-financial-qa/calendar/' + calendarRef;
await bucket.put(key, raw);
await db
  .prepare(
    'INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)'
  )
  .bind(
    calendarRef,
    'calendar',
    '*',
    key,
    createHash('sha256').update(raw).digest('hex'),
    raw.length,
    JSON.stringify({
      label: '合成验收日历 · 非交易所证据',
      calendarRoot: calendar.scope.calendarRoot,
      coverageStart: calendar.payload.coverage_start,
      coverageEnd: calendar.payload.coverage_end,
      complete: calendar.payload.complete,
      evidenceLevel: calendar.evidenceLevel,
    }),
    new Date().toISOString()
  )
  .run();
await mf.ready;
const baseUrl = `http://localhost:${port}`;
const session = await mf.dispatchFetch(baseUrl + '/quant/api/session');
const privateConfig = path.join(root, 'private/financial-preview-session.json');
await fs.writeFile(
  privateConfig,
  JSON.stringify(
    {
      baseUrl,
      runnerSecret,
      cookie: session.headers.get('set-cookie').split(';')[0],
      calendarRef,
      proofRefs: [],
      sourcePath: path.join(directory, 'strict-unbound-package.json'),
      revisionTemplatePath: path.join(directory, 'ui-revision-body-template.json'),
      adminExportRequestPath: path.join(root, 'private/financial-preview-export-request.json'),
      adminExportResponsePath: path.join(root, 'private/financial-preview-export-response.json'),
      synthetic: true,
      providerCalls: 0,
    },
    null,
    2
  ),
  { mode: 0o600 }
);
console.log(`Financial preview: ${baseUrl}/quant/#quant/studio/financial`);
console.log(`Private consumer/bootstrap config: ${privateConfig}`);
let lastExportRequest = null;
let revision = (await fs.stat(path.join(root, 'dist/worker.mjs'))).mtimeMs,
  busy = false;
const timer = setInterval(async () => {
  if (busy) return;
  busy = true;
  try {
    const exportPath = path.join(root, 'private/financial-preview-export-request.json');
    const exportRequest = await fs
      .readFile(exportPath, 'utf8')
      .then(JSON.parse)
      .catch(() => null);
    if (exportRequest && exportRequest.requestId !== lastExportRequest) {
      lastExportRequest = exportRequest.requestId;
      let response;
      try {
        response = { ok: true, ...(await exportFinancialPreview(db, bucket, root, exportRequest)) };
      } catch (error) {
        response = { ok: false, requestId: exportRequest.requestId, error: error.message };
      }
      await fs.writeFile(
        path.join(root, 'private/financial-preview-export-response.json'),
        JSON.stringify(response, null, 2),
        { mode: 0o600 }
      );
    }
    const latest = (await fs.stat(path.join(root, 'dist/worker.mjs'))).mtimeMs;
    if (latest !== revision) {
      revision = latest;
      await mf.setOptions(options);
      console.log('Financial preview assets updated.');
    }
  } catch (error) {
    console.error(error.message);
  } finally {
    busy = false;
  }
}, 1500);
async function close() {
  clearInterval(timer);
  await mf.dispose();
  process.exit(0);
}
process.on('SIGINT', close);
process.on('SIGTERM', close);
