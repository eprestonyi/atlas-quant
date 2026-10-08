import { marketAdmission } from './research-data-binding.js';
import { financialBindingErrors } from './financial/research-binding.js';
import { validateStoredStatisticalQuant } from '../../edge/statistical-quant/validation.mjs';
import { validateExpression } from '../../edge/factor-language.mjs';

const result = (status, message, selectable = true) => ({ status, message, selectable });
const day = text => /^\d{8}$/.test(text || '') ? Date.UTC(+text.slice(0, 4), +text.slice(4, 6) - 1, +text.slice(6, 8)) / 86400000 : NaN;
export const admissionLabel = status => ({ blocked: '当前不可用', pending: '待核对', ready: '可准备数据', declared: '配置入口可用' })[status] || '待核对';

// This is a UI preflight, never an alternative to the server's exact admission.
export function marketPreparationAdmission(strategy, { verified = false, scopeMatches = false, admissions, fields } = {}) {
  const profile = marketAdmission(strategy), u = strategy.universe, n = u.symbols.length;
  if (!profile) return result('blocked', ({
    trend: strategy.model.estimator === 'auto' ? '当前完整池尚未开放趋势自动拟合；不会自动更换拟合方法或截取成员。' : null,
    pair_reversion: '当前配对研究使用最多 50 个完整成员的通用来源，并需明确两条篮子腿；完整池冻结行情尚不支持配对。',
    fundamental: '当前财务自动研究最多 50 个成员，需要实际财务或估值来源；完整池行情尚未接通此机制。',
    event: '当前事件研究最多 50 个完整成员，需要带可用时点的事件输入；完整池行情尚未接通事件协议。',
  })[strategy.model.family] || '当前完整池计算仅支持单资产价格目标及已声明机制；不会自动更换模型或截取成员。', false);
  if (!n) return result('pending', '先解析完整筛选集合。');
  let validated;
  try {
    // Use exactly the full static validator used by market validateCapacity.
    // It clones stored candidates; this check never repairs the user's draft.
    validated = validateStoredStatisticalQuant(strategy, { scopeSymbolLimit: 1000 });
  } catch (error) {
    return result('blocked', `研究配置未通过校验：${error.code ? error.message : '配置结构无效，请检查研究设置。'}`);
  }
  if (n > 1000 || u.symbols.some(x => !/\.(SH|SZ)$/.test(x))) return result('blocked', '完整池行情协议支持最多 1000 个沪深成员；请调整筛选条件，系统不会取样。');
  const span = day(u.end) - day(u.start) + 1;
  if (!Number.isFinite(span) || span < 1 || span > 366) return result('blocked', '此完整池协议要求研究窗口不超过 366 个自然日（含首尾）；请在研究设置调整日期。');
  if (strategy.factors.length > 16 || strategy.model.refitDays < 20 || strategy.validation.innerFolds !== 2 || strategy.validation.outerFolds !== 2)
    return result('blocked', '当前配置超出完整池拟合预算：最多 16 因子、重拟合至少 20 日、2×2 时间检验。');
  if (!verified || !scopeMatches) return result('pending', '核对当前完整范围与请求预算后，读取服务器的实际计算准入；尚未开始供应商请求。');
  if (!Array.isArray(fields) || !fields.every(x => typeof x === 'string')) return result('pending', '准备计划尚未返回可核对的行情字段，不能开始数据准备。');
  const required = new Set(validated.factors.flatMap(f => validateExpression(f.expression).fields));
  const missing = [...required].filter(field => !fields.includes(field));
  if (missing.length) return result('blocked', `准备计划未包含因子所需字段：${missing.join('、')}。请选择实际包含这些字段的数据来源；不会补取或伪造。`);
  const a = admissions?.find(x => x.admissionProfile === profile && Array.isArray(x.families) && x.families.includes(strategy.model.family) && x.estimator === strategy.model.estimator && x.targetKind === strategy.target.kind && x.executionEnabled === false);
  if (!a) return result('blocked', '服务器没有返回此机制的计算准入；暂不能为本次研究开始数据准备。');
  if (a.available !== true) return result('blocked', ({ MARKET_RESEARCH_DISABLED: '服务器尚未开放完整池模型计算。', RUNNER_OFFLINE: '完整池计算节点当前离线。', RUNNER_UPGRADE_REQUIRED: '计算节点尚未声明支持此机制的拟合协议。' })[a.reason] || '服务器尚未确认此机制可计算。');
  if (!['maxSymbols', 'maxCalendarDays', 'maxFactors', 'innerFolds', 'outerFolds', 'minRefitDays'].every(k => Number.isSafeInteger(a[k]) && a[k] > 0)) return result('pending', '服务器计算预算声明不完整，暂不能开始数据准备。');
  if (n > a.maxSymbols || span > a.maxCalendarDays || strategy.factors.length > a.maxFactors || strategy.model.refitDays < a.minRefitDays || strategy.validation.innerFolds !== a.innerFolds || strategy.validation.outerFolds !== a.outerFolds)
    return result('blocked', '当前完整范围或检验设置超出服务器声明的计算预算；不会截取成员或缩短窗口。');
  return result('ready', `服务器已声明支持当前 ${n} 个完整成员的计算配置。准备完成后仍须核验实际覆盖与训练样本；不代表模型有效。`);
}

export function mechanismAdmission(state, family, marketCheck, studio = false) {
  const original = state.strategy;
  const strategy = { ...original, model: { ...original.model, family, estimator: studio ? original.model.estimator : 'auto' } };
  if (family === 'pair_reversion' && original.model.family !== family) strategy.target = { ...original.target, kind: 'frozen_basket', basket: { method: 'pair_ols', symbols: [] } };
  const n = strategy.universe.symbols.length;
  if (state.dataSource === 'ready_dataset') {
    if (family !== 'fundamental') return result('blocked', '当前冻结财务来源只支持基本面条件预测；其他机制需另外选择适用来源。', false);
    const errors = financialBindingErrors(state);
    return errors.length ? result('blocked', errors[0]) : result('declared', '保留已保存的财务范围和拟合协议；运行时重新核验服务准入与实际样本。');
  }
  if (state.dataSource === 'ready_market' || n > 50) {
    if (n > 50 && ['demo', 'upload'].includes(state.dataSource)) return result('blocked', '当前通用来源协议最多 50 个成员；完整池研究须使用适用的冻结行情协议，不会自动改来源或取样。');
    return marketCheck(strategy);
  }
  if (!n) return result('pending', '先解析完整筛选集合，再核对来源和研究设置。');
  if (family === 'pair_reversion') return result('pending', '通用来源最多 50 个完整成员；需明确两条篮子腿和形成窗口，不会自动搜索全池配对。');
  if (family === 'fundamental') return result('pending', '需要实际已披露的财务或估值字段；可绑定最多 50 个成员的财务数据集。只有行情或因子名称还不能拟合。');
  if (family === 'event') return result('pending', '需要带可用时点的真实事件输入，并指定事件角色；普通日线行情不是事件源。当前通用协议最多 50 个完整成员。');
  if (state.dataSource === 'upload' && !state.dataset) return result('pending', '此机制支持当前完整范围；请导入研究数据，实际覆盖和样本仍需核验。');
  if (state.dataSource === 'tushare' && !state.session?.capabilities?.tushareHosted) return result('pending', '此机制支持最多 50 个完整成员；Tushare 接入当前尚未确认可用。');
  return result('declared', `当前 ${n} 个完整成员可使用此机制的通用自动拟合入口；数据覆盖、成熟标签与样本条件仍须检验。`);
}
