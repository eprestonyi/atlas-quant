/** Bounded independent index archives. Hashes prove contents, not data authority. */
import { ApiError } from '../errors.mjs';
import { sha } from '../runtime.mjs';
import { HASH, DATE } from './profile.mjs';
import { keys, object } from './json.mjs';
import registry from '../../engine/atlas_quant/context_sources.json' with {type:'json'};

const fail = () => { throw new ApiError('BUNDLE_CONTEXT_SOURCE', '独立指数冻结来源与摘要不一致'); };
const exact = (value, allowed) => {
  keys(value, allowed, 'context source');
  if (Object.keys(value).length !== allowed.length) fail();
};
const summaryKeys = ['api','params','fields','sha256','rowCount'];
const sorted = value => Array.isArray(value) ? value.map(sorted) : object(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key=>[key,sorted(value[key])])) : value;
const same = (a,b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
const validDate = value => {
  if(typeof value!=='string'||!DATE.test(value)) return false;
  const date=new Date(value.slice(0,4)+'-'+value.slice(4,6)+'-'+value.slice(6,8)+'T00:00:00Z');
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10).replaceAll('-','')===value;
};

function summary(value) {
  exact(value, summaryKeys); exact(value.params, ['ts_code','start_date','end_date']);
  if (!registry.items.some(item=>item.api===value.api && item.ts_code===value.params.ts_code)
      || !validDate(value.params.start_date) || !validDate(value.params.end_date)
      || value.params.start_date > value.params.end_date || typeof value.sha256!=='string' || !HASH.test(value.sha256)
      || !Number.isSafeInteger(value.rowCount) || value.rowCount<1 || value.rowCount>4000
      || !Array.isArray(value.fields) || value.fields.length<3
      || value.fields[0]!=='ts_code' || value.fields[1]!=='trade_date') fail();
  const valid = ['close','vol','amount',...(value.api==='sw_daily'?['pe','pb','total_mv','float_mv']:[])];
  const fields = value.fields.slice(2);
  if (fields.some(field=>!valid.includes(field)) || !same(fields,[...new Set(fields)].sort())) fail();
  return value;
}

export function assertContextManifest(parsed) {
  const collection = parsed.collections.get('snapshotContextSources');
  if (!collection) {
    if (parsed.manifest.kind==='forecast' && (parsed.metadata.snapshot.fingerprintVersion==='research_input_context_v1'
        || Object.hasOwn(parsed.metadata.snapshot.provenance,'contextSourceRoot'))) fail();
    return;
  }
  const report = parsed.metadata.report.provenance, snapshot = parsed.metadata.snapshot?.provenance;
  if (parsed.manifest.kind!=='forecast' || collection.rowCount<1 || collection.rowCount>16
      || parsed.metadata.snapshot.fingerprintVersion!=='research_input_context_v1'
      || !object(snapshot) || !HASH.test(snapshot.contextSourceRoot)
      || snapshot.contextSourceRoot!==report.contextSourceRoot
      || !Array.isArray(report.contextSources) || report.contextSources.length!==collection.rowCount
      || snapshot.contextScope!=='named_index_series_broadcast_by_date'
      || snapshot.contextObservationClock!=='after_daily_publication_before_next_open'
      || report.contextScope!==snapshot.contextScope
      || report.contextObservationClock!==snapshot.contextObservationClock) fail();
  let previous = '';
  for (const item of report.contextSources) {
    summary(item);
    const identity = item.api+'/'+item.params.ts_code;
    if (identity<=previous) fail();
    previous=identity;
  }
}

// Input is already a strictly validated canonical JSON array of objects. Keep
// each object's exact tokens so large integers and float exponents never pass
// through JSON.stringify when checking its original records hash.
function rawSources(text) {
  const result=[];
  let start=1,depth=0,quoted=false,escaped=false;
  for(let i=1;i<text.length-1;i++) {
    const c=text[i];
    if(quoted) { if(escaped) escaped=false; else if(c==='\\') escaped=true; else if(c==='"') quoted=false; continue; }
    if(c==='"') quoted=true;
    else if(c==='{'||c==='[') depth++;
    else if(c==='}'||c===']') depth--;
    else if(c===','&&depth===0) {result.push(text.slice(start,i));start=i+1;}
  }
  result.push(text.slice(start,-1));
  return result;
}

export async function validateContextChunk(text, rows, start, summaries) {
  const raw = rawSources(text);
  if(raw.length!==rows.length || !Array.isArray(summaries)) fail();
  for(const [i,row] of rows.entries()) {
    exact(row,['api','params','fields','records','sha256','classification','notWireBytes','historicalRevisionVerified']);
    if(!Array.isArray(row.records) || row.classification!=='PARSED_PROVIDER_RESPONSE'
       || row.notWireBytes!==true || row.historicalRevisionVerified!==false) fail();
    const current = summary(Object.fromEntries(summaryKeys.map(key=>[key,key==='rowCount'?row.records.length:row[key]])));
    if(!same(current,summaries[start+i])) fail();
    let previous='';
    for(const record of row.records) {
      exact(record,row.fields);
      if(record.ts_code!==row.params.ts_code || !validDate(record.trade_date)
          || record.trade_date<=previous || record.trade_date<row.params.start_date
          || record.trade_date>row.params.end_date) fail();
      previous=record.trade_date;
      for(const field of row.fields.slice(2)) {
        const value=record[field];
        if(value!==null && (typeof value!=='number' || !Number.isFinite(value)
            || (field==='close'&&value<=0) || (['vol','amount','total_mv','float_mv'].includes(field)&&value<0))) fail();
      }
    }
    // In the exact, sorted source object records is followed by sha256; nested
    // records have only the registered numerical field names checked above.
    const begin=raw[i].indexOf(',"records":')+11, end=raw[i].lastIndexOf(',"sha256":');
    if(begin<11 || end<begin || await sha(raw[i].slice(begin,end))!==row.sha256) fail();
  }
}

export async function verifyContextRoot(parsed, readChunk) {
  const collection=parsed.collections.get('snapshotContextSources');
  if(!collection) return;
  const digest=new crypto.DigestStream('SHA-256'), writer=digest.getWriter(), encoder=new TextEncoder();
  try {
    await writer.write(encoder.encode('['));
    for(const descriptor of collection.chunks) {
      const bytes=await readChunk(collection.id,descriptor);
      if(bytes[0]!==91||bytes[bytes.length-1]!==93) fail();
      if(descriptor.ordinal) await writer.write(encoder.encode(','));
      await writer.write(bytes.subarray(1,-1));
    }
    await writer.write(encoder.encode(']')); await writer.close();
    const actual=[...new Uint8Array(await digest.digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(actual!==parsed.metadata.snapshot.provenance.contextSourceRoot) fail();
  } catch(error) {
    digest.digest.catch(()=>{});
    try { await writer.abort(error); } catch {}
    throw error;
  } finally { writer.releaseLock(); }
}
