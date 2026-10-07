import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from '../scripts/worker-source.mjs';

const script=await buildWorkerSource({buildId:'claim-idempotence-test'});
const mf=new Miniflare({modules:true,script,compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],bindings:{RUNNER_SECRET:'claim-test-secret'}});
const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('ARTIFACTS');
await db.exec((await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8')).replaceAll('\n',' '));
const strategy=JSON.parse(await fs.readFile(new URL('../engine/examples/basic.json',import.meta.url),'utf8'));
async function seed(id=randomUUID(),spec=strategy,datasetKey=null) {
  const now=new Date().toISOString();
  await db.prepare("INSERT INTO jobs(id,owner,name,status,data_source,spec,dataset_key,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?,?)")
    .bind(id,'owner-'+id,'Claim fixture',datasetKey?'upload':'demo',JSON.stringify(spec),datasetKey,now,now).run();
  return id;
}
async function claim(requestId=randomUUID(),extra={}) {
  return mf.dispatchFetch('https://atlas.test/quant/api/runner/claim',{method:'POST',headers:{authorization:'Bearer claim-test-secret','content-type':'application/json'},body:JSON.stringify({engineVersion:'0.4.0',...(requestId===undefined?{}:{requestId}),...extra})});
}
async function read(id){return db.prepare('SELECT * FROM jobs WHERE id=?').bind(id).first();}
test.beforeEach(async()=>{
  await db.batch([db.prepare('DELETE FROM runner_claims'),db.prepare('DELETE FROM jobs'),db.prepare('DELETE FROM meta')]);
});
test.after(()=>mf.dispose());

test('lost response retries return original job and lease while next job stays queued',async()=>{
  const first=await seed('first'),second=await seed('second'),requestId=randomUUID();
  const ignored=await claim(requestId);assert.equal(ignored.status,200);await ignored.arrayBuffer();
  const before=await read(first);assert.equal(before.status,'running');
  const retried=await (await claim(requestId)).json();
  assert.equal(retried.job.id,first);assert.equal(retried.job.leaseToken,before.lease_token);
  assert.deepEqual(retried.claim,{requestId,status:'running',jobId:first});
  assert.equal((await read(second)).status,'queued');
  assert.equal((await read(first)).lease_until,before.lease_until,'claim retry does not extend the lease');
});

test('concurrent identical request IDs atomically reserve exactly one job',async()=>{
  await Promise.all(Array.from({length:5},(_,i)=>seed('same-'+i)));
  const requestId=randomUUID();
  const responses=await Promise.all(Array.from({length:16},()=>claim(requestId)));
  assert.ok(responses.every(r=>r.status===200));
  const values=await Promise.all(responses.map(r=>r.json()));
  assert.equal(new Set(values.map(r=>r.job.id)).size,1);
  assert.equal(new Set(values.map(r=>r.job.leaseToken)).size,1);
  assert.equal((await db.prepare("SELECT count(*) n FROM jobs WHERE status='running'").first()).n,1);
  assert.equal((await db.prepare('SELECT count(*) n FROM runner_claims').first()).n,1);
});

test('different request IDs and legacy requests cannot claim the same queued job',async()=>{
  await Promise.all(Array.from({length:10},(_,i)=>seed('different-'+i)));
  const replies=await Promise.all(Array.from({length:12},(_,i)=>claim(randomUUID(),i%2?{}:{requestId:undefined})));
  const values=await Promise.all(replies.map(r=>r.json()));
  const jobs=values.flatMap(x=>x.job?[x.job.id]:[]);
  assert.equal(jobs.length,10);assert.equal(new Set(jobs).size,10);
  assert.ok(replies.every(r=>r.status===200));
});

test('mapping insert and job state update roll back together on a database error',async()=>{
  const jobId=await seed('rollback'),requestId=randomUUID();
  await db.exec("CREATE TRIGGER claim_abort BEFORE UPDATE OF status ON jobs WHEN NEW.status='running' BEGIN SELECT RAISE(ABORT,'claim test rollback'); END");
  try {
    assert.equal((await claim(requestId)).status,500);
    assert.equal((await read(jobId)).status,'queued');
    assert.equal((await db.prepare('SELECT count(*) n FROM runner_claims').first()).n,0);
  } finally {await db.exec('DROP TRIGGER claim_abort');}
  const retry=await (await claim(requestId)).json();assert.equal(retry.job.id,jobId);
});

for(const terminal of ['completed','failed','cancelled']) {
  test(`terminal ${terminal} receipt never allocates another job`,async()=>{
    const jobId=await seed('terminal'),requestId=randomUUID();await claim(requestId);
    await db.prepare('UPDATE jobs SET status=? WHERE id=?').bind(terminal,jobId).run();
    const next=await seed('next');const result=await (await claim(requestId)).json();
    assert.deepEqual(result,{job:null,claim:{requestId,status:terminal,jobId}});
    assert.equal((await read(next)).status,'queued');
  });
}

test('expired lease becomes failed receipt and cannot be revived by claim retry',async()=>{
  const jobId=await seed('expired'),requestId=randomUUID();const first=await (await claim(requestId)).json();
  await db.prepare('UPDATE jobs SET lease_until=? WHERE id=?').bind('2000-01-01T00:00:00.000Z',jobId).run();
  const next=await seed('next');const reply=await (await claim(requestId)).json();
  assert.deepEqual(reply,{job:null,claim:{requestId,status:'failed',jobId}});
  const row=await read(jobId);assert.equal(JSON.parse(row.error).code,'RUNNER_INTERRUPTED');
  assert.equal(row.lease_token,first.job.leaseToken);assert.equal((await read(next)).status,'queued');
});

test('maintenance permits recovery of existing claim but blocks new reservations',async()=>{
  const jobId=await seed('recover'),requestId=randomUUID();const first=await (await claim(requestId)).json();
  const next=await seed('next');await db.prepare("INSERT INTO meta VALUES('runner_maintenance','paused',?)").bind(new Date().toISOString()).run();
  assert.deepEqual(await (await claim(requestId)).json(),first);
  const newId=randomUUID();assert.deepEqual(await (await claim(newId)).json(),{job:null,claim:{requestId:newId,status:'empty'}});
  assert.equal((await read(next)).status,'queued');assert.equal((await read(jobId)).status,'running');
});

test('empty queue receipt reserves nothing and same unreserved request can later claim',async()=>{
  const requestId=randomUUID();assert.deepEqual(await (await claim(requestId)).json(),{job:null,claim:{requestId,status:'empty'}});
  assert.equal((await db.prepare('SELECT count(*) n FROM runner_claims').first()).n,0);
  const jobId=await seed();assert.equal((await (await claim(requestId)).json()).job.id,jobId);
});

test('request ID validates before reserving work and auth remains required',async()=>{
  const jobId=await seed();
  for(const requestId of [null,42,[],{},'',randomUUID().toUpperCase(),'../../credentials']) {
    const response=await claim(requestId);assert.equal(response.status,400);assert.equal((await response.json()).error.code,'INVALID_CLAIM_REQUEST');
  }
  const response=await mf.dispatchFetch('https://atlas.test/quant/api/runner/claim',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({requestId:randomUUID()})});assert.equal(response.status,401);
  assert.equal((await read(jobId)).status,'queued');assert.equal((await db.prepare('SELECT count(*) n FROM runner_claims').first()).n,0);
});

test('0.3 runner cannot claim schema2 but can claim eligible legacy task behind it',async()=>{
  const forecast=await seed('forecast',{schemaVersion:2}),legacy=await seed('legacy');
  await db.prepare("UPDATE jobs SET created_at='2000-01-01T00:00:00Z' WHERE id=?").bind(forecast).run();
  const result=await (await claim(randomUUID(),{engineVersion:'0.3.0'})).json();
  assert.equal(result.job.id,legacy);assert.equal((await read(forecast)).status,'queued');
});

test('failure after claim commit recovers mapping and upload payload on exact retry',async()=>{
  const key='claim-fixture/data.json';await bucket.put(key,'not JSON');const jobId=await seed('payload',strategy,key),requestId=randomUUID();
  assert.equal((await claim(requestId)).status,500);
  const first=await read(jobId);assert.equal(first.status,'running');
  const dataset={rows:[{ts_code:'000001.SZ',trade_date:'20230103',close:10}],provenance:{source:'SYNTHETIC'}};
  await bucket.put(key,JSON.stringify(dataset));
  const retry=await (await claim(requestId)).json();
  assert.equal(retry.job.id,jobId);assert.equal(retry.job.leaseToken,first.lease_token);assert.deepEqual(retry.job.dataset,dataset);
});

test('additive claim migration is idempotent and preserves existing job history',async()=>{
  const jobId=await seed(),requestId=randomUUID();await claim(requestId);
  const migration=await fs.readFile(new URL('../edge/migrations/0004_runner_claims.sql',import.meta.url),'utf8');
  // D1 exec strips line comments before flattening to avoid swallowing SQL.
  const sql=migration.replace(/^--.*$/gm,'').replaceAll('\n',' ');
  await db.exec(sql);await db.exec(sql);
  assert.equal((await (await claim(requestId)).json()).job.id,jobId);
  assert.equal((await db.prepare('SELECT count(*) n FROM runner_claims').first()).n,1);
});
