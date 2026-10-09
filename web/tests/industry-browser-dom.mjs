/** Catalog UI evidence only. It does not fetch any industry or ETF observations. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM } from 'jsdom';
import { createForms } from '../quant-workspace/forms.js';
import { createIndustryBrowser } from '../quant-workspace/industry-browser.js';
const catalog=JSON.parse(fs.readFileSync('engine/atlas_quant/industry_sources.json','utf8'));
const dom=new JSDOM('<main></main>',{url:'http://localhost/quant/'});globalThis.document=dom.window.document;
const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const C={esc:e,icon:()=>'',state:{catalog:{industrySources:catalog},strategy:{factors:[]}},render:()=>{document.querySelector('main').innerHTML=browser.view();}};
const browser=createIndustryBrowser(C,createForms(C)),main=document.querySelector('main');
C.render();assert.equal(main.querySelectorAll('.sq-industry-card').length,18);
assert(main.textContent.includes('官方指数'));
browser.state.query='白酒';C.render();assert(main.querySelectorAll('.sq-industry-card').length>0);
const add=main.querySelector('[data-v2="add-factor"]');assert(add&&!add.disabled);
assert(catalog.items.some(x=>x.factorId===add.dataset.id&&x.historyStatus==='adapter_supported_requires_observations'));
C.state.strategy.factors=[{id:add.dataset.id}];C.render();assert(main.querySelector(`[data-id="${add.dataset.id}"]`).disabled);
browser.state.query='半导体';C.render();assert(main.textContent.includes('半导体'));
browser.state.query='';const market=main.querySelector('#sq-industry-market');market.value='US';browser.onChange(market);
assert(main.textContent.includes('官方分类'));
assert(main.querySelectorAll('.sq-industry-card').length<=18);
assert([...main.querySelectorAll('.sq-industry-card footer')].every(footer=>footer.querySelector('button')?.disabled),'US official classification itself has no invented history');
browser.state.kind='etf';C.render();assert(main.textContent.includes('ETF 代理'));
assert(main.textContent.includes('不等同于官方行业指数'));
for(const button of main.querySelectorAll('[data-v2="add-factor"]'))assert(catalog.proxies.some(p=>p.factorId===button.dataset.id&&p.historyStatus==='adapter_supported_requires_observations'));
browser.state.query='unlikely-nonexistent';C.render();assert(main.textContent.includes('没有匹配来源'));
assert(!main.querySelector('[data-v2="add-factor"]'));
// An implemented adapter without observed history is not yet selectable.
C.state.catalog.industrySources={items:[],proxies:[{id:'unverified-etf',market:'US',name:'History probe',symbol:'XSD',sourceKind:'etf_proxy',factorId:'xsd_return',historyStatus:'adapter_supported_history_unverified'}]};
Object.assign(browser.state,{query:'',market:'US',kind:'etf'});C.render();
assert(main.textContent.includes('历史待验'));assert(!main.querySelector('[data-v2="add-factor"]'));assert(main.querySelector('footer button').disabled);
// Unknown links and executable-looking names stay inert in the displayed catalog.
C.state.catalog.industrySources={items:[{id:'unsafe',market:'CN',name:'<img src=x onerror=alert(1)>',sourceUrl:'javascript:alert(1)',historyStatus:'not_connected'}],proxies:[]};
Object.assign(browser.state,{query:'',market:'CN',kind:'all'});C.render();assert(!main.querySelector('img'));assert(!main.querySelector('a'));assert(main.textContent.includes('<img'));
console.log(JSON.stringify({hierarchicalIndustrySearch:true,explicitProxyLabels:true,noInventedCoverage:true,onlyConnectedFactorsSelectable:true,pagedCatalog:true,safeSourceLinks:true,providerCalls:0}));
dom.window.close();
