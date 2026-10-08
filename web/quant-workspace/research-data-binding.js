// Source identity and compute admission are independent, immutable research inputs.
export const SOURCE_LABELS = Object.freeze({ tushare: 'Tushare 实际数据', upload: '当前导入数据', demo: '合成教学数据', ready_dataset: '已冻结行情与财务数据集', ready_market: '完整筛选池的冻结行情' });
export const scopeKey = u => JSON.stringify({ selection: u.selection, resolutionHash: u.resolutionHash, snapshotHash: u.snapshotHash, symbols: u.symbols, start: u.start, end: u.end });
export const activeBinding = s => s.dataSource === 'ready_dataset' ? s.datasetBinding : s.dataSource === 'ready_market' ? s.marketDatasetBinding : null;
export function bindingFields(source, binding) {
  if (source === 'ready_dataset' && binding) return { datasetRef: structuredClone(binding.datasetRef), admissionProfile: binding.admissionProfile };
  if (source === 'ready_market' && binding) return { marketDatasetRef: structuredClone(binding.marketDatasetRef), universeScopeRef: structuredClone(binding.universeScopeRef), admissionProfile: binding.admissionProfile };
  return {};
}
export function matchesMarketScope(binding, universe) {
  const scope = binding?.scope;
  return !!scope && scope.start === universe.start && scope.end === universe.end && JSON.stringify(scope.symbols) === JSON.stringify(universe.symbols) && (!binding.selectionKey || binding.selectionKey === scopeKey(universe));
}
export function marketAdmission(strategy) {
  if (strategy.target.kind !== 'asset_price' || strategy.execution.enabled) return null;
  if (strategy.model.estimator === 'auto' && strategy.model.family === 'mean_reversion') return 'pooled_asset_1000_auto_candidate_v1';
  if (strategy.model.estimator === 'ridge' && ['mean_reversion', 'trend'].includes(strategy.model.family)) return 'pooled_asset_1000_v1';
  return null;
}
export function marketBindingErrors(s, { run = false } = {}) {
  if (s.dataSource !== 'ready_market') return [];
  const b = s.marketDatasetBinding, errors = [];
  if (!b) return ['市场数据尚未完成准备并绑定；模型研究不会代为请求供应商。'];
  if (!s.session?.workspace?.id || b.workspaceId !== s.session.workspace.id) errors.push('此本地数据绑定尚未在当前工作区核验。请从“我的研究”重新读取已保存版本；本地原记录保留。');
  if (!matchesMarketScope(b, s.strategy.universe)) errors.push('筛选规则、完整成员或研究日期已变化。请为当前完整范围重新核对数据准备；旧数据不会自动截取或补齐。');
  if (run) {
    const profile = marketAdmission(s.strategy);
    if (!profile || profile !== b.admissionProfile) errors.push('当前机制与冻结计算协议不匹配，请在研究设置重新绑定适用协议；系统不会替换估计器。');
    if (s.strategy.factors.length > 16 || s.strategy.model.refitDays < 20 || s.strategy.validation.innerFolds !== 2 || s.strategy.validation.outerFolds !== 2) errors.push('完整池计算协议要求最多 16 个因子、重拟合间隔至少 20 日，以及 2 个内层和 2 个外层时间折。');
  }
  return errors;
}
export function restoreBindings(s, item) {
  s.datasetBinding = item.datasetBinding ? structuredClone(item.datasetBinding) : null;
  s.marketDatasetBinding = item.marketDatasetBinding ? structuredClone(item.marketDatasetBinding) : null;
  if (s.datasetBinding) {
    s.datasetBinding.selectedStateIds = s.datasetBinding.selectedStateIds?.length ? s.datasetBinding.selectedStateIds : s.strategy.factors.map(f => f.id);
    s.dataSource = 'ready_dataset';
  } else if (s.marketDatasetBinding) {
    // The immutable experiment supplies the frozen rules; no current catalog lookup.
    s.marketDatasetBinding.workspaceId = s.session?.workspace?.id || null;
    s.marketDatasetBinding.selectionKey = scopeKey(s.strategy.universe);
    s.dataSource = 'ready_market';
  } else if (['ready_dataset', 'ready_market'].includes(s.dataSource)) s.dataSource = 'tushare';
}
