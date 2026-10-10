import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {typedFactorDescriptor} from '../web/factor-preprocess-semantics.js';
import {ECONOMIC_TRANSFORMS} from '../web/factor-preprocess-contract.js';
const catalog=JSON.parse(fs.readFileSync(new URL('../engine/atlas_quant/catalog.json',import.meta.url)));
const describe=(expression,kind,role='predictor')=>typedFactorDescriptor({id:'input',expression,direction:1,role},kind?{transform:ECONOMIC_TRANSFORMS[kind]}:undefined);
test('every published catalog recipe agrees with the Python generated economic descriptor',()=>{
  assert(catalog.factors.length>5000);
  for(const factor of catalog.factors){
    const actual=typedFactorDescriptor(factor);
    assert.equal(factor.automaticProcessing.schema,'auto-factor-preprocess/2');
    assert.equal(factor.automaticProcessing.status,'defined');
    assert.deepEqual(actual,factor.automaticProcessing.descriptor,factor.id);
  }
});
test('typed compounds retain quantities and convert once rather than by AST shape',()=>{
  for(const expression of ['close','+close','close*1','lag(close,1)','ts_mean(close,20)'])assert.equal(describe(expression).transform.kind,'simple_return',expression);
  for(const expression of ['returns(close,20)','close/ts_mean(close,20)-1','1/pe_ttm','turnover_rate/100','turnover_rate*0.01','fd_eps/raw_close'])assert.equal(describe(expression).transform.kind,'identity',expression);
  assert.equal(describe('delta(fd_netprofit_margin,20)').transform.kind,'percent_to_fraction');
  assert.equal(describe('log(close)').transform.kind,'first_difference');
  assert.equal(describe('log(close)-lag(log(close),1)').transform.kind,'identity');
  assert.equal(describe('fd_ebit').sourceUnit,'CNY');
  assert.deepEqual(describe('fd_ebit').transform,{kind:'signed_log1p',referenceUnit:1,invalid:'missing'});
  assert.equal(describe('pe-pb').transform.kind,'identity');
});
test('undefined scale, mixed units, raw prices and global zscore fail before fitting',()=>{
  for(const expression of ['close-ts_mean(close,20)','0-close','-close','delta(total_mv,20)','ext_unknown','-1*total_mv','total_mv/-2','0*close'])assert.throws(()=>describe(expression),{code:'FACTOR_TRANSFORM_REQUIRED'},expression);
  for(const expression of ['close+vol','log(close+vol)','rank(close+vol)','log(log(close))','returns(close,1)/0','close/-0'])assert.throws(()=>describe(expression,'identity'),{code:'FACTOR_UNIT_MISMATCH'},expression);
  for(const expression of ['raw_close','raw_close+0','log(raw_close)','returns(raw_close,1)'])assert.throws(()=>describe(expression),{code:'AUTO_FACTOR_REQUIRES_ADJUSTED_PRICE'},expression);
  assert.throws(()=>describe('close','identity'),{code:'PRICE_RETURN_REQUIRED'});
  assert.throws(()=>describe('log(close)','simple_return'),{code:'PRICE_RETURN_REQUIRED'});
  assert.throws(()=>describe('zscore(ext_ctx_000300_sh_close)'),{code:'GLOBAL_FACTOR_CROSS_SECTION'});
  assert.equal(describe('ext_unknown','identity').economicType,'custom_numeric');
  assert.equal(describe('log(ext_unknown)').transform.kind,'identity');
  assert.throws(()=>describe('ext_unknown','reciprocal_nonzero','event'),{code:'EVENT_TRANSFORM_ZERO'});
  assert.equal(describe('ext_unknown','signed_log1p','event').sourceUnit,'declared_numeric');
});
test('global provider clock follows exact source identity',()=>{
  assert.equal(describe('ext_ctx_yf_xsd_close').clock,'observed_source_sessions_asof');
  assert.equal(describe('returns(ext_ctx_yf_xsd_close,20)').clock,'observed_source_sessions_asof');
  assert.equal(describe('returns(ext_ctx_yf_xsd_close,20)-returns(ext_ctx_yf_xlk_close,20)').clock,'research_sessions');
  assert.equal(describe('returns(close,20)-returns(ext_ctx_yf_xsd_close,20)').scope,'asset');
  assert.equal(describe('ext_ctx_000300_sh_close').clock,'research_sessions');
});
