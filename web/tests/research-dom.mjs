/* Real DOM-event regression in jsdom; browser acceptance remains separate. */
import fs from 'node:fs/promises';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',{url:'http://localhost/quant/#research/signals',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;w.scrollTo=()=>{};w.matchMedia=()=>({matches:false});
let savedPayload=null;
w.fetch=async(url,options={})=>{const data=options.body?JSON.parse(options.body):null;if(String(url).endsWith('/strategies')&&data){savedPayload=data;return {ok:true,status:201,text:async()=>JSON.stringify({item:{id:'local-dom-test',version:1,strategy:data.strategy}})};}return {ok:true,status:200,text:async()=>JSON.stringify({items:[]})};};
for(const name of ['research-workflow.js','studio.js'])w.eval(await fs.readFile(new URL('../'+name,import.meta.url),'utf8'));
const source=(await fs.readFile(new URL('../app.js',import.meta.url),'utf8')).replace('  init();','  window.qa={state,studio,parseRoute,render};');const bundled=await build({stdin:{contents:source,resolveDir:fileURLToPath(new URL('..',import.meta.url)),sourcefile:'app.js'},bundle:true,format:'iife',write:false});w.eval(bundled.outputFiles[0].text);
const q=w.qa;q.state.loading=false;q.state.session={capabilities:{tushareHosted:true},runner:{online:true}};q.state.strategy.universe.symbols=['000001.SZ','000002.SZ','600000.SH','600036.SH','600519.SH','000333.SZ','000651.SZ','601318.SH'];q.parseRoute();q.render();
const tick=()=>new Promise(r=>setTimeout(r,25));
function input(path,value){const el=w.document.querySelector(`[data-config="${path}"]`);assert(el,`${path} control exists`);el.focus();el.value=value;el.dispatchEvent(new w.InputEvent('input',{bubbles:true,inputType:'insertText',data:value}));return el;}
const observer=input('research.observationDays','5');
assert.equal(q.state.strategy.research.observationDays,5,'input event persists before blur');
assert.equal(JSON.parse(w.localStorage.getItem('atlas-quant-draft-v1')).strategy.research.observationDays,5);
// Background refresh can replace the active DOM before blur/change happens.
q.render();assert.equal(w.document.querySelector('[data-config="research.observationDays"]').value,'5');
w.document.querySelector('[data-v2="research-next"]').click();await tick();
assert.equal(w.location.hash,'#research/execution');assert.match(w.document.querySelector('.rq-summary').textContent,/5 \/ 1 交易日/);
const rebalance=input('portfolio.rebalanceDays','10');rebalance.dispatchEvent(new w.Event('change',{bubbles:true}));rebalance.blur();await tick();
assert.equal(w.document.querySelector('[data-config="portfolio.rebalanceDays"]').value,'10');
w.document.querySelector('[data-v2="research-next"]').click();await tick();
assert.equal(w.location.hash,'#research/review');assert.match(w.document.querySelector('.rq-review-grid').textContent,/每 5 个交易日观察 \/ 每 10 日调仓检查/);
w.document.querySelector('.rq-summary [data-action="save"]').click();await tick();
assert(savedPayload);assert.equal(savedPayload.strategy.research.observationDays,5);assert.equal(savedPayload.strategy.portfolio.rebalanceDays,10);
assert.equal(q.state.strategy.research.observationDays,5,'save/readback normalization retains observation cadence');
assert.equal(q.state.strategy.portfolio.rebalanceDays,10,'save/readback normalization retains execution cadence');
w.location.hash='#research/signals';await tick();const empty=input('statArb.entryZ','');assert.equal(q.state.strategy.statArb.entryZ,null,'empty input does not silently become zero');empty.value='2.5';empty.dispatchEvent(new w.InputEvent('input',{bubbles:true}));q.render();assert.equal(w.document.querySelector('[data-config="statArb.entryZ"]').value,'2.5');
console.log(JSON.stringify({passed:12,dom:'jsdom',inputEvents:true,backgroundRerender:true,routeClicks:true,savePayload:{observationDays:5,rebalanceDays:10},browserVerified:false}));
dom.window.close();
