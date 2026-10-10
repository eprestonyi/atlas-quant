// Explicit opt-in keeps pre-existing price and basket studies unchanged.
export const RETURN_STUDY_SCHEMA = 'asset-return-study/1';
export const RETURN_MODES = { forecast: '当期因子 → 未来收益', association: '同期因子响应' };
export const isReturnStudy = s => s?.research?.returnStudy?.schema === RETURN_STUDY_SCHEMA;
export const isReturnFunction = a => a?.schema === 'atlas-model-function/4';
export const returnUnit = s => (s?.target?.normalization || s?.featureConstruction?.targetSpecification?.normalization)?.kind === 'trailing_volatility' ? '波动标准化收益' : '收益率';
export const returnTiming = s => (s?.research?.returnStudy?.mode || s?.scope?.studyMode) === 'association' ? '同期已观测因子 → 同期收益；未来输入仅用于情景推演' : '起点已知因子 → 后续收益';
export const volatilityNormalization = () => ({ kind: 'trailing_volatility', windowSessions: 20, ddof: 1, horizonScale: 'sqrt_h', minimum: 1e-8 });
export function enableReturnStudy(s, mode = 'forecast') {
  s.research.returnStudy = { schema: RETURN_STUDY_SCHEMA, mode };
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
  if (!study || Object.keys(study).length !== 2 || study.schema !== RETURN_STUDY_SCHEMA || !Object.hasOwn(RETURN_MODES, study.mode)) errors.push('选择有效的收益研究模式。');
  if (s.target?.kind !== 'asset_return' || s.target.basket !== undefined) errors.push('收益研究按资产独立建模，不接受篮子权重。');
  if (!n || (n.kind === 'none' ? Object.keys(n).length !== 1 : n.kind !== 'trailing_volatility' || Object.keys(n).length !== 5 || !Number.isInteger(n.windowSessions) || n.windowSessions < 20 || n.windowSessions > 252 || n.ddof !== 1 || n.horizonScale !== 'sqrt_h' || n.minimum !== 1e-8)) errors.push('选择收益率或有效的历史波动标准化收益。');
  if (s.model?.parameterSharing !== 'per_target') errors.push('研究集合中的每只资产须独立拟合参数。');
  if (s.preprocess?.automatic?.schema !== 'auto-factor-preprocess/2') errors.push('收益研究需要第二版类型化因子处理。');
  if (s.execution?.enabled !== false) errors.push('收益因子研究不启用交易执行。');
  if (s.model?.family === 'pair_reversion') errors.push('配对研究请使用独立的两腿目标。');
  return errors;
}
