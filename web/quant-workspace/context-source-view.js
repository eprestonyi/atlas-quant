import { providerLabel } from './source-labels.js';

// Only frozen report metadata is rendered here. Catalog probes never substitute
// for the observations actually saved by this research run.
export function renderContextSources(C, F, provenance) {
  const sources = provenance?.contextSources;
  if (!Array.isArray(sources) || !sources.length) return '';
  const e = C.esc;
  const date = value => /^\d{8}$/.test(value || '')
    ? `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}` : value || '—';
  const row = s => `<tr><th scope="row">${e(s.params?.ts_code || s.symbol || '—')}</th><td>${e(providerLabel(s) || '未提供')}<small>${e(s.api || s.providerApi || '—')}</small></td><td>${e(date(s.params?.start_date))} — ${e(date(s.params?.end_date))}</td><td class="numeric">${Number.isSafeInteger(s.rowCount) && s.rowCount >= 0 ? e(s.rowCount) : '—'}</td><td>${e((s.fields || []).join(', '))}</td></tr>`;
  const providerDetails = value => value && typeof value === 'object' ? Object.fromEntries(
    ['provider','libraryVersion','retrievedAt','currency','exchangeTimezoneName','instrumentType','libraryCalls','httpReceipts']
      .filter(key => Object.hasOwn(value,key)).map(key => [key,value[key]])
  ) : undefined;
  const details = {
    contextSourceRoot: provenance.contextSourceRoot,
    contextScope: provenance.contextScope,
    contextObservationClock: provenance.contextObservationClock,
    sources: sources.map(s => ({
      api: s.api, symbol: s.params?.ts_code || s.symbol, sha256: s.sha256,
      ...(s.provider ? { provider: s.provider } : {}),
      ...(s.priceAdjustment ? { priceAdjustment: s.priceAdjustment } : {}),
      ...(s.alignment ? { alignment: s.alignment } : {}),
      ...(s.providerVersion ? { providerVersion: s.providerVersion } : {}),
      ...(s.providerDetails ? { providerDetails: providerDetails(s.providerDetails) } : {}),
      ...(s.observedRange ? { observedRange: s.observedRange } : {}),
      ...(typeof s.historicalRevisionVerified === 'boolean' ? { historicalRevisionVerified: s.historicalRevisionVerified } : {}),
    })),
  };
  return F.panel('指数与 ETF 数据来源', `<div class="sq-table-scroll"><table class="sq-table"><thead><tr>${['来源标的','供应商 / 接口','请求区间','已保存行数','字段'].map(v=>`<th>${v}</th>`).join('')}</tr></thead><tbody>${sources.map(row).join('')}</tbody></table></div>` +
    F.advanced('时点、复权与来源身份', `<p>请求区间不代表逐日完整覆盖；各来源的实际观测与处理口径保存在冻结来源包。</p><pre class="sq-report-code">${e(JSON.stringify(details,null,2))}</pre>`));
}
