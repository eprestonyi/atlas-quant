/* Local API integration: one private research record, no run/provider/deployment. */
import assert from 'node:assert/strict';
const base=process.env.ATLAS_API_BASE||'http://127.0.0.1:8895/quant/api';
if(!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/quant\/api$/.test(base))throw Error('This test only mutates a local development workspace.');
let cookie='',checks=0;
async function api(path,body){const r=await fetch(base+path,{method:body?'POST':'GET',headers:{...(cookie?{cookie}:{}),...(body?{'content-type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});if(r.headers.get('set-cookie'))cookie=r.headers.get('set-cookie').split(';')[0];const data=await r.json();if(!r.ok)throw Error(`${r.status}: ${JSON.stringify(data)}`);return data;}
await api('/session');
const options=await api('/universe-options');assert(options.fields.some(f=>f.field==='area'));checks++;
const universe=(await api('/universes?q=中证1000&pageSize=3')).items.find(x=>x.name==='中证1000');assert(universe);checks++;
const selection={version:1,includeGroups:[{id:'index',name:'中证1000',filters:[{field:'universe',value:universe.id}]}],excludeGroups:[],includeSymbols:[],excludeSymbols:[]};
const all=await api('/universes/resolve',{selection});assert.equal(all.symbolCount,1000);assert.equal(all.members.length,1000);assert(all.requiresSubset);checks++;
selection.includeGroups[0].filters.push({field:'area',value:['北京']});
const and=await api('/universes/resolve',{selection});assert(and.symbolCount>50&&and.symbolCount<1000);assert(and.members.every(m=>m.area==='北京'));checks++;
selection.includeGroups.push({id:'shenzhen',name:'深圳 OR',filters:[{field:'universe',value:universe.id},{field:'area',value:['深圳']}]});
const or=await api('/universes/resolve',{selection});assert(or.symbolCount>=and.symbolCount);assert(or.members.every(m=>['北京','深圳'].includes(m.area)));checks++;
selection.excludeSymbols=[or.symbols[0]];
const subtracted=await api('/universes/resolve',{selection});assert.equal(subtracted.symbolCount,or.symbolCount-1);assert(!subtracted.symbols.includes(or.symbols[0]));checks++;
selection.excludeGroups=[{id:'exclude-shenzhen',name:'剔除深圳',filters:[{field:'area',value:'深圳'}]}];
const filtered=await api('/universes/resolve',{selection});assert(filtered.members.every(m=>m.area==='北京'));checks++;
const empty=await api('/universes/resolve',{selection:{version:1,includeGroups:[],excludeGroups:[],includeSymbols:[],excludeSymbols:[]}});assert.equal(empty.symbolCount,0);checks++;
const presets=await api('/research-presets');assert.equal(presets.strategies.length,3);assert(presets.strategies.every(p=>p.strategy.research.mode==='stat_arb'));checks++;
const strategy=structuredClone(presets.strategies[0].strategy);strategy.name='五步集合保存 QA';strategy.universe={...strategy.universe,symbols:filtered.symbols.slice(0,20),selection:filtered.selection,resolutionHash:filtered.resolutionHash,snapshotHash:filtered.snapshotHash,catalogSnapshot:filtered.catalogSnapshot,subsetPolicy:'explicit'};strategy.research.baseline={id:presets.strategies[0].id,name:presets.strategies[0].name};
const saved=await api('/strategies',{strategy});assert(saved.item.id);assert.equal(saved.item.strategy.universe.symbols.length,20);assert.equal(saved.item.strategy.universe.resolutionHash,filtered.resolutionHash);assert.deepEqual(saved.item.strategy.universe.selection,filtered.selection);checks++;
const reloaded=(await api('/strategies')).items.find(x=>x.id===saved.item.id);assert.equal(reloaded.strategy.research.mode,'stat_arb');assert.equal(reloaded.strategy.research.baseline.id,presets.strategies[0].id);assert.equal(reloaded.strategy.universe.snapshotHash,filtered.snapshotHash);checks++;
console.log(JSON.stringify({passed:checks,localOnly:true,completeCSI1000:all.symbolCount,andCount:and.symbolCount,orCount:or.symbolCount,afterExclusions:filtered.symbolCount,explicitSubset:saved.item.strategy.universe.symbols.length,allThreeBaselinesStatArb:true,savedVersion:saved.item.version}));
