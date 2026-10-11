import { FACTOR_TIME_SCHEMA, isFactorTimeStudy, factorTimeErrors } from './factor-time.js';
// Explicit opt-in keeps pre-existing price, basket and /1 studies unchanged.
export const RETURN_STUDY_SCHEMA = FACTOR_TIME_SCHEMA;
export const RETURN_STUDY_SCHEMAS = ['asset-return-study/1', RETURN_STUDY_SCHEMA];
export const RETURN_MODES = { forecast: '当期因子 → 未来收益', association: '同期因子响应' };
export const isReturnStudy = s => RETURN_STUDY_SCHEMAS.includes(s?.research?.returnStudy?.schema);
export const isReturnFunction = a => ['atlas-model-function/4','atlas-model-function/5'].includes(a?.schema);
export const returnUnit = s => (s?.target?.normalization || s?.featureConstruction?.targetSpecification?.normalization)?.kind === 'trailing_volatility' ? '波动标准化收益' : '收益率';
export const returnStudyLabel = s => isFactorTimeStudy(s) || s?.schema === 'atlas-model-function/5' ? '逐资产因子方程' : RETURN_MODES[s?.research?.returnStudy?.mode || s?.scope?.studyMode] || '逐资产收益研究';
export const returnTiming = s => isFactorTimeStudy(s) || s?.schema === 'atlas-model-function/5' ? '各因子独立时点 · 响应区间 t → t+h' : (s?.research?.returnStudy?.mode || s?.scope?.studyMode) === 'association' ? '同期已观测因子 → 同期收益；未来输入仅用于情景推演' : '起点已知因子 → 后续收益';
export const volatilityNormalization = () => ({ kind: 'trailing_volatility', windowSessions: 20, ddof: 1, horizonScale: 'sqrt_h', minimum: 1e-8 });
export function enableReturnStudy(s, mode) {
  s.research.returnStudy = mode === undefined ? { schema: RETURN_STUDY_SCHEMA } : { schema: 'asset-return-study/1', mode };
  s.target = { kind: 'asset_return', horizonSessions: s.target?.horizonSessions || 1, normalization: { kind: 'none' } };
  s.model.parameterSharing = 'per_target';
  if (s.model.family === 'pair_reversion') s.model.family = 'mean_reversion';
  if (s.preprocess.automatic?.schema !== 'auto-factor-preprocess/2') s.preprocess.automatic = { schema: 'auto-factor-preprocess/2' };
  s.execution.enabled = false;
  return s;
}
export function leaveReturnStudy(s) {
  delete s.research.returnStudy;
  delete s.target.normalization;
  if (s.target.kind === 'asset_return') s.target.kind = 'asset_price';
  delete s.model.parameterSharing;
}
export function returnStudyErrors(s) {
  if (!isReturnStudy(s) && s?.target?.kind !== 'asset_return' && s?.research?.returnStudy === undefined) return [];
  const errors = [], study = s.research?.returnStudy, n = s.target?.normalization;
  if (!study || (isFactorTimeStudy(s) ? Object.keys(study).length !== 1 : study.schema !== 'asset-return-study/1' || Object.keys(study).length !== 2 || !Object.hasOwn(RETURN_MODES, study.mode))) errors.push('收益研究协议无效。');
  if (isFactorTimeStudy(s)) errors.push(...factorTimeErrors(s.factors || []));
  if (s.target?.kind !== 'asset_return' || s.target.basket !== undefined) errors.push('收益研究按资产独立建模，不接受篮子权重。');
  if (!n || (n.kind === 'none' ? Object.keys(n).length !== 1 : n.kind !== 'trailing_volatility' || Object.keys(n).length !== 5 || !Number.isInteger(n.windowSessions) || n.windowSessions < 20 || n.windowSessions > 252 || n.ddof !== 1 || n.horizonScale !== 'sqrt_h' || n.minimum !== 1e-8)) errors.push('选择收益率或有效的历史波动标准化收益。');
  if (s.model?.parameterSharing !== 'per_target') errors.push('研究集合中的每只资产须独立拟合参数。');
  if (s.preprocess?.automatic?.schema !== 'auto-factor-preprocess/2') errors.push('收益研究需要第二版类型化因子处理。');
  if (s.execution?.enabled !== false) errors.push('收益因子研究不启用交易执行。');
  if (s.model?.family === 'pair_reversion') errors.push('配对研究请使用独立的两腿目标。');
  return errors;
}
