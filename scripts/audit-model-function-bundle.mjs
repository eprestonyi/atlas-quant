/** Offline consumer audit of an EXISTING archive. No fitting, provider or cloud access.
 * Seeds isolated D1/R2 read receipts; this is not publication/source-closure proof.
 * Usage: node scripts/audit-model-function-bundle.mjs /absolute/existing.tar
 */
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from './worker-source.mjs';
import {parsedStage} from '../edge/bundles/storage.mjs';
import {recordIndex,indexStatements} from '../edge/bundles/records.mjs';

const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
function entries(bytes) {
  const result=new Map();
  for(let offset=0;offset+512<=bytes.length&&bytes[offset];) {
    const header=bytes.subarray(offset,offset+512);
    const name=header.subarray(0,100).toString().split('\0')[0];
    const length=Number.parseInt(header.subarray(124,136).toString().replaceAll('\0',''),8);
    assert(Number.isSafeInteger(length)&&length>=0&&offset+512+length<=bytes.length);
    assert(!result.has(name));result.set(name,bytes.subarray(offset+512,offset+512+length));
    offset+=512+Math.ceil(length/512)*512;
  }
  return result;
}
const path=process.argv[2];
assert(path,'Supply an existing archive; this audit never creates a research run.');
assert((await fs.stat(path)).size<64*1024*1024,'Offline audit archive budget exceeded');
const archive=await fs.readFile(path),files=entries(archive),manifestText=files.get('manifest.json').toString();
const bundleId=hash(manifestText),parsed=await parsedStage({manifest_text:manifestText,bundle_id:bundleId});
const stageId=crypto.randomUUID(),runId=crypto.randomUUID(),time=new Date().toISOString();
const mf=new Miniflare({modules:true,script:await buildWorkerSource(),compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS']});
try {
  const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('ARTIFACTS');
  await db.exec((await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8')).replaceAll('\n',' '));
  const request=(path,cookie,data)=>mf.dispatchFetch('https://audit.test/quant/api'+path,{method:data===undefined?'GET':'POST',headers:{...(cookie?{cookie}:{}),...(data?{'content-type':'application/json'}:{})},...(data?{body:JSON.stringify(data)}:{})});
  const json=async response=>{const value=await response.json();assert.equal(response.status,200,JSON.stringify(value));return value;};
  const session=await request('/session'),cookie=session.headers.get('set-cookie').split(';')[0],owner=(await session.json()).workspace.id;
  const another=await request('/session'),other=another.headers.get('set-cookie').split(';')[0];
  const strategy=parsed.metadata.forecast.sourceStrategy;
  await db.prepare('INSERT INTO jobs(id,owner,name,status,data_source,spec,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)').bind(runId,owner,'Offline archive consumer audit','running','ready_dataset',JSON.stringify(strategy),'audit',new Date(Date.now()+3600000).toISOString(),time,time).run();
  await db.prepare('INSERT INTO quant_bundle_stages VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(stageId,owner,runId,'audit',bundleId,manifestText,'audit/manifest.json','{}','staging',time,time).run();
  await bucket.put('audit/manifest.json',manifestText);
  const originals=new Map();
  for(const collection of parsed.collections.values()) {
    const all=[];
    for(const chunk of collection.chunks) {
      const key=`chunks/${collection.id}/${chunk.ordinal}.json`,bytes=files.get(key);
      assert(bytes,key);assert.equal(hash(bytes),chunk.sha256);assert.equal(bytes.length,chunk.byteLength);
      const rows=JSON.parse(bytes);all.push(...rows);await bucket.put(key,bytes);
      await db.prepare('INSERT INTO quant_bundle_chunks VALUES(?,?,?,?,?,?,?,?,?)').bind(stageId,collection.id,chunk.ordinal,chunk.start,chunk.count,chunk.sha256,chunk.byteLength,key,time).run();
      const indices=await Promise.all(rows.map((row,i)=>recordIndex(collection.id,row,chunk.start+i,chunk.ordinal,i)));
      for(const statement of indexStatements({DB:db},{id:stageId},collection.id,indices))await statement.run();
    }
    originals.set(collection.id,all);
  }
  await db.prepare("UPDATE quant_bundle_stages SET status='committed' WHERE id=?").bind(stageId).run();
  await db.prepare("UPDATE jobs SET status='completed',result_key='audit/manifest.json' WHERE id=?").bind(runId).run();
  await db.prepare('INSERT INTO quant_bundle_runs VALUES(?,?,?)').bind(runId,owner,stageId).run();
  const base=`/runs/${runId}/report`,query=`bundleId=${bundleId}`;
  const summary=await json(await request(base,cookie));assert.equal(summary.transport.format,parsed.manifest.format);
  const counts={};
  for(const collection of ['modelFits','factorFeatures','factorJointDistributions']) {
    const expected=originals.get(collection)||[],actual=[];let offset=0;
    do {
      const page=await json(await request(`${base}/pages?${query}&collection=${collection}&limit=2&offset=${offset}`,cookie));
      actual.push(...page.items);offset=page.nextOffset;
    }while(offset!==null);
    assert.deepEqual(actual,expected);counts[collection]=actual.length;
  }
  const functions=[];
  for(const fit of originals.get('modelFits')||[]) {
    if(!fit.functionArtifact)continue;
    const source={runId,bundleId,modelFitId:fit.id},artifact=fit.functionArtifact;
    const resolved=await json(await request('/model-functions/resolve',cookie,{source}));assert.deepEqual(resolved.artifact,artifact);
    assert.equal((await request('/model-functions/resolve',other,{source})).status,404);
    const origin=originals.get('forecasts').find(x=>x.modelFitId===fit.id&&x.status==='valid');assert(origin);
    // Explicit user inference input, not new market features or a new backtest.
    const input={rows:[Object.fromEntries(artifact.inputSchema.map(x=>[x.name,null]))],currentState:[origin.currentState],scale:[origin.scale]};
    const evaluated=await json(await request('/model-functions/evaluate',cookie,{source,input}));
    assert.equal(evaluated.inferenceOnly,true);assert.equal(evaluated.newValidationPerformed,false);
    if(artifact.estimator.kind==='constant') {
      assert.equal(evaluated.result.levels[0].expectedFuture,origin.expectedFuture);
      assert.equal(evaluated.result.levels[0].e,origin.edgeGap);
    }
    const parameter=artifact.estimator.kind==='constant'?'/estimator/value/1':artifact.estimator.kind==='linear'?'/estimator/intercepts/1':'/estimator/outputs/1/baseline';
    const previous=parameter.split('/').slice(1).reduce((v,k)=>v[k],artifact),edits=[{path:parameter,value:previous+.01}];
    const edited=await json(await request('/model-functions/evaluate',cookie,{source,input,edits}));
    assert.equal(edited.result.evidenceStatus,'UNVALIDATED_USER_EDIT');
    assert(Math.abs(edited.result.levels[0].expectedFuture-evaluated.result.levels[0].expectedFuture-origin.scale*.01)<1e-10);
    const payload={source,edits,name:'Offline audit derivative',requestId:crypto.randomUUID()};
    const saved=await json(await request('/model-functions/derive',cookie,payload));
    assert.equal(saved.artifact.lineage.parentArtifactId,artifact.artifactId);
    assert.equal((await json(await request('/model-functions/derive',cookie,payload))).idempotent,true);
    const reopened=await json(await request(`/model-functions/${saved.item.id}?artifactId=${saved.artifact.artifactId}`,cookie));assert.deepEqual(reopened.artifact,saved.artifact);
    assert.deepEqual((await json(await request('/model-functions/evaluate',cookie,{source:saved.item.ref,input}))).result,edited.result);
    assert.deepEqual((await json(await request('/model-functions/resolve',cookie,{source}))).artifact,artifact);
    functions.push({modelFitId:fit.id,artifactId:artifact.artifactId,estimator:fit.estimator,scope:artifact.scope});
  }
  assert(functions.length,'Archive contains no exported fitted functions');
  const downloaded=await request(`${base}/download?${query}`,cookie);assert.equal(downloaded.status,200);
  const report=Buffer.from(await downloaded.arrayBuffer());assert.equal(hash(report),parsed.manifest.documents.report.sha256);assert.equal(report.length,parsed.manifest.documents.report.byteLength);
  const downloadedArchive=await request(`${base}/bundle?${query}`,cookie);assert.equal(downloadedArchive.status,200);
  const copied=entries(Buffer.from(await downloadedArchive.arrayBuffer()));assert.deepEqual([...copied.keys()].sort(),[...files.keys()].sort());
  for(const [name,bytes] of files)assert.deepEqual(copied.get(name),bytes,name);
  process.stdout.write(JSON.stringify({audit:'isolated_existing_bundle_consumer',publicationRetested:false,providerRequests:0,modelFitsRun:0,archiveSha256:hash(archive),bundleId,format:parsed.manifest.format,counts,functions,reportDownloadSha256:hash(report),archiveEntriesByteIdentical:true,privateFunctionRoundTrip:true},null,2)+'\n');
}finally {await mf.dispose();}
