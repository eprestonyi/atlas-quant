/** Real controls + frozen API doubles. No provider, fit, deployment or layout claim. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {defaultStrategy,normalizeStrategy,validateStrategy} from '../quant-workspace/defaults.js';
import {enableReturnStudy} from '../quant-workspace/return-study.js';
import {validateStatisticalQuant} from '../../edge/statistical-quant/validation.mjs';
const definition={id:'momentum20',name:'20日动量',expression:'returns(close,20)',direction:1,version:1,availability:{status:'ready'}};
const legacy=defaultStrategy({automatic:true});enableReturnStudy(legacy,'association');legacy.universe.symbols=['000001.SZ','600000.SH'];legacy.factors=[{id:'flow',expression:'vol',direction:1,role:'predictor'}];
const frozenLegacy=JSON.stringify(legacy);assert.deepEqual(normalizeStrategy(legacy),legacy);
const dom=new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',{url:'http://localhost/quant/#quant/researches',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;w.structuredClone=structuredClone;w.scrollTo=()=>{};w.matchMedia=()=>({matches:false,addEventListener(){}});
let saved=null,writes=0,readbacks=0;const requests=[];
w.fetch=async(url,options={})=>{
 const path=String(url),body=options.body?JSON.parse(options.body):null;requests.push({path,method:options.method||'GET'});
 assert(!/\/(run|runner|acquisitions)(\/|$)/.test(path),'no numerical/provider calls');
 let result={items:[],total:0};
 if(path.includes('/factor-catalog'))result={items:[definition],total:1,page:1,pageSize:24};
 if(path.endsWith('/factors/momentum20'))result={item:definition};
 if(path.endsWith('/statistical-quant/experiments')&&options.method==='POST'){
  const normalized=validateStatisticalQuant(body.strategy);assert.deepEqual(normalized.factors,body.strategy.factors);assert.deepEqual(normalized.research.returnStudy,{schema:'asset-return-study/2'});
  saved={id:'time-fixture',version:1,strategy:structuredClone(body.strategy)};writes++;result={experiment:saved};
 }else if(path.endsWith('/statistical-quant/experiments/time-fixture')){readbacks++;result={experiment:saved};}
 else if(path.includes('/statistical-quant/experiments')&&(options.method||'GET')==='GET')result={items:saved?[saved]:[],total:saved?1:0};
 assert(!options.method||options.method==='GET'||path.endsWith('/statistical-quant/experiments'),'only isolated fixture save');
 return{ok:true,status:200,text:async()=>JSON.stringify(result)};
};
const bundle=await build({entryPoints:['web/main.js'],bundle:true,write:false,format:'iife',plugins:[{name:'no-init',setup(b){b.onLoad({filter:/\/web\/app\.js$/},async args=>({contents:(await fs.readFile(args.path,'utf8')).replace('  init();','  window.qa={state,studio,workspace,parseRoute,render};'),loader:'js'}));}}]});
w.eval(bundle.outputFiles[0].text);const q=w.qa,s=q.state;
s.loading=false;s.session={workspace:{id:'fixture-owner'},capabilities:{tushareHosted:true},runner:{online:true,factorPreprocessFormats:['auto-factor-preprocess/2'],returnStudyFormats:['asset-return-study/2']}};s.factors=[definition];q.parseRoute();q.render();
const tick=()=>new Promise(r=>setTimeout(r,25));
const wait=async(test,label)=>{for(let n=0;!test();n++){assert(n<100,label);await tick();}};
const route=async path=>{w.location.hash='#quant/'+path;await tick();};
const click=async selector=>{const el=w.document.querySelector(selector);assert(el,selector);assert(!el.disabled,selector+' enabled');el.click();await tick();};
const change=async(selector,value)=>{const el=w.document.querySelector(selector);assert(el,selector);el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();};
await click('[data-sq="new"]');assert.deepEqual(JSON.parse(JSON.stringify(s.strategy.research.returnStudy)),{schema:'asset-return-study/2'});s.dataSource='demo';s.strategy.universe.symbols=['000001.SZ','600000.SH'];q.studio.flow.reset();q.render();
await route('easy/model');assert(!w.document.querySelector('[data-sq="return-mode"]'));assert(w.document.querySelector('.sq-unified-equation'));assert(!w.document.querySelector('main').textContent.includes('同期因子响应'));
await route('easy/state');await click('[data-v2="catalog-category"][data-id=""]');await wait(()=>w.document.querySelector('[data-v2="add-factor"][data-id="momentum20"]'),'catalogue');
await click('[data-v2="add-factor"][data-id="momentum20"]');assert.equal(s.strategy.factors.length,1);const first=s.strategy.factors[0].id;
assert.equal(s.strategy.factors[0].baseFactorId,'momentum20');assert.equal(s.strategy.factors[0].timing.offsetSessions,0);
await click('[data-v2="add-factor"][data-id="momentum20"]');assert.equal(s.strategy.factors.length,2);const second=s.strategy.factors[1].id;assert.notEqual(first,second);
await change(`[data-sq-factor-time="${first}"]`,'past');await change(`[data-sq-factor-time-distance="${first}"]`,'5');
await change(`[data-sq-factor-time="${second}"]`,'future');await change(`[data-sq-factor-time-distance="${second}"]`,'1');
assert.deepEqual(Array.from(s.strategy.factors,f=>f.timing.offsetSessions),[-5,1]);assert(s.strategy.factors.every(f=>f.expression==='returns(close,20)'));
await click(`[data-sq="duplicate-factor-time"][data-id="${second}"]`);assert.equal(s.strategy.factors.length,3);const third=s.strategy.factors[2].id;assert.notEqual(third,second);
await change(`[data-sq-factor-time="${third}"]`,'current');assert(w.document.querySelector(`[data-factor-instance="${first}"]`).textContent.includes('[t−5]'));assert(w.document.querySelector(`[data-factor-instance="${second}"]`).textContent.includes('[t+1]'));assert(w.document.querySelector(`[data-factor-instance="${third}"]`).textContent.includes('[t]'));
assert(!w.document.querySelector('[data-sq-factor-offset]'),'Easy has no precision form');
await route('studio/state');await change(`[data-sq-factor-offset="${first}"]`,'-252');await change(`[data-sq-factor-expression="${third}"]`,'returns(close,10)');
assert.equal(s.strategy.factors[0].id,first);assert.equal(s.strategy.factors[2].expression,'returns(close,10)');
await change(`[data-sq-factor-offset="${first}"]`,'253');assert(validateStrategy(s.strategy,{step:'state'}).some(x=>x.includes('252')));await change(`[data-sq-factor-offset="${first}"]`,'-5');
// Real fill dispatches input before blur/change. Preserve both the value and
// the already-targeted Save node, rather than only testing synthetic change.
await click(`[data-sq="duplicate-factor-time"][data-id="${third}"]`);
const fourth=s.strategy.factors[3].id;
await change(`[data-sq-factor-time="${fourth}"]`,'future');assert.equal(s.strategy.factors[3].timing.offsetSessions,1);
const inputOnly=(selector,value)=>{const el=w.document.querySelector(selector);assert(el);el.focus();el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));return el;};
inputOnly(`[data-sq-factor-time-distance="${first}"]`,'');
inputOnly(`[data-sq-factor-time-distance="${first}"]`,'5');assert.equal(s.strategy.factors[0].timing.offsetSessions,-5,'empty replacement keeps past direction');
const saveBeforeBlur=w.document.querySelector('[data-sq="save"]');
const distance=inputOnly(`[data-sq-factor-time-distance="${fourth}"]`,'7');
assert.equal(s.strategy.factors[3].timing.offsetSessions,7,'input commits before blur or change');
assert.equal(w.document.querySelector(`[data-sq-factor-offset="${fourth}"]`).value,'7');
assert(w.document.querySelector(`[data-factor-instance="${fourth}"] strong`).textContent.includes('[t+7]'));
distance.dispatchEvent(new w.Event('change',{bubbles:true}));distance.blur();
assert(saveBeforeBlur.isConnected,'blur must not detach the button the user is clicking');
const exact=JSON.stringify(s.strategy.factors);saveBeforeBlur.click();await tick();await wait(()=>writes===1&&!s.saving,'saved');assert.equal(JSON.stringify(saved.strategy.factors),exact);
// Reopen from the server-shaped readback after replacing the in-memory draft.
await route('researches');await click('[data-sq="new"]');assert.equal(s.strategy.factors.length,0);await route('researches');await click('[data-sq="refresh-experiments"]');await click('[data-sq="experiment-load"][data-id="time-fixture"]');await wait(()=>readbacks===1,'readback');assert.equal(JSON.stringify(s.strategy.factors),exact);await route('studio/state');assert.equal(w.document.querySelector(`[data-sq-factor-offset="${first}"]`).value,'-5');assert.equal(w.document.querySelector(`[data-sq-factor-offset="${second}"]`).value,'1');assert.equal(w.document.querySelector(`[data-sq-factor-offset="${fourth}"]`).value,'7');
await click(`[data-sq="remove-factor"][data-id="${second}"]`);assert.deepEqual(Array.from(s.strategy.factors,f=>f.id),[first,third,fourth]);assert.equal(writes,1);
// Loading a legacy equation keeps bytes; conversion is explicit and detaches identity.
s.strategy=structuredClone(legacy);q.workspace.ui.activeId='old-id';q.workspace.ui.activeVersion=7;q.render();await route('studio/model');assert.equal(JSON.stringify(s.strategy),frozenLegacy);assert(!w.document.querySelector('[data-sq="return-mode"]'));await click('[data-sq="copy-unified-study"]');assert.equal(q.workspace.ui.activeId,null);assert.equal(s.strategy.research.returnStudy.schema,'asset-return-study/2');assert.equal(s.strategy.factors[0].timing.offsetSessions,0);assert.equal(s.strategy.factors[0].expression,'vol');assert.equal(JSON.stringify(legacy),frozenLegacy);assert.equal(writes,1);
console.log(JSON.stringify({repeatedDefinition:true,independentInstanceIds:true,shiftDoesNotRewriteWindow:true,easyTiming:true,studioExactExpressionAndOffset:true,saveReopenExact:true,inputBeforeBlurSaved:true,blurKeepsSaveTarget:true,pastDirectionSurvivesEmptyInput:true,removeOneInstanceOnly:true,legacyBytesPreserved:true,explicitCopyDetached:true,writes,providerCalls:0,fitCalls:0,fixtureOnly:true}));dom.window.close();
