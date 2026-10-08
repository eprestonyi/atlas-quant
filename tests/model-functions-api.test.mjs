/** Synthetic transport fixture with actual fitted numeric golden functions; no alpha evidence. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from '../scripts/worker-source.mjs';
import {bundleFixture} from './fixtures/bundle-fixture.mjs';
const golden=JSON.parse(await fs.readFile(new URL('../engine/tests/fixtures/model-function-golden-v1.json',import.meta.url),'utf8')).cases[1];
const fitted=JSON.parse(await fs.readFile(new URL('./fixtures/model-source-ridge-v1.json',import.meta.url),'utf8'));
const mf=new Miniflare({modules:true,script:await buildWorkerSource(),compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],bindings:{RUNNER_SECRET:'test-model-functions-only'}});
const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('ARTIFACTS');
const migration=await fs.readFile(new URL('../edge/migrations/0010_model_functions.sql',import.meta.url),'utf8');
const schema=await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8');
await db.exec(schema.replace(migration,'').replaceAll('\n',' '));
await db.prepare("INSERT INTO meta(key,value,updated_at) VALUES('migration-sentinel','preserve','test')").run();
await db.exec(migration.replaceAll('\n',' '));await db.exec(migration.replaceAll('\n',' '));
test.after(()=>mf.dispose());
const call=(path,{data,cookie,runner=false,method=data===undefined?'GET':'POST',headers={}}={})=>mf.dispatchFetch('https://atlas.test/quant/api'+path,{method,headers:{...(data!==undefined?{'content-type':'application/json'}:{}),...(cookie?{cookie}:{}),...(runner?{authorization:'Bearer test-model-functions-only'}:{}),...headers},...(data===undefined?{}:{body:JSON.stringify(data)})});
const payload=async(response,status=200)=>{const b=await response.json();assert.equal(response.status,status,JSON.stringify(b));return b;};
const session=async()=>(await call('/session')).headers.get('set-cookie').split(';')[0];
const cookie=await session(),other=await session();
const f=bundleFixture({mutate:({forecast,report})=>{
  forecast.sourceStrategy=structuredClone(fitted.strategy);
  report.strategy=forecast.sourceStrategy;
  forecast.modelFits[0]=structuredClone(fitted.fit);
  forecast.factorResearch={diagnostics:{features:[{name:'test',distribution:{mean:1}}],dependence:{jointDistributions:[{x:'a',y:'b',counts:[[2]],probabilities:[[1]]}]}}};
}});
async function publish(f) {
const job=(await payload(await call('/runs',{cookie,data:{strategy:f.strategy,dataSource:'demo'}}),202)).job;
const claim=(await payload(await call('/runner/claim',{runner:true,data:{engineVersion:'0.4.0',transportFormats:['atlas.quant.bundle/1']}}))).job;
assert.equal(claim.id,job.id);
const stage=await payload(await call('/runner/bundles/begin',{runner:true,data:{id:job.id,leaseToken:claim.leaseToken,bundleId:f.bundleId,manifestText:f.manifestText}}));
for(const [key,raw] of f.chunks){const [collection,ordinal]=key.split(':');await payload(await mf.dispatchFetch(`https://atlas.test/quant/api/runner/bundles/${f.bundleId}/chunks/${collection}/${ordinal}`,{method:'PUT',headers:{'content-type':'application/json',authorization:'Bearer test-model-functions-only','X-Quant-Job':job.id,'X-Quant-Lease':claim.leaseToken,'X-Quant-Stage':stage.stageId},body:raw}));}
const packet={id:job.id,leaseToken:claim.leaseToken,bundleId:f.bundleId,stageId:stage.stageId};
await payload(await call('/runner/bundles/finalize',{runner:true,data:packet}));await payload(await call('/runner/complete',{runner:true,data:packet}));
return job;
}
const job=await publish(f);
const source={runId:job.id,bundleId:f.bundleId,modelFitId:'m'};
let saved;
test('additive migration and private pinned model source resolve',async()=>{
  assert.equal((await db.prepare("SELECT value FROM meta WHERE key='migration-sentinel'").first()).value,'preserve');
  const r=await payload(await call('/model-functions/resolve',{cookie,data:{source}}));assert.deepEqual(r.artifact,golden.artifact);
  await payload(await call('/model-functions/resolve',{cookie:other,data:{source}}),404);
  await payload(await call('/model-functions/resolve',{data:{source}}),401);
  await payload(await call('/model-functions/resolve',{cookie,data:{source:{...source,bundleId:'a'.repeat(64)}}}),409);
  await payload(await call('/model-functions/resolve',{cookie,data:{source},headers:{origin:'https://other.test'}}),403);
});
test('factor diagnostics use bounded report pages and leave original bundle intact',async()=>{
  for(const collection of ['factorFeatures','factorJointDistributions']){const r=await payload(await call(`/runs/${job.id}/report/pages?bundleId=${f.bundleId}&collection=${collection}`,{cookie}));assert.equal(r.items.length,1);}
  const s=await payload(await call(`/runs/${job.id}/report`,{cookie}));assert.equal(s.report.forecasts.factorResearch.diagnostics.features,undefined);
});
test('server numerical inference matches golden and edited inference is not validation',async()=>{
  const r=await payload(await call('/model-functions/evaluate',{cookie,data:{source,input:golden.input}}));assert.equal(r.inferenceOnly,true);assert.equal(r.newValidationPerformed,false);
  r.result.normalizedChanges.forEach((x,i)=>x.forEach((v,j)=>assert(Math.abs(v-golden.expected.normalizedChanges[i][j])<1e-12)));
  const edits=[{path:'/estimator/intercepts/1',value:.1}];
  const e=await payload(await call('/model-functions/evaluate',{cookie,data:{source,input:golden.input,edits}}));assert.equal(e.result.evidenceStatus,'UNVALIDATED_USER_EDIT');assert.notEqual(e.result.artifactId,golden.artifact.artifactId);
  await payload(await call('/model-functions/evaluate',{cookie,data:{source,input:{rows:[{}]}}}),400);
  await payload(await call('/model-functions/evaluate',{cookie:other,data:{source,input:golden.input}}),404);
});
test('concurrent identical saves return one immutable receipt; changed retry is rejected',async()=>{
  const request={source,edits:[{path:'/estimator/intercepts/1',value:.1}],name:'Edited F',requestId:crypto.randomUUID()};
  const rs=await Promise.all([call('/model-functions/derive',{cookie,data:request}),call('/model-functions/derive',{cookie,data:request})]);
  const replies=await Promise.all(rs.map(x=>payload(x)));saved=replies[0];assert.equal(saved.item.id,replies[1].item.id);assert.equal(saved.artifact.lineage.status,'UNVALIDATED_USER_EDIT');
  assert.equal((await payload(await call('/model-functions/derive',{cookie,data:request}))).idempotent,true);
  await payload(await call('/model-functions/derive',{cookie,data:{...request,name:'Changed'}}),409);
  assert.equal((await payload(await call('/model-functions',{cookie}))).items.length,1);assert.equal((await payload(await call('/model-functions',{cookie:other}))).items.length,0);
  const read=await payload(await call(`/model-functions/${saved.item.id}?artifactId=${saved.artifact.artifactId}`,{cookie}));assert.deepEqual(read.artifact,saved.artifact);
  await payload(await call(`/model-functions/${saved.item.id}?artifactId=${saved.artifact.artifactId}`,{cookie:other}),404);
  await payload(await call(`/model-functions/${saved.item.id}?artifactId=${'f'.repeat(64)}`,{cookie}),409);
  assert.deepEqual((await payload(await call('/model-functions/resolve',{cookie,data:{source}}))).artifact,golden.artifact);
});
test('derived source can be edited further, but tree or transform overrides cannot be injected',async()=>{
  const r=await payload(await call('/model-functions/derive',{cookie,data:{source:saved.item.ref,edits:[{path:'/estimator/intercepts/0',value:.5}],name:'Second version',requestId:crypto.randomUUID()}}));
  assert.equal(r.artifact.lineage.parentArtifactId,saved.artifact.artifactId);
  await payload(await call('/model-functions/derive',{cookie,data:{source,edits:[{path:'/transforms/scaleMean/0',value:2}],name:'Invalid',requestId:crypto.randomUUID()}}),400);
  const row=await db.prepare('SELECT * FROM quant_model_functions WHERE id=?').bind(r.item.id).first();await bucket.put(row.object_key,'{}');
  const damaged=await payload(await call(`/model-functions/${r.item.id}?artifactId=${r.artifact.artifactId}`,{cookie}),503);assert.equal(damaged.error.code,'FUNCTION_INTEGRITY');
});
test('a self-valid golden from another scope and training fit cannot inherit a report source',async()=>{
  // Exact former positive fixture: single-asset trend report with a three-asset
  // mean-reversion F and unrelated training dates. Every bundle hash is valid.
  const foreign=bundleFixture({mutate:({forecast})=>{forecast.modelFits[0].functionArtifact=golden.artifact;}});
  const foreignJob=await publish(foreign),wrong={runId:foreignJob.id,bundleId:foreign.bundleId,modelFitId:'m'};
  for (const [path,data] of [
    ['/model-functions/resolve',{source:wrong}],
    ['/model-functions/evaluate',{source:wrong,input:golden.input}],
    ['/model-functions/derive',{source:wrong,edits:[{path:'/estimator/intercepts/1',value:.1}],name:'Foreign F',requestId:crypto.randomUUID()}]
  ]) assert.equal((await payload(await call(path,{cookie,data}),503)).error.code,'FUNCTION_SOURCE_MISMATCH');
  // Rejection preserves the complete, inspectable original report.
  const read=await payload(await call(`/runs/${foreignJob.id}/report/detail?bundleId=${foreign.bundleId}&collection=modelFits&id=m`,{cookie}));
  assert.deepEqual(read.item.functionArtifact,golden.artifact);
});
test('old reports without an exported function remain explicit historical read-only records',async()=>{
  const legacy=bundleFixture(),job=await publish(legacy);
  assert.equal((await payload(await call('/model-functions/resolve',{cookie,data:{source:{runId:job.id,bundleId:legacy.bundleId,modelFitId:'m'}}}),409)).error.code,'FUNCTION_NOT_EXPORTED');
});
