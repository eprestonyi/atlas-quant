import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {Miniflare} from 'miniflare';
import {loadWebAssets,buildWorkerSource} from '../scripts/worker-source.mjs';

test('recursive assets bundle the browser entry, preserve binary bytes and revise entry URLs',async()=>{
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'atlas-assets-'));
 try {
  await fs.mkdir(path.join(dir,'quant-workspace'));await fs.mkdir(path.join(dir,'tests'));
  await fs.writeFile(path.join(dir,'index.html'),'<script type="module" src="main.js"></script>');
  await fs.writeFile(path.join(dir,'main.js'),"import './quant-workspace/model.js';\n");
  await fs.writeFile(path.join(dir,'quant-workspace/model.js'),'export const model=1;');
  await fs.writeFile(path.join(dir,'tests/hidden.js'),'MUST_NOT_PUBLISH');
  const binary=Buffer.from([0,1,255,128,32]);await fs.writeFile(path.join(dir,'quant-workspace/icon.png'),binary);
  const first=await loadWebAssets(dir);assert.ok(!first['tests/hidden.js']);assert.doesNotMatch(first['main.js'].body, /import ['"]/);
  await fs.writeFile(path.join(dir,'quant-workspace/model.js'),'export const model=2;');const second=await loadWebAssets(dir);
  assert.notEqual(first['index.html'].body,second['index.html'].body);
  const mf=new Miniflare({modules:true,script:await buildWorkerSource({assets:second}),compatibilityDate:'2026-08-01'});
  try {
   const js=await mf.dispatchFetch('https://atlas.test/quant/quant-workspace/model.js?v=old');assert.equal(js.status,200);assert.equal(js.headers.get('cache-control'),'no-cache');assert.match(js.headers.get('content-type'),/javascript/);assert.ok(js.headers.get('etag'));assert.match(await js.text(),/model=2/);
   const png=await mf.dispatchFetch('https://atlas.test/quant/quant-workspace/icon.png');assert.equal(png.headers.get('content-type'),'image/png');assert.deepEqual(Buffer.from(await png.arrayBuffer()),binary);
   assert.equal((await mf.dispatchFetch('https://atlas.test/quant/tests/hidden.js')).status,404);
  } finally {await mf.dispose();}
 } finally {await fs.rm(dir,{recursive:true,force:true});}
});
