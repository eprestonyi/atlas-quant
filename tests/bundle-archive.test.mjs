import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { archiveEntries, archiveLength, archiveStream, tarHeader } from '../edge/bundles/archive.mjs';
import { validateManifest } from '../edge/bundles/manifest.mjs';
import { bundleFixture } from './fixtures/bundle-fixture.mjs';

test('USTAR round trip preserves every original byte and fixed metadata', async () => {
  const f = bundleFixture();
  const parsed = await validateManifest(f.manifestText, f.bundleId);
  const entries = archiveEntries({manifest_text:f.manifestText}, parsed);
  const raw = Buffer.from(await new Response(archiveStream(entries, async (name, d) =>
    new TextEncoder().encode(f.chunks.get(`${name}:${d.ordinal}`)))).arrayBuffer());
  assert.equal(raw.length, archiveLength(entries));
  assert(raw.subarray(-1024).equals(Buffer.alloc(1024)));
  const script = `import sys,tarfile,io,json,hashlib
raw=sys.stdin.buffer.read()
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as tf:
 result=[]
 for member in tf:
  assert member.isfile() and not member.linkname
  assert member.mode==0o600 and member.uid==member.gid==member.mtime==0
  assert member.uname==member.gname==''
  body=tf.extractfile(member).read()
  result.append({'name':member.name,'size':member.size,'sha256':hashlib.sha256(body).hexdigest()})
 print(json.dumps(result))
`;
  const child = spawnSync('python3', ['-c', script], {input:raw,encoding:'utf8'});
  assert.equal(child.status, 0, child.stderr);
  const members = JSON.parse(child.stdout);
  assert.equal(members.length, entries.length);
  assert.deepEqual(members.map(x=>x.name), entries.map(x=>x.name));
  assert.equal(members[0].sha256, f.bundleId);
  for (let i=1;i<members.length;i++) assert.equal(members[i].sha256, entries[i].descriptor.sha256);
});

test('paths and header sizes cannot introduce links, traversal or oversized files', () => {
  for (const name of ['../manifest.json','/manifest.json','chunks/unknown/0.json',
    'chunks/forecasts/01.json','chunks/forecasts/-1.json','chunks/forecasts/0.json\r\nX: y'])
    assert.throws(()=>tarHeader(name, 1), /复现包/);
  for (const size of [-1,0.5,NaN,Infinity,Number.MAX_SAFE_INTEGER])
    assert.throws(()=>tarHeader('manifest.json',size), /复现包/);
});

test('slow reader and cancellation never start a later R2 read', async () => {
  let loads=0;
  const entries=[{name:'manifest.json',size:2,raw:new Uint8Array([123,125])},
    {name:'chunks/forecasts/0.json',size:2,collection:'forecasts',descriptor:{ordinal:0}},
    {name:'chunks/forecasts/1.json',size:2,collection:'forecasts',descriptor:{ordinal:1}}];
  const stream=archiveStream(entries,async()=>{loads++;return new Uint8Array([91,93]);});
  const reader=stream.getReader();
  await Promise.resolve();assert.equal(loads,0);
  await reader.read();await reader.read();await reader.read(); // Manifest header/body/padding.
  assert.equal(loads,0);
  await reader.read();assert.equal(loads,1); // First chunk verified before its header.
  await reader.cancel();await Promise.resolve();assert.equal(loads,1);
});

test('cancel during an in-flight read discards it without fetching another chunk', async () => {
  let resolveBody, started;
  const fetching=new Promise(resolve=>{started=resolve;});
  let loads=0;
  const entries=[0,1].map(ordinal=>({name:`chunks/forecasts/${ordinal}.json`,size:2,
    collection:'forecasts',descriptor:{ordinal}}));
  const reader=archiveStream(entries,async()=>{loads++;started();
    return new Promise(resolve=>{resolveBody=resolve;});}).getReader();
  const pending=reader.read();await fetching;
  const cancelled=reader.cancel();resolveBody(new Uint8Array([91,93]));
  await cancelled;assert.equal((await pending).done,true);assert.equal(loads,1);
});

test('a failed chunk aborts the archive instead of writing a success trailer', async () => {
  const entries=[{name:'chunks/forecasts/0.json',size:2,collection:'forecasts',descriptor:{ordinal:0}}];
  await assert.rejects(new Response(archiveStream(entries,async()=>{throw Error('bad hash');})).arrayBuffer(),/bad hash/);
  await assert.rejects(new Response(archiveStream(entries,async()=>new Uint8Array(1))).arrayBuffer(),/复现包/);
});
