/** Transport only: numerical observations here are explicit synthetic fixtures. */
import registry from '../../engine/atlas_quant/context_sources.json' with {type:'json'};
import {bundleFixture,canonical,hash} from './bundle-fixture.mjs';
import {COLLECTION_PATHS,OPTIONAL_CONTEXT_PATHS} from '../../edge/bundles/profile.mjs';

export function contextBundleFixture({sourceCount=2,dateCount=30,mutate=null}={}) {
  const sources=registry.items.slice(0,sourceCount).sort((a,b)=>(a.api+'/'+a.ts_code).localeCompare(b.api+'/'+b.ts_code)).map((item,sourceIndex)=> {
    const records=Array.from({length:dateCount},(_,i)=>({ts_code:item.ts_code,
      trade_date:new Date(Date.UTC(2015,0,1+i)).toISOString().slice(0,10).replaceAll('-',''),
      amount:100+i,close:3000.1234567890123+i+sourceIndex,vol:300+i}));
    return {api:item.api,params:{ts_code:item.ts_code,start_date:records[0].trade_date,end_date:records.at(-1).trade_date},
      fields:['ts_code','trade_date','amount','close','vol'],records,sha256:hash(canonical(records)),
      classification:'PARSED_PROVIDER_RESPONSE',notWireBytes:true,historicalRevisionVerified:false};
  });
  return bundleFixture({collectionPaths:{...COLLECTION_PATHS,...OPTIONAL_CONTEXT_PATHS},mutate(inputs){
    inputs.snapshot.fingerprintVersion='research_input_context_v1';
    inputs.report.provenance.contextSourceRoot=hash(canonical(sources));
    inputs.report.provenance.contextScope='named_index_series_broadcast_by_date';
    inputs.report.provenance.contextObservationClock='after_daily_publication_before_next_open';
    inputs.report.provenance.contextSources=sources.map(({api,params,fields,sha256,records})=>({api,params,fields,sha256,rowCount:records.length}));
    Object.assign(inputs.snapshot.provenance,{contextSources:sources,contextSourceRoot:hash(canonical(sources)),
      contextScope:'named_index_series_broadcast_by_date',contextObservationClock:'after_daily_publication_before_next_open'});
    if(mutate) mutate(inputs);
  }});
}
