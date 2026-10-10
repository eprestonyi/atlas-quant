import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateFunction,evaluateFunction,deriveFunction} from '../web/model-function-runtime.js';
import {assertFunctionSource} from '../edge/model-functions/source.mjs';

const golden=JSON.parse(readFileSync(new URL('../engine/tests/fixtures/model-function-v4-golden.json',import.meta.url)));
for (const c of golden.cases) test('actual scalar fitted F Python/JS parity: '+c.name,async()=>{
  await validateFunction(c.artifact);
  const result=await evaluateFunction(c.artifact,c.input);
  for (let i=0;i<c.fittedPrediction.length;i++)
    assert.ok(Math.abs(result.predictedResponse[i]-c.fittedPrediction[i]) <= 1e-12*Math.max(1,Math.abs(c.fittedPrediction[i])));
  assert.equal(result.outputUnit,'asset_return');
  assert.equal(result.mode,'forecast');
  assert.equal(result.scenarioOnly,false);
  assert.equal(result.normalizedChanges,undefined);
  assertFunctionSource(c.artifact,{...c.fitAudit,status:'valid',fitDate:c.artifact.training.informationCutoff},c.strategy);
  const wrong=structuredClone(c.fitAudit); wrong.targetSymbol='600000.SH';
  assert.throws(()=>assertFunctionSource(c.artifact,{...wrong,status:'valid',fitDate:c.artifact.training.informationCutoff},c.strategy));
  if(c.artifact.estimator.kind==='linear' || c.artifact.estimator.kind==='basis_linear') {
    const derived=await deriveFunction(c.artifact,[{path:'/estimator/intercepts/0',value:.123}]);
    assert.equal(derived.lineage.status,'UNVALIDATED_USER_EDIT');
    await assert.rejects(deriveFunction(c.artifact,[{path:'/estimator/intercepts/1',value:.123}]));
  }
});
