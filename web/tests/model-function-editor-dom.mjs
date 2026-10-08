/** DOM and transport-race tests. API doubles below are not numerical or provider evidence. */
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createForms } from '../quant-workspace/forms.js';
import { createModelFunctionEditor } from '../quant-workspace/model-function-editor.js';
const dom = new JSDOM('<main></main><div id="modal-root"></div>', { url: 'http://localhost/quant/' });
globalThis.document = dom.window.document;
const document = dom.window.document, host = document.querySelector('#modal-root');
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]);
const artifact = {
  schema: 'atlas-model-function/1', artifactId: 'a'.repeat(64),
  inputSchema: [{ name:'factor_x', type:'finite_number_or_null' }, { name:'<img onerror=alert(1)>', type:'finite_number_or_null' }],
  estimator: { kind:'linear', coefficients:[[.1,.2],[.3,.4]], intercepts:[0,.01] },
  lineage: { status:'fitted', parentArtifactId:null },
  training: { informationCutoff:'20260101' },
  scope: { symbols:['600000.SH'], horizonSessions:5, observationDays:1 },
  transforms: { imputeMedian:[0,0] },
};
const frozen = JSON.stringify(artifact);
const source = { runId:'11111111-1111-4111-8111-111111111111', bundleId:'b'.repeat(64), modelFitId:'fit_1' };
const derived = { ...structuredClone(artifact), artifactId:'d'.repeat(64), lineage:{ status:'UNVALIDATED_USER_EDIT', parentArtifactId:artifact.artifactId } };
const ref = { functionId:'22222222-2222-4222-8222-222222222222', artifactId:derived.artifactId };
const item = { name:'<img src=x>', ref, artifactId:derived.artifactId };
let apiCalls=[], failSave=true, waitEval, waitSave, waitList, editor, pending, lastDownload, opened=0;
const C = { state:{}, esc, icon:() => '', fmt:(x,n=2)=>x==null?'—':Number(x).toFixed(n),
  openModal(_title,html) { host.innerHTML=html; opened++; }, toast() {}, download(name,value) { lastDownload={name,value}; },
  api:async(path, options={}) => {
    const body=options.body?JSON.parse(options.body):null; apiCalls.push({path,body});
    if (path.endsWith('/derive')) { if(waitSave)await waitSave; if(failSave){failSave=false;throw Error('网络返回中断；保存结果未知');} return {item,artifact:derived,idempotent:true}; }
    if (path.endsWith('/evaluate')) { if(waitEval)await waitEval; return {inferenceOnly:true,newValidationPerformed:false,result:{artifactId:body.edits?.length?derived.artifactId:artifact.artifactId,evidenceStatus:body.edits?.length?'UNVALIDATED_USER_EDIT':'fitted',normalizedChanges:[[.1,.2]],levels:body.input.rows.map((_,n)=>({expectedEntry:body.input.currentState[n]+1,expectedFuture:body.input.currentState[n]+2,e:-2,expectedChange:2}))}}; }
    if(path.endsWith('/resolve'))return {ref:source,artifact};
    if(path.startsWith('/model-functions?')){if(waitList)await waitList;return {items:[item],nextOffset:null};}
    if(path.startsWith('/model-functions/'))return {ref,item,artifact:derived};
    throw Error('unexpected '+path);
  },
};
editor=createModelFunctionEditor(C,createForms(C));
document.addEventListener('input', ev=>editor.onInput(ev.target));
document.addEventListener('change', ev=>editor.onInput(ev.target));
document.addEventListener('click', ev=>{const button=ev.target.closest('[data-sq]');if(button)pending=editor.handle(button);});
const show=(a=artifact,s=source)=>{host.innerHTML=editor.render({id:s.modelFitId,functionArtifact:a},s);};
const input=(selector,value)=>{const el=document.querySelector(selector);assert(el,selector);el.value=value;el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));};
const field=(key,value)=>input(`[data-mfe-input="${key}"]`,value);
const param=(path,value)=>input(`[data-mfe-param="${path}"]`,value);
const click=async(action)=>{document.querySelector(`[data-sq="${action}"]`).click();await pending;};
show();
assert(!document.querySelector('img'));
assert.equal(document.querySelector('[data-mfe-input="currentState"]').value,'');
assert(document.querySelector('[data-mfe-input="rows"]').value.includes('null'));
assert(host.textContent.includes('待填示例'));
assert(host.textContent.includes('观察收盘后，入场为下一官方交易日开盘，未来为其后 h 个交易日开盘'));
assert(host.textContent.includes('h=1 对应第二个后续交易日开盘，不是下一日收盘'));
assert(host.textContent.includes('观察间隔默认 1 表示每天观察一次，与预测期限 h 分开'));
assert(host.textContent.includes('具体观察、入场和目标日期见原报告逐条预测'));
await click('mfe-evaluate');assert.equal(apiCalls.length,0);assert(host.textContent.includes('请填写当前状态'));
field('rows',JSON.stringify([{factor_x:1,'<img onerror=alert(1)>':null}]));field('currentState','100');field('scale','100');
param('/estimator/coefficients/1/0','.35');
await click('mfe-evaluate');
assert(host.textContent.includes('102.000000'));
assert(host.textContent.includes('没有调用实时市场数据'));
assert.equal(apiCalls.at(-1).body.edits[0].path,'/estimator/coefficients/1/0');
assert.equal(apiCalls.at(-1).body.input.rows[0].factor_x,1);
assert.equal(document.querySelector('[data-mfe-stale]').hidden,true);
field('name','name only');assert.equal(document.querySelector('[data-mfe-stale]').hidden,true,'name does not affect inference');
param('/estimator/coefficients/1/0','0.350');assert.equal(document.querySelector('[data-mfe-stale]').hidden,true,'equivalent numeric text does not affect inference');
field('currentState','101');assert.equal(document.querySelector('[data-mfe-stale]').hidden,false);
await click('mfe-derive');assert(host.textContent.includes('保存结果未知'));
const firstSave=apiCalls.at(-1).body;
assert.equal(document.querySelector('[data-mfe-input="currentState"]').value,'101');
await click('mfe-derive');assert.deepEqual(apiCalls.at(-1).body,firstSave,'unknown save retries same ID and payload');
assert(host.textContent.includes('UNVALIDATED_USER_EDIT'));
assert.equal(JSON.stringify(artifact),frozen,'parameter editing never mutates frozen report');
assert.equal(document.querySelector('[data-sq="mfe-download"]').textContent.trim(),'下载当前版本 JSON');
assert(host.textContent.includes('未保存的参数修改不包含在 JSON 中'));
await click('mfe-download');assert.equal(JSON.stringify(lastDownload.value),frozen);
field('name','new derived name');await click('mfe-derive');assert.notEqual(apiCalls.at(-1).body.requestId,firstSave.requestId,'new payload gets new identity');
// Edits during a request remain in the editor; the result identifies the submitted revision.
let releaseSave;waitSave=new Promise(resolve=>{releaseSave=resolve;});
field('name','before delayed save');document.querySelector('[data-sq="mfe-derive"]').click();const inFlightSave=pending;
field('name','after delayed save');releaseSave();await inFlightSave;waitSave=null;
assert.equal(document.querySelector('[data-mfe-input="name"]').value,'after delayed save');
assert(host.textContent.includes('当前后续修改尚未保存'));
// Inference inputs do not change the function payload being saved.
waitSave=new Promise(resolve=>{releaseSave=resolve;});
document.querySelector('[data-sq="mfe-derive"]').click();const inputChangedDuringSave=pending;
field('currentState','103');releaseSave();await inputChangedDuringSave;waitSave=null;
assert(host.textContent.includes('新函数版本已保存'));
assert(!host.textContent.includes('当前后续修改尚未保存'));
// Renaming while inference is running must not mark its numeric result stale.
let releaseNameEval;waitEval=new Promise(resolve=>{releaseNameEval=resolve;});
document.querySelector('[data-sq="mfe-evaluate"]').click();const renamedDuringEval=pending;
field('name','renamed during inference');releaseNameEval();await renamedDuringEval;waitEval=null;
assert.equal(document.querySelector('[data-mfe-stale]').hidden,true);
assert.equal(document.querySelector('[data-mfe-input="name"]').value,'renamed during inference');
// A late result from a different function cannot replace the active function or its inputs.
let releaseEval;waitEval=new Promise(resolve=>{releaseEval=resolve;});
document.querySelector('[data-sq="mfe-evaluate"]').click();const inFlight=pending;
const next={...structuredClone(artifact),artifactId:'c'.repeat(64)};show(next,{...source,modelFitId:'fit_2'});
field('currentState','777');releaseEval();await inFlight;waitEval=null;
assert.equal(document.querySelector('[data-mfe-input="currentState"]').value,'777');
assert(!host.textContent.includes('102.000000'));
assert(host.textContent.includes(next.artifactId));
// Closing a library request cannot reopen the modal when its response arrives.
let releaseList;waitList=new Promise(resolve=>{releaseList=resolve;});
const listing=editor.library();host.innerHTML='';const closedAt=opened;releaseList();await listing;waitList=null;
assert.equal(opened,closedAt);assert.equal(host.innerHTML,'');
await editor.library();assert(!document.querySelector('img'));await click('mfe-open-saved');assert(host.textContent.includes(derived.artifactId));
await click('mfe-resolve');assert(host.textContent.includes('来源返回了不同的函数身份'),'mismatched source resolution is rejected');
// Tree editing exposes only known leaf values, never branch topology.
const tree={...structuredClone(artifact),artifactId:'e'.repeat(64),estimator:{kind:'histogram_trees',outputs:[{baseline:0,trees:[[[0,0,.5,1,2,0,0],[.1,0,0,0,0,1,0],[.2,0,0,0,0,1,0]]]},{baseline:0,trees:[[[0,0,.5,1,2,0,0],[.3,0,0,0,0,1,0],[.4,0,0,0,0,1,0]]]}]}};
show(tree,source);assert(!document.querySelector('[data-mfe-param*="threshold"]'));
field('leafValue','.8');await click('mfe-leaf');assert(host.textContent.includes('/estimator/outputs/1/trees/0/1/0'));
assert.equal(tree.estimator.outputs[1].trees[0][1][0],.3);
field('currentState','100');field('scale','100');await click('mfe-evaluate');
assert.equal(document.querySelector('[data-mfe-stale]').hidden,true);
field('output','0');field('tree','0');field('leaf','2');field('leafValue','.6');
assert.equal(document.querySelector('[data-mfe-stale]').hidden,true,'tree navigation and staged leaf text do not modify F');
await click('mfe-leaf');assert.equal(document.querySelector('[data-mfe-stale]').hidden,false,'applying a leaf edit modifies F');
// Constant functions ignore X and have no imputation or training transforms.
const constant={...structuredClone(artifact),artifactId:'f'.repeat(64),estimator:{kind:'constant',value:[0,0]},transforms:{imputeMedian:null,winsorLower:null,winsorUpper:null,scaleMean:null,scaleScale:null}};
show(constant,source);
assert(host.textContent.includes('常量模型忽略 X，不使用训练输入变换'));
assert(host.textContent.includes('不进行缺失填充'));
assert(!host.textContent.includes('null 会使用训练时冻结的缺失处理'));
assert(!host.textContent.includes('T 使用本次训练冻结'));
param('/estimator/value/1','.01');await click('mfe-download');assert.deepEqual(lastDownload.value,constant,'JSON contains the saved version only');
assert(editor.render({id:'old-fit'},source).includes('不能从旧报告'));
console.log(JSON.stringify({legacyHorizonAnchorExplained:true,observationSeparateFromHorizon:true,separateInferenceAndSaveRevisions:true,treeNavigationPreservesInference:true,constantInputDescription:true,currentVersionDownload:true,numericParameterEditing:true,explicitInputOnly:true,priceGapOutput:true,idempotentUnknownSave:true,newPayloadNewId:true,lateResponseIsolation:true,closedLibraryStaysClosed:true,leafOnlyTreeEditing:true,originalImmutable:true,escapedValues:true,apiDoubles:true}));
dom.window.close();
