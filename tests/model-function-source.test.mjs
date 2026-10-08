import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {assertFunctionSource} from '../edge/model-functions/source.mjs';
import {validateFunction} from '../web/model-function-runtime.js';

const fixture=JSON.parse(await fs.readFile(new URL('./fixtures/model-source-ridge-v1.json',import.meta.url),'utf8'));
const artifact=await validateFunction(fixture.fit.functionArtifact);
test('actual fitted audit binds both an auto-selected Ridge and an explicit Ridge declaration',()=>{
  assert.equal(fixture.strategy.model.estimator,'auto');
  assert.equal(fixture.fit.estimator,'ridge');
  assert.doesNotThrow(()=>assertFunctionSource(artifact,fixture.fit,fixture.strategy));
  const explicit=structuredClone(fixture.strategy);explicit.model.estimator='ridge';
  assert.doesNotThrow(()=>assertFunctionSource(artifact,fixture.fit,explicit));
});
const changes={
  'scope family':(_,s)=>s.model.family='trend',
  'scope full membership':(_,s)=>s.universe.symbols.pop(),
  'scope interval':(_,s)=>s.universe.start='20230101',
  'scope observation':(_,s)=>s.research.observationDays=2,
  'scope horizon':(_,s)=>s.target.horizonSessions=6,
  'training start':f=>f.trainStart='20240102',
  'training end':f=>f.trainEnd='20240702',
  'information cutoff':f=>f.informationCutoff='20240711',
  'fit date':f=>f.fitDate='20240711',
  'label maturity':f=>f.labelEndMax='20240708',
  'training rows':f=>f.trainRows++,
  'training dates':f=>f.trainDates--,
  'missing audit':f=>delete f.trainRows,
  'invalid fit':f=>f.status='invalid',
  'ordered feature schema':f=>f.featureNames.reverse(),
  'output schema':f=>f.outputs.reverse(),
  'factor formula':(_,s)=>s.factors.push({id:'unrelated',expression:'close',direction:1,role:'predictor'}),
  'preprocessing declaration':(_,s)=>s.preprocess.standardize=false,
  'selected estimator':f=>f.estimator='elastic_net',
  'selected parameters':f=>f.params.alpha=10,
  'fixed estimator declaration':(_,s)=>s.model.estimator='no_change',
  'fitted coefficient':f=>f.coefficients[1][0]+=.5,
  'fitted intercept':f=>f.intercepts[1]+=.5,
  'imputation audit':f=>f.imputerMedian[0]+=.5,
  'scaling audit':f=>f.scalerMean[0]+=.5,
  'winsor audit':f=>f.winsorUpper[0]+=.5,
};
for(const [name,change] of Object.entries(changes))test(`self-valid F rejects mismatched ${name}`,()=>{
  const {fit,strategy}=structuredClone(fixture);change(fit,strategy);
  assert.throws(()=>assertFunctionSource(artifact,fit,strategy),e=>e.code==='FUNCTION_SOURCE_MISMATCH'&&e.status===503);
});
