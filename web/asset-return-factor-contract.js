import {parseExpression} from '../edge/factor-language.mjs';
import {typedFactorDescriptor} from './factor-preprocess-semantics.js';
import fieldRegistry from '../engine/atlas_quant/factor_quantity_fields.json' with {type:'json'};

const PRICE_IDENTITY_FIELDS = new Set(['open','high','low','close','raw_close','pe','pe_ttm','pb','ps','ps_ttm','total_mv','circ_mv','float_mv','dv_ratio','dv_ttm']);
const fail = (code,message) => { const error = Error(message); error.code = code; throw error; };
const children = node => node.kind === 'call' ? node.args : node.kind === 'binary' ? [node.left,node.right] : node.kind === 'unary' ? [node.operand] : [];
const priceDerived = node => node.kind === 'field' && fieldRegistry.fields[node.name]?.kind === 'price' || children(node).some(priceDerived);
function pricePeriods(node,horizon,used) {
  if (node.kind === 'call' && (node.name === 'returns' || ['lag','delta'].includes(node.name) && priceDerived(node.args[0]))) {
    if (node.args[1].kind !== 'number' || node.args[1].literal !== horizon) fail('ASSOCIATION_PERIOD_MISMATCH','同期收益因子的显式收益窗口必须与响应期限一致');
    used += horizon;
    if (used > horizon) fail('ASSOCIATION_PERIOD_MISMATCH','同期收益因子不能重复移位或使用上一响应区间');
  }
  children(node).forEach(child => pricePeriods(child,horizon,used));
}

/** Shared step/API timing admission, matching Python return_study/contract.py. */
export function returnFactorDescriptors(strategy) {
  const mode = strategy.research?.returnStudy?.mode, horizon = strategy.target?.horizonSessions;
  const timed = strategy.research?.returnStudy?.schema === 'asset-return-study/2';
  if ((!timed && !['forecast','association'].includes(mode)) || !Number.isInteger(horizon) || horizon < 1 || horizon > 252) fail('RETURN_STUDY','收益研究协议或响应期限无效');
  const overrides = strategy.preprocess?.automatic?.overrides || {};
  return (strategy.factors || []).map(factor => {
    if (factor.role === 'hedge') fail('RETURN_FACTOR_ROLE','独立收益方程不接受篮子对冲腿');
    const item = typedFactorDescriptor(factor,overrides[factor.id]), parsed = parseExpression(factor.expression);
    if (!timed && mode === 'association') {
      const ownReference = parsed.fields.some(field => {
        const source = fieldRegistry.fields[field]?.sourceIdentity;
        if (!source) return false;
        const [api,symbol] = source.split(':');
        return !['index_daily','sw_daily'].includes(api) && (strategy.universe?.symbols || []).includes(symbol);
      });
      if (parsed.fields.some(field => PRICE_IDENTITY_FIELDS.has(field)) || ownReference) fail('ASSOCIATION_TARGET_LEAKAGE','同期关联不能用本证券的价格或含本证券价格的估值构造解释自身收益；可选择明确的市场、行业或其他证券参考');
      pricePeriods(parsed.tree,horizon,['simple_return','log_return','first_difference'].includes(item.transform.kind) ? horizon : 0);
      if (item.transform.kind === 'return_over_trailing_volatility') fail('ASSOCIATION_PERIOD_MISMATCH','同期关联请选择明确的同期限简单收益或对数收益构造');
      if (['simple_return','log_return','first_difference'].includes(item.transform.kind)) item.transform.lag = horizon;
      item.clock = 'research_sessions';
    }
    item.aggregation = item.scope === 'global' ? 'global_once' : 'asset_direct';
    if (timed) {
      const {baseFactorId,timing} = normalizeFactorTiming(factor);
      item.baseFactorId = baseFactorId;
      item.timing = {...timing,offsetClock:'research_sessions'};
    } else item.timing = {kind:mode === 'forecast' ? 'origin_known' : 'matched_period',horizonSessions:horizon};
    return item;
  });
}

/** Instance identity is independent of its economic definition and time position. */
export function normalizeFactorTiming(factor) {
  const baseFactorId = factor.baseFactorId === undefined ? factor.id : factor.baseFactorId;
  if (typeof baseFactorId !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(baseFactorId)) fail('FACTOR_BASE_ID','基础因子 ID 无效');
  const timing = factor.timing === undefined ? {anchor:'target_origin',offsetSessions:0} : factor.timing;
  if (!timing || Array.isArray(timing) || Object.keys(timing).sort().join(',') !== 'anchor,offsetSessions' || timing.anchor !== 'target_origin' || !Number.isInteger(timing.offsetSessions) || Math.abs(timing.offsetSessions) > 252) fail('FACTOR_TIMING','因子时点须为目标起点前后 252 个研究交易日以内的整数');
  return {baseFactorId,timing:{anchor:'target_origin',offsetSessions:timing.offsetSessions}};
}
