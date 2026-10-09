import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateFunction,evaluateFunction,deriveFunction,ModelFunctionError} from '../web/model-function-runtime.js';
import {v2Fixture,sealFunction} from './fixtures/model-function-v2.mjs';

test('v2 evaluates already constructed inputs once, with frozen clip/impute/median-IQR', async () => {
  const a = await v2Fixture(), before = JSON.stringify(a);
  const r = await evaluateFunction(a, {rows:[6,null,100,-100].map(value => ({'factor:size':value})),currentState:[100,100,100,100],scale:[10,10,10,10]});
  assert.deepEqual(r.normalizedChanges,[[2.1,3.2],[.1,.2],[5.1,7.7],[-2.9,-4.3]]);
  assert.equal(r.levels[0].expectedFuture,132);
  assert.equal(r.levels[0].e,-32);
  assert.equal(JSON.stringify(a),before);
});

test('v2 rejects inconsistent construction and well-hashed unsupported economic transforms', async () => {
  const mutations = [
    a => a.featureConstruction.schema = 'origin-state-features/1',
    a => delete a.featureConstruction.preprocess.automatic,
    a => a.featureConstruction.preprocess.automatic.extra = true,
    a => a.featureConstruction.automatic.inputStage = 'raw_market_values',
    a => a.featureConstruction.automatic.scaling = 'full_sample_mean_std',
    a => delete a.featureConstruction.automatic.fitPopulation,
    a => a.featureConstruction.automatic.fitPopulation = 'all_asset_rows',
    a => a.featureConstruction.automatic.factors[0].expression = 'close',
    a => a.featureConstruction.automatic.factors[0].direction = 1,
    a => a.featureConstruction.automatic.factors[0].scope = 'global',
    a => a.featureConstruction.automatic.factors[0].transform.invalid = 'zero',
    a => a.featureConstruction.automatic.factors[0].transform = {kind:'log_positive',invalid:'missing',base:10},
    a => a.featureConstruction.automatic.factors[0].transform = {kind:'percent_to_fraction',divisor:10},
    a => a.featureConstruction.automatic.factors[0].transform = {kind:'return_over_trailing_volatility',returnLag:1,volatilityWindow:20,volatilityLag:0,ddof:1,minVolatility:1e-8,invalid:'missing'},
    a => a.featureConstruction.automatic.factors.push(structuredClone(a.featureConstruction.automatic.factors[0])),
    a => a.inputSchema[0].name = 'undeclared',
    a => a.transforms.scaleScale[0] = 0,
    a => a.featureConstruction.preprocess.winsorize = false,
    a => a.featureConstruction.preprocess.standardize = false,
    a => a.schema = 'atlas-model-function/1'
  ];
  for (const mutate of mutations) {
    const a = await v2Fixture(); mutate(a); await sealFunction(a);
    await assert.rejects(validateFunction(a),ModelFunctionError);
  }
});

test('actual Python v2 exports, hashes, inference and edits match the browser runtime', async () => {
  const golden = JSON.parse(readFileSync(new URL('./fixtures/model-function-v2-golden.json', import.meta.url), 'utf8'));
  assert.equal(golden.schema, 'atlas-model-function-golden/2');
  assert.deepEqual(golden.cases.map(c=>c.name), ['no_change','historical_drift','ridge','hist_gradient_boosting']);
  const compare = (actual, expected) => {
    assert.equal(actual.artifactId,expected.artifactId);
    assert.equal(actual.evidenceStatus,expected.evidenceStatus);
    assert.equal(actual.normalizedChanges.length,expected.normalizedChanges.length);
    actual.normalizedChanges.forEach((row,i)=>row.forEach((v,j)=>assert.ok(Math.abs(v-expected.normalizedChanges[i][j]) <= 1e-11 * Math.max(1,Math.abs(v),Math.abs(expected.normalizedChanges[i][j])))));
  };
  for (const c of golden.cases) {
    await validateFunction(c.artifact);
    compare(await evaluateFunction(c.artifact,{rows:c.rows}), c.expected);
    const edited = await deriveFunction(c.artifact,c.edits);
    assert.deepEqual(edited,c.editedArtifact);
    compare(await evaluateFunction(edited,{rows:c.rows}),c.editedExpected);
  }
});

test('v2 derivation preserves construction and frozen transforms without inheriting validation', async () => {
  const a = await v2Fixture();
  const derived = await deriveFunction(a,[{path:'/estimator/coefficients/1/0',value:4}]);
  assert.notEqual(derived.artifactId,a.artifactId);
  assert.deepEqual(derived.featureConstruction,a.featureConstruction);
  assert.deepEqual(derived.transforms,a.transforms);
  assert.equal(derived.lineage.status,'UNVALIDATED_USER_EDIT');
  assert.deepEqual((await evaluateFunction(derived,{rows:[{'factor:size':6}]})).normalizedChanges,[[2.1,4.2]]);
  for (const path of ['/featureConstruction/automatic/factors/0/direction','/transforms/scaleMean/0'])
    await assert.rejects(deriveFunction(a,[{path,value:1}]),ModelFunctionError);
});

test('v2 constant retains factor construction but applies no input transforms', async () => {
  const a = await v2Fixture();
  a.estimator={kind:'constant',value:[0,0]};
  a.provenance.estimator='no_change';a.provenance.parameters={};
  a.transforms=Object.fromEntries(Object.keys(a.transforms).map(key=>[key,null]));
  await sealFunction(a);
  assert.deepEqual((await evaluateFunction(a,{rows:[{'factor:size':null}]})).normalizedChanges,[[0,0]]);
  a.transforms.scaleMean=[0];a.transforms.scaleScale=[1];await sealFunction(a);
  await assert.rejects(validateFunction(a),ModelFunctionError);
});
