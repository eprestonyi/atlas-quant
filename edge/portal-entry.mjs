// Additive integration: the deployed Portal module remains byte-for-byte intact.
import portal from './worker.js';
export * from './worker.js';
const HOSTS=new Set(['atlas-aletheia.com','www.atlas-aletheia.com']);
const PROVIDER_PARAMS={daily:['ts_code','start_date','end_date','trade_date'],adj_factor:['ts_code','start_date','end_date','trade_date'],trade_cal:['exchange','start_date','end_date','is_open'],daily_basic:['ts_code','start_date','end_date','trade_date'],stock_basic:['ts_code','name','exchange','market','is_hs','list_status'],index_basic:['ts_code','name','market','publisher','category'],index_classify:['index_code','level','src'],index_member_all:['l1_code','l2_code','l3_code','ts_code','is_new'],index_weight:['index_code','trade_date','start_date','end_date'],fina_indicator:['ts_code','ann_date','start_date','end_date','period'],income:['ts_code','ann_date','start_date','end_date','period','report_type','comp_type'],balancesheet:['ts_code','ann_date','start_date','end_date','period','report_type','comp_type'],cashflow:['ts_code','ann_date','start_date','end_date','period','report_type','comp_type']};
const PROVIDER_APIS=new Set(Object.keys(PROVIDER_PARAMS));
const digest=async value=>new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)));
async function authorized(header,secret){if(!secret)return false;const a=await digest(header||''),b=await digest('Bearer '+secret);let difference=0;for(let i=0;i<a.length;i++)difference|=a[i]^b[i];return difference===0;}
const response=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}});
async function limitedJson(response,max){const reader=response.body?.getReader(),decoder=new TextDecoder('utf-8',{fatal:true});let text='',bytes=0;try{if(reader)while(true){const {value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>max){await reader.cancel();throw Error('SIZE');}text+=decoder.decode(value,{stream:true});}text+=decoder.decode();return JSON.parse(text);}finally{reader?.releaseLock();}}
async function personalProvider(request,env){
 if(request.method!=='POST')return response({code:-1,msg:'POST required'},405);
 if(!await authorized(request.headers.get('authorization'),env.ATLAS_QUANT_SERVICE_SECRET))return response({code:-1,msg:'Unauthorized'},401);
 if(!env.TUSHARE_TOKEN)return response({code:-1,msg:'Provider not configured'},503);
 let input;try{input=await limitedJson(request,12000);}catch{return response({code:-1,msg:'Invalid request'},400);}
 if(!input||typeof input!=='object'||Array.isArray(input)||!PROVIDER_APIS.has(input.api_name)||!input.params||typeof input.params!=='object')return response({code:-1,msg:'Unsupported provider request'},400);
 const allowed=new Set(PROVIDER_PARAMS[input.api_name]);
 for(const [key,value]of Object.entries(input.params)){if(!allowed.has(key)||!['string','number'].includes(typeof value)||String(value).length>40)return response({code:-1,msg:'Invalid parameters'},400);if((key==='ts_code'||key==='index_code'||/l[123]_code/.test(key))&&!/^\d{6}\.(SH|SZ|BJ|SI|CSI)$/.test(value))return response({code:-1,msg:'Invalid symbol'},400);if((key.includes('date')||key==='period')&&!/^\d{8}$/.test(value))return response({code:-1,msg:'Invalid date'},400);}
 if(typeof input.fields!=='string'||input.fields.length>2000||!/^(?:[a-z_][a-z0-9_]*(?:,[a-z_][a-z0-9_]*)*)?$/.test(input.fields))return response({code:-1,msg:'Invalid fields'},400);
 try{const upstream=await fetch('https://api.tushare.pro',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({api_name:input.api_name,params:input.params,fields:input.fields,token:env.TUSHARE_TOKEN}),redirect:'manual',signal:AbortSignal.timeout(30000)});if(!upstream.ok){let redirectOrigin=null;try{redirectOrigin=new URL(upstream.headers.get('location')).origin;}catch{}return response({code:-1,msg:'Provider unavailable',upstreamStatus:upstream.status,redirectOrigin},502);}const data=await limitedJson(upstream,12*1024*1024);if(!data||typeof data!=='object')throw Error('PROVIDER_JSON');if(data.code!==0){const message=String(data.msg||'');return response({code:data.code,msg:/权限|permission|积分/i.test(message)?'权限不足，请核对 Tushare 接口积分与授权':/频率|每分钟|rate|limit/i.test(message)?'接口请求频率限制，请稍后重试':'上游数据接口返回错误'});}return response({code:0,msg:'',data:data.data});}catch(e){return response({code:-1,msg:'Provider unavailable',diagnostic:['AbortError','TimeoutError','TypeError','SyntaxError'].includes(e.name)?e.name:(e.message==='SIZE'?'RESPONSE_SIZE':'PROVIDER_RESPONSE')},502);}
}
export default {
 ...portal,
 async fetch(request,env,ctx){const url=new URL(request.url),atlas=HOSTS.has(url.hostname);
 if(atlas&&url.pathname==='/api/internal/atlas-quant/tushare')return personalProvider(request,env);
 if(atlas&&(url.pathname==='/quant'||url.pathname.startsWith('/quant/'))){if(!env.ATLAS_QUANT)return response({error:{code:'UNAVAILABLE',message:'Atlas Quant 暂时不可用'}},503);return env.ATLAS_QUANT.fetch(request);}
 const result=await portal.fetch(request,env,ctx);
 if(atlas&&request.method==='GET'&&/^\/(?:(?:cn|en)\/)?terminal(?:\.html)?(?:\/|$)/i.test(url.pathname)&&result.status===200&&result.headers.get('content-type')?.includes('text/html'))return new HTMLRewriter().on('head',{element(el){el.append('<script src="/quant/atlas-link.js" defer></script>',{html:true});}}).transform(result);
 return result;
 }
};
