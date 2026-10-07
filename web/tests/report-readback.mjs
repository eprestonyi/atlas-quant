/* Render existing private engine reports. Never submits jobs or publishes data. */
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',{url:'http://localhost/quant/#runs/readback',runScripts:'outside-only'});
const w=dom.window;w.scrollTo=()=>{};w.fetch=()=>{throw Error('Readback test must not make network requests');};
for(const name of ['research-workflow.js','studio.js'])w.eval(await fs.readFile(new URL('../'+name,import.meta.url),'utf8'));
w.eval((await fs.readFile(new URL('../app.js',import.meta.url),'utf8')).replace('  init();','  window.qa={state,studio,parseRoute,render,normalizeStrategy};'));
const q=w.qa,paths=process.argv.slice(2);assert(paths.length,'Pass one or more existing private result JSON paths.');const readbacks=[];
for(const path of paths){const r=JSON.parse(await fs.readFile(path,'utf8'));assert(r.statArb);assert.equal(r.research.mode,'stat_arb');assert.equal(r.provenance.synthetic,false);q.state.strategy=q.normalizeStrategy(r.strategy);q.state.loading=false;q.state.view='runs';q.state.runId='readback';q.state.report=r;q.state.job={id:'readback',name:r.strategy.name,dataSource:'tushare',status:'completed',strategy:r.strategy};q.state.session={capabilities:{tushareHosted:true},runner:{online:true}};
  for(const tab of ['overview','signals','fits','trades','provenance']){q.state.reportTab=tab;q.state.arbSignalScope='all';q.render();const root=w.document.querySelector('#app'),text=root.textContent;assert(text.includes('理论多空'));assert(!text.includes('[object Object]'));assert(!text.includes('NaN'));assert(!text.includes('逐股预测'));assert(root.querySelector(`[data-action="report-tab"][data-id="${tab}"].active`));if(tab==='signals')assert(root.querySelectorAll('tbody tr').length<=50);if(tab==='fits')assert(root.querySelectorAll('tbody tr').length<=20);if(tab==='trades')assert(root.querySelectorAll('tbody tr').length<=26);if(tab==='overview'){assert(text.includes('零成本反事实'));assert(text.includes('借券费用'));assert(text.includes(r.statArb.baselineComparison?'当前 · 加入因子':'当前没有附加因子'));}}
  readbacks.push({method:r.statArb.method,factors:r.strategy.factors.length,source:r.provenance.source||r.provenance.provider||r.provenance.dataSource,netReturn:r.metrics.totalReturn,pages:5,signalRows:r.statArb.signals.rows.length,modelFits:r.statArb.modelFits.length,tradeCount:r.trades.length,borrowCostPresent:Number.isFinite(r.metrics.costBreakdown.borrow),actualFactorComparison:Boolean(r.statArb.baselineComparison)});
}
console.log(JSON.stringify({realReports:readbacks.length,pagesRendered:readbacks.length*5,networkRequests:0,publicFilesWritten:0,readbacks}));dom.window.close();
