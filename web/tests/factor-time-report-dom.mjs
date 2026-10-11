/** Actual frozen synthetic F/5 and row projections; no provider, fit, or production call. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {createForms} from '../quant-workspace/forms.js';
import {createForecastReports} from '../quant-workspace/reports.js';
import {createModelFunctionEditor} from '../quant-workspace/model-function-editor.js';
import {evaluateFunction} from '../model-function-runtime.js';
import {inputDefinition} from '../quant-workspace/model-inputs.js';

const projection=JSON.parse(await readFile(new URL('../../tests/fixtures/factor-time-report-projection.json',import.meta.url),'utf8'));
const golden=JSON.parse(await readFile(new URL('../../tests/fixtures/model-function-v5-golden.json',import.meta.url),'utf8'));
const report=projection.report,artifact=golden.artifact;
assert.equal(projection.source.synthetic,true);assert.equal(projection.source.panelRowsComplete,false);
assert.equal(report.forecasts.studyProtocol,'asset-return-study/2');
assert.equal(artifact.schema,'atlas-model-function/5');
const original=JSON.stringify(report),originalArtifact=JSON.stringify(artifact);
const dom=new JSDOM('<main></main><div id="modal"></div>',{url:'http://localhost/quant/'});globalThis.document=dom.window.document;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const source={runId:'11111111-1111-4111-8111-111111111111',bundleId:'b'.repeat(64),modelFitId:'fixture-fit'};
let render=()=>{},calls=[];
const C={state:{runId:source.runId,reportTransport:null,catalog:{factors:[]}},esc,icon:()=>'',fmt:(x,n=2)=>x==null?'—':Number(x).toFixed(n),pct:x=>x==null?'—':(x*100).toFixed(2)+'%',dateText:x=>x||'—',render:()=>render(),openModal:(_,html)=>{document.querySelector('#modal').innerHTML=html;},toast(){},download(){},api:async(path,options)=>{const body=JSON.parse(options.body);calls.push({path,body});return {inferenceOnly:true,newValidationPerformed:false,result:await evaluateFunction(artifact,body.input)};}};
const F=createForms(C),editor=createModelFunctionEditor(C,F),host=document.querySelector('#modal'),main=document.querySelector('main');
host.innerHTML=editor.render({functionArtifact:artifact},source);
const set=(selector,value)=>{const field=host.querySelector(selector);assert(field,selector);field.value=value;editor.onInput(field);};
const field=(key,value)=>set(`[data-mfe-input="${key}"]`,value);
const run=async()=>{await editor.handle(host.querySelector('[data-sq="mfe-evaluate"]'));};
assert.equal(host.querySelector('[data-mfe-input="mode"]'),null,'F5 has no global input mode');
assert.equal(host.querySelectorAll('[data-mfe-evidence-origin]').length,3);
assert(host.textContent.includes('[t−5]'));assert(host.textContent.includes('[t]'));assert(host.textContent.includes('[t+1]'));
assert.equal(host.querySelector('[data-mfe-param*="/coefficients/1/"]'),null);
for(let i=0;i<3;i++){const d=inputDefinition(artifact,i);assert(d.construction.operations.some(x=>x.includes('[t')));}
assert(inputDefinition(artifact,2).equations.some(x=>x.includes('拒绝求值')));
assert(!inputDefinition(artifact,2).equations.some(x=>x.includes('null ?')));
await run();assert.equal(calls.length,0);assert(host.textContent.includes('未来因子需要明确数值'));
// Exact Python-frozen inference case: historical measured missing past/current
// may use the saved imputer; the positive lead is always explicitly present.
field('rows',JSON.stringify(golden.input.rows));field('inputEvidence',JSON.stringify(golden.input.inputEvidence));field('originDates',golden.input.originDates[0]);field('originPrice',JSON.stringify(golden.input.originPrice));
assert([...host.querySelectorAll('[data-mfe-evidence-origin]')].every(x=>x.value==='observed_actual'));
await run();assert.equal(calls.length,1);assert.deepEqual(calls[0].body.input,golden.input);assert(!Object.hasOwn(calls[0].body.input,'mode'));
const expected=golden.expected.predictedResponse[0];assert(host.textContent.includes(expected.toFixed(6)));assert(host.textContent.includes('含起点后实测输入'));
assert.equal(host.querySelector('[data-mfe-stale]').hidden,true);
field('originDates','20230128');assert.equal(host.querySelector('[data-mfe-stale]').hidden,false,'origin date alters evidence classification');
field('originDates',golden.input.originDates[0]);
// Mix a future assumption with past/current actual values in the same F.
set('[data-mfe-evidence-origin="factor:future"]','scenario_assumption');
assert.equal(JSON.parse(host.querySelector('[data-mfe-input="inputEvidence"]').value)[0]['factor:past'].valueOrigin,'observed_actual');
await run();assert.equal(calls.length,2);assert.equal(calls.at(-1).body.input.inputEvidence[0]['factor:future'].valueOrigin,'scenario_assumption');assert(host.textContent.includes('含假设输入'));
// Neither blank evidence nor missing dates become a claim of origin-known data.
field('inputEvidence','');assert.equal(host.querySelector('[data-mfe-stale]').hidden,false);
assert([...host.querySelectorAll('[data-mfe-evidence-origin]')].every(x=>x.value===''));
await run();assert.equal(calls.length,3);assert(!Object.hasOwn(calls.at(-1).body.input,'inputEvidence'));assert(host.textContent.includes('可用性未核验'));
const missing=structuredClone(golden.input.rows);missing[0]['factor:future']=null;field('rows',JSON.stringify(missing));await run();assert.equal(calls.length,3,'future missing rejected before API even without evidence');
field('rows',JSON.stringify(golden.input.rows));field('inputEvidence',JSON.stringify(golden.input.inputEvidence));
set('[data-mfe-evidence-date="factor:future"][data-evidence-field="evaluationDate"]',golden.input.originDates[0]);await run();assert.equal(calls.length,3);assert(host.textContent.includes('时点方向不一致'));
// An incomplete JSON edit remains editable rather than crashing the source selector.
field('inputEvidence','[{}]');set('[data-mfe-evidence-origin="factor:past"]','observed_actual');assert(JSON.parse(host.querySelector('[data-mfe-input="inputEvidence"]').value)[0]['factor:past']);

const reports=createForecastReports(C,F);render=()=>{main.innerHTML=reports.render(report);};render();
const assets=report.forecasts.diagnostics.assetModels;
assert.equal(main.querySelector('#sq-return-asset').options.length,2);
for(const asset of assets){
 let select=main.querySelector('#sq-return-asset');select.value=asset.targetId;reports.onChange(select);
 await reports.handle(main.querySelector('[data-sq="return-tab"][data-id="models"]'));
 const candidate=report.forecasts.diagnostics.modelSearch.candidates.find(x=>x.id===asset.researchCandidateId);
 assert.equal(main.querySelector('[data-saved-function]').dataset.savedFunction,candidate.functionArtifact.artifactId);
 assert(main.textContent.includes('[t−5]'));assert(main.textContent.includes('[t+1]'));
 assert.equal(main.querySelector('[data-sq="forecast-candidate-scenario"]'),null);
 assert(main.querySelectorAll('svg').length>0);
 await reports.handle(main.querySelector('[data-sq="return-tab"][data-id="panel"]'));
 assert.equal(main.querySelector('thead th').textContent,'响应起点 t');
 const selectedRows=report.forecasts.factorResearch.panel.rows.filter(x=>x.targetId===asset.targetId);
 const tables=[...main.querySelectorAll('.sq-input-evidence')];assert.equal(tables.length,selectedRows.length);
 for(let n=0;n<tables.length;n++)for(const [key,value] of Object.entries(selectedRows[n].inputEvidence)){
  const tr=[...tables[n].querySelectorAll('[data-input-evidence]')].find(x=>x.dataset.inputEvidence===key);assert(tr,key);
  assert.equal(tr.cells[2].textContent,value.evaluationDate||'—');assert.equal(tr.cells[3].textContent,value.sourceDate||'—');
  assert(tr.cells[4].textContent.startsWith(value.availableAt||'—'));
  assert.equal(tr.cells[6].textContent,value.knownAtOrigin===true?'是':value.knownAtOrigin===false?'否':'未核验');
 }
 assert(main.textContent.includes('不代表供应商精确发布时间'));assert(main.textContent.includes('含起点后实测输入'));
 await reports.handle(main.querySelector('[data-sq="return-tab"][data-id="observations"]'));
 assert(main.textContent.includes('输入可用边界'));assert.equal(main.querySelector('.sq-input-evidence thead').textContent.includes('经济值 R'),false,'observations never invent unavailable feature values');
 assert(main.querySelectorAll('svg').length>=2);
 for(const tab of ['processing','statistics','correlations','joints','validation']){
  await reports.handle(main.querySelector(`[data-sq="return-tab"][data-id="${tab}"]`));assert.equal(main.querySelector('[data-return-target]').dataset.returnTarget,asset.targetId);
 }
}
assert.equal(JSON.stringify(report),original);assert.equal(JSON.stringify(artifact),originalArtifact);
console.log(JSON.stringify({actualFrozenSyntheticProjection:true,independentTimeColumns:true,f5SingleOutput:true,noGlobalMode:true,measuredAndAssumedMixed:true,evidenceUnknownPreserved:true,positiveLeadNullBlockedBeforeApi:true,evidenceChangeInvalidatesOldResult:true,perAssetFunctionsAndTabs:true,rowEvidenceExactDates:true,noInventedObservationValues:true,inferenceCalls:calls.length,newFits:0,providerCalls:0,productionCalls:0}));
dom.window.close();
