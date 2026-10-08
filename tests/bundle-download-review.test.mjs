/** Independent stream/DOM review. Local synthetic fixture, no provider/R2 service.
 * Native FixedLengthStream runs inside workerd; storage transport is a bounded
 * delayed test double. Ownership is covered by the full-router bundles tests.
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
import {bundleArchiveResponse} from './edge/bundles/archive.mjs';
import {validateManifest} from './edge/bundles/manifest.mjs';
const manifestText=${JSON.stringify(fixture.manifestText)};
const bundleId=${JSON.stringify(fixture.bundleId)};
const bodies=new Map(${JSON.stringify([...fixture.chunks])});
let reads=0;
export default {async fetch(req){
 if(new URL(req.url).pathname==='/reads')return Response.json({reads});
 reads=0;
 const parsed=await validateManifest(manifestText,bundleId);
 const env={DB:{prepare(){return {bind(stage,collection,ordinal){return {async first(){
  const d=parsed.collections.get(collection).chunks[ordinal];
  return {object_key:collection+':'+ordinal,sha256:d.sha256,byte_length:d.byteLength};
 }}}}}},ARTIFACTS:{async get(key){
  reads++;
  await new Promise(resolve=>setTimeout(resolve,40));
  const raw=new TextEncoder().encode(bodies.get(key));
  return {size:raw.byteLength,async arrayBuffer(){return raw.buffer;}};
 }}};
 return bundleArchiveResponse(env,{id:'test-stage',status:'committed',bundle_id:bundleId,manifest_text:manifestText},parsed,'test-run');
}};`;
const built = await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:'review-entry.mjs'},bundle:true,write:false,format:'esm',platform:'browser'});
const mf = new Miniflare({modules:true,script:built.outputFiles[0].text,compatibilityDate:'2026-08-01'});
test.after(()=>mf.dispose());

test('native FixedLengthStream cancellation stops after the in-flight bounded chunk', async()=>{
  const response=await mf.dispatchFetch('http://review.test/archive');
  assert(Number(response.headers.get('content-length'))>1024);
  const reader=response.body.getReader();
  await reader.read();
  await reader.cancel();
  await new Promise(resolve=>setTimeout(resolve,150));
  const count=(await (await mf.dispatchFetch('http://review.test/reads')).json()).reads;
  assert(count<=1,`cancellation fetched ${count} chunks`);
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
