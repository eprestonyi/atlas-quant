import test from 'node:test';
import assert from 'node:assert/strict';
import {validateFunction,evaluateFunction,deriveFunction,ModelFunctionError} from '../web/model-function-runtime.js';
import {returnFunctionFixture,sealReturnFunction} from './fixtures/model-function-v4.mjs';

const rows = (...values) => values.map(value => ({'factor:market':value}));
const near = (a,b) => assert.ok(Math.abs(a-b) <= 1e-12*Math.max(1,Math.abs(a),Math.abs(b)),`${a} != ${b}`);

test('F4 returns one scalar per row with training transforms applied exactly once',async () => {
  const a = await returnFunctionFixture(), before = JSON.stringify(a);
  const result = await evaluateFunction(a,{rows:rows(.03,null,1,-1),mode:'forecast',originPrice:[100,200,300,400]});
  [.041,.001,.181,-.219].forEach((v,i) => { near(result.predictedResponse[i],v); near(result.simpleReturns[i],v); near(result.conditionalPrices[i],[100,200,300,400][i]*(1+v)); });
  assert.equal(result.outputUnit,'asset_return'); assert.equal(result.mode,'forecast'); assert.equal(result.scenarioOnly,false);
  assert.equal(result.evidenceStatus,'fitted'); assert.equal(JSON.stringify(a),before);
  for (const key of ['normalizedChanges','expectedEntry','expectedFuture','levels']) assert.equal(Object.hasOwn(result,key),false);
});

test('F4 association explicitly separates observed relationship and future-factor scenario',async () => {
  const a = await returnFunctionFixture({mode:'association'});
  const historical = await evaluateFunction(a,{rows:rows(.03),mode:'association'});
  const scenario = await evaluateFunction(a,{rows:rows(.03),mode:'future_scenario',originPrice:[123]});
  assert.deepEqual(scenario.predictedResponse,historical.predictedResponse); assert.equal(scenario.scenarioOnly,true);
  assert.equal(scenario.evidenceStatus,'fitted'); assert.equal(scenario.artifactId,historical.artifactId);
  near(scenario.conditionalPrices[0],123*1.041);
  for (const input of [{rows:rows(0),mode:'forecast'},{rows:rows(0),mode:'future_scenario'},{rows:rows(0)},{rows:rows(0),mode:'association',currentState:[100],scale:[100]}]) await assert.rejects(evaluateFunction(a,input),ModelFunctionError);
  const forecast = await returnFunctionFixture();
  for (const mode of ['association','future_scenario']) await assert.rejects(evaluateFunction(forecast,{rows:rows(0),mode,originPrice:[100]}),ModelFunctionError);
});

test('F4 volatility normalization recovers return only from declared origin volatility',async () => {
  const a = await returnFunctionFixture({mode:'association',normalized:true});
  const basic = await evaluateFunction(a,{rows:rows(.03),mode:'association'});
  assert.equal(basic.outputUnit,'volatility_standardized_asset_return'); assert.equal(Object.hasOwn(basic,'simpleReturns'),false);
  const context = {rows:rows(.03),mode:'future_scenario',originPrice:[100],originVolatility:[.02]};
  const result = await evaluateFunction(a,context), expected = .041*.02*Math.sqrt(5);
  near(result.simpleReturns[0],expected); near(result.conditionalPrices[0],100*(1+expected));
  const withoutPrice = await evaluateFunction(a,{rows:rows(.03),mode:'association',originVolatility:[.02]});
  near(withoutPrice.simpleReturns[0],expected); assert.equal(Object.hasOwn(withoutPrice,'conditionalPrices'),false);
  for (const bad of [null,0,1e-8,-1,NaN,Infinity]) await assert.rejects(evaluateFunction(a,{...context,originVolatility:[bad]}),ModelFunctionError);
  for (const bad of [null,0,-1,NaN,Infinity]) await assert.rejects(evaluateFunction(a,{...context,originPrice:[bad]}),ModelFunctionError);
  for (const patch of [{originVolatility:undefined},{originVolatility:[]},{originPrice:[]},{scale:[1]}]) await assert.rejects(evaluateFunction(a,{...context,...patch}),ModelFunctionError);
  await assert.rejects(evaluateFunction(await returnFunctionFixture(),{rows:rows(0),mode:'forecast',originVolatility:[.02]}),ModelFunctionError);
});

test('F4 scalar nonlinear, tree and baseline evaluators preserve their numeric meaning',async () => {
  const nonlinear = await returnFunctionFixture({kind:'basis_linear'});
  const values = await evaluateFunction(nonlinear,{rows:rows(.03,-.01),mode:'forecast'});
  near(values.predictedResponse[0],.001+.02-.03*Math.log(2)+.04*Math.expm1(1));
  near(values.predictedResponse[1],.001+.02+.03*Math.log(2)-.04*Math.expm1(1));
  const tree = await returnFunctionFixture({kind:'histogram_trees'});
  assert.deepEqual((await evaluateFunction(tree,{rows:rows(.01,.01001,null),mode:'forecast'})).predictedResponse,[-.01,.04,-.01]);
  const baseline = await returnFunctionFixture({kind:'constant'});
  assert.deepEqual((await evaluateFunction(baseline,{rows:rows(null,.02),mode:'forecast'})).predictedResponse,[0,0]);
});

test('F4 annual scenario uses the declared 252-session response scale',async () => {
  const a = await returnFunctionFixture({mode:'association',normalized:true,horizon:252});
  const result = await evaluateFunction(a,{rows:rows(.03),mode:'future_scenario',originPrice:[100],originVolatility:[.02]});
  near(result.simpleReturns[0],.041*.02*Math.sqrt(252));
  near(result.conditionalPrices[0],100*(1+.041*.02*Math.sqrt(252)));
  assert.equal(a.featureConstruction.inputs[0].transform.lag,252);
  assert.equal(result.scenarioOnly,true);
  a.scope.horizonSessions = 253; a.featureConstruction.targetSpecification.horizonSessions = 253;
  a.featureConstruction.inputs[0].transform.lag = 253; a.featureConstruction.inputs[0].timing.horizonSessions = 253;
  await sealReturnFunction(a); await assert.rejects(validateFunction(a),ModelFunctionError);
});

test('F4 edits only its sole output and keep evidence distinct from the fitted parent',async () => {
  for (const [kind,path] of [['linear','/estimator/coefficients/0/0'],['basis_linear','/estimator/coefficients/0/0'],['constant','/estimator/value/0'],['histogram_trees','/estimator/outputs/0/trees/0/1/0']]) {
    const a = await returnFunctionFixture({kind}), edited = await deriveFunction(a,[{path,value:.05}]);
    assert.notEqual(edited.artifactId,a.artifactId); assert.equal(edited.lineage.status,'UNVALIDATED_USER_EDIT');
    assert.deepEqual(edited.featureConstruction,a.featureConstruction); assert.deepEqual(edited.transforms,a.transforms);
    assert.equal((await evaluateFunction(edited,{rows:rows(0),mode:'forecast'})).evidenceStatus,'UNVALIDATED_USER_EDIT');
    await assert.rejects(deriveFunction(a,[{path:path.replace('/0','/1'),value:.05}]),ModelFunctionError);
  }
});

test('well-hashed F4 metadata cannot cross modes, horizons, assets or output contracts',async () => {
  const mutations = [
    a => a.outputs.push('asset_return'), a => a.estimator.coefficients.push([0]), a => a.estimator.intercepts.push(0),
    a => a.scope.symbols.push('000001.SZ'), a => a.scope.studyMode = 'future_scenario', a => a.scope.targetKind = 'asset_price',
    a => a.scope.family = 'pair_reversion', a => a.scope.generalizationOutsideScopeValidated = true,
    a => a.training.labelEndMax = a.training.informationCutoff,
    a => a.featureConstruction.targetSpecification.normalization.kind = 'normal',
    a => a.featureConstruction.targetSpecification.normalization.windowSessions = 20,
    a => a.featureConstruction.targetSpecification.horizonSessions = 6,
    a => a.featureConstruction.factors[0].role = 'hedge',
    a => a.featureConstruction.preprocess.automatic.schema = 'auto-factor-preprocess/1',
    a => a.featureConstruction.inputs[0].aggregation = 'signed_origin_dollar_over_gross',
    a => a.featureConstruction.inputs[0].timing.kind = 'matched_period',
    a => a.featureConstruction.inputs[0].timing.horizonSessions = 1,
    a => a.featureConstruction.inputs[0].transform.lag = 5,
    a => a.featureConstruction.inputs[0].transform.invalid = 'zero',
    a => a.inputSchema[0].name = 'change1', a => a.identity.response = 'output[1]',
    a => a.featureConstruction.inputs = [], a => a.featureConstruction.inputs[0].sourceUnit = '未知',
    a => a.schema = 'atlas-model-function/2'
  ];
  for (const mutate of mutations) { const a = await returnFunctionFixture(); mutate(a); await sealReturnFunction(a); await assert.rejects(validateFunction(a),ModelFunctionError); }
  const a = await returnFunctionFixture({mode:'association'});
  a.featureConstruction.inputs[0].clock = 'observed_source_sessions_asof'; await sealReturnFunction(a);
  await assert.rejects(validateFunction(a),ModelFunctionError);
});
