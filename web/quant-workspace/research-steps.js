import { STEPS, validateStrategy } from './defaults.js';
import contextRegistry from '../../engine/atlas_quant/context_sources.json' with { type: 'json' };

const contextSources = new Map(contextRegistry.items.flatMap(source =>
  (source.api === 'sw_daily' ? ['close', 'vol', 'amount', 'pe', 'pb', 'total_mv', 'float_mv'] : ['close', 'vol', 'amount'])
    .map(field => ['ext_ctx_' + source.ts_code.toLowerCase().replace('.', '_') + '_' + field, source.ts_code])));
const supports = (runner, field, format) => Array.isArray(runner?.[field]) && runner[field].includes(format);
function hasUploadedContext(dataset, fields) {
  const provenance = dataset?.provenance;
  const foreign = provenance?.contextSources?.some(source => source?.api === 'us_daily_adj');
  return /^[a-f0-9]{64}$/.test(provenance?.contextSourceRoot || '') &&
    provenance?.contextScope === (foreign ? 'named_market_series_asof_broadcast_by_date' : 'named_index_series_broadcast_by_date') &&
    provenance?.contextObservationClock === (foreign ? 'source_session_publication_before_cn_origin' : 'after_daily_publication_before_next_open') &&
    Array.isArray(provenance?.contextSources) && provenance.contextSources.length > 0 &&
    fields.every(field => contextSources.has(field) && provenance.contextSources.some(source => source?.params?.ts_code === contextSources.get(field) &&
      Array.isArray(source?.records) && source.records.length > 0));
}

export const isPairTarget = strategy => strategy.target?.kind === 'frozen_basket' && strategy.target.basket?.method === 'pair_ols';

// A saved combination can outlive a changed filter. Describe it without
// changing its members, quantities, construction method, or prediction target.
export function easyTargetScopeMismatch(strategy) {
  if (strategy.target?.kind !== 'frozen_basket' || strategy.model.family === 'pair_reversion') return false;
  const symbols = strategy.target.basket?.symbols || [];
  return new Set(symbols).size !== symbols.length || symbols.some(code => !strategy.universe.symbols.includes(code));
}
export const canUseIndividualTarget = strategy => easyTargetScopeMismatch(strategy) && !strategy.factors.some(factor => factor.role === 'hedge');
export const easyTargetScopeMessage = strategy => canUseIndividualTarget(strategy)
  ? '原研究组合与当前筛选结果不一致。请调整筛选，或改为逐只研究当前股票。'
  : '原研究组合与当前筛选结果不一致。请调整筛选，或在 Studio 修改原组合。';

// Called only when the user chooses the pair mechanism. Loading a study or
// changing its filter never redefines a saved prediction target.
export function pairTarget(strategy, { automatic = true } = {}) {
  if (isPairTarget(strategy)) return structuredClone(strategy.target);
  const symbols = strategy.universe.symbols;
  return {
    ...strategy.target,
    kind: 'frozen_basket',
    basket: { method: 'pair_ols', symbols: automatic && symbols.length === 2 ? [...symbols] : [], formationDays: 126 },
  };
}

export function stepErrors(strategy, step, options = {}) {
  let errors = validateStrategy(strategy, { ...options, step, includeData: step === 'model' });
  if (step === 'state' && options.easy)
    errors = errors.map(error => error.includes('事件模型需要') ? '添加带可用时间的事件输入，或在 Studio 配置。' : error);
  if (step === 'state') {
    const format = 'auto-factor-preprocess/1', automatic = strategy.preprocess?.automatic?.schema === format;
    const fields = strategy.factors.flatMap(factor => typeof factor.expression === 'string' ? factor.expression.match(/\bext_ctx_[A-Za-z0-9_]*\b/g) || [] : []);
    if (fields.some(field => !contextSources.has(field)))
      errors.push('指数因子来源未登记，请更换因子。');
    if (new Set(fields.map(field => contextSources.get(field)).filter(Boolean)).size > 16)
      errors.push('一次研究最多使用 16 个不同指数来源。');
    if (fields.length && !automatic)
      errors.push('大盘与行业输入需要自动处理，请新建轻松研究后添加。');
    if (automatic && options.session?.runner && !supports(options.session.runner, 'factorPreprocessFormats', format))
      errors.push('计算节点暂不支持自动因子处理，请稍后重试。');
    if (fields.length && options.session?.runner && !supports(options.session.runner, 'contextSourceFormats', 'named-index-history/1'))
      errors.push('计算节点暂不支持指数数据，请稍后重试。');
    if (fields.some(field => /^ext_ctx_[a-z]/.test(field)) && options.session?.runner && !supports(options.session.runner, 'contextSourceFormats', 'named-market-history/2'))
      errors.push('计算节点暂不支持跨市场ETF数据，请稍后重试。');
    if (fields.length && options.dataSource === 'demo')
      errors.push('指数因子请选择 Tushare 或导入对应指数数据。');
    if (fields.length && options.dataSource === 'upload' && !hasUploadedContext(options.dataset, fields))
      errors.push('导入文件缺少所选指数的完整来源包，请重新导入。');
    if (automatic && ['ready_dataset', 'ready_market'].includes(options.dataSource))
      errors.push('此冻结数据来源暂不支持自动因子处理。');
  }
  if (step === 'model' && options.easy)
    errors = errors.map(error =>
      error === '篮子腿需为已选择研究成员的唯一子集。' && strategy.model.family !== 'pair_reversion'
        ? easyTargetScopeMessage(strategy)
        : /形成窗口|共同主成分数量|固定数量|冻结篮子的构造方法|PCA 状态篮子/.test(error) ? '当前组合的设置不完整，请在 Studio 调整。' : error);
  if (step === 'model' && options.easy && strategy.model.family === 'pair_reversion') {
    errors = errors.filter(error => !/配对篮子|篮子腿|两腿 OLS/.test(error));
    const pair = strategy.target?.basket?.symbols || [];
    if (!isPairTarget(strategy) || pair.length !== 2 || ![0, 1].every(index => typeof pair[index] === 'string' && pair[index]) || pair[0] === pair[1])
      errors.push('选择两个不同的配对对象。');
    else if (pair.some(code => !strategy.universe.symbols.includes(code)))
      errors.push('配对对象已不在当前筛选结果中，请重新选择。');
  }
  return errors;
}

export function firstIncompleteStep(strategy, destination, options = {}) {
  const end = STEPS.findIndex(entry => entry.id === destination);
  for (const entry of STEPS.slice(0, Math.max(0, end))) {
    const errors = stepErrors(strategy, entry.id, options);
    if (errors.length) return { step: entry.id, errors };
  }
  return null;
}
