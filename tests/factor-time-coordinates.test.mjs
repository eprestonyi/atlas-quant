import test from 'node:test';
import assert from 'node:assert/strict';
import {validateStatisticalQuant} from '../edge/statistical-quant/validation.mjs';
import {assertFactorCapabilities} from '../edge/factor-capabilities.mjs';
import {returnFactorDescriptors} from '../web/asset-return-factor-contract.js';
import {returnFunctionFixture,sealReturnFunction} from './fixtures/model-function-v4.mjs';
import {validateFunction,evaluateFunction} from '../web/model-function-runtime.js';

const timedStudy = () => ({
  schemaVersion:2,name:'Mixed factor clocks',
  universe:{symbols:['000001.SZ','600000.SH'],start:'20230101',end:'20250930'},
  research:{mode:'statistical_quant',returnStudy:{schema:'asset-return-study/2'}},
  target:{kind:'asset_return',horizonSessions:5,normalization:{kind:'none'}},
  factors:[-5,0,1].map((offsetSessions,index)=>({id:'m'+index,baseFactorId:'momentum_20',expression:'returns(close,20)',timing:{anchor:'target_origin',offsetSessions}})),
  model:{family:'trend',estimator:'ridge',parameterSharing:'per_target'},
  preprocess:{automatic:{schema:'auto-factor-preprocess/2'}},execution:{enabled:false},
});

test('the same economic factor can have independent lag, current and lead instances',()=>{
  const s=validateStatisticalQuant(timedStudy()), inputs=returnFactorDescriptors(s);
  assert.deepEqual(s.research.returnStudy,{schema:'asset-return-study/2'});
  assert.deepEqual(inputs.map(x=>x.feature),['factor:m0','factor:m1','factor:m2']);
  assert.deepEqual(inputs.map(x=>x.timing.offsetSessions),[-5,0,1]);
  assert.ok(inputs.every(x=>x.expression==='returns(close,20)' && x.transform.kind==='identity'));
  assert.ok(inputs.every(x=>x.baseFactorId==='momentum_20' && x.timing.offsetClock==='research_sessions'));
  assert.deepEqual(validateStatisticalQuant(s),s,'saved normalized instances are stable');
  const x=timedStudy(); delete x.factors[0].timing; delete x.factors[0].baseFactorId;
  assert.deepEqual(validateStatisticalQuant(x).factors[0].timing,{anchor:'target_origin',offsetSessions:0});
});

test('timed factors reject mode mixing and unsupported clocks without changing legacy admission',()=>{
  for(const change of [s=>s.research.returnStudy.mode='forecast',s=>s.factors[0].timing.offsetSessions=253,
    s=>s.factors[0].timing.offsetSessions=.5,s=>s.factors[0].timing.offsetSessions=true,
    s=>s.factors[0].timing.anchor='nonmissing_rows',s=>s.factors[0].timing.extra=1,
    s=>s.factors[0].timing=null,s=>s.factors[0].baseFactorId=null,
    s=>s.factors[0].baseFactorId='contains spaces',s=>s.factors[1].id=s.factors[0].id,
    s=>s.research.returnStudy={schema:'asset-return-study/1',mode:'forecast'}]){
    const s=timedStudy();change(s);assert.throws(()=>validateStatisticalQuant(s));
  }
});

test('a runtime must explicitly advertise study/2; study/1 is not a sufficient capability',()=>{
  const s=timedStudy(), runner={factorPreprocessFormats:['auto-factor-preprocess/2'],returnStudyFormats:['asset-return-study/1']};
  assert.throws(()=>assertFactorCapabilities(s,runner));
  assert.doesNotThrow(()=>assertFactorCapabilities(s,{...runner,returnStudyFormats:['asset-return-study/2']}));
});

async function fixture(){
  const a=await returnFunctionFixture(), s=validateStatisticalQuant(timedStudy());
  a.schema='atlas-model-function/5';delete a.scope.studyMode;a.scope.studyProtocol='asset-return-study/2';
  a.training.inputAvailableAtMax='20231221';a.training.sampleReadyAtMax='20231229';
  a.featureConstruction.schema='asset-return-features/2';
  a.featureConstruction.factors=s.factors;a.featureConstruction.inputs=returnFactorDescriptors(s);
  a.inputSchema=s.factors.map(f=>({name:'factor:'+f.id,type:'finite_number_or_null'}));
  a.transforms={imputeMedian:[0,0,0],winsorLower:null,winsorUpper:null,scaleMean:null,scaleScale:null};
  a.featureConstruction.preprocess.winsorize=false;a.featureConstruction.preprocess.standardize=false;
  a.estimator.coefficients=[[2,-1,.5]];a.estimator.intercepts=[.1];
  return sealReturnFunction(a);
}
const values={'factor:m0':.01,'factor:m1':.02,'factor:m2':.04};
const origins=['20240102'];
const evidence=()=>[Object.fromEntries(['20231226','20240102','20240103'].map((date,i)=>['factor:m'+i,{valueOrigin:'observed_actual',evaluationDate:date,availableAt:date}]))];

test('one F evaluates mixed-time values with row-level source labels and no model mode',async()=>{
  const a=await fixture();await validateFunction(a);
  const baseline=await evaluateFunction(a,{rows:[values]});
  assert.ok(Math.abs(baseline.predictedResponse[0]-.12)<1e-15);
  assert.deepEqual(baseline.availabilityClasses,['availability_unverified']);
  const realized=await evaluateFunction(a,{rows:[values],originDates:origins,inputEvidence:evidence()});
  assert.deepEqual(realized.availabilityClasses,['conditional_realized']);
  assert.equal(realized.inputEvidenceStatus,'caller_declared');
  const mixed=evidence();mixed[0]['factor:m2']={valueOrigin:'scenario_assumption',evaluationDate:'20240103',availableAt:null};
  const scenario=await evaluateFunction(a,{rows:[values],originDates:origins,inputEvidence:mixed,originPrice:[100]});
  assert.deepEqual(scenario.availabilityClasses,['conditional_scenario']);
  assert.deepEqual(scenario.predictedResponse,realized.predictedResponse);
  assert.ok(Math.abs(scenario.conditionalPrices[0]-112)<1e-12);
  assert.equal(scenario.mode,undefined);assert.equal(scenario.scenarioOnly,undefined);
});

test('future missing inputs never become predictions through imputation',async()=>{
  const a=await fixture();
  await assert.rejects(evaluateFunction(a,{rows:[{...values,'factor:m2':null}]}),/未来因子/);
  await assert.rejects(evaluateFunction(a,{rows:[values],mode:'future_scenario'}),/字段/);
  const bad=evidence();bad[0]['factor:m2'].availableAt='20240102';
  await assert.rejects(evaluateFunction(a,{rows:[values],originDates:origins,inputEvidence:bad}),/可用日/);
  bad[0]['factor:m2']={valueOrigin:'observed_actual',evaluationDate:'20231226',availableAt:'20240103'};
  await assert.rejects(evaluateFunction(a,{rows:[values],originDates:origins,inputEvidence:bad}),/方向/);
});

test('rehashing cannot hide input maturity or shifted economic definitions',async()=>{
  for(const mutate of [a=>a.training.inputAvailableAtMax='20240105',
    a=>a.training.sampleReadyAtMax='20240105',a=>a.scope.studyMode='association',
    a=>a.featureConstruction.inputs[0].timing.offsetSessions=-4,
    a=>a.featureConstruction.inputs[1].expression='returns(close,5)',
    a=>a.featureConstruction.inputs[1].baseFactorId='other']){
    const a=await fixture();mutate(a);await sealReturnFunction(a);await assert.rejects(validateFunction(a));
  }
});
