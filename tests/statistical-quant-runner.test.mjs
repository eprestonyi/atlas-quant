import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from '../scripts/worker-source.mjs';
import {universeRegistryStatements} from '../scripts/universe-registry.mjs';

const root=path.resolve(import.meta.dirname,'..');
async function runOnce(config) {
 const code="import os,json; from atlas_quant.runner import serve; raise SystemExit(serve(json.loads(os.environ['ATLAS_QUANT_TEST_CONFIG']),once=True))";
 return new Promise((resolve,reject)=>{
  const child=spawn(path.join(root,'.venv/bin/python'),['-c',code],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'engine'),ATLAS_QUANT_TEST_CONFIG:JSON.stringify(config)},stdio:['ignore','pipe','pipe']});
  let output='';child.stdout.on('data',x=>output+=x);child.stderr.on('data',x=>output+=x);
  const timer=setTimeout(()=>{child.kill('SIGKILL');reject(Error('Forecast/replay E2E exceeded 85 seconds'));},85000);
  child.on('error',e=>{clearTimeout(timer);reject(e);});child.on('exit',status=>{clearTimeout(timer);resolve({status,output});});
 });
}

test('actual catalog rules survive save/update/claim/Python forecast and immutable execution replay',{timeout:180000},async()=>{
 const secret='isolated-forecast-e2e-not-production';
 const mf=new Miniflare({modules:true,script:await buildWorkerSource(),compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],bindings:{RUNNER_SECRET:secret}});
 const delivery=await fs.mkdtemp(path.join(os.tmpdir(),'atlas-forecast-e2e-'));
 try {
  const db=await mf.getD1Database('DB');await db.exec((await fs.readFile(path.join(root,'edge/schema.sql'),'utf8')).replaceAll('\n',' '));
  const catalog=JSON.parse(await fs.readFile(path.join(root,'data/universes.json'),'utf8'));
  for(let i=0;i<catalog.items.length;i+=50) await db.batch(catalog.items.slice(i,i+50).map(u=>db.prepare('INSERT INTO research_universes(id,name,category,symbol_count,search_text,metadata) VALUES(?,?,?,?,?,?)').bind(u.id,u.name,u.category,u.symbols.length,u.name,JSON.stringify(u))));
  for(const sql of universeRegistryStatements(catalog).statements) await db.exec(sql);
  const listen=await mf.ready,origin=new URL('/quant/api',listen).href;
  const request=(p,method='GET',data,cookie)=>mf.dispatchFetch(origin+p,{method,headers:{...(cookie?{cookie}:{}),...(data===undefined?{}:{'content-type':'application/json'})},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const session=await request('/session'),cookie=session.headers.get('set-cookie').split(';')[0];
  const strategy={schemaVersion:2,name:'SYNTHETIC full transport acceptance',research:{mode:'statistical_quant',observationDays:5},universe:{symbols:['000001.SZ','600000.SH'],start:'20230101',end:'20250930'},factors:[{id:'mom',expression:'returns(close,20)',role:'predictor'}],target:{kind:'asset_price',horizonSessions:5},model:{family:'trend',estimator:'ridge',trainWindow:252,refitDays:20},validation:{minTrainDates:40,innerFolds:2,outerFolds:2},execution:{enabled:false}};
  const selection={version:1,includeGroups:[{id:'actual_banks',name:'Current actual banking identities',filters:[{field:'industry',value:'银行'}]}]};
  const resolvedResponse=await request('/universes/resolve','POST',{selection},cookie);assert.equal(resolvedResponse.status,200);
  const resolved=await resolvedResponse.json();assert.ok(resolved.symbols.length>2);assert.ok(strategy.universe.symbols.every(symbol=>resolved.symbols.includes(symbol)));
  const compactSnapshot=({hash,asOf,historicalMembershipVerified})=>({hash,asOf,historicalMembershipVerified});
  Object.assign(strategy.universe,{selection:resolved.selection,resolutionHash:resolved.resolutionHash,snapshotHash:resolved.snapshotHash,subsetPolicy:'explicit',catalogSnapshot:compactSnapshot(resolved.catalogSnapshot)});
  const rawCandidate=structuredClone(strategy);rawCandidate.universe.catalogSnapshot=resolved.catalogSnapshot;
  assert.equal((await request('/statistical-quant/experiments','POST',{strategy:rawCandidate},cookie)).status,400,'public unknown snapshot fields remain strict');
  const saved=await request('/statistical-quant/experiments','POST',{strategy},cookie);assert.equal(saved.status,201);const experiment=(await saved.json()).experiment;
  assert.deepEqual(Object.keys(experiment.strategy.universe.catalogSnapshot).sort(),['asOf','hash','historicalMembershipVerified']);
  strategy.name='SYNTHETIC full transport acceptance revision 2';
  const updated=await request('/statistical-quant/experiments/'+experiment.id,'PUT',{strategy,version:1},cookie);assert.equal(updated.status,200);assert.equal((await updated.json()).experiment.version,2);
  // Model a previously saved server-generated candidate, without broadening
  // public input or rewriting any immutable forecast artifact.
  const storedCandidate=structuredClone(strategy);storedCandidate.universe.catalogSnapshot=resolved.catalogSnapshot;
  await db.prepare('UPDATE quant_experiments SET spec=? WHERE id=?').bind(JSON.stringify(storedCandidate),experiment.id).run();
  const restored=await (await request('/statistical-quant/experiments/'+experiment.id,'GET',undefined,cookie)).json();assert.deepEqual(restored.experiment.strategy.universe.catalogSnapshot,compactSnapshot(resolved.catalogSnapshot));
  const started=await request('/statistical-quant/experiments/'+experiment.id+'/run','POST',{version:2,dataSource:'demo'},cookie);assert.equal(started.status,202);const job=(await started.json()).job;
  const queued=JSON.parse((await db.prepare('SELECT spec FROM jobs WHERE id=?').bind(job.id).first()).spec);assert.deepEqual(queued.universe.catalogSnapshot,compactSnapshot(resolved.catalogSnapshot));
  const config={api_base:origin,runner_secret:secret,delivery_dir:delivery,job_timeout:75,poll_seconds:3};
  const first=await runOnce(config);assert.equal(first.status,0,first.output);
  const completed=await (await request('/runs/'+job.id,'GET',undefined,cookie)).json();assert.equal(completed.job.status,'completed',JSON.stringify(completed.job.error));assert.equal(completed.result.metrics,null);assert.equal(completed.result.forecasts.truncated,false);assert.ok(completed.result.forecasts.rows.length>5);assert.deepEqual(completed.result.strategy.universe.catalogSnapshot,compactSnapshot(resolved.catalogSnapshot));assert.deepEqual(completed.result.forecasts.sourceStrategy.universe.catalogSnapshot,compactSnapshot(resolved.catalogSnapshot));
  const artifactId=completed.result.forecasts.artifactId;
  const detail=await (await request('/statistical-quant/experiments/'+experiment.id,'GET',undefined,cookie)).json();assert.equal(detail.forecasts[0].id,artifactId);
  const replay=await request('/statistical-quant/executions','POST',{forecastArtifactId:artifactId,execution:{minEdgeBps:20},costs:{commissionBps:7}},cookie);assert.equal(replay.status,202);const execution=(await replay.json()).execution;
  const second=await runOnce(config);assert.equal(second.status,0,second.output);
  const output=await (await request('/statistical-quant/executions/'+execution.id,'GET',undefined,cookie)).json();assert.equal(output.execution.status,'completed',JSON.stringify(output.execution.error));assert.equal(output.result.research.executionOnly,true);assert.equal(output.result.research.predictionRefitPerformed,false);assert.equal(output.result.forecasts.artifactId,artifactId);assert.deepEqual(output.result.forecasts,completed.result.forecasts);
  assert.equal(output.result.strategy.costs.commissionBps,7);assert.equal((await fs.readdir(delivery)).filter(x=>x.endsWith('.enc')).length,0);
 } finally {await mf.dispose();await fs.rm(delivery,{recursive:true,force:true});}
});
