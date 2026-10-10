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
  if (!['forecast','association'].includes(mode) || !Number.isInteger(horizon) || horizon < 1 || horizon > 252) fail('RETURN_STUDY','收益研究模式或响应期限无效');
  const overrides = strategy.preprocess?.automatic?.overrides || {};
  return (strategy.factors || []).map(factor => {
    if (factor.role === 'hedge') fail('RETURN_FACTOR_ROLE','独立收益方程不接受篮子对冲腿');
    const item = typedFactorDescriptor(factor,overrides[factor.id]), parsed = parseExpression(factor.expression);
    if (mode === 'association') {
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
    item.timing = {kind:mode === 'forecast' ? 'origin_known' : 'matched_period',horizonSessions:horizon};
    return item;
  });
}
