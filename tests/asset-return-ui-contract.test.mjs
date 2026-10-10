import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultStrategy,normalizeStrategy,validateStrategy } from '../web/quant-workspace/defaults.js';
import {enableReturnStudy,leaveReturnStudy,volatilityNormalization} from '../web/quant-workspace/return-study.js';
import {stepErrors} from '../web/quant-workspace/research-steps.js';
const study=()=>{const s=defaultStrategy({automatic:true,returnStudy:true});s.universe.symbols=['000001.SZ','600000.SH'];s.factors=[{id:'size',expression:'total_mv',direction:1,role:'predictor'}];return s;};
test('new explicit return defaults and saved legacy protocols stay distinct',()=>{
  const s=study();assert.equal(s.target.kind,'asset_return');assert.equal(s.model.parameterSharing,'per_target');assert.deepEqual(s.target.normalization,{kind:'none'});assert.equal(s.execution.enabled,false);
  assert.deepEqual(validateStrategy(s),[]);assert.deepEqual(normalizeStrategy(s),s);
  const legacy=defaultStrategy();assert.equal(normalizeStrategy(legacy).research.returnStudy,undefined);assert.equal(normalizeStrategy(legacy).target.kind,'asset_price');
  const old=defaultStrategy({automatic:true});old.preprocess.automatic.schema='auto-factor-preprocess/1';assert.equal(normalizeStrategy(old).preprocess.automatic.schema,'auto-factor-preprocess/1');
  enableReturnStudy(old,'association');assert.equal(old.research.returnStudy.mode,'association');leaveReturnStudy(old);assert.equal(old.target.kind,'asset_price');assert.equal(old.target.normalization,undefined);
});
test('return UI rejects invalid scalar scope, target scale and incompatible workers before running',()=>{
  for(const mutate of [s=>s.model.parameterSharing='pooled',s=>s.target.basket={},s=>s.research.returnStudy.mode='future_scenario',s=>s.target.normalization={kind:'none',minimum:0},s=>s.execution.enabled=true,s=>s.preprocess.automatic.schema='auto-factor-preprocess/1']){const s=study();mutate(s);assert(validateStrategy(s,{step:'model'}).length>0);}
  const s=study();s.target.normalization=volatilityNormalization();assert.deepEqual(validateStrategy(s),[]);s.target.normalization.windowSessions=10;assert(validateStrategy(s,{step:'model'}).length>0);
  assert(stepErrors(study(),'model',{dataSource:'demo',session:{runner:{}}}).some(x=>x.includes('逐资产收益')));
  assert(!stepErrors(study(),'model',{dataSource:'demo',session:{runner:{returnStudyFormats:['asset-return-study/1']}}}).some(x=>x.includes('逐资产收益')));
});

test('annual return intervals do not widen saved legacy horizons',()=>{const s=study();s.target.horizonSessions=252;assert.deepEqual(validateStrategy(s,{step:'model'}),[]);s.target.horizonSessions=253;assert(validateStrategy(s,{step:'model'}).length);const old=defaultStrategy();old.target.horizonSessions=252;assert(validateStrategy(old,{step:'model'}).length);});

test('explicit conversion retains typed Studio overrides',()=>{const s=defaultStrategy({automatic:true});s.preprocess.automatic.overrides={size:{transform:{kind:'signed_log1p',referenceUnit:1,invalid:'missing'}}};const before=structuredClone(s.preprocess.automatic);enableReturnStudy(s,'association');assert.deepEqual(s.preprocess.automatic,before);});
