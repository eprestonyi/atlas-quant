/* Deterministic current-snapshot set algebra. No market data or credentials. */
export class UniverseRuleError extends Error {
 constructor(code,message,status=400){super(message);this.code=code;this.status=status;}
}
export const UNIVERSE_RULE_LIMITS=Object.freeze({maxGroups:20,maxFiltersPerGroup:20,maxValuesPerFilter:64,maxExplicitSymbols:6000,maxRunSymbols:50,maxCatalogSecurities:10000,maxCatalogUniverses:5000,maxCatalogMemberships:250000});
const UNIVERSE_FIELDS=Object.freeze({universe:'股票池 / 指数',area:'地域',industry:'行业',market:'板块',exchange:'交易所',list_status:'上市状态',is_hs:'互联互通'});
const UNIVERSE_SYMBOL=/^\d{6}\.(?:SH|SZ|BJ)$/;
const universeFail=(code,message,status)=>{throw new UniverseRuleError(code,message,status);};
const universeObject=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
function universeKeys(x,allowed,label){if(!universeObject(x)||Object.keys(x).some(k=>!allowed.includes(k)))universeFail('INVALID_UNIVERSE_SELECTION',`${label}结构或字段无效。`);}
function universeText(x,max,label){if(typeof x!=='string'||!x.trim()||x.length>max)universeFail('INVALID_UNIVERSE_SELECTION',`${label}需为1–${max}字符。`);return x.trim();}
const universeValidSymbol=s=>typeof s==='string'&&UNIVERSE_SYMBOL.test(s)&&!s.startsWith('900')&&!s.startsWith('200');
const universeSorted=s=>[...s].sort();
function universeSymbols(value,label){if(!Array.isArray(value)||value.length>UNIVERSE_RULE_LIMITS.maxExplicitSymbols||value.some(s=>!universeValidSymbol(s)))universeFail('INVALID_UNIVERSE_SELECTION',`${label}须为有限、合法的A股证券代码数组。`);return universeSorted(new Set(value));}
export function validateUniverseSelection(value){
 universeKeys(value,['version','includeGroups','excludeGroups','includeSymbols','excludeSymbols'],'股票池规则');
 if(value.version!==1)universeFail('INVALID_UNIVERSE_SELECTION','需要股票池规则 version=1。');
 const include=value.includeGroups===undefined?[]:value.includeGroups,exclude=value.excludeGroups===undefined?[]:value.excludeGroups;
 if(!Array.isArray(include)||!Array.isArray(exclude)||include.length+exclude.length>UNIVERSE_RULE_LIMITS.maxGroups)universeFail('INVALID_UNIVERSE_SELECTION','股票池包含和排除分组总计最多20个。');
 const ids=new Set();
 const group=g=>{
  universeKeys(g,['id','name','filters'],'筛选分组');
  const id=universeText(g.id,80,'分组ID');if(!/^[A-Za-z0-9_-]+$/.test(id)||ids.has(id))universeFail('INVALID_UNIVERSE_SELECTION','分组ID须唯一且仅含字母、数字、下划线或连字符。');ids.add(id);
  const name=g.name===undefined?id:universeText(g.name,100,'分组名称');
  if(!Array.isArray(g.filters)||!g.filters.length||g.filters.length>UNIVERSE_RULE_LIMITS.maxFiltersPerGroup)universeFail('INVALID_UNIVERSE_SELECTION','每个分组需要1–20条筛选条件。');
  const filters=g.filters.map(f=>{universeKeys(f,['field','value'],'筛选条件');if(typeof f.field!=='string'||!Object.hasOwn(UNIVERSE_FIELDS,f.field))universeFail('INVALID_UNIVERSE_SELECTION','不支持的股票池筛选字段。');const values=Array.isArray(f.value)?f.value:[f.value];if(!values.length||values.length>UNIVERSE_RULE_LIMITS.maxValuesPerFilter)universeFail('INVALID_UNIVERSE_SELECTION','每条条件需要1–64个选项。');const normalized=universeSorted(new Set(values.map(v=>universeText(v,160,'筛选值'))));return {field:f.field,value:normalized.length===1?normalized[0]:normalized};});
  return {id,name,filters};
 };
 return {version:1,includeGroups:include.map(group),excludeGroups:exclude.map(group),includeSymbols:universeSymbols(value.includeSymbols===undefined?[]:value.includeSymbols,'加入个股'),excludeSymbols:universeSymbols(value.excludeSymbols===undefined?[]:value.excludeSymbols,'剔除个股')};
}
export function compileUniverseCatalog({securities,items,hash,asOf,source='TUSHARE_PRO'}){
 if(!Array.isArray(securities)||!Array.isArray(items)||securities.length>UNIVERSE_RULE_LIMITS.maxCatalogSecurities||items.length>UNIVERSE_RULE_LIMITS.maxCatalogUniverses)universeFail('UNIVERSE_CATALOG_LIMIT','股票池目录缺失或超出有界读取容量。',503);
 if(typeof hash!=='string'||!/^[a-f0-9]{64}$/.test(hash))universeFail('UNIVERSE_CATALOG_VERSION','股票池目录缺少可信的内容版本。',503);
 const identities=new Map(),universes=new Map(),indexes=new Map(Object.keys(UNIVERSE_FIELDS).map(k=>[k,new Map()]));
 const addIndex=(field,value,symbol)=>{if(typeof value!=='string'||!value)return;const m=indexes.get(field);if(!m.has(value))m.set(value,new Set());m.get(value).add(symbol);};
 for(const row of securities){
  if(!universeObject(row)||!universeValidSymbol(row.ts_code))continue;
  if(identities.has(row.ts_code))universeFail('UNIVERSE_CATALOG_IDENTITY','股票池身份目录有重复代码。',503);
  const item={ts_code:row.ts_code,name:typeof row.name==='string'?row.name:null,metadataStatus:'observed_identity'};
  for(const field of Object.keys(UNIVERSE_FIELDS).filter(f=>f!=='universe')){item[field]=typeof row[field]==='string'?row[field]:null;addIndex(field,item[field],row.ts_code);}
  identities.set(row.ts_code,item);
 }
 let memberships=0,missingIdentityCount=0;
 for(const u of items){
  if(!universeObject(u)||typeof u.id!=='string'||!u.id||universes.has(u.id)||!Array.isArray(u.symbols))universeFail('UNIVERSE_CATALOG_IDENTITY','股票池定义有缺失或重复身份。',503);
  const members=new Set();
  for(const symbol of u.symbols){
   if(!universeValidSymbol(symbol))universeFail('UNIVERSE_CATALOG_IDENTITY','股票池成员不是支持的A股代码。',503);
   members.add(symbol);
   if(!identities.has(symbol)){identities.set(symbol,{ts_code:symbol,name:null,area:null,industry:null,market:null,exchange:null,list_status:null,is_hs:null,metadataStatus:'missing_identity_metadata'});missingIdentityCount++;}
  }
  memberships+=members.size;if(memberships>UNIVERSE_RULE_LIMITS.maxCatalogMemberships||identities.size>UNIVERSE_RULE_LIMITS.maxCatalogSecurities)universeFail('UNIVERSE_CATALOG_LIMIT','股票池成员规模超出有界读取容量。',503);
  const info={id:u.id,name:String(u.name||u.id),category:String(u.category||'unknown'),symbolCount:members.size,curated:u.curated===true,source:u.source||source,snapshotHash:u.snapshotHash||null,asOf:u.asOf||asOf||null,membershipKind:u.membershipKind||'current_snapshot',historicalMembershipVerified:false};
  universes.set(u.id,info);indexes.get('universe').set(u.id,members);
 }
 const snapshot={hash,asOf:asOf||null,source,securityCount:identities.size,universeCount:universes.size,missingIdentityCount,historicalMembershipVerified:false};
 return {identities,universes,indexes,allSymbols:new Set(identities.keys()),snapshot};
}
export function universeOptions(catalog){
 const labels={exchange:{SSE:'上海证券交易所',SZSE:'深圳证券交易所',BSE:'北京证券交易所'},list_status:{L:'上市',D:'退市',P:'暂停上市'},is_hs:{N:'非沪深股通',H:'沪股通',S:'深股通'}};
 const fields=Object.entries(UNIVERSE_FIELDS).map(([field,label])=>({field,label,kind:field==='universe'?'universe':'category',...(field==='universe'?{lookupUrl:'/quant/api/universes',values:[...catalog.universes.values()].filter(u=>u.curated||u.category==='index').map(u=>({value:u.id,label:u.name,count:u.symbolCount}))}:{values:[...catalog.indexes.get(field)].map(([value,members])=>({value,label:labels[field]?.[value]||value,count:members.size})).sort((a,b)=>a.value.localeCompare(b.value,'zh-Hans-CN'))})}));
 const categories=new Map();for(const u of catalog.universes.values())categories.set(u.category,(categories.get(u.category)||0)+1);
 return {schemaVersion:1,fields,universeCategories:[...categories].map(([category,count])=>({category,count})),catalogSnapshot:catalog.snapshot,limits:UNIVERSE_RULE_LIMITS,semantics:{withinFilter:'OR',withinGroup:'AND',includeGroups:'OR',order:['includeGroups','includeSymbols','excludeGroups','excludeSymbols'],emptyInclude:'empty_set',population:'all_registered_supported_a_share_identities_and_actual_pool_members'},historicalMembershipVerified:false};
}
function universeIntersection(a,b){const result=new Set();const [small,large]=a.size<=b.size?[a,b]:[b,a];for(const value of small)if(large.has(value))result.add(value);return result;}
function universeUnion(into,other){for(const value of other)into.add(value);return into;}
export function resolveUniverseSelection(input,catalog){
 const selection=validateUniverseSelection(input),steps=[];let selected=new Set();
 const evaluate=(group,kind)=>{
  let members=new Set(catalog.allSymbols);
  group.filters.forEach((f,filterIndex)=>{const values=Array.isArray(f.value)?f.value:[f.value],matching=new Set();for(const value of values){const existing=catalog.indexes.get(f.field).get(value);if(!existing)universeFail('UNKNOWN_UNIVERSE_FILTER',`筛选选项不存在：${f.field} / ${value}`);universeUnion(matching,existing);}const before=members.size;members=universeIntersection(members,matching);steps.push({id:`${kind}:${group.id}:${filterIndex}`,stage:`${kind}_filter`,groupId:group.id,name:group.name,filterIndex,field:f.field,value:f.value,before,after:members.size,removed:before-members.size});});
  return members;
 };
 for(const group of selection.includeGroups){const members=evaluate(group,'include'),before=selected.size;universeUnion(selected,members);steps.push({id:`include:${group.id}:union`,stage:'include_union',groupId:group.id,name:group.name,before,after:selected.size,added:selected.size-before});}
 const verifySymbols=list=>{for(const s of list)if(!catalog.identities.has(s))universeFail('UNKNOWN_UNIVERSE_SYMBOL',`证券目录没有该代码：${s}；不会自动创建身份。`);};
 verifySymbols(selection.includeSymbols);verifySymbols(selection.excludeSymbols);
 let before=selected.size;universeUnion(selected,selection.includeSymbols);steps.push({id:'include:symbols',stage:'include_symbols',before,after:selected.size,added:selected.size-before,requested:selection.includeSymbols.length});
 const excluded=new Set();for(const group of selection.excludeGroups)universeUnion(excluded,evaluate(group,'exclude'));
 before=selected.size;for(const s of excluded)selected.delete(s);steps.push({id:'exclude:groups',stage:'exclude_union',before,after:selected.size,removed:before-selected.size,matchedUniverseSymbols:excluded.size});
 before=selected.size;for(const s of selection.excludeSymbols)selected.delete(s);steps.push({id:'exclude:symbols',stage:'exclude_symbols',before,after:selected.size,removed:before-selected.size,requested:selection.excludeSymbols.length});
 const symbols=universeSorted(selected),missing=symbols.filter(s=>catalog.identities.get(s).metadataStatus==='missing_identity_metadata').length;
 const warnings=['使用当前分类与实际成员快照，尚未验证历史时点成员，不能据此声称消除幸存者偏差。'];
 if(missing)warnings.push(`${missing}只实际股票池成员缺少完整身份属性；成员资格保留，未知地域与行业不会参与属性匹配。`);
 if(symbols.length>UNIVERSE_RULE_LIMITS.maxRunSymbols)warnings.push('保留完整股票池；运行前请继续筛选或明确选择不超过50只的研究子集。');
 const usedIds=new Set([...selection.includeGroups,...selection.excludeGroups].flatMap(g=>g.filters.filter(f=>f.field==='universe').flatMap(f=>Array.isArray(f.value)?f.value:[f.value])));
 return {selection,symbols,symbolCount:symbols.length,members:symbols.map(s=>({...catalog.identities.get(s)})),steps,catalogSnapshot:{...catalog.snapshot},snapshotHash:catalog.snapshot.hash,sourceUniverses:universeSorted(usedIds).map(id=>({...catalog.universes.get(id)})),algorithmVersion:'universe-set-v1',requiresSubset:symbols.length>UNIVERSE_RULE_LIMITS.maxRunSymbols,maxRunSymbols:UNIVERSE_RULE_LIMITS.maxRunSymbols,historicalMembershipVerified:false,warnings};
}
export async function universeResolutionHash(result){const input={algorithmVersion:result.algorithmVersion,catalogHash:result.catalogSnapshot.hash,selection:result.selection,symbols:result.symbols};const data=new TextEncoder().encode(JSON.stringify(input));return [...new Uint8Array(await crypto.subtle.digest('SHA-256',data))].map(x=>x.toString(16).padStart(2,'0')).join('');}
