import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateFunction,evaluateFunction,deriveFunction,functionDigest} from '../web/model-function-runtime.js';
import {assertFunctionSource} from '../edge/model-functions/source.mjs';
const golden=JSON.parse(readFileSync(new URL('../engine/tests/fixtures/typed-preprocessing-function-golden.json',import.meta.url)));
for(const c of golden.cases)test('actual typed Python F matches portable R-input function: '+c.name,async()=>{
  const before=JSON.stringify(c);
  await validateFunction(c.artifact);
  const output=await evaluateFunction(c.artifact,{rows:c.rows,currentState:c.rows.map(()=>100),scale:c.rows.map(()=>100)});
  output.normalizedChanges.forEach((row,i)=>row.forEach((value,j)=>assert.ok(Math.abs(value-c.expected.normalizedChanges[i][j])<=1e-10*Math.max(1,Math.abs(value)))));
  for(let i=0;i<output.levels.length;i++)for(const key of ['expectedEntry','expectedFuture','e'])assert.ok(Math.abs(output.levels[i][key]-c.expected.levels[i][key])<=1e-10*Math.max(1,Math.abs(output.levels[i][key])));
  const fit={...c.fitAudit,status:'valid',fitDate:c.artifact.training.informationCutoff};
  assert.doesNotThrow(()=>assertFunctionSource(c.artifact,fit,c.strategy));
  const edited=await deriveFunction(c.artifact,[{path:'/estimator/coefficients/1/0',value:.01}]);
  assert.equal(edited.lineage.status,'UNVALIDATED_USER_EDIT');
  assert.deepEqual(edited.featureConstruction,c.artifact.featureConstruction);
  assert.equal(JSON.stringify(c),before,'frozen source is never mutated or retransformed');
});
test('well-hashed altered typed contracts reject undefined units, clocks and override drift',async()=>{
  for(const mutate of [
    a=>delete a.featureConstruction.automatic.stateFeatures,
    a=>a.featureConstruction.automatic.stateFeatures='legacy_price_differences',
    a=>a.featureConstruction.automatic.factors[0].clock='future_calendar',
    a=>a.featureConstruction.automatic.factors[0].sourceUnit='',
    a=>a.featureConstruction.automatic.factors[0].sourceUnit='单位',
    a=>a.featureConstruction.automatic.factors[0].economicType='inferred_by_ai',
    a=>a.featureConstruction.preprocess.automatic.overrides={size:{transform:{kind:'identity'}}},
    a=>a.featureConstruction.preprocess.automatic.schema='auto-factor-preprocess/1',
    a=>a.featureConstruction.automatic.factors[0].transform={kind:'simple_return',lag:2,invalid:'missing'}
  ]){
    const artifact=structuredClone(golden.cases[0].artifact);mutate(artifact);delete artifact.artifactId;artifact.artifactId=await functionDigest(artifact);
    await assert.rejects(validateFunction(artifact));
  }
});
