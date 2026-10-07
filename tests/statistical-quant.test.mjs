import {cleanupFailedForecastSnapshots} from '../edge/statistical-quant/persistence.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {buildWorkerSource} from '../scripts/worker-source.mjs';
import {validateStatisticalQuant,validateStoredStatisticalQuant} from '../edge/statistical-quant/validation.mjs';
import {moduleDefinitions,recipeDefinitions} from '../edge/statistical-quant/modules.mjs';
import {researchPresets} from '../scripts/presets.mjs';

const base={schemaVersion:2,name:'SYNTHETIC forecast contract fixture',universe:{symbols:['000001.SZ','600000.SH','000002.SZ'],start:'20230101',end:'20250930'},research:{mode:'statistical_quant',observationDays:1},factors:[],target:{kind:'asset_price',horizonSessions:5},model:{family:'trend',estimator:'ridge'},execution:{enabled:false},portfolio:{},costs:{}};
const normalized=validateStatisticalQuant(base);

test('schema2 accepts one asset and two-leg pairs without naked factor_score',()=>{
 assert.equal(validateStatisticalQuant({...base,universe:{...base.universe,symbols:['000001.SZ']}}).schemaVersion,2);
 const pair=validateStatisticalQuant({...base,target:{kind:'frozen_basket',basket:{method:'pair_ols',symbols:base.universe.symbols.slice(0,2)}},model:{family:'pair_reversion'}});
 assert.equal(pair.target.basket.formationDays,126);
 assert.throws(()=>validateStatisticalQuant({...base,model:{family:'trend',estimator:'factor_score'}}));
});

test('strict typed composition rejects unsupported parameters and data-free mechanism claims',()=>{
 const cases=[
  {...base,schemaVersion:1}, {...base,execution:{volatilityTarget:.1}}, {...base,execution:{enabled:0}},
  {...base,model:{family:'fundamental'}}, {...base,model:{family:'event'}}, {...base,model:{family:'pair_reversion'}},
  {...base,target:{kind:'asset_price',basket:{method:'fixed'}}}, {...base,target:{kind:'asset_price',horizonSessions:true}},
  {...base,portfolio:{grossExposure:null}}, {...base,validation:{minTrainDates:252},model:{family:'trend',trainWindow:120}},
  {...base,factors:[{id:'h',expression:'close',role:'hedge'}]}, {...base,factors:[{id:'x',expression:'close',role:'unknown'}]},
 ];
 for(const strategy of cases) assert.throws(()=>validateStatisticalQuant(strategy));
 const event=validateStatisticalQuant({...base,model:{family:'event'},factors:[{id:'event',expression:'delta(fd_roe,1)',role:'event'}]});
 assert.equal(event.factors[0].role,'event');
});

const catalog=JSON.parse(await fs.readFile(new URL('../engine/atlas_quant/catalog.json',import.meta.url)));
const oldTemplate=JSON.parse(await fs.readFile(new URL('../engine/examples/basic.json',import.meta.url)));
const presets=researchPresets(catalog,oldTemplate);
test('real module definitions and every generated recipe form a compatible concrete schema',()=>{
 const modules=moduleDefinitions(catalog),recipes=recipeDefinitions(catalog,presets.packs),moduleIds=new Set(modules.map(m=>m.id));
 assert.ok(modules.length>400);assert.ok(recipes.length>300);assert.equal(new Set(recipes.map(r=>r.id)).size,recipes.length);
 for(const item of modules) {assert.ok(item.inputType&&item.outputType);assert.ok(item.configPatch);assert.equal(item.capabilities.independentlyValidatedAlpha,false);}
 for(const recipe of recipes) {
  const strategy=structuredClone(base);Object.assign(strategy,recipe.configPatch);
  if(strategy.target.kind==='frozen_basket') strategy.target.basket.symbols=base.universe.symbols.slice(0,strategy.target.basket.method==='pair_ols'?2:3);
  validateStatisticalQuant(strategy);assert.ok(recipe.moduleIds.every(id=>moduleIds.has(id)));assert.equal(recipe.countsAs,'configuration_recipe');assert.equal(recipe.validatedAlpha,false);
 }
});

const snapshotRaceWrapper=`export default {async fetch(req,env,ctx) {
 const injected={...env,ARTIFACTS:{get:(...x)=>env.ARTIFACTS.get(...x),delete:(...x)=>env.ARTIFACTS.delete(...x),put:async(key,...args)=>{
  const race=key.startsWith('forecast-data/')?await env.DB.prepare("SELECT value FROM meta WHERE key='test_snapshot_cancel'").first():null;
  if(race) await env.DB.prepare("UPDATE jobs SET status='cancelled' WHERE id=?").bind(race.value).run();
  return env.ARTIFACTS.put(key,...args);
 }}};
 return productionWorker.fetch(req,injected,ctx);
}};`;
const script=await buildWorkerSource({catalog,presets,buildId:'statistical-quant-test',wrapper:snapshotRaceWrapper});
const mf=new Miniflare({modules:true,script,compatibilityDate:'2026-08-01',d1Databases:['DB'],r2Buckets:['ARTIFACTS'],bindings:{RUNNER_SECRET:'isolated-statistical-test-runner'}});
const db=await mf.getD1Database('DB');const schema=await fs.readFile(new URL('../edge/schema.sql',import.meta.url),'utf8');await db.exec(schema.replaceAll('\n',' '));
const origin='https://atlas.test';
const request=(path,method='GET',data,cookie,runner=false)=>mf.dispatchFetch(origin+'/quant/api'+path,{method,headers:{...(data===undefined?{}:{'content-type':'application/json'}),...(cookie?{cookie}:{}),...(runner?{authorization:'Bearer isolated-statistical-test-runner'}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
const session=async()=>{const r=await request('/session');return r.headers.get('set-cookie').split(';')[0];};
const user=await session(),other=await session();
const api=(path,method='GET',data,cookie=user)=>request('/statistical-quant'+path,method,data,cookie);
const runner=(path,data)=>request('/runner/'+path,'POST',path==='claim'?{engineVersion:'0.4.0',...data}:data,null,true);
test.after(()=>mf.dispose());

function resultFixture(strategy,artifactId='a'.repeat(64)) {
 const sourceStrategy=structuredClone(strategy);
 const rows=[0,1].map(i=>({forecastId:'forecast_'+i,date:'2025010'+(i+2),targetId:'target_0',modelFitId:'fit_0',informationCutoff:'2025010'+(i+2),entryDate:'2025010'+(i+3),targetDate:'20250110',horizonSessions:5,currentState:10,scale:10,expectedEntry:10,expectedFuture:10.1,edgeGap:-.1,expectedChange:.1,expectedGrossPnl:.1,expectedGrossBps:100,realizedEntry:10,realizedFuture:10.05,forecastError:-.05,labelMaturedAt:'20250110',status:'valid',invalidReason:null,uncertainty:null}));
 const artifact={schemaVersion:1,artifactId,predictionConfigHash:'b'.repeat(64),dataFingerprint:'c'.repeat(64),sourceStrategy,rows,totalRows:rows.length,truncated:false,targetDefinitions:[{id:'target_0',kind:'asset_price',symbols:['000001.SZ'],quantities:{'000001.SZ':1},unit:'CNY'}],modelFits:[{id:'fit_0',trainingStart:'20230101',trainingEnd:'20241231'}],diagnostics:{source:'SYNTHETIC_TRANSPORT_FIXTURE_NOT_NUMERICAL_ACCEPTANCE',metrics:{mse:0.1},factorIncrement:{status:'available',pairedDates:2,withFactorsMse:0.1,stateOnlyMse:0.2,dateBalancedMseImprovement:0.1,baselineRows:rows,baselineModelFits:[{id:'baseline_fit'}],dailyLosses:[{date:'20250102',withFactorsMse:0.1}],baselineValidation:{metrics:{mse:0.2}}}},hedgeFits:[{date:'20250102',targetIds:['target_0']}]};
 return {schemaVersion:2,status:'completed',engineVersion:'0.4.0',strategy:sourceStrategy,research:{mode:'statistical_quant'},provenance:{synthetic:true},forecasts:artifact,validation:{},selection:{winner:'ridge',evidenceStatus:'UNVALIDATED_RESEARCH'},metrics:null,equity:[],trades:[],execution:{ledger:[]}};
}

let experiment,job,claim,result,snapshot;
test('experiment ownership, versioning, copy and additive schema preserve legacy data',async()=>{
 const created=await api('/experiments','POST',{strategy:base});assert.equal(created.status,201);experiment=(await created.json()).experiment;
 assert.equal((await api('/experiments/'+experiment.id,'GET',undefined,other)).status,404);
 assert.equal((await api('/experiments/'+experiment.id,'PUT',{strategy:base,version:99})).status,409);
 const copy=await api('/experiments/'+experiment.id+'/copy','POST',{name:'Copied research'});assert.equal(copy.status,201);assert.equal((await copy.json()).experiment.parentId,experiment.id);
 await db.exec("INSERT INTO strategies(id,owner,name,spec,created_at,updated_at) VALUES('preserved','old-owner','Legacy','{}','old','old')");
 const migration=await fs.readFile(new URL('../edge/migrations/0003_statistical_quant.sql',import.meta.url),'utf8');await db.exec(migration.replaceAll('\n',' '));await db.exec(migration.replaceAll('\n',' '));
 assert.equal((await db.prepare("SELECT name FROM strategies WHERE id='preserved'").first()).name,'Legacy');
});

test('forecast run uses atomic queue linkage and requires an immutable frozen dataset before completion',async()=>{
 const queued=await api('/experiments/'+experiment.id+'/run','POST',{version:1,dataSource:'demo'});assert.equal(queued.status,202);job=(await queued.json()).job;
 claim=(await (await runner('claim',{})).json()).job;assert.equal(claim.id,job.id);assert.equal(claim.jobKind,'forecast');assert.equal(claim.experimentId,experiment.id);
 result=resultFixture(claim.strategy);
 const packet={id:claim.id,leaseToken:claim.leaseToken,result};assert.equal((await runner('complete',packet)).status,409);
 snapshot={schemaVersion:1,rows:[{ts_code:'000001.SZ',trade_date:'20250102',open:10,close:10,vol:100}],provenance:{synthetic:true,tradingDates:['20250102'],dataFingerprint:'d'.repeat(64)},dataFingerprint:'c'.repeat(64)};
 assert.equal((await runner('snapshot',{id:claim.id,leaseToken:claim.leaseToken,snapshot})).status,200);
 assert.equal((await runner('snapshot',{id:claim.id,leaseToken:claim.leaseToken,snapshot:{...snapshot,provenance:{...snapshot.provenance,changed:true}}})).status,409);
 assert.equal((await runner('complete',packet)).status,200);
 assert.equal((await (await runner('snapshot',{id:claim.id,leaseToken:claim.leaseToken,snapshot})).json()).idempotent,true);
 assert.equal((await (await runner('complete',packet)).json()).idempotent,true);
});

test('complete private forecasts download without preview truncation and cannot cross owners',async()=>{
 const id=result.forecasts.artifactId;
 const preview=await api('/forecasts/'+id+'?limit=1');assert.equal(preview.status,200);const p=await preview.json();assert.equal(p.artifact.rows.length,1);assert.equal(p.artifact.truncated,true);assert.equal(p.artifact.totalRows,2);assert.equal(p.artifact.diagnostics.factorIncrement.baselineRows,undefined);assert.equal(p.artifact.diagnostics.factorIncrement.detailCounts.baselineRows,2);assert.equal(p.artifact.hedgeFits,undefined);assert.equal(p.artifact.hedgeFitCount,1);
 const download=await api('/forecasts/'+id+'/download');assert.match(download.headers.get('content-disposition'),/attachment/);const full=await download.json();assert.equal(full.artifact.rows.length,2);assert.equal(full.artifact.diagnostics.factorIncrement.baselineRows.length,2);assert.equal(full.artifact.hedgeFits.length,1);
 const manifest=(await (await api('/forecasts')).json()).items[0];assert.equal(manifest.metadata.diagnostics.factorIncrement.baselineRows,undefined);assert.equal(manifest.metadata.validation.detailAvailability,'complete_artifact_download');
 assert.equal((await api('/forecasts/'+id,'GET',undefined,other)).status,404);
 const details=await (await api('/experiments/'+experiment.id)).json();assert.equal(details.forecasts.length,1);assert.equal(details.runs[0].forecastArtifactId,id);
 assert.equal((await (await api('/model-versions')).json()).items.length,1);
});

let firstExecution;
test('execution reuses original forecast and dataset under its lease without a new provider request',async()=>{
 const id=result.forecasts.artifactId;
 assert.equal((await api('/executions','POST',{forecastArtifactId:id,execution:{minEdgeBps:20}},other)).status,404);
 assert.equal((await api('/executions','POST',{forecastArtifactId:id,model:{family:'event'}})).status,400);
 const created=await api('/executions','POST',{forecastArtifactId:id,execution:{minEdgeBps:20},costs:{commissionBps:6}});assert.equal(created.status,202);firstExecution=(await created.json()).execution;
 const executionClaim=(await (await runner('claim',{})).json()).job;assert.equal(executionClaim.jobKind,'execution');assert.equal(executionClaim.forecastArtifactId,id);assert.equal(executionClaim.dataSource,'replay');assert.equal(executionClaim.dataset,null);
 const lease={id:executionClaim.id,leaseToken:executionClaim.leaseToken};
 const data=await (await runner('replay',{...lease,kind:'dataset'})).json();assert.deepEqual(data.snapshot,snapshot);
 const replay=await (await runner('replay',{...lease,kind:'forecast'})).json();assert.deepEqual(replay.artifact,result.forecasts);
 assert.equal((await runner('replay',{...lease,leaseToken:'wrong',kind:'dataset'})).status,409);
 const executed={...result,strategy:executionClaim.strategy,research:{mode:'statistical_quant',executionOnly:true},metrics:{totalReturn:-.001,totalCosts:20},equity:[{date:'20250110',equity:999000}],trades:[{forecastId:'forecast_0',targetId:'target_0',exitReason:'target_maturity'}]};
 assert.equal((await runner('complete',{...lease,result:{...executed,trades:[{forecastId:'nonexistent'}]}})).status,400);
 assert.equal((await runner('complete',{...lease,result:executed})).status,200);
 const details=await (await api('/executions/'+executionClaim.id)).json();assert.equal(details.execution.status,'completed');assert.equal(details.result.forecasts.artifactId,id);
});

test('controlled execution comparisons persist owner-isolated references without refitting',async()=>{
 const id=result.forecasts.artifactId;
 const second=(await (await api('/executions','POST',{forecastArtifactId:id,costs:{commissionBps:9}})).json()).execution;
 const c=(await (await runner('claim',{})).json()).job;
 const completed={...result,strategy:c.strategy,research:{mode:'statistical_quant',executionOnly:true},metrics:{totalReturn:-.002,totalCosts:30},equity:[],trades:[]};
 assert.equal((await runner('complete',{id:c.id,leaseToken:c.leaseToken,result:completed})).status,200);
 const response=await api('/comparisons','POST',{kind:'execution',members:[firstExecution.id,second.id],name:'Same forecasts, costs changed'});assert.equal(response.status,201);const comparison=(await response.json()).comparison;
 assert.equal(comparison.controlledComparison,true);assert.equal(comparison.compatibility.refittingPerformed,false);
 assert.equal((await api('/comparisons/'+comparison.id,'GET',undefined,other)).status,404);
 assert.equal((await api('/comparisons/'+comparison.id+'/download')).status,200);
});

test('public module and recipe catalogs expose finite definitions, not fabricated experiment counts',async()=>{
 const r=await api('/modules?stage=model&pageSize=2');assert.equal(r.status,200);const catalog=await r.json();assert.equal(catalog.items.length,2);assert.ok(catalog.total>2);assert.ok(catalog.items.every(x=>x.stage==='model'));assert.equal(catalog.limits.maxForecastRows,25000);
 const recipes=await (await api('/recipes?q=pair_reversion&pageSize=4')).json();assert.equal(recipes.items.length,4);assert.ok(recipes.items.every(x=>x.id.includes('pair_reversion')));
 const workspaces=await (await api('/workspaces')).json();assert.equal(workspaces.items.filter(x=>x.implemented).length,1);
});

test('shared Python/JavaScript schema fixtures have identical acceptance boundaries',async()=>{
 const fixtures=JSON.parse(await fs.readFile(new URL('./fixtures/statistical-quant-configs.json',import.meta.url)));
 for(const {name,strategy} of fixtures.valid) assert.doesNotThrow(()=>validateStatisticalQuant(strategy),name);
 for(const {name,strategy} of fixtures.invalid) assert.throws(()=>validateStatisticalQuant(strategy),name);
});

test('old runners skip forecast jobs but can claim older protocols behind them',async()=>{
 const cookie=await session();const created=(await (await api('/experiments','POST',{strategy:base},cookie)).json()).experiment;
 const queued=(await (await api('/experiments/'+created.id+'/run','POST',{version:1,dataSource:'demo'},cookie)).json()).job;
 const legacy=JSON.parse(await fs.readFile(new URL('../engine/examples/basic.json',import.meta.url)));
 await db.prepare("INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES('legacy-compatible','legacy-version-owner','Legacy','queued','demo',?,'2099-01-01','2099-01-01')").bind(JSON.stringify(legacy)).run();
 const old=(await (await request('/runner/claim','POST',{engineVersion:'0.3.0'},null,true)).json()).job;
 assert.equal(old.id,'legacy-compatible');assert.equal((await db.prepare('SELECT status FROM jobs WHERE id=?').bind(queued.id).first()).status,'queued');
 const modern=(await (await runner('claim',{})).json()).job;assert.equal(modern.id,queued.id);assert.equal(modern.jobKind,'forecast');
 await request('/runs/'+queued.id+'/cancel','POST',{},cookie);
 const discarded=await (await runner('snapshot',{id:modern.id,leaseToken:modern.leaseToken,snapshot})).json();assert.equal(discarded.terminalDiscard,true);
 assert.equal((await (await runner('complete',{id:modern.id,leaseToken:modern.leaseToken,result})).json()).ignored,true);
 await db.prepare("UPDATE jobs SET status='failed' WHERE id='legacy-compatible'").run();
});


test('research indexes and observed counts survive reload and remain owner-isolated',async()=>{
 const summary=await (await api('/summary')).json();assert.equal(summary.forecastArtifacts,1);assert.equal(summary.completedExecutions,2);assert.equal(summary.comparisons,1);
 for(const collection of ['forecasts','executions','comparisons']) {
  const own=await (await api('/'+collection)).json(),foreign=await (await api('/'+collection,'GET',undefined,other)).json();
  assert.ok(own.items.length>0);assert.equal(foreign.total,0);assert.deepEqual(foreign.items,[]);
 }
 const version=(await (await api('/model-versions')).json()).items[0];
 assert.equal((await api('/model-versions/'+version.id,'GET',undefined,other)).status,404);
 assert.equal((await (await api('/model-versions/'+version.id)).json()).modelVersion.id,version.id);
});


test('cancellation racing a frozen-data write acknowledges discard and cleans the losing object',async()=>{
 const cookie=await session();const exp=(await (await api('/experiments','POST',{strategy:base},cookie)).json()).experiment;
 const queued=await api('/experiments/'+exp.id+'/run','POST',{version:1,dataSource:'demo'},cookie);assert.equal(queued.status,202);
 const c=(await (await runner('claim',{})).json()).job;
 await db.prepare("INSERT INTO meta(key,value,updated_at) VALUES('test_snapshot_cancel',?,'test')").bind(c.id).run();
 try {
  const response=await runner('snapshot',{id:c.id,leaseToken:c.leaseToken,snapshot});assert.equal(response.status,200);assert.equal((await response.json()).terminalDiscard,true);
  const stored=await (await mf.getR2Bucket('ARTIFACTS')).list({prefix:'forecast-data/'+c.workspaceId+'/'+c.id+'/'});assert.equal(stored.objects.length,0);
  assert.equal((await (await runner('complete',{id:c.id,leaseToken:c.leaseToken,result})).json()).ignored,true);
 } finally {await db.prepare("DELETE FROM meta WHERE key='test_snapshot_cancel'").run();}
});


test('retention removes only old failed snapshots and preserves completed forecast inputs',async()=>{
 const bucket=await mf.getR2Bucket('ARTIFACTS'),orphanKey='forecast-data/orphan-owner/failed-input.json';
 await bucket.put(orphanKey,JSON.stringify(snapshot));
 await db.prepare("INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES('old-failed','orphan-owner','Old failed','failed','demo','{}','2000-01-01','2000-01-01')").run();
 await db.prepare("INSERT INTO quant_runs(job_id,owner,experiment_id,experiment_version,kind,snapshot_key,snapshot_hash,snapshot_fingerprint,created_at) VALUES('old-failed','orphan-owner','historical-experiment',1,'forecast',?,'hash','fingerprint','2000-01-01')").bind(orphanKey).run();
 const retained=await db.prepare('SELECT dataset_key FROM quant_forecast_artifacts WHERE id=?').bind(result.forecasts.artifactId).first();
 assert.ok(await bucket.get(retained.dataset_key));
 assert.equal(await cleanupFailedForecastSnapshots({DB:db,ARTIFACTS:bucket},'2024-01-01'),1);
 assert.equal(await bucket.get(orphanKey),null);assert.ok(await bucket.get(retained.dataset_key));
});


test('identical immutable forecasts remain linked to every originating experiment',async()=>{
 const copy=(await (await api('/experiments','POST',{strategy:base})).json()).experiment;
 assert.equal((await api('/experiments/'+copy.id+'/run','POST',{version:1,dataSource:'demo'})).status,202);
 const c=(await (await runner('claim',{})).json()).job;
 assert.equal((await runner('snapshot',{id:c.id,leaseToken:c.leaseToken,snapshot})).status,200);
 assert.equal((await runner('complete',{id:c.id,leaseToken:c.leaseToken,result})).status,200);
 const first=await (await api('/experiments/'+experiment.id)).json(),second=await (await api('/experiments/'+copy.id)).json();
 assert.equal(first.forecasts[0].id,result.forecasts.artifactId);assert.equal(second.forecasts[0].id,result.forecasts.artifactId);
 assert.equal(second.forecasts[0].experimentId,copy.id);assert.equal(second.forecasts[0].sourceExperimentId,experiment.id);
 assert.equal((await db.prepare('SELECT count(*) n FROM quant_forecast_artifacts WHERE id=?').bind(result.forecasts.artifactId).first()).n,1);
});


test('R2 corruption is rejected by actual content hashes before download or replay',async()=>{
 const bucket=await mf.getR2Bucket('ARTIFACTS'),record=await db.prepare('SELECT * FROM quant_forecast_artifacts WHERE id=?').bind(result.forecasts.artifactId).first();
 const stored=await (await bucket.get(record.artifact_key)).text();
 await bucket.put(record.artifact_key,JSON.stringify({...result.forecasts,rows:[]}));
 try {
  const download=await api('/forecasts/'+record.id+'/download');assert.equal(download.status,503);assert.equal((await download.json()).error.code,'ARTIFACT_INTEGRITY');
  assert.equal((await api('/executions','POST',{forecastArtifactId:record.id})).status,503);
 } finally {await bucket.put(record.artifact_key,stored);}
 const run=await api('/executions','POST',{forecastArtifactId:record.id});assert.equal(run.status,202);
 const c=(await (await runner('claim',{})).json()).job,lease={id:c.id,leaseToken:c.leaseToken};
 const dataset=await (await bucket.get(record.dataset_key)).text();await bucket.put(record.dataset_key,JSON.stringify({...snapshot,rows:[]}));
 try {const replay=await runner('replay',{...lease,kind:'dataset'});assert.equal(replay.status,503);assert.equal((await replay.json()).error.code,'ARTIFACT_INTEGRITY');}
 finally {await bucket.put(record.dataset_key,dataset);await runner('complete',{...lease,error:{code:'SYNTHETIC_INTEGRITY_TEST',message:'Deliberate isolated corruption test'}});}
});


test('multi-megabyte ablation evidence stays complete in R2 and compact in D1 indexes',async()=>{
 const cookie=await session(),created=(await (await api('/experiments','POST',{strategy:base},cookie)).json()).experiment;
 await api('/experiments/'+created.id+'/run','POST',{version:1,dataSource:'demo'},cookie);
 const c=(await (await runner('claim',{})).json()).job,large=resultFixture(c.strategy,'e'.repeat(64));
 const detail=large.forecasts.diagnostics.factorIncrement;
 detail.baselineRows=Array.from({length:25000},(_,i)=>({forecastId:'baseline_'+i,date:'20250102',targetId:'target_0',expectedFuture:10.125,realizedFuture:10.1,synthetic:true}));
 detail.baselineModelFits=Array.from({length:2000},(_,i)=>({id:'baseline_fit_'+i,informationCutoff:'20241231',synthetic:true}));
 large.forecasts.diagnostics.perTarget=Array.from({length:20000},(_,i)=>({targetId:'synthetic_'+i,observations:1,priceRmse:0.1}));
 large.validation=structuredClone(large.forecasts.diagnostics);
 assert.ok(Buffer.byteLength(JSON.stringify(large))>8*1024*1024);
 assert.equal((await runner('snapshot',{id:c.id,leaseToken:c.leaseToken,snapshot})).status,200);
 assert.equal((await runner('complete',{id:c.id,leaseToken:c.leaseToken,result:large})).status,200);
 const manifest=await db.prepare('SELECT metadata FROM quant_forecast_artifacts WHERE id=?').bind(large.forecasts.artifactId).first();
 assert.ok(Buffer.byteLength(manifest.metadata)<12000);
 const listText=await (await api('/forecasts','GET',undefined,cookie)).text();assert.ok(Buffer.byteLength(listText)<16000);assert.equal(JSON.parse(listText).items[0].metadata.diagnostics.factorIncrement.detailCounts.baselineRows,25000);
 const previewText=await (await api('/forecasts/'+large.forecasts.artifactId+'?limit=1','GET',undefined,cookie)).text();assert.ok(Buffer.byteLength(previewText)<20000);
 const full=await (await api('/forecasts/'+large.forecasts.artifactId+'/download','GET',undefined,cookie)).json();assert.equal(full.artifact.diagnostics.factorIncrement.baselineRows.length,25000);assert.equal(full.artifact.diagnostics.perTarget.length,20000);
});


test('experiment head and immutable revision commit atomically under insert failure and concurrent CAS',async()=>{
 const cookie=await session(),created=(await (await api('/experiments','POST',{strategy:base},cookie)).json()).experiment;
 await db.prepare("CREATE TRIGGER test_quant_version_abort BEFORE INSERT ON quant_experiment_versions WHEN NEW.version=2 BEGIN SELECT RAISE(ABORT,'isolated history insertion failure'); END").run();
 try {
  const failed=await api('/experiments/'+created.id,'PUT',{strategy:{...base,name:'must rollback'},version:1},cookie);assert.equal(failed.status,500);
  const head=await db.prepare('SELECT version,name FROM quant_experiments WHERE id=?').bind(created.id).first();assert.equal(head.version,1);assert.equal(head.name,base.name);
  const history=await db.prepare('SELECT version FROM quant_experiment_versions WHERE experiment_id=?').bind(created.id).all();assert.deepEqual(history.results,[{version:1}]);
 } finally {await db.prepare('DROP TRIGGER test_quant_version_abort').run();}
 const saved=await api('/experiments/'+created.id,'PUT',{strategy:{...base,name:'committed version'},version:1},cookie);assert.equal(saved.status,200);assert.equal((await saved.json()).experiment.version,2);
 const replies=await Promise.all(['concurrent A','concurrent B'].map(name=>api('/experiments/'+created.id,'PUT',{strategy:{...base,name},version:2},cookie)));
 assert.deepEqual(replies.map(r=>r.status).sort(),[200,409]);
 const final=await db.prepare('SELECT version,spec FROM quant_experiments WHERE id=?').bind(created.id).first();assert.equal(final.version,3);
 const revisions=await db.prepare('SELECT version,spec FROM quant_experiment_versions WHERE experiment_id=? ORDER BY version').bind(created.id).all();assert.deepEqual(revisions.results.map(r=>r.version),[1,2,3]);assert.equal(revisions.results[2].spec,final.spec);
});


test('stored candidate compatibility removes only validated known resolver metadata',async()=>{
 const fixtures=JSON.parse(await fs.readFile(new URL('./fixtures/statistical-quant-configs.json',import.meta.url)));
 const candidate=structuredClone(fixtures.valid.find(x=>x.name==='saved_universe_state').strategy);
 Object.assign(candidate.universe.catalogSnapshot,{source:'TUSHARE_PRO',securityCount:5911,universeCount:1354,missingIdentityCount:4});
 assert.throws(()=>validateStatisticalQuant(candidate));
 const clean=validateStoredStatisticalQuant(candidate);assert.deepEqual(Object.keys(clean.universe.catalogSnapshot).sort(),['asOf','hash','historicalMembershipVerified']);
 for(const extra of [{token:'forbidden'},{securityCount:'5911'},{source:{}},{universeCount:5001},{historicalMembershipVerified:true}]) {
  const bad=structuredClone(candidate);Object.assign(bad.universe.catalogSnapshot,extra);assert.throws(()=>validateStoredStatisticalQuant(bad));
 }
 assert.equal(candidate.universe.catalogSnapshot.securityCount,5911,'read projection does not mutate the saved source');
});


test('experiment listing includes the latest forecast or execution without cross-owner leakage',async()=>{
 const cookie=await session(), outsider=await session();
 const owner=(await (await request('/session','GET',undefined,cookie)).json()).workspace.id;
 const foreign=(await (await request('/session','GET',undefined,outsider)).json()).workspace.id;
 const exp=(await (await api('/experiments','POST',{strategy:base},cookie)).json()).experiment;
 let listing=await (await api('/experiments','GET',undefined,cookie)).json();
 assert.equal(listing.items.find(x=>x.id===exp.id).latestRun,null);
 const add=async(id,jobOwner,linkOwner,kind,time)=>{
  await db.batch([
   db.prepare("INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES(?,?,?,'completed','demo',?,?,?)").bind(id,jobOwner,'List fixture',JSON.stringify(base),time,time),
   db.prepare('INSERT INTO quant_runs(job_id,owner,experiment_id,experiment_version,kind,created_at) VALUES(?,?,?,2,?,?)').bind(id,linkOwner,exp.id,kind,time)
  ]);
 };
 await add('latest-fixture-a',owner,owner,'forecast','2026-01-01T00:00:00Z');
 await add('latest-fixture-b',owner,owner,'execution','2026-01-01T00:00:00Z');
 await add('latest-fixture-foreign-job',foreign,owner,'execution','2099-01-01T00:00:00Z');
 await add('latest-fixture-foreign-link',owner,foreign,'forecast','2099-01-01T00:00:00Z');
 listing=await (await api('/experiments','GET',undefined,cookie)).json();
 assert.deepEqual(listing.items.find(x=>x.id===exp.id).latestRun,{id:'latest-fixture-b',status:'completed',jobKind:'execution',experimentVersion:2,createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z'});
 assert.ok(!(await (await api('/experiments','GET',undefined,outsider)).json()).items.some(x=>x.id===exp.id));
});
