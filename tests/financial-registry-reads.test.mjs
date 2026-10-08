/** Count real D1 statements through the actual runner HTTP route. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';

const script = await buildWorkerSource({
  wrapper: `
export default {async fetch(req,env,ctx){
 let registryReads=0,queries=0,maxBindings=0,r2Reads=0;
 const DB={prepare(sql){queries++;if(sql.includes('FROM financial_registry_entries'))registryReads++;
  const stmt=env.DB.prepare(sql);return new Proxy(stmt,{get(target,key){
   if(key==='bind')return (...params)=>{maxBindings=Math.max(maxBindings,params.length);return target.bind(...params)};
   const v=target[key];return typeof v==='function'?v.bind(target):v;
  }});
 },batch:env.DB.batch.bind(env.DB),exec:env.DB.exec.bind(env.DB)};
 const ARTIFACTS={get(...args){r2Reads++;return env.ARTIFACTS.get(...args)}};
 const response=await productionWorker.fetch(req,{...env,DB,ARTIFACTS},ctx);
 const headers=new Headers(response.headers);
 for(const [key,value] of Object.entries({registryReads,queries,maxBindings,r2Reads}))headers.set('x-test-'+key,String(value));
 return new Response(response.body,{status:response.status,headers});
}};`,
});
const schema = (
  await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')
).replaceAll('\n', ' ');
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');

async function fixture(t, count = 32) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS'],
    bindings: { RUNNER_SECRET: 'registry-read-test' },
  });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB'),
    bucket = await mf.getR2Bucket('ARTIFACTS');
  await db.exec(schema);
  const owner = randomUUID(),
    input = randomUUID(),
    job = randomUUID(),
    lease = randomUUID();
  const calendar = randomUUID(),
    proofs = Array.from({ length: count }, () => randomUUID());
  const refs = [calendar, ...proofs],
    raw = Buffer.from('{"explicitTransportFixture":true}');
  const now = new Date().toISOString(),
    future = new Date(Date.now() + 120000).toISOString();
  const statements = refs.map((ref, index) =>
    db
      .prepare(
        `INSERT INTO financial_registry_entries
    (id,kind,owner,object_key,sha256,byte_length,metadata,created_at) VALUES(?,?,?,?,?,?,'{}',?)`
      )
      .bind(
        ref,
        index ? 'unit_proof' : 'calendar',
        owner,
        'registry/' + ref,
        sha(raw),
        raw.length,
        now
      )
  );
  for (let n = 0; n < statements.length; n += 64) await db.batch(statements.slice(n, n + 64));
  await bucket.put('registry/' + calendar, raw);
  await bucket.put('registry/' + proofs[0], raw);
  await db
    .prepare(
      `INSERT INTO financial_inputs
    (id,owner,name,status,calendar_ref,proof_refs,declared_bytes,source_hash,source_bytes,request_id,request_hash,created_at,updated_at)
    VALUES(?,?,'Queue transport fixture','validating',?,?,?,?,?,?,?,?,?)`
    )
    .bind(
      input,
      owner,
      calendar,
      JSON.stringify(proofs),
      raw.length,
      sha(raw),
      raw.length,
      randomUUID(),
      sha(raw),
      now,
      now
    )
    .run();
  await db
    .prepare(
      `INSERT INTO financial_jobs
    (id,owner,input_id,kind,status,spec,request_id,request_hash,lease_token,lease_until,deadline,created_at,updated_at)
    VALUES(?,?,?,'financial_validate','running','{}',?,?,?,?,?,?,?)`
    )
    .bind(job, owner, input, randomUUID(), sha(raw), lease, future, future, now, now)
    .run();
  const request = (tail = 'input') =>
    mf.dispatchFetch(`https://quant.test/quant/api/runner/financial/jobs/${job}/${tail}`, {
      headers: { authorization: 'Bearer registry-read-test', 'X-Financial-Lease': lease },
    });
  return { db, bucket, owner, input, calendar, proofs, raw, request };
}
const count = (response, key) => Number(response.headers.get('x-test-' + key));

test('33 registry descriptors use one bounded D1 select; each body reads only its own entry', async (t) => {
  const f = await fixture(t);
  const metadata = await f.request();
  assert.equal(metadata.status, 200);
  assert.equal(count(metadata, 'registryReads'), 1);
  assert.equal(count(metadata, 'maxBindings'), 34);
  const data = await metadata.json();
  assert.equal(data.calendar.ref, f.calendar);
  assert.deepEqual(
    data.proofs.map((x) => x.ref),
    f.proofs
  );
  for (const ref of [f.calendar, f.proofs[0]]) {
    const response = await f.request('registry/' + ref);
    assert.equal(response.status, 200);
    assert.equal(count(response, 'registryReads'), 1);
    assert.equal(count(response, 'r2Reads'), 1);
    assert.equal(count(response, 'queries'), 3);
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), f.raw);
  }
  const outsider = await f.request('registry/' + randomUUID());
  assert.equal(outsider.status, 404);
  assert.equal(count(outsider, 'registryReads'), 0);
  assert.equal(count(outsider, 'r2Reads'), 0);
});

test('maximum 257 descriptors use five selects within 65 bind values and retain original order', async (t) => {
  const f = await fixture(t, 256),
    response = await f.request();
  assert.equal(response.status, 200);
  assert.equal(count(response, 'registryReads'), 5);
  assert.equal(count(response, 'maxBindings'), 65);
  assert.deepEqual(
    (await response.json()).proofs.map((x) => x.ref),
    f.proofs
  );
});

test('batched metadata and direct downloads preserve owner, active and kind authorization', async (t) => {
  const f = await fixture(t);
  for (const [column, value, restore] of [
    ['owner', randomUUID(), f.owner],
    ['status', 'revoked', 'active'],
    ['kind', 'calendar', 'unit_proof'],
  ]) {
    await f.db
      .prepare(`UPDATE financial_registry_entries SET ${column}=? WHERE id=?`)
      .bind(value, f.proofs[0])
      .run();
    for (const route of ['input', 'registry/' + f.proofs[0]]) {
      const response = await f.request(route);
      assert.equal(response.status, 404, column + ':' + route);
      assert.equal(count(response, 'r2Reads'), 0);
    }
    await f.db
      .prepare(`UPDATE financial_registry_entries SET ${column}=? WHERE id=?`)
      .bind(restore, f.proofs[0])
      .run();
  }
  // Existing explicit global registry semantics are preserved, not newly granted.
  await f.db
    .prepare("UPDATE financial_registry_entries SET owner='*' WHERE id=?")
    .bind(f.proofs[0])
    .run();
  assert.equal((await f.request()).status, 200);
  assert.equal((await f.request('registry/' + f.proofs[0])).status, 200);
  for (const refs of [[randomUUID()], ['invalid-uuid'], [f.proofs[0], f.proofs[0]]]) {
    await f.db
      .prepare('UPDATE financial_inputs SET proof_refs=? WHERE id=?')
      .bind(JSON.stringify(refs), f.input)
      .run();
    const response = await f.request();
    assert.equal(response.status, refs[0] === 'invalid-uuid' || refs.length === 2 ? 400 : 404);
  }
});

test('descriptor size budgets and direct R2 size/hash checks remain enforced', async (t) => {
  const f = await fixture(t, 129);
  await f.db
    .prepare('UPDATE financial_registry_entries SET byte_length=?')
    .bind(256 * 1024)
    .run();
  let response = await f.request();
  assert.equal(response.status, 413);
  assert.equal((await response.json()).error.code, 'REGISTRY_BYTE_BUDGET');
  await f.db
    .prepare('UPDATE financial_registry_entries SET byte_length=?')
    .bind(f.raw.length)
    .run();
  await f.db
    .prepare('UPDATE financial_registry_entries SET byte_length=? WHERE id=?')
    .bind(256 * 1024 + 1, f.proofs[0])
    .run();
  assert.equal((await f.request()).status, 413);
  assert.equal((await f.request('registry/' + f.proofs[0])).status, 409);
  await f.db
    .prepare('UPDATE financial_registry_entries SET byte_length=? WHERE id=?')
    .bind(f.raw.length, f.proofs[0])
    .run();
  await f.bucket.put('registry/' + f.proofs[0], Buffer.alloc(f.raw.length, 120));
  response = await f.request('registry/' + f.proofs[0]);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error.code, 'REGISTRY_INTEGRITY');
});
