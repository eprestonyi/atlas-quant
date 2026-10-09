/** Python-packed frozen numeric fixtures -> Worker upload/detail -> private F APIs.
 * Synthetic transport evidence only. This test fits no estimator and calls no provider.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from '../scripts/worker-source.mjs';
import {bundleFixture} from './fixtures/bundle-fixture.mjs';
import {validateManifest} from '../edge/bundles/manifest.mjs';

const golden = JSON.parse(await fs.readFile(new URL('../engine/tests/fixtures/model-function-v3-golden.json', import.meta.url), 'utf8')).cases[0];
const capabilities = {engineVersion:'0.11.0', transportFormats:['atlas.quant.bundle/1'], functionSearchFormats:['factor-model-search/1']};
const secret = 'isolated-model-search-function-fixture';
const mf = new Miniflare({modules:true, script:await buildWorkerSource(), compatibilityDate:'2026-08-01', d1Databases:['DB'], r2Buckets:['ARTIFACTS'], bindings:{RUNNER_SECRET:secret}});
const db = await mf.getD1Database('DB');
await db.exec((await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' '));
test.after(() => mf.dispose());
const call = (path, {data, cookie, runner=false, method=data===undefined?'GET':'POST'}={}) => mf.dispatchFetch('https://atlas.test/quant/api'+path, {method, headers:{...(data!==undefined?{'content-type':'application/json'}:{}), ...(cookie?{cookie}:{}), ...(runner?{authorization:'Bearer '+secret}:{})}, ...(data===undefined?{}:{body:JSON.stringify(data)})});
const payload = async (response, status=200) => {const b=await response.json(); assert.equal(response.status,status,JSON.stringify(b)); return b;};
const session = async () => (await call('/session')).headers.get('set-cookie').split(';')[0];
const owner = await session(), other = await session();

function pack(f, extra={}) {
  const result=spawnSync(process.env.PYTHON || '.venv/bin/python', ['tests/helpers/model-search-bundle.py'], {
    input:JSON.stringify({report:f.report,snapshot:f.snapshot,coverage:f.coverage,syntheticEnvelope:true,...extra}),
    encoding:'utf8', env:{...process.env,PYTHONPATH:'engine'}, timeout:30000, maxBuffer:16*1024**2
  });
  assert.equal(result.status,0,result.stderr || String(result.error));
  const packed=JSON.parse(result.stdout);
  assert.equal(packed.providerCalls,0); assert.equal(packed.estimatorCalls,0); assert.equal(packed.verification.verified,true);
  return {...packed, strategy:f.strategy};
}

function fixture({perTarget=false, mutate}={}) {
  return pack(bundleFixture({mutate:({forecast,report}) => {
    const strategy=structuredClone(golden.strategy);
    strategy.model={...strategy.model,estimator:'auto',search:{schema:'factor-model-search/1'},parameterSharing:perTarget?'per_target':'pooled'};
    if(perTarget) strategy.universe.symbols.push('600000.SH');
    forecast.sourceStrategy=strategy; report.strategy=strategy; report.engineVersion='0.11.0'; report.research.executionOnly=false;
    const fit={...structuredClone(golden.fitAudit),id:'m',status:'valid',fitDate:golden.artifact.training.informationCutoff};
    if(perTarget) Object.assign(fit,{targetId:'t',targetSymbol:'000001.SZ'});
    forecast.modelFits=[{...structuredClone(fit),functionArtifact:structuredClone(golden.artifact)}];
    const id=(perTarget?'t::':'')+'polynomial_ridge:0';
    const candidate={id,status:'valid',estimator:fit.estimator,params:structuredClone(fit.params),selected:false,baseline:false,
      validationScore:.01,withinTolerance:true,fit:{...fit,id:'candidate_fit_t'},functionArtifact:structuredClone(golden.artifact),
      ...(perTarget?{targetId:'t',symbols:['000001.SZ']}:{})};
    forecast.diagnostics={holdoutStart:'20250102',modelSearch:{schema:'factor-model-search-report/1',
      parameterSharing:perTarget?'per_target':'pooled',selectedCandidateId:perTarget?'per_target':'no_change:0',
      researchCandidateId:id,freezeCutoff:fit.fitDate,usesTerminalOutcomes:false,trainingPlotIsOutOfSample:false,
      researchCandidateIsDeploymentQualified:false,candidates:[candidate]}};
    if(mutate) mutate({forecast,report,candidate,fit});
  }}));
}

async function publish(f) {
  await payload(await call('/runner/heartbeat',{runner:true,data:capabilities}));
  const job=(await payload(await call('/runs',{cookie:owner,data:{strategy:f.strategy,dataSource:'demo'}}),202)).job;
  const claim=(await payload(await call('/runner/claim',{runner:true,data:capabilities}))).job;
  assert.equal(claim.id,job.id);
  const stage=await payload(await call('/runner/bundles/begin',{runner:true,data:{id:job.id,leaseToken:claim.leaseToken,bundleId:f.bundleId,manifestText:f.manifestText}}));
  for(const [key,raw] of Object.entries(f.chunks)) {
    const [collection,ordinal]=key.split(':');
    await payload(await mf.dispatchFetch(`https://atlas.test/quant/api/runner/bundles/${f.bundleId}/chunks/${collection}/${ordinal}`,{method:'PUT',headers:{'content-type':'application/json',authorization:'Bearer '+secret,'X-Quant-Job':job.id,'X-Quant-Lease':claim.leaseToken,'X-Quant-Stage':stage.stageId},body:raw}));
  }
  const packet={id:job.id,leaseToken:claim.leaseToken,bundleId:f.bundleId,stageId:stage.stageId};
  await payload(await call('/runner/bundles/finalize',{runner:true,data:packet}));
  await payload(await call('/runner/complete',{runner:true,data:packet}));
  return job;
}

test('absent optional candidates preserve legacy Python manifest and chunk bytes', async () => {
  const f=bundleFixture({mutate:({forecast,report})=>{forecast.diagnostics.holdoutStart='20250102';report.research.executionOnly=false;}});
  const packed=pack(f,{verifyLegacyBytes:true}); assert.equal(packed.legacyBytesUnchanged,true);
  const parsed=await validateManifest(packed.manifestText,packed.bundleId);
  assert.equal(parsed.collections.has('modelSearchCandidates'),false);
});

test('non-winning candidate survives Python pack, private detail, summary, evaluation and immutable derive',async()=>{
  const f=fixture(), job=await publish(f), id='polynomial_ridge:0';
  const source={runId:job.id,bundleId:f.bundleId,modelSearchCandidateId:id};
  const summary=(await payload(await call(`/runs/${job.id}/report`,{cookie:owner}))).report.forecasts.diagnostics.modelSearch;
  assert.equal(summary.researchCandidateId,id); assert.equal(summary.selectedCandidateId,'no_change:0');
  assert.equal(summary.candidateCount,1); assert.equal(summary.candidates,undefined); assert.equal(summary.usesTerminalOutcomes,false);
  const page=await payload(await call(`/runs/${job.id}/report/pages?bundleId=${f.bundleId}&collection=modelSearchCandidates`,{cookie:owner}));
  assert.equal(page.items[0].id,id);
  const detail=(await payload(await call(`/runs/${job.id}/report/detail?bundleId=${f.bundleId}&collection=modelSearchCandidates&id=${id}`,{cookie:owner}))).item;
  assert.deepEqual(detail.functionArtifact,golden.artifact); assert.equal(detail.selected,false);
  assert.deepEqual((await payload(await call('/model-functions/resolve',{cookie:owner,data:{source}}))).artifact,golden.artifact);
  const inference=await payload(await call('/model-functions/evaluate',{cookie:owner,data:{source,input:{rows:golden.rows}}}));
  assert.equal(inference.newValidationPerformed,false);
  inference.result.normalizedChanges.forEach((row,i)=>row.forEach((v,j)=>assert(Math.abs(v-golden.expected.normalizedChanges[i][j])<1e-10)));
  const request={source,edits:[{path:'/estimator/coefficients/1/0',value:.2}],name:'Edited candidate',requestId:crypto.randomUUID()};
  const edited=await payload(await call('/model-functions/derive',{cookie:owner,data:request}));
  assert.equal(edited.artifact.lineage.status,'UNVALIDATED_USER_EDIT');
  assert.equal(edited.artifact.lineage.parentArtifactId,golden.artifact.artifactId);
  assert.equal((await payload(await call('/model-functions/derive',{cookie:owner,data:request}))).idempotent,true);
  assert.deepEqual((await payload(await call('/model-functions/resolve',{cookie:owner,data:{source}}))).artifact,golden.artifact);
  for(const path of ['resolve','evaluate','derive']) {
    const data=path==='derive'?{...request,requestId:crypto.randomUUID()}:path==='evaluate'?{source,input:{rows:golden.rows}}:{source};
    await payload(await call('/model-functions/'+path,{cookie:other,data}),404);
  }
  await payload(await call(`/runs/${job.id}/report/detail?bundleId=${f.bundleId}&collection=modelSearchCandidates&id=${id}`,{cookie:other}),404);
  await payload(await call(`/model-functions/${edited.item.id}?artifactId=${edited.artifact.artifactId}`,{cookie:other}),404);
  await payload(await call('/model-functions/resolve',{cookie:owner,data:{source:{...source,bundleId:'a'.repeat(64)}}}),409);
});

test('per-target candidates bind both single-symbol scope and frozen target identity',async()=>{
  const f=fixture({perTarget:true}),job=await publish(f);
  const source={runId:job.id,bundleId:f.bundleId,modelSearchCandidateId:'t::polynomial_ridge:0'};
  const actual=(await payload(await call('/model-functions/resolve',{cookie:owner,data:{source}}))).artifact;
  assert.deepEqual(actual.scope.symbols,['000001.SZ']);
  for(const mutate of [
    ({candidate})=>{candidate.fit.targetSymbol='600000.SH';},
    ({forecast})=>{forecast.targetDefinitions[0].symbols=['600000.SH'];},
    ({candidate})=>{delete candidate.fit.targetId;},
    ({candidate})=>{candidate.params.alpha+=1;},
    ({candidate})=>{candidate.fit.basisFit.termCenter[0]+=.01;},
    ({candidate})=>{candidate.status='invalid';}
  ]) {
    const broken=fixture({perTarget:true,mutate}), badJob=await publish(broken);
    const ref={runId:badJob.id,bundleId:broken.bundleId,modelSearchCandidateId:'t::polynomial_ridge:0'};
    const rejected=await payload(await call('/model-functions/resolve',{cookie:owner,data:{source:ref}}),503);
    assert.equal(rejected.error.code,'FUNCTION_SOURCE_MISMATCH');
    await payload(await call(`/runs/${badJob.id}/report`,{cookie:owner}));
  }
});
