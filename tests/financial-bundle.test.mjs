import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
import { validateFinancialManifest } from '../edge/financial-bundles/manifest.mjs';
import { validateManifest } from '../edge/bundles/manifest.mjs';
import { parseStrictJson } from '../edge/bundles/json.mjs';
import { canonical, hash } from './fixtures/bundle-fixture.mjs';
import { financialBundleFixture } from './fixtures/financial-bundle-fixture.mjs';

const vectors = JSON.parse(
  await fs.readFile(new URL('./fixtures/financial-bundle-v1.json', import.meta.url))
);
test('separate financial numeric codec preserves exact tokens and rejects legacy rewriting', () => {
  for (const { financial, forecast } of vectors.valid) {
    assert.doesNotThrow(() => parseStrictJson(financial, { codec: 'financial_json_v1' }));
    assert.doesNotThrow(() => parseStrictJson(forecast));
    if (financial !== forecast) assert.throws(() => parseStrictJson(financial));
  }
  for (const raw of vectors.invalidFinancial)
    assert.throws(() => parseStrictJson(raw, { codec: 'financial_json_v1' }));
});
test('financial float grammar differentially matches fixed 10000 finite Python float encodings', () => {
  const run = spawnSync(
    'python3',
    [
      '-c',
      `import random,struct,json,math
r=random.Random(9817)
values=[]
while len(values)<10000:
 x=struct.unpack('>d',r.getrandbits(64).to_bytes(8,'big'))[0]
 if math.isfinite(x):values.append(json.dumps(x,allow_nan=False))
print(json.dumps(values))`
    ],
    { encoding: 'utf8', maxBuffer: 2 * 1024 * 1024 }
  );
  assert.equal(run.status, 0, run.stderr);
  for (const token of JSON.parse(run.stdout))
    assert.doesNotThrow(() => parseStrictJson(token, { codec: 'financial_json_v1' }), token);
});
test('format discriminator, snapshot codec, closure and execution fail closed', async () => {
  const f = financialBundleFixture();
  await validateFinancialManifest(f.manifestText, f.bundleId);
  await assert.rejects(validateManifest(f.manifestText));
  for (const change of [
    (m) => (m.kind = 'execution'),
    (m) => (m.documents.snapshot.codec = 'forecast_json_v1'),
    (m) => (m.sourceEvidence.admissionProfile = 'invented'),
    (m) => (m.sourceEvidence.datasetRef.version = 1),
    (m) =>
      (m.documents.snapshot.parts = m.documents.snapshot.parts.map((p) =>
        p.literal
          ? {
              literal: p.literal.replace(
                'separate_research_dataset_v2',
                'separate_research_dataset_v1'
              )
            }
          : p
      ))
  ]) {
    const m = structuredClone(f.manifest);
    change(m);
    await assert.rejects(validateFinancialManifest(canonical(m)));
  }
});

const script = await buildWorkerSource({
  wrapper: `
import {financialBundleRunnerApi} from './edge/financial-bundles/runner-api.mjs';
const assertRunDataset=async(env,job)=>{
 const row=await env.DB.prepare('SELECT evidence,manifest FROM test_dataset_admission WHERE job_id=? AND owner=? AND ready=1').bind(job.id,job.owner).first();
 if(!row)throw Object.assign(new Error('Dataset revoked'),{code:'DATASET_REVOKED',status:409});
 return {sourceEvidence:JSON.parse(row.evidence),manifest:JSON.parse(row.manifest)};
};
export default {async fetch(req,env,ctx){
 const path=new URL(req.url).pathname.replace(/^\\/quant\\/api/,'');
 try {if(path.startsWith('/runner/')&&req.headers.get('authorization')==='Bearer financial-bundle-test'){
   const r=await financialBundleRunnerApi(req,env,path,{assertRunDataset});if(r)return r;
 }}catch(e){return Response.json({error:{code:e.code||'TEST_ERROR',message:e.message}},{status:e.status||400});}
 return productionWorker.fetch(req,env,ctx);
}};`
});
const mf = new Miniflare({
  modules: true,
  script,
  compatibilityDate: '2026-08-01',
  d1Databases: ['DB'],
  r2Buckets: ['ARTIFACTS'],
  bindings: { RUNNER_SECRET: 'financial-bundle-test' }
});
const db = await mf.getD1Database('DB'),
  bucket = await mf.getR2Bucket('ARTIFACTS');
await db.exec(
  (await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' ')
);
await db.exec(
  'CREATE TABLE test_dataset_admission(job_id TEXT PRIMARY KEY,owner TEXT,evidence TEXT,ready INTEGER,manifest TEXT)'
);
const call = (path, { data, raw, cookie, headers = {} } = {}) =>
  mf.dispatchFetch('https://atlas.test/quant/api' + path, {
    method: data !== undefined ? 'POST' : raw !== undefined ? 'PUT' : 'GET',
    headers: {
      'content-type': 'application/json',
      ...(cookie ? { cookie } : {}),
      ...(path.startsWith('/runner/') ? { authorization: 'Bearer financial-bundle-test' } : {}),
      ...headers
    },
    ...(data !== undefined
      ? { body: JSON.stringify(data) }
      : raw !== undefined
        ? { body: raw }
        : {})
  });
const payload = async (response, status = 200) => {
  const data = await response.json();
  assert.equal(response.status, status, JSON.stringify(data));
  return data;
};
async function queued(f) {
  const cookie = (await call('/session')).headers.get('set-cookie').split(';')[0];
  const created = await payload(
    await call('/runs', {
      cookie,
      data: { strategy: f.strategy, dataSource: 'demo' }
    }),
    202
  );
  const job = (
    await payload(
      await call('/runner/claim', {
        data: {
          engineVersion: '0.8.0',
          transportFormats: ['atlas.quant.bundle/1', 'atlas.quant.financial_bundle/1']
        }
      })
    )
  ).job;
  assert.equal(job.id, created.job.id);
  const owner = (await db.prepare('SELECT owner FROM jobs WHERE id=?').bind(job.id).first()).owner;
  await db
    .prepare('INSERT INTO test_dataset_admission VALUES(?,?,?,1,?)')
    .bind(job.id, owner, JSON.stringify(f.sourceEvidence), JSON.stringify(f.sourceDatasetManifest))
    .run();
  return { cookie, job, owner };
}
const packet = (f, q, s) => ({
  id: q.job.id,
  leaseToken: q.job.leaseToken,
  bundleId: f.bundleId,
  stageId: s.stageId
});
const begin = (f, q) =>
  call('/runner/financial-bundles/begin', {
    data: {
      id: q.job.id,
      leaseToken: q.job.leaseToken,
      bundleId: f.bundleId,
      manifestText: f.manifestText
    }
  });
async function upload(f, q, s) {
  for (const [key, raw] of f.chunks) {
    const [c, n] = key.split(':');
    await payload(
      await call('/runner/financial-bundles/' + f.bundleId + '/chunks/' + c + '/' + n, {
        raw,
        headers: {
          'X-Quant-Job': q.job.id,
          'X-Quant-Lease': q.job.leaseToken,
          'X-Quant-Stage': s.stageId
        }
      })
    );
  }
}
test.after(() => mf.dispose());

test('actual D1/R2 publishes typed snapshot with old forecast identity and owner private readback', async () => {
  const f = financialBundleFixture(),
    q = await queued(f),
    s = await payload(await begin(f, q));
  await upload(f, q, s);
  await payload(await call('/runner/bundles/finalize', { data: packet(f, q, s) }), 409);
  await payload(await call('/runner/financial-bundles/finalize', { data: packet(f, q, s) }));
  await payload(await call('/runner/financial-bundles/complete', { data: packet(f, q, s) }));
  const summary = await payload(await call('/runs/' + q.job.id + '/report', { cookie: q.cookie }));
  assert.equal(summary.transport.format, 'atlas.quant.financial_bundle');
  assert.equal(summary.transport.executionEligible, false);
  assert.deepEqual(summary.transport.sourceEvidence, f.sourceEvidence);
  const page = await payload(
    await call('/runs/' + q.job.id + '/report/pages?collection=forecasts&bundleId=' + f.bundleId, {
      cookie: q.cookie
    })
  );
  assert.equal(page.items.length, f.forecast.rows.length);
  const archive = await call('/runs/' + q.job.id + '/report/bundle?bundleId=' + f.bundleId, {
    cookie: q.cookie
  });
  const bytes = Buffer.from(await archive.arrayBuffer());
  assert(bytes.includes(Buffer.from('"negativeZero":-0.0')));
  assert(bytes.includes(Buffer.from('"positiveFloat":1.0')));
  const other = (await call('/session')).headers.get('set-cookie').split(';')[0];
  assert.equal((await call('/runs/' + q.job.id + '/report', { cookie: other })).status, 404);
  await db.prepare('UPDATE test_dataset_admission SET ready=0 WHERE job_id=?').bind(q.job.id).run();
  assert.equal(
    (
      await payload(
        await call('/runner/financial-bundles/complete', {
          data: packet(f, q, s)
        })
      )
    ).idempotent,
    true
  );
});
test('real HTTP archive retains exact manifest and financial chunk bytes', async () => {
  const f = financialBundleFixture(),
    q = await queued(f),
    s = await payload(await begin(f, q));
  await upload(f, q, s);
  await payload(await call('/runner/financial-bundles/finalize', { data: packet(f, q, s) }));
  await payload(await call('/runner/financial-bundles/complete', { data: packet(f, q, s) }));
  const base = await mf.ready;
  const response = await fetch(
    new URL('/quant/api/runs/' + q.job.id + '/report/bundle?bundleId=' + f.bundleId, base),
    { headers: { cookie: q.cookie, 'accept-encoding': 'identity' } }
  );
  assert.equal(response.status, 200);
  const raw = Buffer.from(await response.arrayBuffer());
  assert.equal(raw.length, Number(response.headers.get('content-length')));
  let offset = 0;
  const entries = new Map();
  while (raw[offset] !== 0) {
    const header = raw.subarray(offset, offset + 512);
    const name = header.subarray(0, 100).toString().split('\0')[0];
    const length = Number.parseInt(header.subarray(124, 136).toString().replaceAll('\0', ''), 8);
    assert(!entries.has(name));
    entries.set(name, raw.subarray(offset + 512, offset + 512 + length));
    offset += 512 + Math.ceil(length / 512) * 512;
  }
  assert.equal(raw.length - offset, 1024);
  assert(raw.subarray(offset).every((byte) => byte === 0));
  assert.equal(entries.size, f.chunks.size + 1);
  assert.equal(entries.get('manifest.json').toString(), f.manifestText);
  for (const [key, text] of f.chunks) {
    const [collection, ordinal] = key.split(':');
    assert.equal(entries.get('chunks/' + collection + '/' + ordinal + '.json').toString(), text);
  }
});
test('fresh dataset admission gates begin, chunk, finalize and first publication without reviving state', async () => {
  const f = financialBundleFixture(),
    q = await queued(f);
  const ready = async (value) =>
    db
      .prepare('UPDATE test_dataset_admission SET ready=? WHERE job_id=?')
      .bind(value, q.job.id)
      .run();
  await ready(0);
  await payload(await begin(f, q), 409);
  await ready(1);
  const s = await payload(await begin(f, q));
  await upload(f, q, s);
  await ready(0);
  await payload(await call('/runner/financial-bundles/finalize', { data: packet(f, q, s) }), 409);
  await ready(1);
  await payload(await call('/runner/financial-bundles/finalize', { data: packet(f, q, s) }));
  await ready(0);
  await payload(await call('/runner/financial-bundles/complete', { data: packet(f, q, s) }), 409);
  assert.equal(
    (await db.prepare('SELECT status FROM jobs WHERE id=?').bind(q.job.id).first()).status,
    'running'
  );
  await ready(1);
  await payload(await call('/runner/financial-bundles/complete', { data: packet(f, q, s) }));
});
test('self-declared dataset roots cannot replace owner admission, even after complete rehash', async () => {
  const f = financialBundleFixture(),
    q = await queued(f);
  await db
    .prepare('UPDATE test_dataset_admission SET evidence=? WHERE job_id=?')
    .bind(
      JSON.stringify({
        ...f.sourceEvidence,
        datasetRef: {
          ...f.sourceEvidence.datasetRef,
          datasetRoot: 'f'.repeat(64)
        }
      }),
      q.job.id
    )
    .run();
  await payload(await begin(f, q), 409);
  assert.equal(
    (
      await db
        .prepare('SELECT count(*) n FROM quant_bundle_stages WHERE job_id=?')
        .bind(q.job.id)
        .first()
    ).n,
    0
  );
  await db.prepare("UPDATE jobs SET status='failed' WHERE id=?").bind(q.job.id).run();
});

test('old upload/complete and replay cannot acquire new financial semantics', async () => {
  const f = financialBundleFixture(),
    q = await queued(f),
    s = await payload(await begin(f, q));
  const [key, raw] = [...f.chunks][0],
    [collection, ordinal] = key.split(':');
  await payload(
    await call('/runner/bundles/' + f.bundleId + '/chunks/' + collection + '/' + ordinal, {
      raw,
      headers: {
        'X-Quant-Job': q.job.id,
        'X-Quant-Lease': q.job.leaseToken,
        'X-Quant-Stage': s.stageId
      }
    }),
    409
  );
  await upload(f, q, s);
  await payload(await call('/runner/financial-bundles/finalize', { data: packet(f, q, s) }));
  await payload(await call('/runner/complete', { data: packet(f, q, s) }), 409);
  await payload(await call('/runner/financial-bundles/complete', { data: packet(f, q, s) }));
  const response = await call('/statistical-quant/executions', {
    cookie: q.cookie,
    data: {
      forecastArtifactId: f.manifest.forecastArtifactId,
      execution: { enabled: true }
    }
  });
  // Preserve the failing admission assertion without allowing an unexpected
  // queued job to be claimed by the following independent lease tests.
  if (response.status === 202)
    await db
      .prepare("UPDATE jobs SET status='failed' WHERE owner=? AND status='queued'")
      .bind(q.owner)
      .run();
  assert.notEqual(
    response.status,
    202,
    'Financial forecast must not enter executable replay queue'
  );
});
test('expired or cancelled financial lease cannot revive staged results', async () => {
  for (const expired of [false, true]) {
    const f = financialBundleFixture(),
      q = await queued(f),
      s = await payload(await begin(f, q));
    await upload(f, q, s);
    if (expired)
      await db
        .prepare("UPDATE jobs SET lease_until='2000-01-01T00:00:00.000Z' WHERE id=?")
        .bind(q.job.id)
        .run();
    else await db.prepare("UPDATE jobs SET status='cancelled' WHERE id=?").bind(q.job.id).run();
    const reply = await payload(
      await call('/runner/financial-bundles/finalize', {
        data: packet(f, q, s)
      })
    );
    assert.equal(reply.terminalDiscard, true);
    assert.equal(
      (
        await db
          .prepare('SELECT count(*) n FROM quant_bundle_runs WHERE job_id=?')
          .bind(q.job.id)
          .first()
      ).n,
      0
    );
  }
});

test('complete rehash of altered typed snapshot still fails source payload equality', async () => {
  const f=financialBundleFixture();
  const collection=f.manifest.collections.find(c=>c.id==='snapshotRows');
  const descriptor=collection.chunks[0], key='snapshotRows:0';
  const old=f.chunks.get(key), changed=old.replace('"positiveFloat":1.0','"positiveFloat":1');
  assert.notEqual(changed,old);
  f.chunks.set(key,changed);
  descriptor.sha256=hash(changed);descriptor.byteLength=Buffer.byteLength(changed);
  const snapshot=f.manifest.documents.snapshot;
  const full=snapshot.parts.map(p=>p.literal??'['+collection.chunks.map(d=>f.chunks.get('snapshotRows:'+d.ordinal).slice(1,-1)).join(',')+']').join('');
  snapshot.sha256=hash(full);snapshot.byteLength=Buffer.byteLength(full);
  f.manifest.totals.chunkBytes=[...f.chunks.values()].reduce((n,x)=>n+Buffer.byteLength(x),0);
  f.manifestText=canonical(f.manifest);f.bundleId=hash(f.manifestText);
  const q=await queued(f),s=await payload(await begin(f,q));await upload(f,q,s);
  const rejected=await payload(await call('/runner/financial-bundles/finalize',{data:packet(f,q,s)}),409);
  assert.equal(rejected.error.code,'FINANCIAL_BUNDLE_SOURCE');
  assert.equal((await db.prepare('SELECT status FROM quant_bundle_stages WHERE id=?').bind(s.stageId).first()).status,'staging');
  await db.prepare("UPDATE jobs SET status='failed' WHERE id=?").bind(q.job.id).run();
});
