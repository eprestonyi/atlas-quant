import test from 'node:test';
import assert from 'node:assert/strict';
import {returnFactorDescriptors} from '../web/asset-return-factor-contract.js';
import {validateStatisticalQuant} from '../edge/statistical-quant/validation.mjs';

const strategy = (expression,mode='association',horizon=5) => ({
  schemaVersion:2,name:'Return factor timing',universe:{symbols:['600519.SH'],start:'20230101',end:'20250930'},
  research:{mode:'statistical_quant',returnStudy:{schema:'asset-return-study/1',mode}},
  target:{kind:'asset_return',horizonSessions:horizon,normalization:{kind:'none'}},
  model:{family:'trend',estimator:'ridge',parameterSharing:'per_target'},
  factors:[{id:'input',expression,direction:1,role:'predictor'}],
  preprocess:{automatic:{schema:'auto-factor-preprocess/2'}},execution:{enabled:false}
});

test('association rejects target price identities even inside valid compound DSL',() => {
  for (const expression of ['close','log(close)','returns(close,5)','close/lag(close,5)-1','1/pe_ttm','log(circ_mv)','fd_eps/raw_close','lag(close,20)']) {
    const s = strategy(expression);
    assert.throws(() => returnFactorDescriptors(s),error => error.code === 'ASSOCIATION_TARGET_LEAKAGE');
    assert.throws(() => validateStatisticalQuant(s));
  }
  // The scope is deliberately conservative; historical own-price inputs remain
  // available in the separate known-input forecast mode.
  assert.equal(returnFactorDescriptors(strategy('returns(close,20)','forecast'))[0].timing.kind,'origin_known');
  assert.doesNotThrow(() => validateStatisticalQuant(strategy('returns(close,20)','forecast')));
});

test('association reference price factors use the response horizon on the research grid',() => {
  for (const expression of ['ext_ctx_000300_sh_close','log(ext_ctx_000300_sh_close)','ext_ctx_xsd_close']) {
    const a = returnFactorDescriptors(strategy(expression))[0];
    assert.equal(a.transform.lag,5); assert.equal(a.clock,'research_sessions');
    assert.equal(a.aggregation,'global_once'); assert.deepEqual(a.timing,{kind:'matched_period',horizonSessions:5});
  }
  const forecast = returnFactorDescriptors(strategy('ext_ctx_xsd_close','forecast'))[0];
  assert.equal(forecast.transform.lag,1); assert.equal(forecast.clock,'observed_source_sessions_asof');
  const s = strategy('returns(ext_ctx_000300_sh_close,5)');
  assert.equal(returnFactorDescriptors(s)[0].transform.kind,'identity');
  for (const expression of ['returns(ext_ctx_000300_sh_close,1)','returns(ext_ctx_000300_sh_close,+5)','ts_mean(returns(ext_ctx_000300_sh_close,20),5)'])
    assert.throws(() => returnFactorDescriptors(strategy(expression)),error => error.code === 'ASSOCIATION_PERIOD_MISMATCH');
});

test('association accepts disclosed non-price financial inputs and forbids implicit return-vol windows',() => {
  const financial = returnFactorDescriptors(strategy('fd_netprofit_margin'))[0];
  assert.deepEqual(financial.transform,{kind:'percent_to_fraction',divisor:100}); assert.equal(financial.aggregation,'asset_direct');
  const s = strategy('ext_ctx_000300_sh_close');
  s.preprocess.automatic.overrides = {input:{transform:{kind:'return_over_trailing_volatility',returnLag:1,volatilityWindow:20,volatilityLag:1,ddof:1,minVolatility:1e-8,invalid:'missing'}}};
  assert.throws(() => returnFactorDescriptors(s),error => error.code === 'ASSOCIATION_PERIOD_MISMATCH');
  s.research.returnStudy.mode = 'forecast'; assert.equal(returnFactorDescriptors(s)[0].transform.kind,'return_over_trailing_volatility');
});

test('association period checks include equivalent log/ratio returns and repeated shifts',() => {
  const x = 'ext_ctx_000300_sh_close';
  for (const expression of [`${x}/lag(${x},5)-1`,`delta(log(${x}),5)`,`log(${x})-log(lag(${x},5))`])
    assert.equal(returnFactorDescriptors(strategy(expression))[0].transform.kind,'identity');
  for (const expression of [`${x}/lag(${x},20)-1`,`delta(log(${x}),20)`,`log(${x})-log(lag(${x},20))`,`returns(lag(${x},5),5)`,`lag(${x},5)`,`log(lag(${x},5))`,`returns(returns(${x},5),5)`])
    assert.throws(() => returnFactorDescriptors(strategy(expression)),error => error.code === 'ASSOCIATION_PERIOD_MISMATCH',expression);
  assert.equal(returnFactorDescriptors(strategy(`returns(${x},252)`,'association',252))[0].transform.kind,'identity');
});

test('source identity distinguishes an index namespace from a same-target reference',() => {
  const self = strategy('ext_ctx_xsd_close'); self.universe.symbols = ['XSD'];
  assert.throws(() => returnFactorDescriptors(self),error => error.code === 'ASSOCIATION_TARGET_LEAKAGE');
  const index = strategy('ext_ctx_000300_sh_close'); index.universe.symbols = ['000300.SH'];
  assert.doesNotThrow(() => returnFactorDescriptors(index));
});
