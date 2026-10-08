/** Explicit model admission does not mutate frozen source-composition semantics. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { validateStatisticalQuant } from '../edge/statistical-quant/validation.mjs';
import {
  FINANCIAL_AUTO_PROFILE as AUTO,
  FINANCIAL_SOURCE_PROFILES as SOURCE,
  assertFinancialResearchConfig,
  financialResearchAdmissions,
  registeredFinancialProfile,
} from '../edge/datasets/research-profile.mjs';
import { supportsFinancialResearchProfile } from '../edge/datasets/research.mjs';

function strategy() {
  return validateStatisticalQuant({
    schemaVersion:2,name:'Synthetic frozen financial auto',
    research:{mode:'statistical_quant'},universe:{symbols:['600000.SH'],start:'20240101',end:'20241231'},
    target:{kind:'asset_price',horizonSessions:5},
    model:{family:'fundamental',estimator:'auto',refitDays:60,trainWindow:120},
    validation:{innerFolds:2,outerFolds:2,minTrainDates:40},
    factors:[{id:'margin',expression:'model_fin_operating_margin',role:'predictor'}],
    execution:{enabled:false},
  });
}

test('new fundamental auto admission is explicit, v2-only, and preserves legacy Ridge', () => {
  const s=strategy();
  assert.equal(assertFinancialResearchConfig(s,AUTO,2),s);
  assert.throws(()=>assertFinancialResearchConfig(s,SOURCE[2],2));
  const ridge=structuredClone(s);ridge.model.estimator='ridge';
  assert.equal(assertFinancialResearchConfig(ridge,SOURCE[2],2),ridge);
  assert.equal(assertFinancialResearchConfig(ridge,SOURCE[1],1),ridge);
  for (const [profile,version] of [[AUTO,1],[AUTO,'2'],['invented',2],[SOURCE[1],2]]) {
    assert.equal(registeredFinancialProfile(profile,version),false);
    assert.throws(()=>assertFinancialResearchConfig(s,profile,version));
  }
  for (const change of [
    s=>s.execution.enabled=true,s=>s.model.estimator='ridge',s=>s.model.family='trend',
    s=>s.model.refitDays=19,s=>s.validation.innerFolds=3,s=>s.validation.outerFolds=3,
    s=>s.universe.start='20230101',s=>s.universe.end='20250101',
    s=>s.universe.symbols=Array.from({length:51},(_,i)=>`${600000+i}.SH`),
    s=>s.factors=Array.from({length:17},()=>s.factors[0]),s=>s.factors[0].role='hedge',
    s=>s.universe.selection={id:'unrelated'},s=>s.dataBindings={financial:{field:'temporary'}},
  ]) {
    const bad=structuredClone(s);change(bad);
    assert.throws(()=>assertFinancialResearchConfig(bad,AUTO,2));
  }
});

test('old runners retain legacy capability but cannot claim new estimator admission', () => {
  const old={datasetFormats:['atlas.quant.research_dataset/2'],snapshotFormats:['financial_json_v1'],
    transportFormats:['atlas.quant.financial_bundle/1']};
  assert.equal(supportsFinancialResearchProfile(old,SOURCE[2]),true);
  assert.equal(supportsFinancialResearchProfile(old,AUTO),false);
  assert.equal(supportsFinancialResearchProfile({...old,financialResearchProfiles:[AUTO]},AUTO),true);
  assert.equal(supportsFinancialResearchProfile({...old,financialResearchProfiles:[AUTO],snapshotFormats:[]},AUTO),false);
  assert.equal(supportsFinancialResearchProfile({...old,financialResearchProfiles:AUTO},AUTO),false);
  assert.equal(supportsFinancialResearchProfile(old,'invented'),false);
});

test('dataset detail reports one-year auto scope eligibility independently from runner availability', () => {
  const scope=strategy().universe;
  let admissions=financialResearchAdmissions(true,{ridge:true,auto:false},scope);
  assert.equal(admissions[1].configurationEligible,true);
  assert.equal(admissions[1].runnerAvailable,false);
  assert.equal(admissions[1].sampleStatus,'not_checked');
  admissions=financialResearchAdmissions(true,{ridge:true,auto:true},{...scope,start:'20180101'});
  assert.equal(admissions[0].configurationEligible,true);
  assert.equal(admissions[1].configurationEligible,false);
  assert.deepEqual(admissions[1].reasonCodes,['DATASET_SCOPE_EXCEEDS_AUTO_PROFILE']);
  assert.equal(financialResearchAdmissions(false,{ridge:true,auto:true},scope)[1].configurationEligible,false);
  for(const scope of [null,{}, {symbols:[],start:'20240101',end:'20241231'}])
    assert.equal(financialResearchAdmissions(true,{auto:true},scope)[1].configurationEligible,false);
});
