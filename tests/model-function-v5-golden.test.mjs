import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {evaluateFunction,validateFunction,deriveFunction} from '../web/model-function-runtime.js';

// One actual frozen synthetic Python fit, never re-trained by this test.
const g=JSON.parse(await fs.readFile(new URL('./fixtures/model-function-v5-golden.json',import.meta.url),'utf8'));
test('actual Python mixed-time F and browser agree numerically and on conditional evidence',async()=>{
  await validateFunction(g.artifact);
  const output=await evaluateFunction(g.artifact,g.input);
  for(const key of ['artifactId','outputUnit','evidenceStatus','inputEvidenceStatus','availabilityClasses']) assert.deepEqual(output[key],g.expected[key]);
  for(const key of ['predictedResponse','simpleReturns','conditionalPrices']) output[key].forEach((v,i)=>assert.ok(Math.abs(v-g.expected[key][i])<1e-12));
  assert.deepEqual(g.artifact.featureConstruction.inputs.map(x=>x.timing.offsetSessions),[-5,0,1]);
  assert.ok(g.artifact.training.sampleReadyAtMax<g.artifact.training.informationCutoff);
});

test('a frozen mixed-time F changes inputs or parameters without rewriting its fitted evidence',async()=>{
  const before=JSON.stringify(g.artifact),input=structuredClone(g.input);
  input.inputEvidence[0]['factor:future'].valueOrigin='scenario_assumption';
  input.inputEvidence[0]['factor:future'].availableAt=null;
  const assumed=await evaluateFunction(g.artifact,input);
  assert.deepEqual(assumed.predictedResponse,g.expected.predictedResponse);
  assert.deepEqual(assumed.availabilityClasses,['conditional_scenario']);
  const edited=await deriveFunction(g.artifact,[{path:'/estimator/intercepts/0',value:g.artifact.estimator.intercepts[0]+.01}]);
  const changed=await evaluateFunction(edited,input);
  assert.ok(Math.abs(changed.predictedResponse[0]-assumed.predictedResponse[0]-.01)<1e-12);
  assert.equal(changed.evidenceStatus,'UNVALIDATED_USER_EDIT');
  assert.equal(JSON.stringify(g.artifact),before);
});
