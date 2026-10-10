import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateFunction,evaluateFunction,deriveFunction,functionDigest} from '../web/model-function-runtime.js';
import {assertFunctionSource} from '../edge/model-functions/source.mjs';

const golden = JSON.parse(readFileSync(new URL('../engine/tests/fixtures/model-function-v4-return-modes-golden.json',import.meta.url)));
const near = (x,y) => assert.ok(Math.abs(x-y) <= 1e-12*Math.max(1,Math.abs(x),Math.abs(y)),`${x} differs from ${y}`);
for (const c of golden.cases) test('real fitted return modes and conditional inverse: '+c.name,async () => {
  await validateFunction(c.artifact);
  assertFunctionSource(c.artifact,{...c.fitAudit,status:'valid',fitDate:c.artifact.training.informationCutoff},c.strategy);
  for (let k=0;k<c.inputs.length;k++) {
    const input = c.inputs[k], actual = await evaluateFunction(c.artifact,input), expected = c.expected[k];
    assert.deepEqual(Object.keys(actual).sort(),Object.keys(expected).sort());
    for (const key of ['artifactId','mode','outputUnit','scenarioOnly','evidenceStatus']) assert.equal(actual[key],expected[key]);
    for (let i=0;i<c.fittedPrediction.length;i++) {
      near(actual.predictedResponse[i],c.fittedPrediction[i]);
      near(actual.predictedResponse[i],expected.predictedResponse[i]);
      const scale = input.originVolatility ? input.originVolatility[i]*Math.sqrt(c.artifact.scope.horizonSessions) : 1;
      near(actual.simpleReturns[i],c.fittedPrediction[i]*scale);
      near(actual.conditionalPrices[i],input.originPrice[i]*(1+c.fittedPrediction[i]*scale));
    }
    for (const key of ['expectedEntry','expectedFuture','levels','normalizedChanges']) assert.equal(Object.hasOwn(actual,key),false);
  }
  const estimator = c.artifact.estimator;
  const path = estimator.kind === 'constant' ? '/estimator/value/0' : estimator.kind === 'histogram_trees' ? '/estimator/outputs/0/baseline' : '/estimator/intercepts/0';
  const changed = await deriveFunction(c.artifact,[{path,value:.023}]);
  assert.deepEqual(changed.featureConstruction,c.artifact.featureConstruction); assert.equal(changed.lineage.status,'UNVALIDATED_USER_EDIT');
  const rerun = await evaluateFunction(changed,c.inputs.at(-1)); assert.equal(rerun.evidenceStatus,'UNVALIDATED_USER_EDIT');
  assert.equal(rerun.scenarioOnly,c.inputs.at(-1).mode === 'future_scenario');
});

test('real association functions reject forged clock, normalization and output claims after rehash',async () => {
  const original = golden.cases.find(c => c.name === 'ridge-association-trailing_volatility-h5').artifact;
  for (const mutate of [
    a => a.featureConstruction.targetSpecification.normalization.ddof = true,
    a => a.featureConstruction.inputs[0].timing.horizonSessions = true,
    a => a.featureConstruction.inputs[0].aggregation = 'signed_origin_dollar_over_gross',
    a => a.featureConstruction.inputs[0].clock = 'observed_source_sessions_asof',
    a => a.outputs[0] = 'asset_return',
    a => a.estimator.coefficients.push([...a.estimator.coefficients[0]])
  ]) {
    const artifact = structuredClone(original); mutate(artifact); delete artifact.artifactId; artifact.artifactId = await functionDigest(artifact);
    await assert.rejects(validateFunction(artifact));
  }
  const rows = golden.cases.find(c => c.artifact.artifactId === original.artifactId).inputs[0].rows;
  for (const input of [{rows,mode:'association',originPrice:null},{rows,mode:'association',originVolatility:null},{rows,mode:'forecast'},{rows,mode:'future_scenario'}]) await assert.rejects(evaluateFunction(original,input));
});
