import test from 'node:test';
import assert from 'node:assert/strict';
import {validateManifest,validateChunk} from '../edge/bundles/manifest.mjs';
import {validateContextChunk} from '../edge/bundles/context-sources.mjs';
import {archiveEntries} from '../edge/bundles/archive.mjs';
import {transportView} from '../edge/bundles/user-api.mjs';
import {contextBundleFixture,foreignContextBundleFixture} from './fixtures/context-bundle-fixture.mjs';
import {bundleFixture,canonical,hash} from './fixtures/bundle-fixture.mjs';

test('16 independent histories fit bounded manifest and private archive paths',async()=>{
  const f=contextBundleFixture({sourceCount:16,dateCount:2200});
  assert(Buffer.byteLength(canonical(f.snapshot.provenance.contextSources))>512*1024);
  assert(Buffer.byteLength(f.manifestText)<32*1024);
  const p=await validateManifest(f.manifestText,f.bundleId),c=p.collections.get('snapshotContextSources');
  assert.equal(c.rowCount,16);
  for(const d of c.chunks) {
    const raw=f.chunks.get('snapshotContextSources:'+d.ordinal);
    const rows=await validateChunk(raw,d);
    await validateContextChunk(raw,rows,d.start,p.metadata.report.provenance.contextSources);
  }
  const entries=archiveEntries({manifest_text:f.manifestText},p);
  assert.equal(entries.filter(x=>x.collection==='snapshotContextSources').length,c.chunks.length);
  assert(!transportView({bundle_id:f.bundleId},p,'j').collections.snapshotContextSources);
  assert(f.report.provenance.contextSources.every(x=>!Object.hasOwn(x,'records')));
});

test('source descriptions, paths and root mismatches fail before admission',async()=>{
  for(const mutate of [
    i=>i.report.provenance.contextSourceRoot='0'.repeat(64),
    i=>i.report.provenance.contextSources[0].rowCount=0,
    i=>i.report.provenance.contextSources[0].params.ts_code='600000.SH',
    i=>i.report.provenance.contextSources.reverse(),
    i=>i.snapshot.provenance.contextObservationClock='before_open',
    i=>i.snapshot.fingerprintVersion='research_input_v1',
  ]) {
    const f=contextBundleFixture({mutate});
    await assert.rejects(validateManifest(f.manifestText,f.bundleId));
  }
});

test('exact source record SHA, values, identity and count checked before upload',async()=>{
  const f=contextBundleFixture(),p=await validateManifest(f.manifestText),summaries=p.metadata.report.provenance.contextSources;
  const original=JSON.parse(f.chunks.get('snapshotContextSources:0'));
  for(const mutate of [
    r=>r[0].records[0].close=1,
    r=>r[0].records[0].ts_code='000001.SZ',
    r=>r[0].records[0].trade_date='20141231',
    r=>r[0].records[0].trade_date='20150230',
    r=>r[0].records[0].close=true,
    r=>r[0].records[0].amount=-1,
    r=>r[0].records.push(r[0].records[0]),
    r=>r[0].records.reverse(),
    r=>r[0].historicalRevisionVerified=true,
    r=>r[0].fields.push('future_price'),
  ]) {
    const changed=structuredClone(original);mutate(changed);
    await assert.rejects(validateContextChunk(canonical(changed),changed,0,summaries));
  }
});

test('noninteger Python exponent tokens are hashed as original record bytes',async()=>{
  const f=contextBundleFixture(),rows=JSON.parse(f.chunks.get('snapshotContextSources:0'));
  rows[0].records[0].close=0.0000123;
  const recordsRaw=canonical(rows[0].records).replace('0.0000123','1.23e-05');
  rows[0].sha256=hash(recordsRaw);
  const summaries=rows.map(({api,params,fields,sha256,records})=>({api,params,fields,sha256,rowCount:records.length}));
  const raw=canonical(rows).replace('0.0000123','1.23e-05');
  const parsed=await validateChunk(raw,{sha256:hash(raw),byteLength:Buffer.byteLength(raw),count:rows.length});
  await validateContextChunk(raw,parsed,0,summaries);
});

test('absent optional context leaves the legacy fixture bytes unchanged',async()=>{
  const a=bundleFixture(),b=bundleFixture({collectionPaths:{...Object.fromEntries(a.manifest.collections.map(c=>[c.id,[c.document,c.path]])),snapshotContextSources:['snapshot','/provenance/contextSources']}});
  assert.equal(a.manifestText,b.manifestText);assert.deepEqual(a.chunks,b.chunks);
  await validateManifest(a.manifestText,a.bundleId);
});


test('foreign archives retain raw adjustment data and require foreign clock',async()=>{
  const f=foreignContextBundleFixture(),p=await validateManifest(f.manifestText,f.bundleId);
  const raw=f.chunks.get('snapshotContextSources:0'),descriptor=p.collections.get('snapshotContextSources').chunks[0];
  const rows=await validateChunk(raw,descriptor);
  assert.equal(rows[0].records[0].adj_factor,0.5);
  assert.equal(rows[0].records[0].close,f.snapshot.provenance.contextSources[0].records[0].close);
  await validateContextChunk(raw,rows,0,p.metadata.report.provenance.contextSources);
  for(const mutate of [
    i=>{i.report.provenance.contextObservationClock=i.snapshot.provenance.contextObservationClock='after_daily_publication_before_next_open'},
    i=>i.report.provenance.contextSources[0].fields.splice(2,1),
  ]) {
    const broken=foreignContextBundleFixture({mutate});
    await assert.rejects(validateManifest(broken.manifestText,broken.bundleId));
  }
  rows[0].records[0].adj_factor=0;
  await assert.rejects(validateContextChunk(canonical(rows),rows,0,p.metadata.report.provenance.contextSources));
});

test('Yahoo source has a separate frozen API, direct adjusted close and bounded provider metadata',async()=>{
  const {yahooContextBundleFixture}=await import('./fixtures/context-bundle-fixture.mjs');
  const f=yahooContextBundleFixture(),p=await validateManifest(f.manifestText,f.bundleId);
  const raw=f.chunks.get('snapshotContextSources:0'),rows=JSON.parse(raw);
  await validateContextChunk(raw,rows,0,p.metadata.report.provenance.contextSources);
  for(const mutate of [
    r=>delete r.providerDetails,
    r=>r.providerDetails.libraryVersion='unfrozen',
    r=>r.providerDetails.cookie='private',
    r=>r.providerDetails.httpReceipts[0].path+='?crumb=private',
    r=>r.providerDetails.httpReceipts[0].bytes=8388609,
    r=>r.records[0].adj_close=0,
    r=>r.fields.push('amount'),
  ]) {
    const changed=structuredClone(rows);mutate(changed[0]);
    await assert.rejects(validateContextChunk(canonical(changed),changed,0,p.metadata.report.provenance.contextSources));
  }
});
