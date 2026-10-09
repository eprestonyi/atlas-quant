const PROVIDERS = Object.freeze({
  YAHOO_YFINANCE: 'Yahoo Finance / yfinance',
  TUSHARE: 'Tushare',
});
const APIS = Object.freeze({
  yfinance_history: 'Yahoo Finance / yfinance',
  index_daily: 'Tushare', sw_daily: 'Tushare', us_daily_adj: 'Tushare',
});

export function providerLabel(source = {}) {
  return PROVIDERS[source.provider] || APIS[source.providerApi || source.api]
    || (typeof source.source === 'string' ? source.source : '')
    || source.provider || '';
}
