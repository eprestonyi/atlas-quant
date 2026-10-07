import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {Miniflare} from 'miniflare';

const validation=(await fs.readFile(new URL('../edge/validation.mjs',import.meta.url),'utf8')).replace(/^import .* from '\.\/universe\.mjs';\n/,'');
const universe=await fs.readFile(new URL('../edge/universe.mjs',import.meta.url),'utf8');
const worker=(await fs.readFile(new URL('../edge/worker.mjs',import.meta.url),'utf8')).replace(/^import .* from '\.\/validation\.mjs';\n/,'');
const studio=await fs.readFile(new URL('../edge/studio.mjs',import.meta.url),'utf8');
const script='const RESEARCH_PRESETS={};const WEB_ASSETS={"index.html":{body:"Atlas Quant",type:"text/html"}};const CATALOG={factors:[],models:[],templates:[]};const BUILD_ID="queue-race-test";\n'+validation+'\n'+universe+'\n'+studio+'\n'+worker;
const secret='queue-test-only-secret-not-production';
const mf=new Miniflare({modules:true,script,compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],bindings:{RUNNER_SECRET:secret}});
const db=await mf.getD1Database('DB');
const bucket=await mf.getR2Bucket('ARTIFACTS');
await db.exec((await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8')).replaceAll('\n',' '));
const origin='https://atlas.test';
const strategy={schemaVersion:1,name:'队列验收',universe:{symbols:['000001.SZ','000002.SZ','600000.SH'],start:'20230101',end:'20260930'},factors:[{id:'momentum_20',expression:'returns(close,20)',direction:1}],preprocess:{winsorize:true,standardize:true},model:{mode:'auto',candidates:['factor_score','ridge'],horizon:5,metric:'rank_ic'},portfolio:{topN:2,maxWeight:.4,rebalanceDays:5,initialCapital:1000000},costs:{commissionBps:3,slippageBps:10,sellTaxBps:5},graph:{nodes:[],edges:[]}};
function report(value=1){return {schemaVersion:1,status:'completed',engineVersion:'race-test',strategy,provenance:{source:'SYNTHETIC'},selection:{winner:'ridge',holdoutUsedForSelection:false},metrics:{totalReturn:value/100,totalCosts:25},equity:[{date:'20260930',equity:100+value,benchmark:100,drawdown:0}],trades:[],factors:[]};}
function request(path,{method='POST',data={},cookie,runner=false}={}){
 const headers={'content-type':'application/json'};
 if(cookie)headers.cookie=cookie;
 if(runner)headers.authorization='Bearer '+secret;
 return mf.dispatchFetch(origin+'/quant/api'+path,{method,headers,body:method==='GET'?undefined:JSON.stringify(data)});
}
async function session(){const response=await request('/session',{method:'GET'});assert.equal(response.status,200);return response.headers.get('set-cookie').split(';')[0];}
async function claim(cookie){const created=await request('/runs',{cookie,data:{strategy,dataSource:'demo'}});assert.equal(created.status,202);const response=await request('/runner/claim',{runner:true});assert.equal(response.status,200);const {job}=await response.json();assert.ok(job);return job;}
async function completion(job,result=report()){return request('/runner/complete',{runner:true,data:{id:job.id,leaseToken:job.leaseToken,result}});}
test.after(async()=>{await mf.dispose();});

test('concurrent exact completion retries preserve committed R2 bytes',async()=>{
 const cookie=await session(),job=await claim(cookie),result=report();
 const responses=await Promise.all(Array.from({length:12},()=>completion(job,result)));
 assert.ok(responses.every(response=>response.status===200));
 const row=await db.prepare('SELECT status,result_key FROM jobs WHERE id=?').bind(job.id).first();
 assert.equal(row.status,'completed');
 assert.match(row.result_key,/\/[a-f0-9]{64}\.json$/);
 const stored=await bucket.get(row.result_key);
 assert.ok(stored,'committed result must survive losing CAS retry');
 assert.deepEqual(await stored.json(),result);
 const view=await (await request('/runs/'+job.id,{method:'GET',cookie})).json();
 assert.deepEqual(view.result,result);
 assert.ok(!JSON.stringify(view).includes(job.leaseToken));
});

test('competing distinct completion payloads cannot overwrite winning report',async()=>{
 const cookie=await session(),job=await claim(cookie);
 const values=Array.from({length:8},(_,i)=>report(i+1));
 const responses=await Promise.all(values.map(value=>completion(job,value)));
 assert.ok(responses.some(r=>r.status===200));
 assert.ok(responses.every(r=>r.status===200||r.status===409));
 const row=await db.prepare('SELECT status,result_key FROM jobs WHERE id=?').bind(job.id).first();
 const stored=await bucket.get(row.result_key);assert.ok(stored);
 const value=await stored.json();
 assert.ok(values.some(candidate=>JSON.stringify(candidate)===JSON.stringify(value)));
 assert.equal((await completion(job,value)).status,200);
 const conflict=await completion(job,report(99));assert.equal(conflict.status,409);
 assert.deepEqual(await (await bucket.get(row.result_key)).json(),value);
});

test('cancelled delivery is acknowledged without resurrecting terminal state',async()=>{
 const cookie=await session(),job=await claim(cookie);
 assert.equal((await request('/runs/'+job.id+'/cancel',{cookie})).status,200);
 const beat=await (await request('/runner/heartbeat',{runner:true,data:{id:job.id,leaseToken:job.leaseToken}})).json();
 assert.equal(beat.leaseValid,false);
 const response=await request('/runner/complete',{runner:true,data:{id:job.id,leaseToken:job.leaseToken,error:{code:'JOB_CANCELLED',message:'cancelled'}}});
 assert.equal(response.status,200);
 assert.deepEqual(await response.json(),{ok:true,status:'cancelled',ignored:true});
 const row=await db.prepare('SELECT status,result_key FROM jobs WHERE id=?').bind(job.id).first();
 assert.equal(row.status,'cancelled');assert.equal(row.result_key,null);
 const next=await claim(cookie);assert.notEqual(next.id,job.id);
 await completion(next);
});

test('expired computation delivery is permanently acknowledged for spool recovery',async()=>{
 const cookie=await session(),job=await claim(cookie);
 await db.prepare('UPDATE jobs SET lease_until=? WHERE id=?').bind('2000-01-01T00:00:00.000Z',job.id).run();
 assert.equal((await (await request('/runner/claim',{runner:true})).json()).job,null);
 const response=await completion(job);
 assert.equal(response.status,200);
 const receipt=await response.json();assert.equal(receipt.ignored,true);assert.equal(receipt.status,'failed');
 const row=await db.prepare('SELECT status,error,result_key FROM jobs WHERE id=?').bind(job.id).first();
 assert.equal(row.status,'failed');assert.equal(JSON.parse(row.error).code,'RUNNER_INTERRUPTED');assert.equal(row.result_key,null);
 const next=await claim(cookie);await completion(next);
});

test('streaming request cap counts UTF-8 bytes without Content-Length',async()=>{
 const cookie=await session();
 const bytes=new TextEncoder().encode(JSON.stringify({strategy,note:'界'.repeat(70000)}));
 const stream=new ReadableStream({start(controller){controller.enqueue(bytes);controller.close();}});
 const response=await mf.dispatchFetch(origin+'/quant/api/strategies',{method:'POST',headers:{cookie,'content-type':'application/json'},body:stream,duplex:'half'});
 assert.equal(response.status,413);
 assert.equal((await response.json()).error.code,'TOO_LARGE');
});

test('real Python runner claims, computes, delivers and exposes private report end to end',{timeout:90000},async()=>{
 const cookie=await session();
 const fullStrategy=structuredClone(strategy);
 fullStrategy.model.candidates=['factor_score','ridge','elastic_net','hist_gradient_boosting'];
 const created=await (await request('/runs',{cookie,data:{strategy:fullStrategy,dataSource:'demo'}})).json();
 assert.ok(created.job?.id);
 const root=path.resolve(import.meta.dirname,'..');
 const python=path.join(root,'.venv/bin/python');
 const deliveryDir=await fs.mkdtemp(path.join(os.tmpdir(),'atlas-quant-e2e-'));
 const listen=await mf.ready;
 const config={api_base:new URL('/quant/api',listen).href,runner_secret:secret,delivery_dir:deliveryDir,job_timeout:60,poll_seconds:3};
 const code="import os,json; from atlas_quant.runner import serve; raise SystemExit(serve(json.loads(os.environ['ATLAS_QUANT_TEST_CONFIG']),once=True))";
 try{
  const exit=await new Promise((resolve,reject)=>{
   const child=spawn(python,['-c',code],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'engine'),ATLAS_QUANT_TEST_CONFIG:JSON.stringify(config)},stdio:['ignore','pipe','pipe']});
   let output='';child.stdout.on('data',data=>{output+=data;});child.stderr.on('data',data=>{output+=data;});
   const timer=setTimeout(()=>{child.kill('SIGKILL');reject(new Error('Python runner E2E timed out'));},75000);
   child.on('error',error=>{clearTimeout(timer);reject(error);});
   child.on('exit',status=>{clearTimeout(timer);resolve({status,output});});
  });
  assert.equal(exit.status,0,exit.output);
  const response=await request('/runs/'+created.job.id,{method:'GET',cookie});assert.equal(response.status,200);
  const {job,result}=await response.json();
  assert.equal(job.status,'completed');assert.equal(result.provenance.source,'SYNTHETIC');
  assert.equal(result.selection.holdoutUsedForSelection,false);assert.equal(result.selection.candidates.length,4);
  assert.ok(result.equity.length>100);assert.ok(result.trades.length>0);
  assert.equal((await fs.readdir(deliveryDir)).filter(p=>p.endsWith('.enc')).length,0);
  const another=await session();assert.equal((await request('/runs/'+job.id,{method:'GET',cookie:another})).status,404);
 }finally{await fs.rm(deliveryDir,{recursive:true,force:true});}
});

test('published factor details and forks remain addressable beyond latest 300',async()=>{
 const statements=Array.from({length:305},(_,i)=>db.prepare("INSERT INTO factors(id,owner,name,description,expression,direction,category,author,license,status,metadata,created_at) VALUES(?,?,?,?,?,1,'community','Test','MIT','published','{}',?)").bind('historical_'+i,'historical-owner','Factor '+i,'Recorded factor','returns(close,20)',new Date(Date.UTC(2025,0,1,0,i)).toISOString()));
 await db.batch(statements);
 const listed=await (await request('/factors',{method:'GET'})).json();
 assert.equal(listed.items.length,300);
 assert.ok(!listed.items.some(item=>item.id==='historical_0'));
 const detail=await request('/factors/historical_0',{method:'GET'});
 assert.equal(detail.status,200);assert.equal((await detail.json()).item.id,'historical_0');
 const cookie=await session();
 const fork={name:'Long-lived fork',description:'Fork of a factor older than catalog window',expression:'returns(close,20)',direction:1,license:'MIT',forkOf:'historical_0'};
 const created=await request('/factors',{cookie,data:fork});
 assert.equal(created.status,201);assert.equal((await created.json()).item.forkOf,'historical_0');
 await db.prepare("UPDATE factors SET status='withdrawn' WHERE id='historical_0'").run();
 assert.equal((await request('/factors/historical_0',{method:'GET'})).status,404);
 const withdrawnFork=await request('/factors',{cookie,data:fork});
 assert.equal(withdrawnFork.status,400);assert.equal((await withdrawnFork.json()).error.code,'INVALID_FORK');
});

test('saved strategies project every nested field before reports can echo credentials',async()=>{
 const cookie=await session(),input=structuredClone(strategy);
 input.token='must-be-stripped';
 for(const key of ['universe','preprocess','model','portfolio','costs','graph'])input[key].serviceToken='must-be-stripped';
 input.factors[0].authorization='must-be-stripped';
 input.factors[0].version=2;
 const stages=['universe','factors','preprocess','model','portfolio','backtest'];
 input.graph.nodes=stages.map((stage,i)=>({id:stage,type:stage,x:20*i,y:10,token:'must-be-stripped'}));
 input.graph.edges=stages.slice(1).map((stage,i)=>({source:stages[i],target:stage,password:'must-be-stripped'}));
 const response=await request('/strategies',{cookie,data:{strategy:input}});
 assert.equal(response.status,201);
 const saved=(await response.json()).item.strategy;
 assert.ok(!JSON.stringify(saved).includes('must-be-stripped'));
 assert.equal(saved.factors[0].version,2);
 assert.equal(saved.graph.nodes.length,6);assert.equal(saved.graph.edges.length,5);
 const created=await request('/runs',{cookie,data:{strategy:input,dataSource:'demo'}});
 assert.equal(created.status,202);
 const claimed=(await (await request('/runner/claim',{runner:true})).json()).job;
 assert.deepEqual(claimed.strategy,saved);
 await completion(claimed);
});

test('global queue bound survives simultaneous workspace admissions',async()=>{
 // Separate IP keys only avoid the unrelated session-creation limiter in this test.
 const cookies=await Promise.all(Array.from({length:35},async(_,i)=>{
  const response=await mf.dispatchFetch(origin+'/quant/api/session',{headers:{'cf-connecting-ip':'192.0.2.'+(i+1)}});
  assert.equal(response.status,200);return response.headers.get('set-cookie').split(';')[0];
 }));
 const responses=await Promise.all(cookies.map(cookie=>request('/runs',{cookie,data:{strategy,dataSource:'demo'}})));
 const accepted=responses.filter(r=>r.status===202);
 assert.equal(accepted.length,30);
 const row=await db.prepare("SELECT count(*) AS n FROM jobs WHERE status IN ('queued','running')").first();
 assert.equal(row.n,30);
});
