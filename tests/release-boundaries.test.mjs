import {buildWorkerSource} from '../scripts/worker-source.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {validateStrategy,ApiError} from '../edge/validation.mjs';

const example=JSON.parse(await fs.readFile(new URL('../engine/examples/stat-arb.json',import.meta.url)));
test('factor IDs and object types reject at the edge before numerical execution',()=>{
 for(const factor of [null,[],1,{id:'波动率',expression:'close'},{id:'white space',expression:'close'},{id:'x'.repeat(101),expression:'close'}]) {
  assert.throws(()=>validateStrategy({...example,factors:[factor]}),e=>e instanceof ApiError&&e.status===400);
 }
 assert.equal(validateStrategy({...example,factors:[{id:'x'.repeat(100),expression:'close',direction:1}]}).factors[0].id.length,100);
});

test('large valid selection rules survive private strategy creation, update and readback',async()=>{
 const schema=await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8');
 const script=await buildWorkerSource({buildId:'release-test'});
 const mf=new Miniflare({modules:true,script,compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS']});
 try {
  const db=await mf.getD1Database('DB');await db.exec(schema.replaceAll('\n',' '));
  const session=await mf.dispatchFetch('https://atlas.test/quant/api/session');const cookie=session.headers.get('set-cookie').split(';')[0];
  const request=(path,method,data)=>mf.dispatchFetch('https://atlas.test/quant/api'+path,{method,headers:{cookie,'content-type':'application/json'},...(data?{body:JSON.stringify(data)}:{})});
  const symbols=Array.from({length:4500},(_,i)=>String(i+1).padStart(6,'0')+'.SZ');
  const strategy=structuredClone(example);strategy.universe={...strategy.universe,selection:{version:1,includeGroups:[],excludeGroups:[],includeSymbols:symbols,excludeSymbols:symbols.slice(3)},resolutionHash:'a'.repeat(64),snapshotHash:'b'.repeat(64),subsetPolicy:'explicit'};
  const bytes=Buffer.byteLength(JSON.stringify({strategy}));assert.ok(bytes>100000&&bytes<200000);
  const created=await request('/strategies','POST',{strategy});assert.equal(created.status,201);const first=(await created.json()).item;
  assert.equal(first.strategy.universe.selection.includeSymbols.length,4500);
  strategy.name='Updated large rules';const updated=await request('/strategies/'+first.id,'PUT',{strategy,version:1});assert.equal(updated.status,200);
  const read=await request('/strategies/'+first.id,'GET');const saved=(await read.json()).item;assert.equal(saved.version,2);assert.equal(saved.strategy.name,strategy.name);assert.deepEqual(saved.strategy.universe.selection,first.strategy.universe.selection);
  assert.equal((await request('/strategies','POST',{strategy,padding:'x'.repeat(200001)})).status,413);
 } finally {await mf.dispose();}
});
