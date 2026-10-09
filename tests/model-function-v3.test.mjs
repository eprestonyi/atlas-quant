import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
import {validateFunction,evaluateFunction,deriveFunction,functionDigest} from '../web/model-function-runtime.js';
import {assertFunctionSource} from '../edge/model-functions/source.mjs';
const golden=JSON.parse(readFileSync(new URL('../engine/tests/fixtures/model-function-v3-golden.json',import.meta.url)));
for(const c of golden.cases)test('Python/JS actual fitted nonlinear function: '+c.name,async()=>{
 await validateFunction(c.artifact);const output=await evaluateFunction(c.artifact,{rows:c.rows});
 output.normalizedChanges.forEach((row,i)=>row.forEach((v,j)=>assert.ok(Math.abs(v-c.expected.normalizedChanges[i][j])<1e-10*Math.max(1,Math.abs(v)))));
 const fit={...c.fitAudit,status:'valid',fitDate:c.artifact.training.informationCutoff};
 assert.doesNotThrow(()=>assertFunctionSource(c.artifact,fit,c.strategy));
 const bad=structuredClone(fit);bad.basisFit.termCenter[0]+=.01;assert.throws(()=>assertFunctionSource(c.artifact,bad,c.strategy),/基函数/);
 const edited=await deriveFunction(c.artifact,[{path:'/estimator/coefficients/1/0',value:.01}]);
 assert.equal(edited.lineage.status,'UNVALIDATED_USER_EDIT');assert.notEqual(edited.artifactId,c.artifact.artifactId);
 assert.deepEqual(edited.estimator.terms,c.artifact.estimator.terms);
});
test('well-hashed invalid dictionaries cannot smuggle arbitrary computation',async()=>{
 for(const mutate of [a=>a.estimator.terms[0].kind='eval',a=>a.estimator.terms[0].feature=128,a=>a.estimator.terms[0].degree=4,a=>a.estimator.signedExpm1AbsoluteInputCap=1000,a=>a.estimator.termScale[0]=0,a=>a.schema='atlas-model-function/2']){
  const a=structuredClone(golden.cases[0].artifact);mutate(a);delete a.artifactId;a.artifactId=await functionDigest(a);await assert.rejects(validateFunction(a));
 }
});
