/** UI set operations against the real deterministic resolver; no market/F calls. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {compileUniverseCatalog, universeOptions, resolveUniverseSelection, universeResolutionHash} from '../../edge/universe.mjs';
const securities = Array.from({length:1000}, (_,n) => ({
  ts_code: `${String(n + 1).padStart(6,'0')}.${n % 2 ? 'SZ' : 'SH'}`,
  name:'股票'+n, area:n < 80 ? '北京' : '上海', industry:n < 20 ? '白酒' : n < 60 ? '半导体' : '材料', exchange:n % 2 ? 'SZSE' : 'SSE',
}));
const pool={id:'csi1000',name:'中证1000',category:'index',curated:true,availability:{status:'ready'},symbols:securities.map(x=>x.ts_code),symbolCount:1000};
const catalog=compileUniverseCatalog({securities,items:[pool],hash:'a'.repeat(64),asOf:'2026-10-09'});
const dom=new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',{url:'http://localhost/quant/#quant/easy/universe',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;w.structuredClone=structuredClone;w.scrollTo=()=>{};w.matchMedia=()=>({matches:false,addEventListener(){}});
let resolutions=0,pendingResponses=0;
const responseDelayMs=Number(process.env.DOM_RESPONSE_DELAY_MS??120);
assert(Number.isInteger(responseDelayMs)&&responseDelayMs>=0&&responseDelayMs<=2000);
w.fetch=async(url,opts={})=>{
  pendingResponses++;
  if(responseDelayMs)await new Promise(resolve=>setTimeout(resolve,responseDelayMs));
  const p=new URL(url,'http://localhost').pathname;let value={items:[],total:0};
  if(p.endsWith('/universe-options'))value=universeOptions(catalog);
  if(p.endsWith('/universes/csi1000'))value={item:pool};
  if(p.endsWith('/universes/resolve')){resolutions++;value=resolveUniverseSelection(JSON.parse(opts.body).selection,catalog);value.resolutionHash=await universeResolutionHash(value);}
  return {ok:true,status:200,text:async()=>{pendingResponses--;return JSON.stringify(value);}};
};
const bundle=await build({entryPoints:['web/main.js'],bundle:true,write:false,format:'iife',plugins:[{name:'no-init',setup(b){b.onLoad({filter:/\/web\/app\.js$/},async a=>({contents:(await fs.readFile(a.path,'utf8')).replace('  init();','  window.qa={state,studio,workspace,parseRoute,render};'),loader:'js'}));}}]});
w.eval(bundle.outputFiles[0].text);const q=w.qa,s=q.state;s.loading=false;s.session={capabilities:{tushareHosted:true},runner:{online:true}};q.parseRoute();
await q.studio.flow.initialize();q.render();
// An event listener does not return its async handler to dispatchEvent. Wait for
// the actual response consumption, resolver completion and resulting UI state.
async function settle(label,ready=()=>true){
  const deadline=Date.now()+5000;
  while(pendingResponses||q.studio.flow.__test.u.resolving||!ready()){
    assert(Date.now()<deadline,`${label}: pending=${pendingResponses}, resolving=${q.studio.flow.__test.u.resolving}`);
    await new Promise(resolve=>setTimeout(resolve,5));
  }
}
const click=async(selector,ready)=>{const el=w.document.querySelector(selector);assert(el,selector);el.click();await settle(selector,ready);};
const resolveClick=async selector=>{const prior=q.studio.flow.__test.u.resolution;await click(selector,()=>q.studio.flow.__test.u.resolution&&q.studio.flow.__test.u.resolution!==prior);};
const change=async(selector,value)=>{const el=w.document.querySelector(selector);assert(el,selector);el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));await settle(selector);};
assert.equal(w.document.querySelectorAll('.uf-workbench').length,1);
assert.equal(w.document.querySelectorAll('.v2-universe-grid').length,0);
await resolveClick('[data-v2="pool-preset"][data-id="csi1000"]');
assert.equal(s.strategy.universe.symbols.length,1000);
assert.equal(w.document.querySelectorAll('.uf-results tbody tr').length,40);
await click('[data-v2="pool-preset"][data-id="csi1000"]');
assert.equal(s.strategy.universe.selection.includeGroups.length,1,'same recommendation does not duplicate groups');
assert.equal(resolutions,1);
await click('[data-v2="pool-add-filter"]');
const group=s.strategy.universe.selection.includeGroups[0].id;
assert(!w.document.querySelector('select[multiple]'),'inclusion uses searchable explicit choices');
const areaSearch=w.document.querySelector(`[data-rq-search="${group}"][data-index="1"]`);
assert(areaSearch);areaSearch.value='北京';areaSearch.dispatchEvent(new w.Event('input',{bubbles:true}));
assert.equal(areaSearch.closest('.uf-value-picker').querySelectorAll('.uf-value-options button').length,1);
assert.equal(s.strategy.universe.selection.includeGroups[0].filters[1].value,'北京','search itself does not mutate the selected condition');
await resolveClick('[data-v2="pool-resolve"]');
assert.equal(s.strategy.universe.symbols.length,80);
const beijingResolutions=resolutions;
await click('[data-v2="pool-preset"][data-id="csi1000"]');
assert.equal(s.strategy.universe.symbols.length,80,'reselecting a recommendation preserves its AND filters');
assert.equal(s.strategy.universe.selection.includeGroups.length,1,'filtered recommendation does not add an unfiltered OR group');
assert.equal(resolutions,beijingResolutions,'reselecting an included recommendation makes no new resolution request');
await click('[data-v2="pool-add-group"][data-scope="excludeGroups"]');
await resolveClick('[data-v2="pool-resolve"]');
assert.equal(s.strategy.universe.symbols.length,40);
assert(s.strategy.universe.symbols.every(code=>code.endsWith('.SZ')));
const excludedResolutions=resolutions;
await click('[data-v2="pool-preset"][data-id="csi1000"]');
assert.equal(s.strategy.universe.symbols.length,40,'reselecting preserves AND filters and exclusion groups');
assert.equal(s.strategy.universe.selection.includeGroups.length,1);
assert.equal(s.strategy.universe.selection.excludeGroups.length,1);
assert.equal(resolutions,excludedResolutions);
const removed=s.strategy.universe.symbols[0];
await resolveClick(`[data-v2="pool-exclude-symbol"][data-id="${removed}"]`);
assert.equal(s.strategy.universe.symbols.length,39);
assert(s.strategy.universe.selection.excludeSymbols.includes(removed));
assert(!s.strategy.universe.symbols.includes(removed));
assert.equal(s.strategy.universe.subsetPolicy,'all');
const all=[...s.strategy.universe.symbols];
await change('#rq-member-search','nonexistent');
assert.deepEqual([...s.strategy.universe.symbols],all,'display search does not change scope');
assert(!w.document.querySelector('[data-rq-member]'));
assert(!w.document.querySelector('[data-v2="pool-take"]'));
assert(!w.document.querySelector('input[type="date"]'));
assert(!w.document.querySelector('.uf-workbench').textContent.includes('尚未验证历史'));
await click('[data-v2="pool-add-filter"]');
await change(`[data-rq-field="${group}"][data-index="2"]`,'industry');
for(const button of [...w.document.querySelectorAll(`[data-v2="pool-toggle-value"][data-id="${group}"][data-index="2"]`)]) {
  if(button.closest('.uf-value-selected')){await click(`[data-v2="pool-toggle-value"][data-id="${group}"][data-index="2"][data-value="${button.dataset.value}"]`);}
}
const industrySearch=w.document.querySelector(`[data-rq-search="${group}"][data-index="2"]`);
industrySearch.value='半导体';industrySearch.dispatchEvent(new w.Event('input',{bubbles:true}));
assert.equal(industrySearch.closest('.uf-value-picker').querySelectorAll('.uf-value-options button').length,1);
await click(`[data-v2="pool-toggle-value"][data-id="${group}"][data-index="2"][data-value="半导体"]`);
assert.deepEqual([...s.strategy.universe.selection.includeGroups[0].filters[2].value],['半导体']);
await resolveClick('[data-v2="pool-resolve"]');
assert.equal(s.strategy.universe.symbols.length,20,'industry intersects existing pool/area and respects exclusion');
assert(w.document.querySelector(`[data-rq-search="${group}"][data-index="2"]`).value==='半导体','query persists through explicit membership update');
console.log(JSON.stringify({searchableInclusion:true,singleFilter:true,completeCounts:[1000,80,40,39],realSetResolver:true,noProviderOrModelCalls:true,responseDelayMs,waitsForResolution:true}));
dom.window.close();
