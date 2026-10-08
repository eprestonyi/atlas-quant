import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
import { bundleFixture, canonical, hash } from './fixtures/bundle-fixture.mjs';
import { validateManifest } from '../edge/bundles/manifest.mjs';
import {
  INDEXED_SNAPSHOT,
  SORTED_SNAPSHOT,
  SNAPSHOT_VALIDATION_BYTES,
  stageMetadata,
  snapshotValidation
} from '../edge/bundles/snapshot-index.mjs';
import { BUNDLE_PROFILE } from '../edge/bundles/profile.mjs';

const script = await buildWorkerSource({
  wrapper: `
export default {async fetch(req,env,ctx){
  const enabled=req.headers.get('X-Test-Policy-Enabled')??'true';
  const wrapped={...env,BUNDLE_SNAPSHOT_SORTED_V1:enabled,ARTIFACTS:{
    get:(...args)=>env.ARTIFACTS.get(...args),delete:(...args)=>env.ARTIFACTS.delete(...args),
    put:async(key,...args)=>{
      const result=await env.ARTIFACTS.put(key,...args);
      if(key.includes('/snapshotRows/')){
        const fault=await env.DB.prepare("SELECT value FROM meta WHERE key='snapshot_test_cancel'").first();
        if(fault){
          await env.DB.prepare("UPDATE jobs SET status='cancelled' WHERE id=?").bind(fault.value).run();
          await env.DB.prepare("DELETE FROM meta WHERE key='snapshot_test_cancel'").run();
        }
      }
      return result;
    }
  }};
  return productionWorker.fetch(req,wrapped,ctx);
}};`
});
const mf = new Miniflare({
  modules: true,
  script,
  compatibilityDate: '2026-08-01',
  d1Databases: ['DB'],
  r2Buckets: ['ARTIFACTS'],
  bindings: { RUNNER_SECRET: 'snapshot-isolated-test' }
});
const db = await mf.getD1Database('DB'),
  bucket = await mf.getR2Bucket('ARTIFACTS');
await db.exec(
  (await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' ')
);
test.after(() => mf.dispose());

function call(path, { method = 'GET', data, cookie, runner = false, raw, headers = {} } = {}) {
  return mf.dispatchFetch('https://atlas.test/quant/api' + path, {
    method,
    headers: {
      ...(data !== undefined || raw !== undefined ? { 'content-type': 'application/json' } : {}),
      ...(cookie ? { cookie } : {}),
      ...(runner ? { authorization: 'Bearer snapshot-isolated-test' } : {}),
      ...headers
    },
    ...(data !== undefined
      ? { body: JSON.stringify(data) }
      : raw !== undefined
        ? { body: raw }
        : {})
  });
}
async function payload(response, status = 200) {
  const result = await response.json();
  assert.equal(response.status, status, JSON.stringify(result));
  return result;
}
function fixture(change) {
  return bundleFixture({
    rowsPerChunk: 2,
    mutate(input) {
      input.snapshot.rows = ['20250102', '20250103', '20250106'].flatMap((trade_date) =>
        ['000001.SZ', '000002.SZ'].map((ts_code) => ({
          trade_date,
          ts_code,
          open: 10,
          close: 10,
          vol: 100
        }))
      );
      input.snapshot.provenance.tradingDates = ['20250102', '20250103', '20250106'];
      change?.(input);
    }
  });
}
async function queue(f) {
  const cookie = (await call('/session')).headers.get('set-cookie').split(';')[0];
  const created = await payload(
    await call('/runs', {
      method: 'POST',
      cookie,
      data: { strategy: f.strategy, dataSource: 'demo' }
    }),
    202
  );
  const { job } = await payload(
    await call('/runner/claim', {
      method: 'POST',
      runner: true,
      data: {
        engineVersion: '0.4.0',
        transportFormats: ['atlas.quant.bundle/1']
      }
    })
  );
  assert.equal(job.id, created.job.id);
  return { cookie, job };
}
const packet = (f, job, stage) => ({
  id: job.id,
  leaseToken: job.leaseToken,
  bundleId: f.bundleId,
  ...(stage ? { stageId: stage.stageId } : {})
});
function begin(f, job, extra = {}, headers = {}) {
  return call('/runner/bundles/begin', {
    method: 'POST',
    runner: true,
    headers,
    data: { ...packet(f, job), manifestText: f.manifestText, ...extra }
  });
}
async function setup(f = fixture()) {
  const q = await queue(f);
  const stage = await payload(await begin(f, q.job, { snapshotIndexStrategy: SORTED_SNAPSHOT }));
  assert.equal(stage.snapshotIndexStrategy, SORTED_SNAPSHOT);
  return { f, ...q, stage };
}
function put(context, name, ordinal, extra = {}) {
  const { f, job, stage } = context;
  return call(`/runner/bundles/${f.bundleId}/chunks/${name}/${ordinal}`, {
    method: 'PUT',
    runner: true,
    raw: f.chunks.get(name + ':' + ordinal),
    headers: {
      'X-Quant-Job': job.id,
      'X-Quant-Lease': job.leaseToken,
      'X-Quant-Stage': stage.stageId,
      ...extra
    }
  });
}
async function uploadAll(context, { reverse = false } = {}) {
  let names = [...context.f.chunks.keys()];
  if (reverse) names.reverse();
  for (const key of names) {
    const [name, ordinal] = key.split(':');
    await payload(await put(context, name, +ordinal));
  }
}
function finalize(c) {
  return call('/runner/bundles/finalize', {
    method: 'POST',
    runner: true,
    data: packet(c.f, c.job, c.stage)
  });
}
async function stored(c) {
  return db.prepare('SELECT * FROM quant_bundle_stages WHERE id=?').bind(c.stage.stageId).first();
}
async function indexCount(c, collection) {
  return (
    await db
      .prepare('SELECT count(*) n FROM quant_bundle_records WHERE stage_id=? AND collection=?')
      .bind(c.stage.stageId, collection)
      .first()
  ).n;
}

test('new strategy is explicit, gated only at admission, frozen on resume, and server-owned', async () => {
  const f = fixture(),
    { job } = await queue(f);
  await payload(
    await begin(
      f,
      job,
      { snapshotIndexStrategy: SORTED_SNAPSHOT },
      { 'X-Test-Policy-Enabled': 'false' }
    ),
    409
  );
  for (const value of [null, {}, 'unknown'])
    await payload(await begin(f, job, { snapshotIndexStrategy: value }), 400);
  await payload(
    await begin(f, job, {
      indexValidation: { snapshotRows: { receipts: {} } }
    }),
    400
  );
  const stage = await payload(await begin(f, job, { snapshotIndexStrategy: SORTED_SNAPSHOT }));
  const resumed = await payload(await begin(f, job, {}, { 'X-Test-Policy-Enabled': 'false' }));
  assert.equal(resumed.stageId, stage.stageId);
  assert.equal(resumed.snapshotIndexStrategy, SORTED_SNAPSHOT);
  await payload(await begin(f, job, { snapshotIndexStrategy: INDEXED_SNAPSHOT }), 409);
});

test('out-of-order arrival merges receipts atomically, stores no snapshot rows, preserves full documents', async () => {
  const c = await setup();
  await uploadAll(c, { reverse: true });
  const state = snapshotValidation(await stored(c));
  assert.equal(Object.keys(state.receipts).length, 3);
  assert.equal(state.receipts['0'].firstKey, '20250102|000001.SZ');
  assert.equal(state.receipts['2'].lastKey, '20250106|000002.SZ');
  assert.equal(await indexCount(c, 'snapshotRows'), 0);
  assert.equal(await indexCount(c, 'plannedOrigins'), 4);
  assert.equal(await indexCount(c, 'forecasts'), 4);
  assert.equal((await payload(await finalize(c))).status, 'verified');
  await payload(
    await call('/runner/complete', {
      method: 'POST',
      runner: true,
      data: packet(c.f, c.job, c.stage)
    })
  );
  const report = await payload(await call('/runs/' + c.job.id, { cookie: c.cookie }));
  assert.deepEqual(report.result.forecasts, c.f.report.forecasts);
  const receipt = await db
    .prepare(
      "SELECT * FROM quant_bundle_chunks WHERE stage_id=? AND collection='snapshotRows' AND ordinal=1"
    )
    .bind(c.stage.stageId)
    .first();
  assert.equal(
    await (await bucket.get(receipt.object_key)).text(),
    c.f.chunks.get('snapshotRows:1')
  );
});

test('default and explicit legacy policy keep the original index and accept unique unsorted snapshots', async () => {
  for (const extra of [{}, { snapshotIndexStrategy: INDEXED_SNAPSHOT }]) {
    const f = fixture((input) => input.snapshot.rows.reverse()),
      q = await queue(f);
    const stage = await payload(await begin(f, q.job, extra));
    assert.equal(stage.snapshotIndexStrategy, INDEXED_SNAPSHOT);
    const c = { f, ...q, stage };
    await uploadAll(c);
    assert.equal(await indexCount(c, 'snapshotRows'), 6);
    assert.equal((await payload(await finalize(c))).status, 'verified');
    await payload(await begin(f, q.job, { snapshotIndexStrategy: SORTED_SNAPSHOT }), 409);
  }
});

test('concurrent distinct chunks cannot overwrite another receipt; duplicate ACK retries are idempotent', async () => {
  const c = await setup();
  const answers = await Promise.all(
    [2, 0, 1, 0, 2].map(async (ordinal) => payload(await put(c, 'snapshotRows', ordinal)))
  );
  assert(answers.some((answer) => answer.idempotent));
  assert.equal(Object.keys(snapshotValidation(await stored(c)).receipts).length, 3);
  assert.equal(await indexCount(c, 'snapshotRows'), 0);
  await uploadAll(c);
  await payload(await finalize(c));
});

for (const [name, change] of [
  [
    'duplicate in one chunk',
    (input) => {
      input.snapshot.rows[1] = { ...input.snapshot.rows[0] };
    }
  ],
  [
    'descending in one chunk',
    (input) => {
      [input.snapshot.rows[0], input.snapshot.rows[1]] = [
        input.snapshot.rows[1],
        input.snapshot.rows[0]
      ];
    }
  ],
  [
    'invalid date identity',
    (input) => {
      input.snapshot.rows[0].trade_date = '2025-01-02';
    }
  ],
  [
    'invalid symbol identity',
    (input) => {
      input.snapshot.rows[0].ts_code = 'not-a-security';
    }
  ]
])
  test('self-consistently rehashed ' + name + ' is rejected before storing a receipt', async () => {
    const c = await setup(fixture(change));
    await payload(await put(c, 'snapshotRows', 0), 400);
    assert.deepEqual(snapshotValidation(await stored(c)).receipts, {});
    assert.equal(await indexCount(c, 'snapshotRows'), 0);
  });

for (const [name, change] of [
  [
    'duplicate',
    (input) => {
      input.snapshot.rows[2] = { ...input.snapshot.rows[1] };
    }
  ],
  [
    'descending dates',
    (input) => {
      input.snapshot.rows = [
        ...input.snapshot.rows.slice(2, 4),
        ...input.snapshot.rows.slice(0, 2),
        ...input.snapshot.rows.slice(4)
      ];
    }
  ]
])
  test('cross-chunk ' + name + ' cannot finalize despite valid per-chunk SHA/counts', async () => {
    const c = await setup(fixture(change));
    await uploadAll(c);
    await payload(await finalize(c), 409);
    assert.equal((await stored(c)).status, 'staging');
  });

test('missing trading-date rows are preserved as missing, not invented or rejected as duplicate', async () => {
  const c = await setup(
    fixture((input) => {
      input.snapshot.rows.splice(2, 2);
    })
  );
  await uploadAll(c);
  await payload(await finalize(c));
  const state = snapshotValidation(await stored(c));
  assert.equal(
    Object.values(state.receipts).reduce((n, receipt) => n + receipt.count, 0),
    4
  );
});

test('missing chunk, receipt count/hash forgery and unexpected old row index each prevent finalize', async () => {
  const c = await setup();
  for (const key of c.f.chunks.keys()) {
    const [name, ordinal] = key.split(':');
    if (key !== 'snapshotRows:2') await payload(await put(c, name, +ordinal));
  }
  await payload(await finalize(c), 409);
  await payload(await put(c, 'snapshotRows', 2));
  const original = (await stored(c)).metadata;
  for (const [field, value] of [
    ['count', 1],
    ['sha256', '0'.repeat(64)],
    ['firstKey', '20990101|000001.SZ']
  ]) {
    const metadata = JSON.parse(original);
    metadata.indexValidation.snapshotRows.receipts['1'][field] = value;
    await db
      .prepare('UPDATE quant_bundle_stages SET metadata=? WHERE id=?')
      .bind(JSON.stringify(metadata), c.stage.stageId)
      .run();
    await payload(await finalize(c), 409);
  }
  await db
    .prepare('UPDATE quant_bundle_stages SET metadata=? WHERE id=?')
    .bind(original, c.stage.stageId)
    .run();
  await db
    .prepare(
      "INSERT INTO quant_bundle_records(stage_id,collection,ordinal,chunk_ordinal,item_index,metadata) VALUES(?,'snapshotRows',0,0,0,'{}')"
    )
    .bind(c.stage.stageId)
    .run();
  await payload(await finalize(c), 409);
});

test('R2 corruption still prevents finalization although compact receipts are intact', async () => {
  const c = await setup();
  await uploadAll(c);
  const receipt = await db
    .prepare(
      "SELECT object_key FROM quant_bundle_chunks WHERE stage_id=? AND collection='snapshotRows' AND ordinal=0"
    )
    .bind(c.stage.stageId)
    .first();
  await bucket.put(receipt.object_key, c.f.chunks.get('snapshotRows:0').replace('100', '101'));
  await payload(await finalize(c), 503);
});

test('actual D1 transaction failure rolls back the JSON receipt and chunk row together', async () => {
  const c = await setup();
  await db.exec(
    "CREATE TRIGGER snapshot_fail BEFORE INSERT ON quant_bundle_chunks WHEN NEW.collection='snapshotRows' BEGIN SELECT RAISE(ABORT,'intentional test failure'); END;"
  );
  try {
    await payload(await put(c, 'snapshotRows', 0), 500);
    assert.deepEqual(snapshotValidation(await stored(c)).receipts, {});
    assert.equal(
      (
        await db
          .prepare('SELECT count(*) n FROM quant_bundle_chunks WHERE stage_id=?')
          .bind(c.stage.stageId)
          .first()
      ).n,
      0
    );
  } finally {
    await db.exec('DROP TRIGGER snapshot_fail');
  }
  await payload(await put(c, 'snapshotRows', 0));
  assert.equal(snapshotValidation(await stored(c)).receipts['0'].count, 2);
});

test('cancellation between R2 write and receipt batch leaves neither receipt representation', async () => {
  const c = await setup();
  await db
    .prepare("INSERT INTO meta(key,value,updated_at) VALUES('snapshot_test_cancel',?,'test')")
    .bind(c.job.id)
    .run();
  assert.equal((await payload(await put(c, 'snapshotRows', 0))).terminalDiscard, true);
  assert.deepEqual(snapshotValidation(await stored(c)).receipts, {});
  assert.equal(
    (
      await db
        .prepare('SELECT count(*) n FROM quant_bundle_chunks WHERE stage_id=?')
        .bind(c.stage.stageId)
        .first()
    ).n,
    0
  );
  assert.equal((await payload(await finalize(c))).terminalDiscard, true);
});

test('expired or wrong leases cannot write sorted receipts', async () => {
  const c = await setup();
  await payload(await put(c, 'snapshotRows', 0, { 'X-Quant-Lease': 'not-the-lease' }), 409);
  await db
    .prepare("UPDATE jobs SET lease_until='2000-01-01T00:00:00.000Z' WHERE id=?")
    .bind(c.job.id)
    .run();
  assert.equal((await payload(await put(c, 'snapshotRows', 0))).terminalDiscard, true);
  assert.deepEqual(snapshotValidation(await stored(c)).receipts, {});
});

test('sorted strategy never bypasses independent planned-origin completeness and ordering', async () => {
  const c = await setup(fixture((input) => input.coverage.origins.reverse()));
  await uploadAll(c);
  assert.equal(await indexCount(c, 'plannedOrigins'), 4);
  await payload(await finalize(c), 409);
});

test('manifest ordering, count, empty snapshot and existing row budgets remain unchanged', async () => {
  const f = fixture();
  const manifest = JSON.parse(f.manifestText);
  const collection = manifest.collections.find((c) => c.id === 'snapshotRows');
  collection.chunks.reverse();
  const raw = canonical(manifest);
  await assert.rejects(validateManifest(raw, hash(raw)));
  const empty = fixture((input) => {
    input.snapshot.rows = [];
  });
  await assert.rejects(validateManifest(empty.manifestText, empty.bundleId));
  const tooMany = JSON.parse(f.manifestText);
  tooMany.collections.find((c) => c.id === 'snapshotRows').rowCount = 110001;
  await assert.rejects(validateManifest(canonical(tooMany)));
  const c = await setup();
  const body = c.f.chunks.get('snapshotRows:0');
  c.f.chunks.set('snapshotRows:0', body.replace('100', '101'));
  await payload(await put(c, 'snapshotRows', 0), 400);
});

test('all summaries are reserved at begin under independent and aggregate hard budgets', async () => {
  const parsed = await validateManifest(fixture().manifestText);
  const metadata = stageMetadata(parsed, SORTED_SNAPSHOT);
  assert(Buffer.byteLength(metadata) <= BUNDLE_PROFILE.manifestBytes);
  assert(
    Buffer.byteLength(JSON.stringify(JSON.parse(metadata).indexValidation)) <=
      SNAPSHOT_VALIDATION_BYTES
  );
  const maximumChunks = structuredClone(parsed);
  maximumChunks.collections = new Map(parsed.collections);
  maximumChunks.collections.set('snapshotRows', {
    chunks: Array.from({ length: 256 }, (_, ordinal) => ({
      ordinal,
      start: ordinal * 1000,
      count: 1000,
      sha256: 'f'.repeat(64)
    }))
  });
  assert.doesNotThrow(() => stageMetadata(maximumChunks, SORTED_SNAPSHOT));
  maximumChunks.metadata = {
    payload: 'x'.repeat(BUNDLE_PROFILE.manifestBytes - 1000)
  };
  assert.throws(
    () => stageMetadata(maximumChunks, SORTED_SNAPSHOT),
    (error) => error.code === 'BUNDLE_BUDGET'
  );
  maximumChunks.metadata = { indexValidation: {} };
  assert.throws(() => stageMetadata(maximumChunks, SORTED_SNAPSHOT));
});
