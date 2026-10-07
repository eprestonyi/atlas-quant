import fs from 'node:fs/promises';
import path from 'node:path';
import {universeRegistryStatements} from './universe-registry.mjs';
const root=path.resolve(import.meta.dirname,'..');
const q=value=>value===null||value===undefined?'NULL':typeof value==='number'?String(value):"'"+String(value).replaceAll("'","''")+"'";
export async function registryStatements(){
 const statements=[];const pcd=JSON.parse(await fs.readFile(path.join(root,'data/pcd-fields.json'),'utf8'));
 const catalog=JSON.parse(await fs.readFile(path.join(root,'engine/atlas_quant/catalog.json'),'utf8'));
 const fields=pcd.fields.map(f=>({id:f.id,database:f.source||'PCD',dataType:f.dataType,numericEligible:f.numericEligible,alias:f.alias,metadata:f}));
 const marketFields='open high low close raw_close vol amount adj_factor turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv'.split(' ');
 for(const id of marketFields)fields.push({id:'MKT.'+id,database:'MKT',dataType:'number',numericEligible:true,alias:id,metadata:{id:'MKT.'+id,label:id,source:'MKT',path:id,description:'Tushare日行情或每日估值字段；单位和来源在报告中保留。',unit:id==='vol'?'手':id==='amount'?'千元':'source_native',availabilityStatus:'ready'}});
 const financial=[...new Set(catalog.factors.flatMap(f=>(f.requiredFields||[]).filter(x=>x.startsWith('fd_'))))];
 for(const alias of financial)fields.push({id:'FD.'+alias,database:'FD',dataType:'number',numericEligible:true,alias,metadata:{id:'FD.'+alias,label:alias.replace(/^fd_/,''),source:'FD',path:'Tushare.fina_indicator.'+alias.replace(/^fd_/,''),description:'按公告后首个官方交易日对齐的财务指标。原始披露版本历史未由数据提供方验证。',unit:'source_native',availabilityStatus:'ready'}});
 for(let i=0;i<fields.length;i+=40){const rows=fields.slice(i,i+40).map(f=>[f.id,f.database,f.dataType,f.numericEligible?1:0,f.alias,[f.id,f.metadata.label,f.metadata.description,f.metadata.module].join(' ').toLowerCase(),JSON.stringify(f.metadata)].map(q).join(','));statements.push('INSERT OR REPLACE INTO data_fields(id,database_key,data_type,numeric_eligible,alias,search_text,metadata) VALUES '+rows.map(x=>'('+x+')').join(',')+';');}
 const {fields:omitted,...metadata}=pcd;statements.push(`INSERT INTO meta(key,value,updated_at) VALUES('field_registry',${q(JSON.stringify(metadata))},${q(new Date().toISOString())}) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;`);
 let universeCount=0;try{const file=JSON.parse(await fs.readFile(path.join(root,'data/universes.json'),'utf8'));const items=Array.isArray(file)?file:file.items;universeCount=items.length;for(let i=0;i<items.length;i+=1){const rows=items.slice(i,i+1).map(u=>[u.id,u.name,u.category,u.symbolCount||u.symbols.length,[u.id,u.name,u.category,u.description].join(' ').toLowerCase(),JSON.stringify(u)].map(q).join(','));statements.push('INSERT OR REPLACE INTO research_universes(id,name,category,symbol_count,search_text,metadata) VALUES '+rows.map(x=>'('+x+')').join(',')+';');}if(!Array.isArray(file))statements.push(...universeRegistryStatements(file).statements);}catch(e){if(e.code!=='ENOENT')throw e;}
 return {statements,summary:{fields:fields.length,pcdFields:pcd.fields.length,pcdNumeric:pcd.counts.numericEligible,financialFields:financial.length,universes:universeCount}};
}
export async function seedLocal(db){const {statements,summary}=await registryStatements();for(let i=0;i<statements.length;i+=20)await db.batch(statements.slice(i,i+20).map(sql=>db.prepare(sql)));return summary;}
