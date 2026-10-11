export const availabilityLabel = value => ({
  origin_known_by_policy: '起点已知 · 日频规则',
  origin_known_declared: '起点已知 · 用户声明',
  conditional_realized: '含起点后实测输入',
  conditional_scenario: '含假设输入',
  availability_unverified: '可用性未核验',
  unresolved_input: '输入未齐备',
})[value] || value || '未提供';
export const valueOriginLabel = value => ({
  observed_actual: '实测值', scenario_assumption: '假设值',
  model_prediction: '模型值',
})[value] || value || '未提供';

// Only records frozen with the row are evidence. Never fill dates from the
// current catalogue or mistake a session-policy bound for publication time.
export function renderInputEvidence(row, {esc:e, fmt, label}) {
  const evidence = row.inputEvidence;
  if (!evidence || typeof evidence !== 'object') return '<span>未保存逐输入时间证据</span>';
  const values=!!row.features;
  const heads=['输入',...(values?['经济值 R']:[]),'观察日','来源日','可用边界','来源类型','起点已知','状态'];
  return `<div class="sq-table-scroll"><table class="sq-table sq-input-evidence"><thead><tr>${heads.map(x=>`<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${Object.entries(evidence).map(([key,x])=>`<tr data-input-evidence="${e(key)}"><th>${e(label(key))}</th>${values?`<td>${fmt(row.features[key],6)}</td>`:''}<td>${e(x.evaluationDate || '—')}</td><td>${e(x.sourceDate || '—')}</td><td>${e(x.availableAt || '—')}<small>${e(x.availabilityBasis || '未提供依据')}</small></td><td>${e(valueOriginLabel(x.valueOrigin))}</td><td>${x.knownAtOrigin === true ? '是' : x.knownAtOrigin === false ? '否' : '未核验'}</td><td>${e(x.status || '—')}</td></tr>`).join('')}</tbody></table></div><small class="sq-subtle">可用边界按冻结规则记录，不代表供应商精确发布时间。</small>`;
}
