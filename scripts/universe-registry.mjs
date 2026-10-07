/* Small D1 metadata chunks, finalized by a content-addressed registry marker. */
import {createHash} from 'node:crypto';
const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const sql=value=>"'"+String(value).replaceAll("'","''")+"'";
export function universeRegistryStatements(file){
 if(!file||!Array.isArray(file.securities)||!Array.isArray(file.items))throw new Error('Universe registry requires source identities and memberships');
 if(file.securities.length>10000||file.items.length>5000)throw new Error('Universe registry exceeds bounded catalog size');
 const securities=[...file.securities].sort((a,b)=>a.ts_code.localeCompare(b.ts_code));
 const items=[...file.items].sort((a,b)=>a.id.localeCompare(b.id));
 const securityChunkKeys=[],statements=[],time=file.fetchedAt||new Date().toISOString();
 const put=(key,value)=>`INSERT INTO meta(key,value,updated_at) VALUES(${sql(key)},${sql(JSON.stringify(value))},${sql(time)}) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;`;
 for(let i=0;i<securities.length;i+=200){const key='universe_securities:'+String(i/200).padStart(4,'0');securityChunkKeys.push(key);statements.push(put(key,securities.slice(i,i+200)));}
 const securityContentHash=digest(securities),universeContentHash=digest(items);
 const source=file.source||'TUSHARE_PRO',asOf=file.fetchedAt||null;
 const hash=digest({version:1,source,asOf,securityContentHash,universeContentHash});
 const registry={schemaVersion:1,hash,source,asOf,securityCount:securities.length,universeCount:items.length,securityChunkKeys,securityContentHash,universeContentHash,universeIds:items.map(u=>u.id),historicalMembershipVerified:false};
 // This marker must be published after all identity chunks and memberships.
 statements.push(put('universe_registry',registry));
 return {statements,registry};
}
