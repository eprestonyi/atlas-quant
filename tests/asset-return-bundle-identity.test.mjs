/** Real local D1 queries, synthetic records; no provider or research execution. */
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from '../scripts/worker-source.mjs';

const script = await buildWorkerSource({wrapper:`
import {recordIndex,indexStatements} from './edge/bundles/records.mjs';
import {verifyRecords} from './edge/bundles/verify.mjs';
export default {async fetch(req,env) {
 try {
  const p=await req.json(),id=crypto.randomUUID(),lease=crypto.randomUUID(),now=new Date().toISOString();
  await env.DB.prepare('INSERT INTO jobs(id,owner,name,status,data_source,spec,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)').bind(id,'fixture','return','running','demo','{}',lease,new Date(Date.now()+120000).toISOString(),now,now).run();
  await env.DB.prepare('INSERT INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(id,'fixture',id,lease,'a'.repeat(64),'{}','fixture','{}','staging',now,now).run();
  const stage={id,metadata:'{}'},rows=p.rows,collections=new Map();
  for(const [collection,items] of Object.entries(rows)) {
    collections.set(collection,{id:collection,rowCount:items.length,chunks:[]});
    const indexes=await Promise.all(items.map((row,i)=>recordIndex(collection,row,i,0,i,{returnStudy:{schema:'asset-return-study/1',mode:'forecast'},returnFactorNames:['factor:market']})));
    if(indexes.length) await env.DB.batch(indexStatements(env,stage,collection,indexes));
  }
  const parsed={collections,metadata:{coverage:{baselineRequired:true},forecast:{studyProtocol:'asset-return-study/1',factorResearch:{panel:{dates:['20260105'],symbols:['000001.SZ']}}}}};
  await verifyRecords(env,stage,parsed); return Response.json({ok:true});
 }catch(error){return Response.json({code:error.code,message:error.message},{status:409});}
}};`});
const mf = new Miniflare({modules:true,script,compatibilityDate:'2026-08-01',d1Databases:['DB']});
await (await mf.getD1Database('DB')).exec((await readFile(new URL('../edge/schema.sql',import.meta.url),'utf8')).replaceAll('\n',' '));
after(()=>mf.dispose());

function records() {
  const observation = {schema:'asset-return-observation/1',forecastId:'prediction',date:'20260105',assetSymbol:'000001.SZ',targetId:'asset1',modelFitId:'fit1',featureDate:'20260105',responseStartDate:'20260105',responseEndDate:'20260106',informationCutoff:'20260105_AFTER_CLOSE',status:'valid',inputValid:true,originPrice:100,responseScale:.02,predictedResponse:.5,predictedReturn:.01,conditionalPrice:101,observedReturn:.012,observedResponse:.6,responseResidual:.1,labelMaturedAt:'20260106'};
  const fit = {id:'fit1',targetId:'asset1',targetSymbol:'000001.SZ',status:'valid'};
  return {
    targets:[{id:'asset1',kind:'asset_return',symbols:['000001.SZ'],construction:'independent_asset_close_to_close'}],
    forecasts:[observation],baselineRows:[{...observation,forecastId:'baseline'}],modelFits:[fit],baselineModelFits:[{...fit}],
    plannedOrigins:[{date:observation.date,targetId:'asset1',inputValid:true,responseStartDate:observation.responseStartDate,responseEndDate:observation.responseEndDate}],
    researchPanel:[{schema:'asset-return-panel-row/1',date:observation.date,targetId:'asset1',assetSymbol:'000001.SZ',inputValid:true,invalidReason:null,featureDate:observation.date,responseStartDate:observation.responseStartDate,responseEndDate:observation.responseEndDate,originPrice:100,responseScale:.02,observedReturn:.012,observedResponse:.6,features:{'factor:market':.01}}],
    equity:[],riskLedger:[]
  };
}
async function check(rows) {
  const response = await mf.dispatchFetch('https://return.test/',{method:'POST',body:JSON.stringify({rows})});
  return {status:response.status,...await response.json()};
}

test('complete scalar records pass real D1 asset identity and coverage queries',async () => {
  const result = await check(records()); assert.equal(result.status,200,JSON.stringify(result));
});
for (const collection of ['forecasts','baselineRows','researchPanel']) test(collection+' cannot relabel a target as another stock',async () => {
  const rows = records(); rows[collection][0].assetSymbol = '600519.SH';
  const result = await check(rows); assert.equal(result.status,409); assert.match(result.message,/证券与逐资产目标/);
});
test('a forecast cannot use a different asset function even when row stock label is correct',async () => {
  const rows = records(); rows.modelFits[0].targetId = 'asset2';
  const result = await check(rows); assert.equal(result.status,409); assert.match(result.message,/另一资产的参数/);
});
