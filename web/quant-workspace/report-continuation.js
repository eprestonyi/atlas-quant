// Report views cannot reconstruct an immutable source binding from strategy JSON.
// The run API does not expose a versioned experiment binding, so reopen saved
// research explicitly instead of guessing an experiment or falling back to a provider.
export function isFrozenReport(job, report, transport) {
  return ['ready_dataset', 'ready_market'].includes(job?.dataSource)
    || ['ready_dataset', 'ready_market'].includes(report?.provenance?.dataSource)
    || transport?.format === 'atlas.quant.financial_bundle'
    || !!transport?.sourceEvidence?.datasetRef
    || !!report?.provenance?.marketSource;
}

export const FROZEN_EDIT_HINT = '请在“我的研究”选择要编辑的已保存版本。当前保存版本可能晚于本次运行；原报告和当前草稿保持不变。';

export function reportErrorMessage(error) {
  if (error?.code === 'CAPACITY_MONITOR') return '资源或拟合进度监测未能完成，本次研究已停止。请在监测恢复后，以新运行检验；此错误不表示数据来源协议无效。';
  return error?.message || error || '请检查数据范围和策略参数后重新运行。';
}
