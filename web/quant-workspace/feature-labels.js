// Display labels only. Financial names mirror edge/financial/definitions.json;
// the DOM contract checks parity. Never rewrite model feature keys or outputs.
export const FINANCIAL_FEATURE_LABELS = Object.freeze({
  model_fin_revenue_quarter_yoy: '收入季度同比',
  model_fin_revenue_ttm_yoy: 'TTM收入同比',
  model_fin_operating_margin: '经营利润率',
  model_fin_parent_net_margin: '归母净利率',
  model_fin_cash_revenue_ratio: '经营现金收入比',
  model_fin_profit_cash_asset_gap: '现金利润差',
  model_fin_cash_assets_ratio: '现金流资产比',
  model_fin_capex_revenue_ratio: '购建现金支出强度',
  model_fin_cash_less_capex_assets: '经营现金流减购建支出与资产比',
  model_fin_assets_yoy: '资产同比',
  model_fin_cash_asset_share: '现金资产占比',
  model_fin_current_coverage: '流动性覆盖',
  model_fin_liability_asset_share: '账面负债占比',
  model_fin_borrowings_asset_share: '短长借款与资产比',
  model_fin_receivable_asset_share: '应收账款资产占比',
  model_fin_goodwill_asset_share: '商誉资产占比'
});
const BUILTIN = Object.freeze({
  volatility20: '20日状态变化波动', state_deviation20: '20日状态偏离', state_deviation60: '60日状态偏离',
  change1: '1日状态变化', change5: '5日状态变化',
  trend1: '1日状态趋势', trend5: '5日状态趋势', trend20: '20日状态趋势', trend60: '60日状态趋势'
});
export function createFeatureLabeler({factors = [], catalog = []} = {}) {
  return (key, definition) => {
    if (typeof key !== 'string') return String(key ?? '');
    if (!key.startsWith('factor:')) return Object.hasOwn(BUILTIN, key) ? BUILTIN[key] : key;
    const id = key.slice(7), factor = definition?.id === id ? definition : factors.find(x => x.id === id);
    if (!factor) return key;
    if (typeof factor.name === 'string' && factor.name.trim()) return factor.name;
    // Same ID in a current catalogue is insufficient if its formula/version changed.
    const entry = catalog.find(x => x.id === id && x.expression === factor.expression &&
      (factor.version === undefined || x.version === factor.version));
    const label = entry?.name || (Object.hasOwn(FINANCIAL_FEATURE_LABELS, factor.expression || '')
      ? FINANCIAL_FEATURE_LABELS[factor.expression] : null);
    return label ? label + (factor.direction === -1 ? '（反向）' : '') : key;
  };
}
export const reportFeatureLabeler = (report, catalog = []) => createFeatureLabeler({
  factors: report?.forecasts?.sourceStrategy?.factors || report?.strategy?.factors || [], catalog
});

export function constructedFeatureLabel(name, construction) {
  const kind = construction?.transform?.kind;
  if (kind === 'return_over_trailing_volatility') return /价格/.test(name) ? name.replace(/价格/g, '波动率标准化收益') : `${name} · 波动率标准化收益`;
  if (kind === 'first_difference' && construction.economicType === 'log_price') return /价格/.test(name) ? name.replace(/价格/g, '对数收益率') : `${name} · 对数收益率`;
  if (kind === 'simple_return' || kind === 'log_return') { const suffix = kind === 'simple_return' ? '简单收益率' : '对数收益率'; return /价格/.test(name) ? name.replace(/价格/g, suffix) : `${name} · ${suffix}`; }
  if (kind === 'signed_log1p') return `${name} · 带符号数量压缩`;
  if (kind === 'log_positive') return `${name} · ln`;
  if (kind === 'log1p_nonnegative') return `${name} · ln(1+x)`;
  if (kind === 'reciprocal_nonzero') return `${name} · 倒数`;
  return name;
}

export function automaticFactorLabel(factor, enabled = false) {
  const name = factor?.name || factor?.label || factor?.id || '未命名';
  if (enabled === 'auto-factor-preprocess/2' && factor?.automaticProcessing?.schema === enabled && factor.automaticProcessing.descriptor?.expression === factor.expression) return constructedFeatureLabel(name, factor.automaticProcessing.descriptor);
  if (!enabled || !/^(open|high|low|close|ext_ctx_[a-z0-9_]+_(open|high|low|close))$/.test(factor?.expression || '')) return name;
  const suffix = enabled === 'auto-factor-preprocess/2' ? '简单收益率' : '波动率标准化收益';
  return /价格/.test(name) ? name.replace(/价格/g, suffix) : `${name} · ${suffix}`;
}
