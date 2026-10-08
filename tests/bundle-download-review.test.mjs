/** Independent stream/DOM review. Local synthetic fixture, no provider/R2 service.
 * Native FixedLengthStream runs inside workerd; storage transport has an
 * explicit in-flight barrier. Ownership is covered by full-router bundle tests.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {Miniflare} from 'miniflare';
import {JSDOM} from 'jsdom';
import {bundleFixture} from './fixtures/bundle-fixture.mjs';
import {createForecastReports} from '../web/quant-workspace/reports.js';
import {createForms} from '../web/quant-workspace/forms.js';
import {defaultStrategy} from '../web/quant-workspace/defaults.js';

const fixture = bundleFixture();
const source = `
import {archiveEntries,archiveLength,archiveStream} from './edge/bundles/archive.mjs';
import {validateManifest} from './edge/bundles/manifest.mjs';
const manifestText=${JSON.stringify(fixture.manifestText)};
const bundleId=${JSON.stringify(fixture.bundleId)};
const bodies=new Map(${JSON.stringify([...fixture.chunks])});
export default {async fetch(req){
 const parsed=await validateManifest(manifestText,bundleId);
 const entries=archiveEntries({manifest_text:manifestText},parsed);
 let reads=0,started,release;
 const firstLoad=new Promise(resolve=>{started=resolve;});
 const blocked=new Promise(resolve=>{release=resolve;});
 const source=archiveStream(entries,async(collection,descriptor)=>{
  reads++;
  started();
  await blocked;
  return new TextEncoder().encode(bodies.get(collection+':'+descriptor.ordinal));
 });
 const fixed=new FixedLengthStream(archiveLength(entries));
 const pumping=source.pipeTo(fixed.writable).then(()=>({status:'fulfilled'}),()=>({status:'cancelled'}));
 const reader=fixed.readable.getReader();
 // Drain only until the first storage read blocks. This cancellation happens
 // beside the producer, so dispatchFetch IPC cannot pre-consume extra chunks.
 const consuming=(async()=>{while(!(await reader.read()).done){}})();
 const consumed=consuming.then(()=>null,()=>null);
 try{
  await firstLoad;
  const atCancellation=reads;
  await reader.cancel('deterministic in-flight cancellation');
  release();
  const settled=await pumping;
  await consumed;
  return Response.json({reads,atCancellation,pipelineSettled:true,pipelineStatus:settled.status,availableChunks:entries.length-1});
 }finally{release();}
}};`;
const built = await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:'review-entry.mjs'},bundle:true,write:false,format:'esm',platform:'browser'});
const mf = new Miniflare({modules:true,script:built.outputFiles[0].text,compatibilityDate:'2026-08-01'});
test.after(()=>mf.dispose());

test('native FixedLengthStream cancellation stops after the explicitly gated in-flight chunk', {timeout:10000}, async()=>{
  const response=await mf.dispatchFetch('http://review.test/cancel-check');
  const result=await response.json();
  assert(result.availableChunks>1,'fixture must contain an observable next chunk');
  assert.equal(result.atCancellation,1,'cancel only after exactly one load has begun');
  assert.equal(result.pipelineSettled,true,'measure after the entire pipe has settled');
  assert.equal(result.pipelineStatus,'cancelled');
  assert.equal(result.reads,1,'no storage load may start after cancellation');
});

test('execution archive UI does not promise frozen market inputs or fetch its body',()=>{
  const dom=new JSDOM('<main></main>');
  const savedDocument=globalThis.document;globalThis.document=dom.window.document;
  try {
    let requests=0;
    const artifactId='a'.repeat(64),bundleId='b'.repeat(64);
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const C={state:{runId:'execution-test',reportTransport:{format:'atlas.quant.bundle',version:1,bundleId,logicalArtifactId:artifactId,complete:true,hasFrozenInputs:false,collections:{forecasts:{total:0},targets:{total:0}},downloadUrl:'/report/download?bundleId='+bundleId,bundleDownloadUrl:'/report/bundle?bundleId='+bundleId}},
      esc,fmt:v=>v==null?'—':String(v),pct:v=>String(v??'—'),dateText:v=>String(v??'—'),icon:()=>'',render:()=>{},toast:()=>{},
      api:()=>{requests++;return new Promise(()=>{});},openModal:()=>{},closeModal:()=>{}};
    const report={strategy:defaultStrategy(),research:{executionOnly:true},forecasts:{artifactId,totalRows:0,diagnostics:{metrics:{},factorIncrement:{status:'not_applicable'}}},selection:{evidenceStatus:'NO_VALIDATED_FORECAST_EDGE'},warnings:[]};
    const view=createForecastReports(C,createForms(C),{onExecution:()=>{}});
    dom.window.document.querySelector('main').innerHTML=view.render(report);
    const links=[...dom.window.document.querySelectorAll('a[download]')];
    const archive=links.find(x=>x.textContent==='下载私有执行记录包');
    assert.equal(archive?.getAttribute('href'),C.state.reportTransport.bundleDownloadUrl);
    assert.match(dom.window.document.body.textContent,/重放还需要来源预测包中的原始行情/);
    assert(!dom.window.document.body.textContent.includes('包含冻结行情、预测与来源'));
    assert(!links.some(x=>x.textContent==='下载私有复现包'));
    assert(requests<=1,'render may request one forecast page, never the archive body');
  } finally {globalThis.document=savedDocument;dom.window.close();}
});
