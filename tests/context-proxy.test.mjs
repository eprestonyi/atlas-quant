import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Miniflare} from 'miniflare';
const entry=await fs.readFile(new URL('../edge/portal-entry.mjs',import.meta.url),'utf8');
const registry=JSON.parse(await fs.readFile(new URL('../engine/atlas_quant/context_sources.json',import.meta.url),'utf8'));
const upstream=[];
const mf=new Miniflare({modules:[{type:'ESModule',path:'portal-entry.mjs',contents:entry},{type:'ESModule',path:'worker.js',contents:'export default {fetch(){return new Response("portal")}}'}],compatibilityDate:'2026-08-01',bindings:{ATLAS_QUANT_SERVICE_SECRET:'context-offline',TUSHARE_TOKEN:'fixture-only'},outboundService:async req=>{upstream.push(await req.json());return Response.json({code:0,data:{fields:['ts_code'],items:[]}});}});
const request=(api,params)=>mf.dispatchFetch('https://atlas-aletheia.com/api/internal/atlas-quant/tushare',{method:'POST',headers:{authorization:'Bearer context-offline'},body:JSON.stringify({api_name:api,params,fields:'ts_code,trade_date,close'})});
test.after(()=>mf.dispose());
test('proxy admits exactly the same registered index identities as the engine',async()=>{
 for(const source of registry.items){const before=upstream.length;const r=await request(source.api,{ts_code:source.ts_code,start_date:'20230101',end_date:'20250101'});assert.equal(r.status,200);assert.equal(upstream.length,before+1);assert.equal(upstream.at(-1).api_name,source.api);assert.equal(upstream.at(-1).params.ts_code,source.ts_code);assert.equal(upstream.at(-1).token,'fixture-only');}
});
test('proxy rejects wrong source, invalid dates and broad requests before any upstream call',async()=>{
 const before=upstream.length;
 for(const [api,params] of [
  ['index_daily',{ts_code:'000001.SZ',start_date:'20230101',end_date:'20250101'}],
  ['sw_daily',{ts_code:'000300.SH',start_date:'20230101',end_date:'20250101'}],
  ['index_daily',{ts_code:'000300.SH',start_date:'20230230',end_date:'20250101'}],
  ['index_daily',{ts_code:'000300.SH',start_date:'20000101',end_date:'20250101'}],
  ['index_daily',{ts_code:'000300.SH',trade_date:'20250101'}],
  ['sw_daily',{start_date:'20230101',end_date:'20250101'}],
  ['sw_daily',{ts_code:'850112.SI',start_date:'20230101',end_date:'20250101'}],
  ['sw_daily',{ts_code:'850816.SI',start_date:'20230101',end_date:'20250101'}],
  ['sw_daily',{ts_code:'XSD',start_date:'20230101',end_date:'20250101'}],
  ['us_daily_adj',{ts_code:'AAPL',start_date:'20230101',end_date:'20250101'}],
  ['us_daily_adj',{ts_code:'XSD',trade_date:'20250101'}],
 ])assert.equal((await request(api,params)).status,400);
 assert.equal(upstream.length,before);
});
