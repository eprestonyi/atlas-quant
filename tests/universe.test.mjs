import {buildWorkerSource} from '../scripts/worker-source.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {compileUniverseCatalog,resolveUniverseSelection,universeResolutionHash,universeOptions,validateUniverseSelection,UniverseRuleError} from '../edge/universe.mjs';
import {universeRegistryStatements} from '../scripts/universe-registry.mjs';

const codes=['000001.SZ','000002.SZ','600000.SH','600036.SH','600519.SH','000003.SZ'];
const source={source:'SYNTHETIC_METADATA_TEST_ONLY',fetchedAt:'2026-10-01T00:00:00Z',securities:codes.map((ts_code,i)=>({ts_code,name:'TEST '+i,area:['北京','上海','北京','深圳','北京','北京'][i],industry:['银行','材料','材料','材料','材料','软件'][i],market:'主板',exchange:ts_code.endsWith('SH')?'SSE':'SZSE',list_status:'L',is_hs:'H'})),items:[
 {id:'zz1000',name:'中证1000 fixture',category:'index',symbols:codes.slice(0,3),curated:true},
 {id:'hs300',name:'沪深300 fixture',category:'index',symbols:codes.slice(1,4),curated:true},
 {id:'excluded_index',name:'Exclusion fixture',category:'index',symbols:[codes[2]]},
 {id:'unknown_identity',name:'Observed membership without identity',category:'industry',symbols:['920157.BJ']}
]};
const registry=universeRegistryStatements(source).registry;
const catalog=compileUniverseCatalog({...source,hash:registry.hash,asOf:source.fetchedAt});
const group=(id,...filters)=>({id,name:id,filters:filters.map(([field,value])=>({field,value}))});
const example={version:1,includeGroups:[group('beijing1000',['universe','zz1000'],['area','北京']),group('materials300',['universe','hs300'],['industry','材料'])],excludeGroups:[group('exclude_index',['universe','excluded_index'])],includeSymbols:[codes[4]],excludeSymbols:[codes[3]]};

test('AND groups, OR groups, explicit additions and final exclusions match the intended set algebra',()=>{
 const r=resolveUniverseSelection(example,catalog);
 assert.deepEqual(r.symbols,[codes[0],codes[1],codes[4]].sort());assert.equal(r.symbolCount,3);
 assert.deepEqual(r.steps.slice(0,3).map(s=>[s.stage,s.before,s.after]),[['include_filter',7,3],['include_filter',3,2],['include_union',0,2]]);
 const union=r.steps.filter(s=>s.stage==='include_union');assert.deepEqual(union.map(s=>[s.before,s.after,s.added]),[[0,2,2],[2,4,2]]);
 assert.deepEqual(r.steps.filter(s=>['include_symbols','exclude_union','exclude_symbols'].includes(s.stage)).map(s=>[s.before,s.after]),[[4,5],[5,4],[4,3]]);
 assert.equal(r.members.length,3);assert.equal(r.historicalMembershipVerified,false);assert.equal(r.requiresSubset,false);
 assert.deepEqual(r.sourceUniverses.map(u=>u.id),['excluded_index','hs300','zz1000']);
});

test('multi-value filters are OR while successive filters remain AND; duplicates normalize',()=>{
 const input={version:1,includeGroups:[group('areas',['area',['上海','北京','上海']],['industry','材料'])],includeSymbols:[codes[4],codes[4]]};
 const r=resolveUniverseSelection(input,catalog);assert.deepEqual(r.symbols,[codes[1],codes[2],codes[4]].sort());
 assert.deepEqual(r.selection.includeSymbols,[codes[4]]);assert.equal(r.selection.includeGroups[0].filters[0].value.length,2);
 assert.deepEqual(resolveUniverseSelection({version:1},catalog).symbols,[]);
 const excluded=resolveUniverseSelection({version:1,includeSymbols:[codes[0]],excludeSymbols:[codes[0]]},catalog);assert.deepEqual(excluded.symbols,[]);
});

test('full member sets remain intact above the research limit and request independent admission without a subset',()=>{
 const symbols=Array.from({length:80},(_,i)=>String(i+1).padStart(6,'0')+'.SZ');
 const c=compileUniverseCatalog({securities:symbols.map(ts_code=>({ts_code})),items:[{id:'all',symbols}],hash:'a'.repeat(64)});
 const r=resolveUniverseSelection({version:1,includeGroups:[group('all',['universe','all'])]},c);
 assert.equal(r.symbolCount,80);assert.deepEqual(r.symbols,symbols);assert.equal(r.members.length,80);assert.equal(r.requiresSubset,false);assert.equal(r.maxRunSymbols,undefined);assert.equal(r.admissionStatus,'preflight_required');
});

test('actual members lacking metadata are preserved without invented area or name',()=>{
 const r=resolveUniverseSelection({version:1,includeGroups:[group('new',['universe','unknown_identity'])]},catalog);
 assert.deepEqual(r.symbols,['920157.BJ']);assert.equal(r.members[0].name,null);assert.equal(r.members[0].area,null);assert.equal(r.members[0].metadataStatus,'missing_identity_metadata');assert.match(r.warnings.join(' '),/缺少完整身份/);
 const filtered=resolveUniverseSelection({version:1,includeGroups:[group('new',['universe','unknown_identity'],['area','北京'])]},catalog);assert.deepEqual(filtered.symbols,[]);
});

test('strict rules reject malformed structure, unknown options, invalid symbols and unbounded work',()=>{
 const invalid=[null,[],{version:2},{version:1,includeGroups:null},{version:1,extra:true},{version:1,includeGroups:[group('empty')]},{version:1,includeGroups:[group('x',['__proto__','x'])]},{version:1,includeGroups:[group('x',['area',[]])]},{version:1,includeGroups:[group('x',['area',0])]},{version:1,includeGroups:[group('x',['area',['x'.repeat(161)]])]},{version:1,includeGroups:[group('same',['area','北京'])],excludeGroups:[group('same',['area','北京'])]},{version:1,includeGroups:Array.from({length:21},(_,i)=>group('g'+i,['area','北京']))},{version:1,includeSymbols:['AAPL']},{version:1,includeSymbols:['900901.SH']},{version:1,includeSymbols:Array(6001).fill(codes[0])},{version:1,includeGroups:[group('g',['area',Array.from({length:65},(_,i)=>String(i))])]}];
 for(const item of invalid)assert.throws(()=>validateUniverseSelection(item),e=>e instanceof UniverseRuleError&&e.status===400,JSON.stringify(item));
 for(const [field,value]of [['universe','not-present'],['area','missing-area']])assert.throws(()=>resolveUniverseSelection({version:1,includeGroups:[group('g',[field,value])]},catalog),e=>e.code==='UNKNOWN_UNIVERSE_FILTER');
 assert.throws(()=>resolveUniverseSelection({version:1,includeSymbols:['000099.SZ']},catalog),e=>e.code==='UNKNOWN_UNIVERSE_SYMBOL');
});

test('rule snapshots are deterministic and depend on the exact rules and catalog version',async()=>{
 const r=resolveUniverseSelection(example,catalog),again=resolveUniverseSelection(structuredClone(example),catalog);
 assert.equal(await universeResolutionHash(r),await universeResolutionHash(again));
 const changed=structuredClone(r);changed.catalogSnapshot.hash='f'.repeat(64);assert.notEqual(await universeResolutionHash(r),await universeResolutionHash(changed));
 changed.catalogSnapshot.hash=r.catalogSnapshot.hash;changed.selection.includeGroups[0].name='renamed';assert.notEqual(await universeResolutionHash(r),await universeResolutionHash(changed));
 const opts=universeOptions(catalog);assert.equal(opts.catalogSnapshot.missingIdentityCount,1);assert.ok(opts.fields.every(f=>f.values.every(v=>!('symbols'in v))));
});

test('registry chunks preserve source identities and finalize a reproducible content marker',()=>{
 const large={...source,securities:Array.from({length:401},(_,i)=>({ts_code:String(i+1).padStart(6,'0')+'.SZ',name:'中文身份'+i}))};
 const r=universeRegistryStatements(large);assert.equal(r.registry.securityChunkKeys.length,3);assert.equal(r.statements.length,4);assert.match(r.statements.at(-1),/VALUES\('universe_registry'/);
 assert.equal(r.registry.hash,universeRegistryStatements({...large,securities:[...large.securities].reverse(),items:[...large.items].reverse()}).registry.hash);
});

const schema=await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8');
const script=await buildWorkerSource({buildId:'universe-test'});
async function apiFixture({alter,missing=false,sourceData=source}={}){
 const mf=new Miniflare({modules:true,script,compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],bindings:{RUNNER_SECRET:'universe-test-only'}}),db=await mf.getD1Database('DB');
 await db.exec(schema.replaceAll('\n',' '));
 for(let i=0;i<sourceData.items.length;i+=50)await db.batch(sourceData.items.slice(i,i+50).map(u=>db.prepare('INSERT INTO research_universes(id,name,category,symbol_count,search_text,metadata) VALUES(?,?,?,?,?,?)').bind(u.id,u.name,u.category,u.symbols.length,u.name,JSON.stringify(u))));
 if(!missing)for(const sql of universeRegistryStatements(sourceData).statements)await db.exec(sql);
 if(alter)await alter(db);
 const request=(path,{method='GET',data,cookie,origin}={})=>mf.dispatchFetch('https://universe.test/quant/api'+path,{method,headers:{...(data?{'content-type':'application/json'}:{}),...(cookie?{cookie}:{}),...(origin?{origin}:{})},...(data?{body:JSON.stringify(data)}:{})});
 const session=await request('/session'),cookie=session.headers.get('set-cookie').split(';')[0];
 return {mf,db,request,cookie};
}

test('HTTP options are small metadata; authenticated resolve returns full audited membership',async()=>{
 const x=await apiFixture();try{
  const response=await x.request('/universe-options');assert.equal(response.status,200);const options=await response.json();assert.equal(options.catalogSnapshot.hash,registry.hash);assert.ok(JSON.stringify(options).length<12000);
  assert.equal((await x.request('/universes/resolve',{method:'POST',data:{selection:example}})).status,401);
  assert.equal((await x.request('/universes/resolve',{method:'POST',cookie:x.cookie,origin:'https://evil.test',data:{selection:example}})).status,403);
  const resolved=await x.request('/universes/resolve',{method:'POST',cookie:x.cookie,data:{selection:example}});assert.equal(resolved.status,200);const r=await resolved.json();assert.deepEqual(r.symbols,[codes[0],codes[1],codes[4]].sort());assert.match(r.resolutionHash,/^[a-f0-9]{64}$/);assert.equal(r.snapshotHash,registry.hash);
  assert.equal((await x.request('/universes/resolve',{method:'POST',cookie:x.cookie,data:{selection:example,owner:'spoof'}})).status,400);
  const invalid=await x.request('/universes/resolve',{method:'POST',cookie:x.cookie,data:{selection:{version:1,includeGroups:[group('g',['area','unregistered'])]}}});assert.equal(invalid.status,400);assert.equal((await invalid.json()).error.code,'UNKNOWN_UNIVERSE_FILTER');
 }finally{await x.mf.dispose();}
});

test('HTTP registry fails closed when identities are absent or snapshots mix versions',async()=>{
 for(const options of [{missing:true},{alter:db=>db.prepare("UPDATE research_universes SET metadata=? WHERE id='zz1000'").bind(JSON.stringify({...source.items[0],symbols:[codes[0]]})).run()}]){
  const x=await apiFixture(options);try{const r=await x.request('/universe-options');assert.equal(r.status,503);assert.ok(['UNIVERSE_CATALOG_NOT_READY','UNIVERSE_CATALOG_CHANGED'].includes((await r.json()).error.code));}finally{await x.mf.dispose();}
 }
});

test('saved strategy retains set rules and enqueue rejects stale snapshots or an unconfirmed full-set claim',async()=>{
 const x=await apiFixture();try{
  const selection={version:1,includeSymbols:codes},response=await x.request('/universes/resolve',{method:'POST',cookie:x.cookie,data:{selection}}),resolved=await response.json();
  assert.equal(response.status,200);
  const strategy={schemaVersion:1,name:'SYNTHETIC universe queue contract',universe:{symbols:codes.slice(0,3),start:'20230101',end:'20241231',selection:resolved.selection,resolutionHash:resolved.resolutionHash,snapshotHash:resolved.snapshotHash,catalogSnapshot:resolved.catalogSnapshot,subsetPolicy:'explicit'},factors:[{id:'momentum',expression:'returns(close,20)',direction:1}],model:{mode:'manual',candidates:['factor_score'],horizon:5},portfolio:{topN:2,maxWeight:.5,rebalanceDays:5,initialCapital:100000},preprocess:{winsorize:true,standardize:true},costs:{commissionBps:3,slippageBps:10,sellTaxBps:5},graph:{nodes:[],edges:[]}};
  const saved=await x.request('/strategies',{method:'POST',cookie:x.cookie,data:{strategy}});assert.equal(saved.status,201);const item=(await saved.json()).item;assert.deepEqual(item.strategy.universe.selection,resolved.selection);assert.equal(item.strategy.universe.subsetPolicy,'explicit');assert.equal(item.strategy.universe.snapshotHash,resolved.snapshotHash);
  const stale=structuredClone(strategy);stale.universe.resolutionHash='0'.repeat(64);const conflict=await x.request('/runs',{method:'POST',cookie:x.cookie,data:{strategy:stale,dataSource:'demo'}});assert.equal(conflict.status,409);assert.equal((await conflict.json()).error.code,'UNIVERSE_CHANGED');
  const wrong=structuredClone(strategy);wrong.universe.subsetPolicy='all';const mismatched=await x.request('/runs',{method:'POST',cookie:x.cookie,data:{strategy:wrong,dataSource:'demo'}});assert.equal(mismatched.status,400);assert.equal((await mismatched.json()).error.code,'UNIVERSE_SUBSET_MISMATCH');
  const accepted=await x.request('/runs',{method:'POST',cookie:x.cookie,data:{strategy,dataSource:'demo'}});assert.equal(accepted.status,202);const job=(await accepted.json()).job;assert.equal(job.status,'queued');
 }finally{await x.mf.dispose();}
});

test('current real catalog resolves exactly the independent full-set calculation, without fetching provider data',async()=>{
 const real=JSON.parse(await fs.readFile(new URL('../data/universes.json',import.meta.url),'utf8')),r=universeRegistryStatements(real).registry,c=compileUniverseCatalog({...real,hash:r.hash,asOf:real.fetchedAt});
 const index=real.items.find(u=>u.category==='index'&&u.definition.index_code==='000852.SH');assert.ok(index);
 const selected=resolveUniverseSelection({version:1,includeGroups:[group('real',['universe',index.id],['area','北京'])]},c);
 const beijing=new Set(real.securities.filter(s=>s.area==='北京').map(s=>s.ts_code));assert.deepEqual(selected.symbols,[...new Set(index.symbols.filter(s=>beijing.has(s)))].sort());
 const full=resolveUniverseSelection({version:1,includeGroups:[group('full',['universe',index.id])]},c);assert.equal(full.symbolCount,new Set(index.symbols).size);assert.ok(full.symbolCount>50);assert.equal(full.requiresSubset,false);assert.equal(full.catalogSnapshot.missingIdentityCount,4);
});

test('full real D1 snapshot loads 5911 identities and 1354 pools without shipping memberships in options',async()=>{
 const real=JSON.parse(await fs.readFile(new URL('../data/universes.json',import.meta.url),'utf8'));
 const x=await apiFixture({sourceData:real});try{
  const response=await x.request('/universe-options');assert.equal(response.status,200);const text=await response.text(),options=JSON.parse(text);
  assert.equal(options.catalogSnapshot.universeCount,real.items.length);assert.equal(options.catalogSnapshot.missingIdentityCount,4);assert.ok(Buffer.byteLength(text)<50000);assert.ok(!text.includes('"symbols":'));
  const index=real.items.find(u=>u.category==='index'&&u.definition.index_code==='000852.SH');
  const result=await x.request('/universes/resolve',{method:'POST',cookie:x.cookie,data:{selection:{version:1,includeGroups:[group('index',['universe',index.id])]}}});assert.equal(result.status,200);const data=await result.json();assert.equal(data.symbolCount,index.symbols.length);assert.equal(data.members.length,index.symbols.length);assert.equal(data.requiresSubset,false);
 }finally{await x.mf.dispose();}
});
