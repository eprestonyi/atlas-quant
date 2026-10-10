import { validateAutomaticFactorConfig, validEconomicTransform, ECONOMIC_TYPES } from './factor-preprocess-contract.js';
import { validateReturnMetadata } from './model-function-return-metadata.js';
/** Strict metadata contract shared with statistical_quant/model_function.py. */
export function validateMetadata(a, {require: ok, keys, number, equal}) {
  if (a.schema === 'atlas-model-function/4') {
    validateReturnMetadata(a, {require:ok,keys,number,equal});
    validateCommonMetadata(a, {require:ok,keys,number,equal});
    return;
  }
  const int = (x, lo, hi) => Number.isInteger(x) && x >= lo && x <= hi;
  const date = x => {
    ok(typeof x === 'string' && /^[0-9]{8}$/.test(x) && x.slice(0, 4) !== '0000', '训练日期格式无效');
    const iso = `${x.slice(0,4)}-${x.slice(4,6)}-${x.slice(6,8)}`;
    const d = new Date(iso + 'T00:00:00Z');
    ok(Number.isFinite(d.valueOf()) && d.toISOString().slice(0,10) === iso, '训练日期不存在');
  };
  const {training: t, scope: s, featureConstruction: c, lineage: l} = a;
  const automatic = a.schema === 'atlas-model-function/2' || (a.schema === 'atlas-model-function/3' && c.schema === 'origin-state-features/2');
  keys(t, ['trainStart','trainEnd','informationCutoff','labelEndMax','trainRows','trainDates'], '训练范围');
  for (const k of ['trainStart','trainEnd','informationCutoff','labelEndMax']) date(t[k]);
  ok(t.trainStart <= t.trainEnd && t.trainEnd <= t.labelEndMax && t.labelEndMax < t.informationCutoff && int(t.trainRows,1,10000000) && int(t.trainDates,1,t.trainRows), '训练时间或数量无效');
  keys(s, ['family','targetKind','horizonSessions','symbols','observationDays','researchStart','researchEnd','generalizationOutsideScopeValidated'], '适用范围');
  ok(['mean_reversion','pair_reversion','trend','fundamental','event'].includes(s.family) && ['asset_price','frozen_basket'].includes(s.targetKind) && int(s.horizonSessions,1,60) && int(s.observationDays,1,60) && s.generalizationOutsideScopeValidated === false, '函数适用范围无效');
  ok(Array.isArray(s.symbols) && s.symbols.length > 0 && s.symbols.length <= 10000 && s.symbols.every(x => typeof x === 'string' && /^[0-9]{6}\.(SH|SZ)$/.test(x)) && new Set(s.symbols).size === s.symbols.length, '函数证券范围无效');
  date(s.researchStart); date(s.researchEnd); ok(s.researchStart < s.researchEnd, '研究区间倒置');
  keys(c, ['schema','family','factors','preprocess','targetSpecification','quantityPolicy', ...(automatic ? ['automatic'] : [])], '特征构建');
  ok(c.schema === (automatic ? 'origin-state-features/2' : 'origin-state-features/1') && c.family === s.family && c.quantityPolicy === 'origin_specific_frozen_quantities', '特征构建协议无效');
  ok(Array.isArray(c.factors) && c.factors.length <= 32, '因子定义数量无效');
  const ids = new Set();
  for (const f of c.factors) {
    keys(f, ['id','expression','direction','role', ...(Object.hasOwn(f ?? {}, 'version') ? ['version'] : [])], '因子');
    ok(typeof f.id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(f.id) && !ids.has(f.id) && typeof f.expression === 'string' && f.expression.length > 0 && f.expression.length <= 500 && ['predictor','event','hedge'].includes(f.role) && number(f.direction) && [-1,1].includes(f.direction) && (!Object.hasOwn(f,'version') || int(f.version,1,1000000)), '因子元数据无效');
    ids.add(f.id);
  }
  const p = c.preprocess;
  keys(p, ['winsorize','standardize','decorrelation','correlationThreshold', ...(automatic ? ['automatic'] : [])], '预处理');
  ok(typeof p.winsorize === 'boolean' && typeof p.standardize === 'boolean' && ['none','drop_correlated'].includes(p.decorrelation) && number(p.correlationThreshold) && p.correlationThreshold >= .5 && p.correlationThreshold <= 1, '预处理元数据无效');
  if (automatic) {
    if (a.estimator.kind !== 'constant') {
      ok((a.transforms.winsorLower !== null) === p.winsorize && (a.transforms.scaleMean !== null) === p.standardize, '训练变换与自动预处理声明不一致');
    }
    let automaticConfig;
    try { automaticConfig = validateAutomaticFactorConfig(p.automatic, c.factors); }
    catch (error) { ok(false, error.message); }
    const typed = automaticConfig.schema === 'auto-factor-preprocess/2';
    const policy = c.automatic;
    keys(policy, ['schema','inputStage','scaling','fitPopulation','factors', ...(typed ? ['stateFeatures'] : [])], '自动因子构建');
    ok(policy.schema === automaticConfig.schema && policy.inputStage === 'after_per_leg_semantic_transform_and_origin_aggregation' && policy.scaling === 'train_fold_median_iqr' && policy.fitPopulation === 'asset_rows_global_dates', '自动因子构建口径无效');
    if (typed) ok(policy.stateFeatures === 'asset_returns_basket_gross/1', '状态输入定义无效');
    const factors = c.factors.filter(f => f.role !== 'hedge');
    ok(Array.isArray(policy.factors) && policy.factors.length === factors.length, '自动因子定义数量无效');
    const features = [];
    policy.factors.forEach((f, index) => {
      keys(f, ['feature','expression','direction','scope','transform','aggregation', ...(typed ? ['economicType','sourceUnit','clock'] : [])], '自动因子');
      const original = factors[index];
      ok(f.feature === 'factor:' + original.id && f.expression === original.expression && f.direction === original.direction && ['asset','global'].includes(f.scope) && f.aggregation === (f.scope === 'global' ? 'global_once' : 'signed_origin_dollar_over_gross'), '自动因子来源不一致');
      ok(validEconomicTransform(f.transform, policy.schema), '经济变换参数无效');
      if (typed) {
        ok(ECONOMIC_TYPES.includes(f.economicType) && typeof f.sourceUnit === 'string' && f.sourceUnit.length > 0 && f.sourceUnit.length <= 80 && /^[\x00-\x7f]*$/.test(f.sourceUnit) && ['research_sessions','observed_source_sessions_asof'].includes(f.clock), '因子经济含义无效');
        const override = automaticConfig.overrides?.[original.id];
        if (override) ok(Object.keys(override.transform).every(key => override.transform[key] === f.transform[key]), '因子变换与保存的覆盖配置不一致');
      }
      features.push(f.feature);
    });
    const builtins = ['volatility20', ...(['mean_reversion','pair_reversion'].includes(s.family)
      ? ['state_deviation20','state_deviation60','change1'] : s.family === 'trend'
        ? ['trend1','trend5','trend20','trend60'] : ['change1','change5'])];
    ok(a.inputSchema.every(input => [...features, ...builtins].includes(input.name)), '函数输入缺少原始构建定义');
  }
  const target = c.targetSpecification;
  keys(target, ['kind','horizonSessions', ...(Object.hasOwn(target ?? {},'basket') ? ['basket'] : [])], '研究目标');
  ok(target.kind === s.targetKind && target.horizonSessions === s.horizonSessions, '研究目标与适用范围不一致');
  if (target.kind === 'asset_price') ok(!Object.hasOwn(target,'basket'), '单资产目标不能有篮子');
  else {
    const b = target.basket;
    ok(b && ['pair_ols','pca_residual','fixed'].includes(b.method), '篮子方法无效');
    keys(b, ['method','symbols','formationDays', ...(b.method === 'fixed' ? ['quantities'] : b.method === 'pca_residual' ? ['components'] : [])], '篮子');
    ok(Array.isArray(b.symbols) && b.symbols.length > 0 && b.symbols.length <= 20 && b.symbols.every(x => s.symbols.includes(x)) && new Set(b.symbols).size === b.symbols.length && int(b.formationDays,60,504), '篮子范围无效');
    if (b.method === 'pair_ols') ok(b.symbols.length === 2, '配对需要两腿');
    if (b.method === 'pca_residual') ok(b.symbols.length >= 3 && int(b.components,1,Math.min(10,b.symbols.length-2)), 'PCA 维度无效');
    if (b.method === 'fixed') {
      keys(b.quantities,b.symbols,'冻结数量');
      ok(Object.values(b.quantities).every(x => number(x) && Math.abs(x) <= 1e6) && Object.values(b.quantities).some(x => x !== 0), '冻结数量无效');
    }
  }
  keys(a.identity, ['entry','future','e','expectedChange','scale'], '状态还原');
  ok(a.identity.entry === 'currentState + scale * output[0]' && a.identity.future === 'currentState + scale * output[1]' && a.identity.e === 'currentState - expectedFuture' && a.identity.expectedChange === '-e' && a.identity.scale === 'origin_known_gross_absolute_leg_value', '状态还原定义无效');
  validateCommonMetadata(a, {require:ok,keys,number,equal});
}

function validateCommonMetadata(a, {require:ok,keys,number,equal}) {
  const l = a.lineage;
  keys(a.provenance,['estimator','parameters','sklearnVersion'],'估计器来源');
  const grids = {no_change:[{}],historical_drift:[{}],ridge:[{alpha:1},{alpha:10},{alpha:100},{alpha:1000}],elastic_net:[{alpha:.0001,l1_ratio:.2},{alpha:.001,l1_ratio:.5},{alpha:.01,l1_ratio:.5}],hist_gradient_boosting:[{max_leaf_nodes:7,l2_regularization:1},{max_leaf_nodes:15,l2_regularization:5}]};
  Object.assign(grids, {polynomial_ridge:[{alpha:10,degree:2},{alpha:100,degree:2},{alpha:1000,degree:2}],polynomial_elastic_net:[{alpha:.001,l1_ratio:.5,degree:2},{alpha:.01,l1_ratio:.5,degree:2}],transformed_ridge:[{alpha:10},{alpha:100},{alpha:1000}],factorwise_basis:[{alpha:.001,l1_ratio:.5},{alpha:.01,l1_ratio:.5},{alpha:.1,l1_ratio:.5}]});
  const same = (x,y) => x && typeof x === 'object' && !Array.isArray(x) && equal(Object.keys(x).sort(),Object.keys(y).sort()) && Object.keys(y).every(k => x[k] === y[k]);
  ok(Object.hasOwn(grids,a.provenance.estimator) && grids[a.provenance.estimator].some(x => same(a.provenance.parameters,x)) && typeof a.provenance.sklearnVersion === 'string' && /^[0-9A-Za-z.+-]{1,40}$/.test(a.provenance.sklearnVersion), '估计器来源无效');
  keys(a.editPolicy,['allowed','arbitraryCode','editedEvidenceStatus'],'编辑约定');
  ok(equal(a.editPolicy.allowed,['estimator_numeric_parameters']) && a.editPolicy.arbitraryCode === false && a.editPolicy.editedEvidenceStatus === 'UNVALIDATED_USER_EDIT', '编辑约定无效');
  ok(l && ['fitted','UNVALIDATED_USER_EDIT'].includes(l.status), '函数来源状态无效');
  keys(l,['parentArtifactId','status', ...(l.status === 'UNVALIDATED_USER_EDIT' ? ['edits'] : [])],'版本来源');
  if (l.status === 'fitted') ok(l.parentArtifactId === null, '拟合函数不能声明编辑来源');
  else {
    ok(typeof l.parentArtifactId === 'string' && /^[a-f0-9]{64}$/.test(l.parentArtifactId) && Array.isArray(l.edits) && l.edits.length > 0 && l.edits.length <= 256, '编辑来源无效');
    for (const e of l.edits) { keys(e,['path','value'],'编辑记录'); ok(typeof e.path === 'string' && e.path.length > 0 && e.path.length <= 160 && number(e.value),'编辑记录无效'); }
  }
}
