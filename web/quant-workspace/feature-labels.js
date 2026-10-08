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
