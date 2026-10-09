import { ApiError } from '../errors.mjs';

const sorted = value => Array.isArray(value) ? value.map(sorted)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])]))
    : value;
const same = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
const object = value => !!value && typeof value === 'object' && !Array.isArray(value);
const requireSame = (actual, expected, label) => {
  if (expected === undefined || !same(actual, expected))
    throw new ApiError('FUNCTION_SOURCE_MISMATCH', `函数与冻结研究的${label}不一致；此来源不能用于试算或派生`, 503);
};

/** Bind an already hash-validated portable F to its enclosing immutable fit.
 * Both bundle codecs keep these fields in forecast JSON. Never normalize a
 * saved strategy here or infer missing fields from the portable artifact.
 */
export function assertFunctionSource(artifact, fit, strategy) {
  requireSame(object(artifact) && ['scope', 'training', 'featureConstruction', 'provenance', 'estimator', 'transforms']
    .every(key => object(artifact[key])) && Array.isArray(artifact.inputSchema) && artifact.inputSchema.every(object), true, '函数结构');
  requireSame(fit?.status, 'valid', '拟合状态');
  requireSame(artifact.scope, {
    family: strategy?.model?.family,
    targetKind: strategy?.target?.kind,
    horizonSessions: strategy?.target?.horizonSessions,
    symbols: strategy?.universe?.symbols,
    observationDays: strategy?.research?.observationDays,
    researchStart: strategy?.universe?.start,
    researchEnd: strategy?.universe?.end,
    generalizationOutsideScopeValidated: false
  }, '适用范围');
  for (const key of ['trainStart', 'trainEnd', 'informationCutoff', 'labelEndMax', 'trainRows', 'trainDates'])
    requireSame(artifact.training[key], fit[key], '训练记录');
  requireSame(artifact.training.informationCutoff, fit.fitDate, '拟合时点');
  requireSame(artifact.inputSchema.map(x => x.name), fit.featureNames, '特征及其顺序');
  requireSame(artifact.outputs, fit.outputs, '输出定义');
  requireSame(artifact.featureConstruction.factors, strategy?.factors, '因子定义');
  requireSame(artifact.featureConstruction.preprocess, strategy?.preprocess, '预处理声明');
  if (strategy?.preprocess?.automatic) {
    requireSame(artifact.schema, 'atlas-model-function/2', '自动因子函数版本');
    requireSame(artifact.featureConstruction.automatic, fit.automaticPreprocessing, '因子经济变换与作用域');
    if (!['no_change', 'historical_drift'].includes(fit.estimator) && strategy.preprocess.standardize)
      requireSame(fit.scalerMethod, 'median_iqr', '训练集稳健尺度');
  } else {
    requireSame(artifact.schema, 'atlas-model-function/1', '传统函数版本');
    requireSame(artifact.featureConstruction.automatic === undefined, true, '传统因子构造');
  }
  requireSame(artifact.featureConstruction.targetSpecification, strategy?.target, '目标定义');
  requireSame(artifact.provenance.estimator, fit.estimator, '已选择估计器');
  requireSame(artifact.provenance.parameters, fit.params, '已选择参数');
  // auto is the declared candidate search, not the estimator selected by it.
  if (strategy?.model?.estimator !== 'auto')
    requireSame(fit.estimator, strategy?.model?.estimator, '估计器声明');
  const kind = {no_change: 'constant', historical_drift: 'constant', ridge: 'linear',
    elastic_net: 'linear', hist_gradient_boosting: 'histogram_trees'}[fit.estimator];
  requireSame(artifact.estimator.kind, kind, '估计器类型');
  if (kind === 'constant') requireSame(artifact.estimator.value, fit.constantPrediction, '拟合常量');
  else {
    for (const [parameter, audit] of Object.entries({imputeMedian: 'imputerMedian', winsorLower: 'winsorLower',
      winsorUpper: 'winsorUpper', scaleMean: 'scalerMean', scaleScale: 'scalerScale'}))
      requireSame(artifact.transforms[parameter], fit[audit] ?? null, '训练变换');
    if (kind === 'linear') {
      requireSame(artifact.estimator.coefficients, fit.coefficients, '拟合系数');
      requireSame(artifact.estimator.intercepts, fit.intercepts, '拟合截距');
    }
  }
}
