/** Saved explicit scopes reopen through the real UI; only fixture set resolution is permitted. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {compileUniverseCatalog, universeOptions, resolveUniverseSelection, universeResolutionHash} from '../../edge/universe.mjs';

const symbols=['600000.SH','600036.SH'];
const securities=symbols.map((ts_code,n)=>({ts_code,name:`股票${n}`,exchange:'SSE',area:n?'上海':'北京',industry:'银行'}));
const pool={id:'bank-pair',name:'两只银行',category:'index',curated:true,symbols,symbolCount:2,availability:{status:'ready'}};
const catalog=compileUniverseCatalog({securities,items:[pool],hash:'a'.repeat(64),asOf:'2026-10-09'});
const dom=new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',{url:'http://localhost/quant/#quant/researches',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;w.structuredClone=structuredClone;w.scrollTo=()=>{};w.matchMedia=()=>({matches:false,addEventListener(){}});
const requests=[],experiments=new Map();
let pendingResponses=0;
const responseDelayMs=Number(process.env.DOM_RESPONSE_DELAY_MS??120);
assert(Number.isInteger(responseDelayMs)&&responseDelayMs>=0&&responseDelayMs<=2000);
w.fetch=async(url,opts={})=>{
  pendingResponses++;
  if(responseDelayMs)await new Promise(resolve=>setTimeout(resolve,responseDelayMs));
  const path=new URL(url,'http://localhost').pathname,body=opts.body?JSON.parse(opts.body):null;
  requests.push({path,method:opts.method||'GET',body});
  let value={items:[],total:0};
  if(path.endsWith('/universe-options'))value=universeOptions(catalog);
  if(path.endsWith('/universes/bank-pair'))value={item:pool};
  if(path.endsWith('/universes/resolve')){
    value=resolveUniverseSelection(body.selection,catalog);
    value.resolutionHash=await universeResolutionHash(value);
  }else assert.equal(opts.method||'GET','GET','only explicit fixture universe resolution may post');
  const id=path.match(/\/statistical-quant\/experiments\/([^/]+)$/)?.[1];
  if(id){assert(experiments.has(id));value={experiment:structuredClone(experiments.get(id))};}
  return {ok:true,status:200,text:async()=>{pendingResponses--;return JSON.stringify(value);}};
};
const bundle=await build({entryPoints:['web/main.js'],bundle:true,write:false,format:'iife',plugins:[{name:'no-init',setup(b){b.onLoad({filter:/\/web\/app\.js$/},async a=>({contents:(await fs.readFile(a.path,'utf8')).replace('  init();','  window.qa={state,studio,workspace,parseRoute,render};'),loader:'js'}));}}]});
w.eval(bundle.outputFiles[0].text);
const q=w.qa,s=q.state,flow=q.studio.flow;
s.loading=false;s.session={workspace:{id:'fixture-owner'},capabilities:{tushareHosted:true},runner:{online:true}};
q.parseRoute();await flow.initialize();
// Response consumption precedes the async application continuation. Polling
// the actual terminal state also covers the hashchange queued by saved loading.
async function settle(label,ready=()=>true){
  const deadline=Date.now()+5000;
  while(pendingResponses||flow.__test.u.resolving||q.workspace.ui.loading||q.workspace.ui.experimentDetailLoading||!ready()){
    assert(Date.now()<deadline,`${label}: pending=${pendingResponses}, resolving=${flow.__test.u.resolving}, step=${s.quantStep}`);
    await new Promise(resolve=>setTimeout(resolve,5));
  }
}
const click=async(selector,ready)=>{const node=w.document.querySelector(selector);assert(node,selector);node.click();await settle(selector,ready);};
const resolveClick=async selector=>{const prior=flow.__test.u.resolution;await click(selector,()=>flow.__test.u.resolution&&flow.__test.u.resolution!==prior);};
const route=async value=>{w.location.hash='#quant/'+value;await settle('route '+value,()=>s.quantStep===value);q.render();};
const count=()=>w.document.querySelector('.uf-result-header strong').textContent;
const displayed=()=>[...w.document.querySelectorAll('.uf-results tbody [data-v2="pool-exclude-symbol"]')].map(el=>el.dataset.id);
const resolves=()=>requests.filter(x=>x.path.endsWith('/universes/resolve'));
const raw=w.AtlasQuantV4.defaultStrategy();raw.name='旧配对研究';raw.universe={symbols:[...symbols],start:'20230101',end:'20251231'};delete raw.validation.testStart;
const expected=JSON.stringify(w.AtlasQuantV4.normalizeStrategy(raw));
const saved={id:'legacy-two',version:2,name:raw.name,strategy:structuredClone(raw)};
experiments.set(saved.id,saved);
async function reopen(id){
  await route('researches');q.workspace.ui.experimentsLoaded=true;q.workspace.ui.experiments=[experiments.get(id)];q.workspace.ui.experimentTotal=1;q.render();
  await click(`[data-sq="experiment-load"][data-id="${id}"]`,()=>s.quantStep==='universe'&&q.workspace.ui.activeId===id&&!!w.document.querySelector('.uf-result-header'));
}

await reopen('legacy-two');
assert.equal(count(),'2只');assert.deepEqual(displayed(),symbols);
assert.equal(w.document.querySelector('#rq-include-symbols').value,symbols.join(', '));
assert.equal(JSON.stringify(s.strategy),expected,'opening preserves the saved research protocol');
assert.equal(s.dirty,false);
assert.equal(flow.__test.u.resolution,null,'explicit members are not a fabricated filter snapshot');
assert.equal(s.strategy.universe.selection,undefined);assert.equal(s.strategy.universe.resolutionHash,undefined);assert.equal(s.strategy.universe.snapshotHash,undefined);
assert(!w.document.querySelector('.uf-workbench').textContent.includes('冻结筛选集合'));
assert.equal(resolves().length,0,'opening does not resolve against the current catalog');

await route('model');await route('universe');
assert.equal(count(),'2只');assert.equal(JSON.stringify(s.strategy),expected,'navigation does not migrate a saved configuration');
await reopen('legacy-two');
assert.equal(count(),'2只');assert.equal(JSON.stringify(s.strategy),expected,'reopening resets UI-only scope state');
await resolveClick('[data-v2="pool-resolve"]');
assert.deepEqual(resolves().at(-1).body.selection.includeSymbols,symbols,'explicit update submits the saved members');
assert.deepEqual([...s.strategy.universe.symbols],symbols);assert.equal(count(),'2只');
assert.equal(s.strategy.universe.subsetPolicy,'all');
assert.match(s.strategy.universe.resolutionHash,/^[a-f0-9]{64}$/);assert.equal(s.strategy.universe.snapshotHash,'a'.repeat(64));
assert.equal(flow.__test.u.savedSymbols,null,'real resolver output replaces the display fallback');

await reopen('legacy-two');
await resolveClick(`[data-v2="pool-exclude-symbol"][data-id="${symbols[0]}"]`);
assert.deepEqual([...s.strategy.universe.symbols],[symbols[1]],'legacy row exclusion keeps the other saved member');
assert.deepEqual(resolves().at(-1).body.selection.includeSymbols,symbols);
assert.deepEqual(resolves().at(-1).body.selection.excludeSymbols,[symbols[0]]);
assert.equal(count(),'1只');

const emptySelection={version:1,includeGroups:[],excludeGroups:[],includeSymbols:[],excludeSymbols:[]};
experiments.set('explicit-empty',{id:'explicit-empty',version:1,strategy:{...structuredClone(raw),universe:{...raw.universe,selection:emptySelection}}});
await reopen('explicit-empty');
assert.equal(count(),'—只');assert.deepEqual(displayed(),[]);assert.equal(w.document.querySelector('#rq-include-symbols').value,'');
assert.equal(flow.__test.u.savedSymbols,null,'an existing empty selection must not resurrect old symbols');
await resolveClick('[data-v2="pool-resolve"]');
assert.deepEqual(resolves().at(-1).body.selection.includeSymbols,[]);assert.deepEqual([...s.strategy.universe.symbols],[]);assert.equal(count(),'0只');
const frozenEmpty=structuredClone(s.strategy);experiments.set('frozen-empty',{id:'frozen-empty',version:1,strategy:frozenEmpty});
await reopen('frozen-empty');assert.equal(count(),'0只');assert.deepEqual(displayed(),[]);assert.equal(flow.__test.u.resolution.restored,true);

const fresh=w.AtlasQuantV4.defaultStrategy();s.strategy=fresh;flow.reset();q.render();
assert.equal(count(),'—只');assert.deepEqual(displayed(),[]);assert.equal(s.strategy.universe.selection,undefined);
await resolveClick('[data-v2="pool-preset"][data-id="bank-pair"]');
assert.deepEqual([...s.strategy.universe.symbols],symbols,'ordinary live filtering still uses its actual complete result');
assert.equal(s.strategy.universe.selection.includeGroups.length,1);assert.deepEqual([...s.strategy.universe.selection.includeSymbols],[]);
assert.equal(flow.__test.u.savedSymbols,null);assert.equal(count(),'2只');
assert.equal(JSON.stringify(experiments.get('legacy-two').strategy),JSON.stringify(raw),'the saved API record was never changed');
assert(!requests.some(x=>/\/runs|\/forecasts|\/executions/.test(x.path)),'no provider, model or execution requests');
console.log(JSON.stringify({legacyReopen:true,readOnlyLoad:true,noInventedHashes:true,updateRetainsMembers:true,legacyExclusion:true,explicitEmptyDoesNotFallback:true,frozenEmptyRestored:true,liveFilterUnchanged:true,fixtureOnly:true,responseDelayMs,waitsForResolution:true}));
dom.window.close();
