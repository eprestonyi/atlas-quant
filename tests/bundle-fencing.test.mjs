import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
import { bundleFixture } from './fixtures/bundle-fixture.mjs';

// Interleave a real D1 cancellation after R2 accepts a chunk but before its
// transactional index/receipt write. No production cancellation hook is added.
const script = await buildWorkerSource({ wrapper: `export default {async fetch(req,env,ctx) {
 const wrapped={...env,ARTIFACTS:{get:(...a)=>env.ARTIFACTS.get(...a),delete:(...a)=>env.ARTIFACTS.delete(...a),put:async(key,...args)=>{
  const result=await env.ARTIFACTS.put(key,...args);
  if(key.includes('/forecasts/')) {
   const stop=await env.DB.prepare("SELECT value FROM meta WHERE key='test_bundle_cancel_on_put'").first();
   if(stop) await env.DB.prepare("UPDATE jobs SET status='cancelled' WHERE id=? AND status='running'").bind(stop.value).run();
  }
  return result;
 }}};
 return productionWorker.fetch(req,wrapped,ctx);
}};` });
const mf = new Miniflare({ modules: true, script, compatibilityDate: '2026-08-01',
  d1Databases: ['DB'], r2Buckets: ['ARTIFACTS'], bindings: { RUNNER_SECRET: 'isolated-fencing-test' } });
const db = await mf.getD1Database('DB'), bucket = await mf.getR2Bucket('ARTIFACTS');
await db.exec((await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' '));
test.after(() => mf.dispose());

async function call(path, { method = 'GET', data, cookie, runner = false, raw, headers = {} } = {}) {
  return mf.dispatchFetch('https://atlas.test/quant/api' + path, { method,
    headers: { ...(cookie ? { cookie } : {}), ...(runner ? { authorization: 'Bearer isolated-fencing-test' } : {}),
      ...(data !== undefined || raw !== undefined ? { 'content-type': 'application/json' } : {}), ...headers },
    ...(data !== undefined ? { body: JSON.stringify(data) } : raw !== undefined ? { body: raw } : {}) });
}
async function answer(response, status = 200) {
  const body = await response.json();
  assert.equal(response.status, status, JSON.stringify(body));
  return body;
}
async function session() { return (await call('/session')).headers.get('set-cookie').split(';')[0]; }
async function claimed(strategy, cookie = null) {
  cookie ??= await session();
  const queued = await answer(await call('/runs', { method: 'POST', cookie, data: { strategy, dataSource: 'demo' } }), 202);
  const job = (await answer(await call('/runner/claim', { method: 'POST', runner: true,
    data: { engineVersion: '0.4.0', transportFormats: ['atlas.quant.bundle/1'] } }))).job;
  assert.equal(job.id, queued.job.id);
  return { job, cookie };
}
const identity = (job) => ({ id: job.id, leaseToken: job.leaseToken });
async function begin(fixture, job, status = 200) {
  return answer(await call('/runner/bundles/begin', { method: 'POST', runner: true,
    data: { ...identity(job), bundleId: fixture.bundleId, manifestText: fixture.manifestText } }), status);
}
async function put(fixture, job, stage, key) {
  const [collection, ordinal] = key.split(':');
  return call(`/runner/bundles/${fixture.bundleId}/chunks/${collection}/${ordinal}`, {
    method: 'PUT', runner: true, raw: fixture.chunks.get(key), headers: {
      'X-Quant-Job': job.id, 'X-Quant-Lease': job.leaseToken, 'X-Quant-Stage': stage.stageId } });
}
async function upload(fixture, job, stage) {
  for (const key of fixture.chunks.keys()) await answer(await put(fixture, job, stage, key));
}
const packet = (fixture, job, stage) => ({ ...identity(job), bundleId: fixture.bundleId, stageId: stage.stageId });
const finalize = (fixture, job, stage) => call('/runner/bundles/finalize', {
  method: 'POST', runner: true, data: packet(fixture, job, stage) });
const complete = (fixture, job, stage) => call('/runner/complete', {
  method: 'POST', runner: true, data: packet(fixture, job, stage) });
async function noPublication(job) {
  for (const table of ['quant_bundle_runs', 'quant_forecast_artifacts']) {
    assert.equal((await db.prepare(`SELECT count(*) n FROM ${table} WHERE job_id=?`).bind(job.id).first()).n, 0);
  }
}

test('cancellation between R2 put and D1 receipt cannot publish or leave indexed partial rows', async () => {
  const fixture = bundleFixture(), { job, cookie } = await claimed(fixture.strategy), stage = await begin(fixture, job);
  await db.prepare("INSERT INTO meta(key,value,updated_at) VALUES('test_bundle_cancel_on_put',?,'test')").bind(job.id).run();
  try {
    const reply = await answer(await put(fixture, job, stage, 'forecasts:0'));
    assert.equal(reply.terminalDiscard, true);
    assert.equal(reply.status, 'cancelled');
    for (const table of ['quant_bundle_records', 'quant_bundle_chunks']) {
      assert.equal((await db.prepare(`SELECT count(*) n FROM ${table} WHERE stage_id=?`).bind(stage.stageId).first()).n, 0);
    }
    const descriptor = fixture.manifest.collections.find(c => c.id === 'forecasts').chunks[0];
    const stored = await db.prepare('SELECT owner FROM jobs WHERE id=?').bind(job.id).first();
    assert.equal(await bucket.get(`bundle/${stored.owner}/${stage.stageId}/forecasts/0-${descriptor.sha256}.json`), null);
    await noPublication(job);
    const report = await answer(await call(`/runs/${job.id}/report`, { cookie }));
    assert.equal(report.report, null);
    assert.equal((await answer(await complete(fixture, job, stage))).terminalDiscard, true);
  } finally { await db.prepare("DELETE FROM meta WHERE key='test_bundle_cancel_on_put'").run(); }
});

test('expired leases cannot finalize or publish an already verified bundle', async () => {
  for (const verified of [false, true]) {
    const fixture = bundleFixture(), { job } = await claimed(fixture.strategy), stage = await begin(fixture, job);
    await upload(fixture, job, stage);
    if (verified) await answer(await finalize(fixture, job, stage));
    await db.prepare("UPDATE jobs SET lease_until='2000-01-01T00:00:00.000Z' WHERE id=?").bind(job.id).run();
    const response = await answer(await (verified ? complete(fixture, job, stage) : finalize(fixture, job, stage)));
    assert.equal(response.terminalDiscard, true); assert.equal(response.status, 'failed');
    assert.notEqual((await db.prepare('SELECT status FROM quant_bundle_stages WHERE id=?').bind(stage.stageId).first()).status, 'committed');
    await noPublication(job);
  }
});

let published;
test('verified is private; a real SQL publication failure rolls back every reference and can retry', async () => {
  const fixture = bundleFixture(), { job, cookie } = await claimed(fixture.strategy), stage = await begin(fixture, job);
  await upload(fixture, job, stage); await answer(await finalize(fixture, job, stage));
  assert.equal((await answer(await call(`/runs/${job.id}/report`, { cookie }))).report, null);
  assert.equal((await call(`/statistical-quant/forecasts/${fixture.manifest.forecastArtifactId}`, { cookie })).status, 404);
  assert.equal((await call('/statistical-quant/executions', { method: 'POST', cookie,
    data: { forecastArtifactId: fixture.manifest.forecastArtifactId } })).status, 404);
  await db.exec("CREATE TRIGGER test_block_bundle_commit BEFORE UPDATE OF status ON jobs WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'injected publication failure'); END");
  try {
    assert.equal((await complete(fixture, job, stage)).status, 500);
    assert.equal((await db.prepare('SELECT status FROM jobs WHERE id=?').bind(job.id).first()).status, 'running');
    assert.equal((await db.prepare('SELECT status FROM quant_bundle_stages WHERE id=?').bind(stage.stageId).first()).status, 'verified');
    await noPublication(job);
    assert.equal((await db.prepare('SELECT count(*) n FROM quant_model_versions WHERE owner=(SELECT owner FROM jobs WHERE id=?)').bind(job.id).first()).n, 0);
  } finally { await db.exec('DROP TRIGGER test_block_bundle_commit'); }
  await answer(await complete(fixture, job, stage));
  assert.equal((await answer(await complete(fixture, job, stage))).idempotent, true);
  published = { fixture, job, cookie, stage };
});

test('stage identity is fenced by job even when content bundleId is exactly the same', async () => {
  const { fixture, stage } = published, { job, cookie } = await claimed(fixture.strategy);
  const own = await begin(fixture, job);
  assert.notEqual(own.stageId, stage.stageId);
  assert.equal((await put(fixture, job, stage, 'forecasts:0')).status, 409);
  assert.equal((await db.prepare('SELECT count(*) n FROM quant_bundle_chunks WHERE stage_id=?').bind(own.stageId).first()).n, 0);
  await answer(await call(`/runs/${job.id}/cancel`, { method: 'POST', cookie, data: {} }));
});

test('execution cannot replace the source origin plan while retaining the identical forecast hash', async () => {
  const { fixture, cookie } = published;
  await answer(await call('/statistical-quant/executions', { method: 'POST', cookie,
    data: { forecastArtifactId: fixture.manifest.forecastArtifactId } }), 202);
  const job = (await answer(await call('/runner/claim', { method: 'POST', runner: true,
    data: { engineVersion: '0.4.0', transportFormats: ['atlas.quant.bundle/1'] } }))).job;
  const changed = bundleFixture({ execution: true, sourceForecast: structuredClone(fixture.forecast),
    mutate: ({ coverage, report }) => { coverage.origins[0].inputValid = false; report.strategy = job.strategy; } });
  assert.equal(changed.manifest.forecastArtifactId, fixture.manifest.forecastArtifactId);
  assert.notEqual(changed.manifest.documents.coverage.sha256, fixture.manifest.documents.coverage.sha256);
  await begin(changed, job, 409);
  await noPublication(job);
  await answer(await call(`/runs/${job.id}/cancel`, { method: 'POST', cookie, data: {} }));
});
