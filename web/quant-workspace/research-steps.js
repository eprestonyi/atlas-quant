import { STEPS, validateStrategy } from './defaults.js';
import contextRegistry from '../../engine/atlas_quant/context_sources.json' with { type: 'json' };

const contextSources = new Map(contextRegistry.items.flatMap(source =>
  (source.api === 'sw_daily' ? ['close', 'vol', 'amount', 'pe', 'pb', 'total_mv', 'float_mv'] : ['close', 'vol', 'amount'])
    .map(field => ['ext_ctx_' + source.ts_code.toLowerCase().replace('.', '_') + '_' + field, source.ts_code])));
const supports = (runner, field, format) => Array.isArray(runner?.[field]) && runner[field].includes(format);
function hasUploadedContext(dataset, fields) {
  const provenance = dataset?.provenance;
  return /^[a-f0-9]{64}$/.test(provenance?.contextSourceRoot || '') &&
    provenance?.contextScope === 'named_index_series_broadcast_by_date' &&
    provenance?.contextObservationClock === 'after_daily_publication_before_next_open' &&
    Array.isArray(provenance?.contextSources) && provenance.contextSources.length > 0 &&
    fields.every(field => contextSources.has(field) && provenance.contextSources.some(source => source?.params?.ts_code === contextSources.get(field) &&
      Array.isArray(source?.records) && source.records.length > 0));
}

export const isPairTarget = strategy => strategy.target?.kind === 'frozen_basket' && strategy.target.basket?.method === 'pair_ols';

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
    if (fields.length && options.dataSource === 'demo')
      errors.push('指数因子请选择 Tushare 或导入对应指数数据。');
    if (fields.length && options.dataSource === 'upload' && !hasUploadedContext(options.dataset, fields))
      errors.push('导入文件缺少所选指数的完整来源包，请重新导入。');
    if (automatic && ['ready_dataset', 'ready_market'].includes(options.dataSource))
      errors.push('此冻结数据来源暂不支持自动因子处理。');
  }
  if (step === 'model' && options.easy)
    errors = errors.map(error => /形成窗口|共同主成分数量|固定数量|冻结篮子的构造方法/.test(error) ? '当前目标的高级设置不完整，请在 Studio 调整。' : error);
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
