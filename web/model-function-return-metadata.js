import { validateAutomaticFactorConfig, validEconomicTransform, ECONOMIC_TYPES } from './factor-preprocess-contract.js';
import {returnFactorDescriptors} from './asset-return-factor-contract.js';

/** F/4 is a scalar per-security return equation, separate from old price outputs. */
export function validateReturnMetadata(a, {require:ok,keys,number,equal}) {
  const int = (x,lo,hi) => Number.isInteger(x) && x >= lo && x <= hi;
  const date = x => {
    ok(typeof x === 'string' && /^[0-9]{8}$/.test(x) && x.slice(0,4) !== '0000','研究日期格式无效');
    const iso = `${x.slice(0,4)}-${x.slice(4,6)}-${x.slice(6,8)}`, d = new Date(iso+'T00:00:00Z');
    ok(Number.isFinite(d.valueOf()) && d.toISOString().slice(0,10) === iso,'研究日期不存在');
  };
  const {training:t,scope:s,featureConstruction:c} = a;
  keys(t,['trainStart','trainEnd','informationCutoff','labelEndMax','trainRows','trainDates'],'训练范围');
  for (const key of ['trainStart','trainEnd','informationCutoff','labelEndMax']) date(t[key]);
  ok(t.trainStart <= t.trainEnd && t.trainEnd <= t.labelEndMax && t.labelEndMax < t.informationCutoff && int(t.trainRows,1,10000000) && int(t.trainDates,1,t.trainRows),'训练时间或数量无效');
  keys(s,['family','targetKind','horizonSessions','symbols','observationDays','researchStart','researchEnd','generalizationOutsideScopeValidated','studyMode'],'收益函数适用范围');
  ok(['mean_reversion','trend','fundamental','event'].includes(s.family) && s.targetKind === 'asset_return' && ['forecast','association'].includes(s.studyMode) && int(s.horizonSessions,1,252) && int(s.observationDays,1,60) && s.generalizationOutsideScopeValidated === false,'收益函数适用范围无效');
  ok(Array.isArray(s.symbols) && s.symbols.length === 1 && typeof s.symbols[0] === 'string' && /^[0-9]{6}\.(SH|SZ)$/.test(s.symbols[0]),'收益函数仅适用于一个已声明证券');
  date(s.researchStart); date(s.researchEnd); ok(s.researchStart < s.researchEnd,'研究区间倒置');
  keys(c,['schema','factors','preprocess','targetSpecification','inputs'],'收益因子构建');
  ok(c.schema === 'asset-return-features/1','收益因子构建协议无效');
  ok(Array.isArray(c.factors) && c.factors.length >= 1 && c.factors.length <= 32,'因子定义数量无效');
  const ids = new Set();
  for (const f of c.factors) {
    keys(f,['id','expression','direction','role',...(Object.hasOwn(f ?? {},'version') ? ['version'] : [])],'因子');
    ok(typeof f.id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(f.id) && !ids.has(f.id) && typeof f.expression === 'string' && f.expression.length > 0 && f.expression.length <= 500 && ['predictor','event'].includes(f.role) && number(f.direction) && [-1,1].includes(f.direction) && (!Object.hasOwn(f,'version') || int(f.version,1,1000000)),'因子元数据无效');
    ids.add(f.id);
  }
  const p = c.preprocess;
  keys(p,['winsorize','standardize','decorrelation','correlationThreshold','automatic'],'预处理');
  ok(typeof p.winsorize === 'boolean' && typeof p.standardize === 'boolean' && ['none','drop_correlated'].includes(p.decorrelation) && number(p.correlationThreshold) && p.correlationThreshold >= .5 && p.correlationThreshold <= 1,'预处理元数据无效');
  let automatic;
  try { automatic = validateAutomaticFactorConfig(p.automatic,c.factors); }
  catch (error) { ok(false,error.message); }
  ok(automatic.schema === 'auto-factor-preprocess/2','收益研究需要类型化因子处理');
  if (a.estimator.kind !== 'constant') ok((a.transforms.winsorLower !== null) === p.winsorize && (a.transforms.scaleMean !== null) === p.standardize,'训练变换与预处理声明不一致');

  const target = c.targetSpecification;
  keys(target,['kind','horizonSessions','normalization'],'收益目标');
  ok(target.kind === s.targetKind && target.horizonSessions === s.horizonSessions,'收益目标与适用范围不一致');
  const normalization = target.normalization;
  ok(normalization && ['none','trailing_volatility'].includes(normalization.kind),'收益标准化类型无效');
  keys(normalization,normalization.kind === 'none' ? ['kind'] : ['kind','windowSessions','ddof','horizonScale','minimum'],'收益标准化');
  if (normalization.kind === 'trailing_volatility') ok(int(normalization.windowSessions,20,252) && normalization.ddof === 1 && normalization.horizonScale === 'sqrt_h' && normalization.minimum === 1e-8,'收益标准化参数无效');
  ok(equal(a.outputs,[normalization.kind === 'none' ? 'asset_return' : 'volatility_standardized_asset_return']),'收益输出单位不一致');

  ok(Array.isArray(c.inputs) && c.inputs.length === c.factors.length,'收益因子来源数量无效');
  const expectedLag = s.studyMode === 'association' ? s.horizonSessions : 1;
  const features = [];
  c.inputs.forEach((f,index) => {
    keys(f,['feature','expression','direction','scope','transform','aggregation','economicType','sourceUnit','clock','timing'],'收益因子来源');
    const original = c.factors[index];
    ok(f.feature === 'factor:'+original.id && f.expression === original.expression && f.direction === original.direction && ['asset','global'].includes(f.scope) && f.aggregation === (f.scope === 'global' ? 'global_once' : 'asset_direct'),'收益因子来源不一致');
    ok(ECONOMIC_TYPES.includes(f.economicType) && typeof f.sourceUnit === 'string' && f.sourceUnit.length > 0 && f.sourceUnit.length <= 80 && /^[\x00-\x7f]*$/.test(f.sourceUnit) && ['research_sessions','observed_source_sessions_asof'].includes(f.clock),'收益因子经济含义无效');
    keys(f.timing,['kind','horizonSessions'],'收益因子时点');
    ok(f.timing.kind === (s.studyMode === 'association' ? 'matched_period' : 'origin_known') && f.timing.horizonSessions === s.horizonSessions,'收益因子时点不一致');
    const horizonTransform = ['simple_return','log_return','first_difference'].includes(f.transform?.kind);
    if (horizonTransform) {
      keys(f.transform,['kind','lag','invalid'],'收益因子经济变换');
      ok(f.transform.lag === expectedLag && f.transform.invalid === 'missing','收益因子变换期限不一致');
    } else ok(validEconomicTransform(f.transform,automatic.schema),'经济变换参数无效');
    if (s.studyMode === 'association') {
      ok(f.transform.kind !== 'return_over_trailing_volatility','同期关系不接受隐含波动窗口变换');
      ok(f.clock === 'research_sessions','同期收益因子必须对齐研究区间');
    }
    const override = automatic.overrides?.[original.id];
    if (override) {
      const expected = {...override.transform};
      if (['simple_return','log_return','first_difference'].includes(expected.kind)) expected.lag = expectedLag;
      ok(equal(Object.keys(expected).sort(),Object.keys(f.transform).sort()) && Object.keys(expected).every(key => expected[key] === f.transform[key]),'因子变换与覆盖配置不一致');
    }
    features.push(f.feature);
  });
  ok(a.inputSchema.every(input => features.includes(input.name)),'函数输入缺少因子定义');
  // Reconstruct the permitted descriptors from saved factor definitions. Hashing
  // alone cannot establish that a claimed transform has the declared meaning.
  let expectedInputs;
  try { expectedInputs = returnFactorDescriptors({research:{returnStudy:{mode:s.studyMode}},universe:{symbols:s.symbols},target,preprocess:p,factors:c.factors}); }
  catch (error) { ok(false,error.message); }
  const same = (x,y) => {
    if (x === y) return true;
    if (x === null || y === null || typeof x !== 'object' || typeof y !== 'object' || Array.isArray(x) !== Array.isArray(y)) return false;
    const xkeys = Object.keys(x).sort(), ykeys = Object.keys(y).sort();
    return equal(xkeys,ykeys) && xkeys.every(key => same(x[key],y[key]));
  };
  ok(same(c.inputs,expectedInputs),'收益因子有效构造与声明不一致');
  keys(a.identity,['response','simpleReturn','conditionalPrice','responseScale'],'收益还原');
  ok(a.identity.response === 'output[0]' && a.identity.simpleReturn === 'response * responseScale' && a.identity.conditionalPrice === 'originPrice * (1 + simpleReturn)' && a.identity.responseScale === 'one_or_origin_known_daily_volatility_times_sqrt_h','收益还原定义无效');
}
