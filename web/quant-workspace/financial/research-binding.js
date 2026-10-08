// Financial composition and estimator admission are deliberately separate contracts.
import { FINANCIAL_AUTO, financialProfile, isFinancialAuto, validDatasetRef } from '../datasets/protocol.js';
export { FINANCIAL_AUTO };
export function financialAdmission(detail, estimator = 'auto') {
  const profile = financialProfile(detail?.datasetRef, estimator);
  if (!profile) return { admission: null, available: false, reason: '此数据版本没有声明所选拟合协议；不会套用其他版本或改用 Ridge。' };
  if (detail.datasetRef.version === 3 && detail.sourceEvidenceClosure !== 'separate_research_dataset_v3') return { admission: null, available: false, reason: '未返回完整的 graph 来源闭包声明。' };
  const admission = detail?.researchAdmissions?.find(x => x.profile === profile && x.estimator === estimator);
  const preferred = estimator !== 'auto' || detail?.preferredResearchAdmission?.profile === profile;
  const available = detail?.status === 'ready' && detail?.researchBindingEnabled === true && admission?.configurationEligible === true && admission?.runnerAvailable === true && preferred;
  let reason = '';
  if (!admission) reason = '服务尚未返回这个拟合协议的可用声明。';
  else if (admission.reasonCodes?.includes('DATASET_SCOPE_EXCEEDS_AUTO_PROFILE')) reason = '当前冻结范围超出自动协议的 50 标的、366 日预算；不会截取成员或缩短日期。';
  else if (!detail.researchBindingEnabled || !admission.configurationEligible) reason = '此数据集的模型研究尚未开放。';
  else if (!admission.runnerAvailable) reason = '计算节点尚未就绪或尚未声明支持此拟合协议。';
  else if (!preferred) reason = '服务未声明自动协议为当前可新建的研究入口。';
  return { admission, available: !!available, reason };
}
export function financialBindingErrors(s) {
  if (s.dataSource !== 'ready_dataset') return [];
  const b = s.datasetBinding;
  if (!b) return ['请重新选择已冻结财务数据集。'];
  const errors = [];
  if (!s.session?.workspace?.id || b.workspaceId !== s.session.workspace.id) errors.push('财务数据绑定尚未在当前私有工作区核验，请从当前“我的研究”重新读取。');
  const profile = financialProfile(b.datasetRef, s.strategy.model.estimator);
  if (!validDatasetRef(b.datasetRef) || !profile || b.admissionProfile !== profile) errors.push('财务数据版本与计算协议不匹配；不会转换旧数据或更换估计器。');
  if (isFinancialAuto(b.admissionProfile)) {
    const st = s.strategy;
    if (st.model.estimator !== 'auto' || st.model.family !== 'fundamental' || st.target.kind !== 'asset_price' || st.execution.enabled) errors.push('此冻结财务数据绑定的是基本面自动拟合、单资产价格、仅预测协议；不会自动更换估计器。');
    if (st.factors.length > 16 || st.model.refitDays < 20 || st.validation.innerFolds !== 2 || st.validation.outerFolds !== 2) errors.push('财务自动协议最多使用 16 个因子，重拟合间隔至少 20 日，并使用 2×2 时间验证。');
  }
  return errors;
}
