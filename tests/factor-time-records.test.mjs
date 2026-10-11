import test from 'node:test';
import assert from 'node:assert/strict';
import {recordIndex} from '../edge/bundles/records.mjs';
import {returnEvidenceContract} from '../edge/bundles/return-evidence.mjs';

const dates = ['20260102','20260105','20260106','20260107','20260108'];
const factor = (id,offset,expression='returns(close,1)') => ({id,baseFactorId:'base',expression,direction:1,role:'predictor',timing:{anchor:'target_origin',offsetSessions:offset}});
function setup(factors=[factor('past',-1),factor('now',0),factor('future',1)]) {
  const strategy = {research:{returnStudy:{schema:'asset-return-study/2'}},universe:{symbols:['000001.SZ']},
    target:{horizonSessions:1},preprocess:{automatic:{schema:'auto-factor-preprocess/2'}},factors};
  return {returnStudy:strategy.research.returnStudy,returnFactorNames:factors.map(f=>'factor:'+f.id),
    returnEvidence:returnEvidenceContract(strategy,dates),returnStrategy:strategy};
}
function panel(options=setup(),t=1) {
  const inputEvidence={},features={};
  let unresolved=false;
  for (const d of options.returnEvidence.descriptors) {
    const evaluation=dates[t+d.timing.offsetSessions]??null,unknown=d.availabilityBasis==='declared_data_alignment';
    inputEvidence[d.feature]={valueOrigin:'observed_actual',evaluationDate:evaluation,availableAt:evaluation,
      sourceDate:unknown?null:evaluation,availabilityBasis:d.availabilityBasis,knownAtOrigin:evaluation===null||unknown?null:evaluation<=dates[t],
      status:evaluation===null?'outside_available_calendar':'observed'};
    features[d.feature]=evaluation===null?null:.1;
    unresolved ||= evaluation===null;
  }
  const available=Object.values(inputEvidence).map(x=>x.availableAt);
  const latest=available.every(x=>x!==null)?[...available].sort().at(-1):null,end=dates[t+1]??null;
  return {schema:'asset-return-panel-row/2',date:dates[t],targetId:'asset',assetSymbol:'000001.SZ',inputValid:!unresolved,
    invalidReason:unresolved?'future_input_outside_available_calendar':null,featureDate:dates[t],responseStartDate:dates[t],responseEndDate:end,
    originPrice:100,responseScale:1,observedReturn:end===null?null:.02,observedResponse:end===null?null:.02,features,inputEvidence,
    inputAvailableAt:latest,sampleReadyAt:latest&&end?[latest,end].sort().at(-1):null,
    availabilityClass:unresolved?'unresolved_input':available.some(x=>x>dates[t])?'conditional_realized':Object.values(inputEvidence).some(x=>x.knownAtOrigin===null)?'availability_unverified':'origin_known_by_policy'};
}
function observation(options=setup(),t=1) {
  const row=panel(options,t);delete row.features;
  return {...row,schema:'asset-return-observation/2',forecastId:'forecast',modelFitId:'fit',status:row.inputValid?'valid':'invalid',
    informationCutoff:row.inputAvailableAt?[row.date,row.inputAvailableAt].sort().at(-1)+'_AFTER_CLOSE':null,
    predictedResponse:.01,predictedReturn:.01,conditionalPrice:101,responseResidual:row.observedResponse===null?null:.01,labelMaturedAt:row.responseEndDate};
}
const checkPanel=(row,options=setup())=>recordIndex('researchPanel',row,0,0,0,options);
const checkObservation=(row,options=setup())=>recordIndex('forecasts',row,0,0,0,options);

test('mixed current/lag/lead archives retain independent evidence and the consumed-input cutoff',async()=>{
  const p=panel(),o=observation();
  const pm=JSON.parse((await checkPanel(p)).at(-1)),om=JSON.parse((await checkObservation(o)).at(-1));
  assert.equal(om.availabilityClass,'conditional_realized');
  assert.equal(om.inputEvidenceHash,pm.inputEvidenceHash);
  assert.equal(om.sampleReadyAt,'20260106');
  for(const damage of [
    row=>row.inputEvidence['factor:future'].knownAtOrigin=true,
    row=>row.inputEvidence['factor:future'].availableAt=row.date,
    row=>row.inputEvidence['factor:future'].evaluationDate=row.date,
    row=>row.inputEvidence['factor:future'].valueOrigin='predicted',
    row=>row.inputEvidence['factor:future'].status='outside_available_calendar',
    row=>row.inputEvidence['factor:past'].sourceDate=row.date,
    row=>row.availabilityClass='origin_known_by_policy',
    row=>row.sampleReadyAt=row.date,
    row=>delete row.inputEvidence['factor:now'],
    row=>row.inputEvidence['factor:now'].providerTimestamp=row.date,
    row=>row.features['factor:future']=null,
  ]){const changed=structuredClone(p);damage(changed);await assert.rejects(checkPanel(changed));}
  const falseCutoff=structuredClone(o);falseCutoff.informationCutoff=falseCutoff.date+'_AFTER_CLOSE';
  await assert.rejects(checkObservation(falseCutoff));
});

test('all-lag evidence cannot claim that the model output existed before its fit origin',async()=>{
  const options=setup([factor('past',-1)]),row=observation(options);
  assert.equal(row.inputAvailableAt,'20260102');assert.equal(row.informationCutoff,'20260105_AFTER_CLOSE');
  await checkObservation(row,options);
  row.informationCutoff='20260102_AFTER_CLOSE';await assert.rejects(checkObservation(row,options));
});

test('unknown financial alignment remains unverified while a mixed future input stays conditional',async()=>{
  const options=setup([factor('margin',0,'fd_netprofit_margin')]),row=panel(options);
  await checkPanel(row,options);assert.equal(row.availabilityClass,'availability_unverified');
  for(const mutate of [r=>r.inputEvidence['factor:margin'].knownAtOrigin=true,r=>r.inputEvidence['factor:margin'].sourceDate=r.date,
    r=>r.inputEvidence['factor:margin'].availabilityBasis='research_session_policy']){
    const wrong=structuredClone(row);mutate(wrong);await assert.rejects(checkPanel(wrong,options));
  }
  const mixed=setup([factor('margin',0,'fd_netprofit_margin'),factor('future',1)]);
  assert.equal(panel(mixed).availabilityClass,'conditional_realized');await checkPanel(panel(mixed),mixed);
});

test('out-of-calendar and missing future inputs are unresolved, never imputed as observed',async()=>{
  const tail=panel(setup(),4);await checkPanel(tail);
  assert.equal(tail.inputAvailableAt,null);assert.equal(tail.sampleReadyAt,null);
  const fake=structuredClone(tail);fake.inputValid=true;fake.invalidReason=null;await assert.rejects(checkPanel(fake));
  const missing=panel();missing.features['factor:future']=null;missing.inputEvidence['factor:future'].status='missing_observation';
  missing.availabilityClass='unresolved_input';missing.inputValid=false;missing.invalidReason='future_input_unavailable';
  await checkPanel(missing);
  missing.inputValid=true;missing.invalidReason=null;await assert.rejects(checkPanel(missing));
});

test('new evidence cannot be relabelled as legacy records or supplied to a mode-bearing v2 study',async()=>{
  const row=observation();row.schema='asset-return-observation/1';
  await assert.rejects(checkObservation(row));
  await assert.rejects(checkObservation(row,{returnStudy:{schema:'asset-return-study/1',mode:'forecast'}}));
  const options=setup();options.returnStudy={...options.returnStudy,mode:'forecast'};
  await assert.rejects(checkObservation(observation(),options));
});
