import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {functionDigest,validateFunction,evaluateFunction,deriveFunction,ModelFunctionError} from '../web/model-function-runtime.js';
const golden=JSON.parse(await fs.readFile(new URL('../engine/tests/fixtures/model-function-golden-v1.json',import.meta.url),'utf8'));
const seal=async(a)=>{delete a.artifactId;a.artifactId=await functionDigest(a);return a;};
function close(a,b){assert.equal(typeof a,typeof b);if(typeof a==='number')assert(Math.abs(a-b)<=1e-12*Math.max(1,Math.abs(b)),`${a} differs from ${b}`);else if(Array.isArray(a)){assert.equal(a.length,b.length);a.forEach((x,i)=>close(x,b[i]));}else if(a&&typeof a==='object'){assert.deepEqual(Object.keys(a).sort(),Object.keys(b).sort());for(const k of Object.keys(a))close(a[k],b[k]);}else assert.equal(a,b);}
test('Python actual fitted-model golden predictions and canonical identities match JS',async()=>{
  assert.equal(await functionDigest(golden.identityGolden.value),golden.identityGolden.sha256);
  for(const c of golden.cases){await validateFunction(c.artifact);const r=await evaluateFunction(c.artifact,c.input);close(r.normalizedChanges,c.expected.normalizedChanges);close(r.levels,c.expected.levels);}
});
test('editing coefficients is immutable, changes identity, and cannot inherit fitted evidence',async()=>{
  const c=golden.cases.find(x=>x.name==='ridge'),old=JSON.stringify(c.artifact);
  const edited=await deriveFunction(c.artifact,[{path:'/estimator/intercepts/1',value:c.artifact.estimator.intercepts[1]+.02}]);
  assert.equal(JSON.stringify(c.artifact),old);assert.notEqual(edited.artifactId,c.artifact.artifactId);
  assert.equal(edited.lineage.parentArtifactId,c.artifact.artifactId);assert.equal(edited.lineage.status,'UNVALIDATED_USER_EDIT');
  const orig=await evaluateFunction(c.artifact,c.input),after=await evaluateFunction(edited,c.input);
  after.normalizedChanges.forEach((x,i)=>close(x[1]-orig.normalizedChanges[i][1],.02));
  assert.deepEqual(edited.scope,c.artifact.scope);assert.deepEqual(edited.training,c.artifact.training);
  for(const path of ['/scope/symbols/0','/transforms/imputeMedian/0','/estimator/__proto__/polluted','/estimator/coefficients/0/99999'])await assert.rejects(deriveFunction(c.artifact,[{path,value:1}]),ModelFunctionError);
});
test('tree edits are restricted to baseline and leaves, never splits/topology',async()=>{
  const a=golden.cases.find(x=>x.name==='hist_gradient_boosting').artifact,t=a.estimator.outputs[0].trees[0];
  const leaf=t.findIndex(x=>x[5]===1),branch=t.findIndex(x=>x[5]===0);
  assert(leaf>=0&&branch>=0);
  const edit=await deriveFunction(a,[{path:`/estimator/outputs/0/trees/0/${leaf}/0`,value:.03}]);
  assert.equal(edit.estimator.outputs[0].trees[0][leaf][0],.03);
  for(const suffix of [`${branch}/0`,`${branch}/2`,`${leaf}/3`])await assert.rejects(deriveFunction(a,[{path:'/estimator/outputs/0/trees/0/'+suffix,value:1}]),ModelFunctionError);
});
test('tampering and well-hashed malformed metadata are rejected',async()=>{
  const base=golden.cases[1].artifact;
  const mutations=[a=>a.identity.e='P+V',a=>a.lineage={},a=>a.scope.generalizationOutsideScopeValidated=true,a=>a.scope.symbols.push(a.scope.symbols[0]),a=>a.training.labelEndMax=a.training.informationCutoff,a=>a.training.trainStart='20240230',a=>a.provenance.parameters={alpha:0},a=>a.featureConstruction.targetSpecification.horizonSessions=60,a=>a.editPolicy.arbitraryCode=true];
  for(const mutate of mutations){const a=structuredClone(base);mutate(a);await seal(a);await assert.rejects(validateFunction(a),ModelFunctionError);}
  const a=structuredClone(base);a.estimator.intercepts[0]+=1;await assert.rejects(validateFunction(a),ModelFunctionError);
  await assert.rejects(functionDigest({a:{$f64:'3ff0000000000000'}}),ModelFunctionError);
  await assert.rejects(functionDigest({a:Infinity}),ModelFunctionError);
  const cycle={};cycle.x=cycle;await assert.rejects(validateFunction(cycle),ModelFunctionError);
});
test('numerical overflow and mismatched feature/context shapes fail before returning V',async()=>{
  const c=golden.cases[1],a=structuredClone(c.artifact);
  for(const input of [{rows:[{}]}, {...c.input,scale:[1]}, {...c.input,scale:c.input.rows.map(()=>0)}, {rows:Array(257).fill(c.input.rows[0])}])await assert.rejects(evaluateFunction(a,input),ModelFunctionError);
  const key=a.inputSchema[0].name;
  await assert.rejects(evaluateFunction(a,{rows:[{...c.input.rows[0],[key]:true}]}),ModelFunctionError);
  a.transforms.winsorLower=a.transforms.winsorUpper=null;a.transforms.scaleMean=a.inputSchema.map(()=>-1e308);a.transforms.scaleScale=a.inputSchema.map(()=>1);await seal(a);
  await assert.rejects(evaluateFunction(a,{rows:[Object.fromEntries(a.inputSchema.map(x=>[x.name,1e308]))]}),ModelFunctionError);
});
