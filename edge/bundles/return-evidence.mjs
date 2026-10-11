/** Immutable per-input evidence for study/2; daily policy dates are not timestamps. */
import {parseExpression} from '../factor-language.mjs';
import {returnFactorDescriptors} from '../../web/asset-return-factor-contract.js';
import quantities from '../../engine/atlas_quant/factor_quantity_fields.json' with {type:'json'};

const object = x => x && typeof x === 'object' && !Array.isArray(x);
const sameKeys = (x, keys) => object(x) && Object.keys(x).sort().join(',') === [...keys].sort().join(',');
const evidenceKeys = ['valueOrigin','evaluationDate','availableAt','sourceDate','availabilityBasis','knownAtOrigin','status'];
export const coordinateKeys = ['inputEvidence','inputAvailableAt','sampleReadyAt','availabilityClass'];

export function returnEvidenceContract(strategy, dates) {
  if (strategy?.research?.returnStudy?.schema !== 'asset-return-study/2') return null;
  const descriptors = returnFactorDescriptors(strategy).map(input => {
    const unknown = parseExpression(input.expression).fields.some(field =>
      field.startsWith('fd_') || field.startsWith('model_fin_') ||
      field.startsWith('ext_') && !quantities.fields[field]?.sourceIdentity);
    return {...input,availabilityBasis:unknown ? 'declared_data_alignment' :
      input.clock === 'observed_source_sessions_asof' ? 'native_source_session_policy' : 'research_session_policy'};
  });
  return {dates,descriptors,horizon:strategy.target.horizonSessions};
}

export function validateReturnEvidence(row, contract, fail) {
  if (!contract || !Array.isArray(contract.dates) || !Array.isArray(contract.descriptors)) fail('时移输入缺少冻结日历与构造');
  const {dates,descriptors,horizon} = contract, t = dates.indexOf(row.date);
  if (t < 0 || !sameKeys(row.inputEvidence,descriptors.map(d => d.feature))) fail('逐输入证据与实例或日历不一致');
  if (row.responseStartDate !== row.date || row.responseEndDate !== (dates[t+horizon] ?? null)) fail('时移研究目标区间与冻结日历不一致');
  let unresolved = false, conditional = false, unknown = false;
  const available = [];
  for (const input of descriptors) {
    const e = row.inputEvidence[input.feature], offset = input.timing.offsetSessions;
    const evaluation = dates[t+offset] ?? null, basis = input.availabilityBasis;
    if (!sameKeys(e,evidenceKeys) || e.valueOrigin !== 'observed_actual' ||
        e.evaluationDate !== evaluation || e.availableAt !== evaluation || e.availabilityBasis !== basis)
      fail('逐输入证据的时移或可用日政策不一致');
    const known = evaluation === null || basis === 'declared_data_alignment' ? null : evaluation <= row.date;
    if (e.knownAtOrigin !== known) fail('条件已实现或未验证输入不能声明起点已知');
    if (evaluation === null) {
      if (e.status !== 'outside_available_calendar' || e.sourceDate !== null) fail('越界输入不能伪造已观测证据');
      unresolved = true;
    } else {
      if (!['observed','missing_observation'].includes(e.status)) fail('可用日内输入证据状态无效');
      if (basis === 'declared_data_alignment') {
        if (e.sourceDate !== null) fail('未验证数据对齐不能伪造来源观察日期');
      } else if (input.clock === 'observed_source_sessions_asof') {
        if (e.sourceDate !== null && (typeof e.sourceDate !== 'string' || !/^\d{8}$/.test(e.sourceDate) || e.sourceDate >= evaluation)) fail('来源日期不能越过已冻结的保守对齐边界');
        if (e.sourceDate === null && e.status !== 'missing_observation') fail('无来源观测不能声明已观测');
      } else if (e.sourceDate !== evaluation) fail('研究格点来源日期与时移不一致');
      if (offset > 0 && e.status === 'missing_observation') unresolved = true;
    }
    if (row.features) {
      const finite = typeof row.features[input.feature] === 'number' && Number.isFinite(row.features[input.feature]);
      if ((e.status === 'observed') !== finite) fail('数值与逐输入观测证据不一致');
    }
    available.push(e.availableAt);
    conditional ||= e.availableAt !== null && e.availableAt > row.date;
    unknown ||= e.knownAtOrigin === null;
  }
  const inputAvailableAt = available.every(d => d !== null) ? [...available].sort().at(-1) : null;
  const sampleReadyAt = inputAvailableAt !== null && row.responseEndDate !== null ? [inputAvailableAt,row.responseEndDate].sort().at(-1) : null;
  const availabilityClass = unresolved ? 'unresolved_input' : conditional ? 'conditional_realized' : unknown ? 'availability_unverified' : 'origin_known_by_policy';
  if (row.inputAvailableAt !== inputAvailableAt || row.sampleReadyAt !== sampleReadyAt || row.availabilityClass !== availabilityClass)
    fail('样本成熟时间或条件输入分类不一致');
  if (unresolved && (row.inputValid !== false || row.status === 'valid')) fail('未解决的未来或越界输入不能成为有效样本');
  return {inputAvailableAt,sampleReadyAt,availabilityClass};
}
