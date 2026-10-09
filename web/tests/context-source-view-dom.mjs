/** Frozen-source presentation only: no provider access, fitting or coverage inference. */
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createForms } from '../quant-workspace/forms.js';
import { renderContextSources } from '../quant-workspace/context-source-view.js';
import { providerLabel } from '../quant-workspace/source-labels.js';
import { createIndustryBrowser } from '../quant-workspace/industry-browser.js';
const dom = new JSDOM('<main></main>'); globalThis.document = dom.window.document;
const e = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const C = { esc:e, icon:()=>'', state:{catalog:{},strategy:{factors:[]}}, render(){} }, F = createForms(C), main=document.querySelector('main');
const provenance = {
  contextSourceRoot:'a'.repeat(64), contextObservationClock:'source_session_publication_before_cn_origin',
  contextSources:[
    {api:'sw_daily',params:{ts_code:'801120.SI',start_date:'20260101',end_date:'20260930'},fields:['ts_code','trade_date','close'],rowCount:175,sha256:'b'.repeat(64)},
    {api:'yfinance_history',params:{ts_code:'XSD',start_date:'20260101',end_date:'20260930'},fields:['ts_code','trade_date','adj_factor','close'],rowCount:178,sha256:'c'.repeat(64),priceAdjustment:'yahoo_adj_close_split_dividend',historicalRevisionVerified:false},
  ],
};
const frozen=JSON.stringify(provenance); main.innerHTML=renderContextSources(C,F,provenance);
assert.equal(main.querySelectorAll('tbody tr').length,2);
assert(main.textContent.includes('Yahoo Finance / yfinance')); assert(main.textContent.includes('Tushare'));
assert(main.textContent.includes('请求区间')); assert(main.textContent.includes('已保存行数'));
assert(main.textContent.includes('请求区间不代表逐日完整覆盖')); assert(main.textContent.includes('2026-01-01 — 2026-09-30'));
assert.equal(main.querySelector('details').open,false); assert(main.textContent.includes('yahoo_adj_close_split_dividend'));
assert.equal(JSON.stringify(provenance),frozen); assert.equal(renderContextSources(C,F,{}),'');
assert.equal(providerLabel({api:'unknown'}),'');
const unsafe=structuredClone(provenance);unsafe.contextSources[0].params.ts_code='<img src=x onerror=alert(1)>';
main.innerHTML=renderContextSources(C,F,unsafe); assert(!main.querySelector('img')); assert(main.textContent.includes('<img'));

// Availability stays specific to each ETF, including when their provider matches.
C.state.catalog.industrySources={items:[],proxies:[
  {id:'xsd',market:'US',symbol:'XSD',name:'Semiconductors',sourceKind:'etf_proxy',provider:'YAHOO_YFINANCE',providerApi:'yfinance_history',historyStatus:'adapter_supported_requires_observations',factorId:'context_yf_xsd_price'},
  {id:'xlk',market:'US',symbol:'XLK',name:'Technology',sourceKind:'etf_proxy',provider:'YAHOO_YFINANCE',providerApi:'yfinance_history',historyStatus:'adapter_supported_history_unverified',factorId:'context_yf_xlk_price'},
]};
const browser=createIndustryBrowser(C,F);browser.state.market='US'; main.innerHTML=browser.view();
assert.equal(main.querySelectorAll('[data-v2="add-factor"]').length,1);
assert.equal(main.querySelector('[data-v2="add-factor"]').dataset.id,'context_yf_xsd_price');
assert(main.querySelectorAll('.sq-industry-card')[1].querySelector('footer button').disabled);
assert(main.textContent.includes('历史待验'));assert(main.textContent.includes('Yahoo Finance / yfinance'));
console.log(JSON.stringify({frozenProviderLabels:true,requestedRangeNotCoverage:true,adjustmentInCollapsedSourceDetails:true,perEtfAvailability:true,noMutation:true,providerCalls:0}));
dom.window.close();
