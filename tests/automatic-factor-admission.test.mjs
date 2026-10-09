/** Local HTTP/D1 admission only: no provider, Python runner or model fitting. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from '../scripts/worker-source.mjs';

const secret='isolated-automatic-admission';
const mf=new Miniflare({modules:true,script:await buildWorkerSource({buildId:'automatic-admission-fixture'}),compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],bindings:{RUNNER_SECRET:secret}});
const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('ARTIFACTS');
await db.exec((await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8')).replaceAll('\n',' '));
const base={schemaVersion:2,name:'Admission fixture',universe:{symbols:['000001.SZ'],start:'20230101',end:'20250930'},research:{mode:'statistical_quant'},factors:[],target:{kind:'asset_price'},model:{family:'trend'},execution:{enabled:false}};
const auto=()=>({...structuredClone(base),preprocess:{automatic:{schema:'auto-factor-preprocess/1'}}});
const capabilities={factorPreprocessFormats:['auto-factor-preprocess/1'],contextSourceFormats:['named-index-history/1']};
let cookie;
const request=(path,data,runner=false)=>mf.dispatchFetch('https://atlas.test/quant/api'+path,{method:data===undefined?'GET':'POST',headers:{...(data===undefined?{}:{'content-type':'application/json'}),...(runner?{authorization:'Bearer '+secret}:cookie?{cookie}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
const heartbeat=async extra=>{const response=await request('/runner/heartbeat',{engineVersion:'99.0.0',...extra},true);assert.equal(response.status,200);};
const run=strategy=>request('/runs',{strategy,dataSource:'demo'});
async function rejected(response,status,code){assert.equal(response.status,status);const body=await response.json();assert.equal(body.error.code,code);return body.error;}
async function noReservation(){
  for(const table of ['jobs','runner_claims','quant_runs']) assert.equal((await db.prepare('SELECT count(*) n FROM '+table).first()).n,0,table+' stays empty');
  assert.equal((await bucket.list()).objects.length,0,'no source or result artifacts are written');
}
test.beforeEach(async()=>{
  await db.batch(['runner_claims','quant_runs','jobs','quant_experiment_versions','quant_experiments','meta','rate_buckets'].map(table=>db.prepare('DELETE FROM '+table)));
  cookie=undefined;const session=await request('/session');cookie=session.headers.get('set-cookie').split(';')[0];
});
test.after(()=>mf.dispose());

test('automatic run requires a fresh exact capability before experiment or job creation',async()=>{
  await rejected(await run(auto()),503,'RUNNER_OFFLINE');await noReservation();
  for(const fields of [{},{factorPreprocessFormats:['auto-factor-preprocess/2']},{contextSourceFormats:['named-index-history/1']}]){
    await heartbeat({factorPreprocessFormats:[],contextSourceFormats:[],...fields});
    await rejected(await run(auto()),409,'RUNNER_UPGRADE_REQUIRED');await noReservation();
    assert.equal((await db.prepare('SELECT count(*) n FROM quant_experiments').first()).n,0);
  }
  await heartbeat(capabilities);
  await db.prepare("UPDATE meta SET updated_at=? WHERE key='runner'").bind('2000-01-01T00:00:00.000Z').run();
  await rejected(await run(auto()),503,'RUNNER_OFFLINE');await noReservation();
  await heartbeat(capabilities);
  const response=await run(auto());assert.equal(response.status,202);
  const id=(await response.json()).job.id,row=await db.prepare('SELECT * FROM jobs WHERE id=?').bind(id).first();
  assert.equal(row.status,'queued');assert.deepEqual(JSON.parse(row.spec).preprocess.automatic,auto().preprocess.automatic);
});

test('registered contexts require context capability and real input; unknown aliases cannot become asset factors',async()=>{
  const strategy=auto();strategy.factors=[{id:'index',expression:'ext_ctx_000300_sh_close'}];
  await heartbeat({factorPreprocessFormats:capabilities.factorPreprocessFormats});
  await rejected(await run(strategy),409,'RUNNER_UPGRADE_REQUIRED');await noReservation();
  await heartbeat(capabilities);
  await rejected(await run(strategy),409,'CONTEXT_SOURCE_REQUIRED');await noReservation();
  for(const expression of ['ext_ctx_999999_sh_close','returns(ext_ctx_000300_sh_unknown,5)','ext_ctx_000300_sh_close + ext_ctx_801780_si_unknown']){
    const unknown={...strategy,factors:[{id:'unknown',expression}]};
    const response=await request('/statistical-quant/experiments',{strategy:unknown});assert.equal(response.status,400);
    assert.match((await response.json()).error.message,/指数因子来源未登记/);
    assert.equal((await db.prepare('SELECT count(*) n FROM quant_experiments').first()).n,0);
    await noReservation();
  }
});

test('legacy studies remain exact and runnable without adopting the automatic protocol',async()=>{
  await heartbeat({});
  const created=await request('/statistical-quant/experiments',{strategy:base});assert.equal(created.status,201);
  const experiment=(await created.json()).experiment;
  assert.equal(experiment.strategy.preprocess.automatic,undefined);
  const before=await db.prepare('SELECT spec FROM quant_experiment_versions WHERE experiment_id=? AND version=1').bind(experiment.id).first();
  const queued=await request('/statistical-quant/experiments/'+experiment.id+'/run',{version:1,dataSource:'demo'});assert.equal(queued.status,202);
  const claimed=await request('/runner/claim',{engineVersion:'0.9.1'},true);assert.equal(claimed.status,200);
  assert.equal((await claimed.json()).job.strategy.preprocess.automatic,undefined);
  const after=await db.prepare('SELECT spec FROM quant_experiment_versions WHERE experiment_id=? AND version=1').bind(experiment.id).first();
  assert.equal(after.spec,before.spec,'claim and run do not rewrite the saved protocol');
});

test('legacy and durable claims cannot reserve automatic jobs; lost capability cannot recover a running payload',async()=>{
  await heartbeat(capabilities);const queued=await run(auto());assert.equal(queued.status,202);const id=(await queued.json()).job.id;
  for(const input of [{engineVersion:'99.0.0'},{engineVersion:'99.0.0',requestId:randomUUID()}]){
    const response=await request('/runner/claim',input,true);assert.equal(response.status,200);assert.equal((await response.json()).job,null);
    const row=await db.prepare('SELECT status,lease_token FROM jobs WHERE id=?').bind(id).first();
    assert.equal(row.status,'queued');assert.equal(row.lease_token,null);
    assert.equal((await db.prepare('SELECT count(*) n FROM runner_claims').first()).n,0);
  }
  const requestId=randomUUID(),input={engineVersion:'99.0.0',requestId,...capabilities};
  const accepted=await request('/runner/claim',input,true);assert.equal(accepted.status,200);const first=(await accepted.json()).job;
  const before=await db.prepare('SELECT status,lease_token,lease_until,updated_at FROM jobs WHERE id=?').bind(id).first();
  await rejected(await request('/runner/claim',{...input,factorPreprocessFormats:[]},true),409,'RUNNER_UPGRADE_REQUIRED');
  const after=await db.prepare('SELECT status,lease_token,lease_until,updated_at FROM jobs WHERE id=?').bind(id).first();
  assert.deepEqual(after,before,'denied retry does not renew, rotate or abandon the existing lease');
  const recovered=await request('/runner/claim',input,true);assert.equal(recovered.status,200);assert.equal((await recovered.json()).job.leaseToken,first.leaseToken);
});

test('malformed declarations and explicit revocation cannot preserve an automatic capability',async()=>{
  await heartbeat({});
  for(const factorPreprocessFormats of ['auto-factor-preprocess/1',{0:'auto-factor-preprocess/1'},['auto-factor-preprocess/1',null]]){
    await rejected(await request('/runner/heartbeat',{engineVersion:'99.0.0',factorPreprocessFormats},true),400,'INVALID_RUNNER_CAPABILITY');
    await rejected(await run(auto()),409,'RUNNER_UPGRADE_REQUIRED');await noReservation();
  }
  await heartbeat(capabilities);await heartbeat({factorPreprocessFormats:[]});await heartbeat({state:'busy'});
  await rejected(await run(auto()),409,'RUNNER_UPGRADE_REQUIRED');await noReservation();
});
