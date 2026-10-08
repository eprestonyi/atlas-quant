/** Real browser events and declared HTTP doubles; no provider or numerical evidence. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
import { marketDatasetDownload } from '../quant-workspace/source-downloads.js';
const id = n => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`;
const symbols = Array.from({ length: 1000 }, (_, n) => `${600000 + n}.SH`), root = 'a'.repeat(64);
const scopeRef = { scopeId: id(1), scopeRoot: root, format: 'atlas.quant.universe_scope', version: 1 };
const marketRef = { datasetId: id(4), datasetRoot: 'd'.repeat(64), format: 'atlas.quant.market_dataset', version: 1 };
const sourceDownload=marketDatasetDownload(marketRef);
assert.equal(sourceDownload,`/quant/api/market-datasets/${id(4)}/download?datasetRoot=${'d'.repeat(64)}`);
for(const ref of [null,{}, {...marketRef,version:2},{...marketRef,format:'atlas.quant.research_dataset'},{...marketRef,datasetRoot:'x'.repeat(64)},{...marketRef,datasetId:'../foreign'}])assert.equal(marketDatasetDownload(ref),null);
const admissions = [{ admissionProfile: 'pooled_asset_1000_auto_candidate_v1', available: true, families: ['mean_reversion'], estimator: 'auto', targetKind: 'asset_price', executionEnabled: false, maxFactors: 16, innerFolds: 2, outerFolds: 2, minRefitDays: 20 }];
const calls = []; let plan, job, saved, startFail = true, saveGate, jobGate, completed = false, sourceKind = 'fixture', wrongSource = false;
const dom = new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>', { url: 'http://localhost/quant/#quant/easy/settings', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window; w.structuredClone = structuredClone; w.scrollTo = () => {}; w.matchMedia = () => ({ matches: false, addEventListener() {} });
w.fetch = async (url, options = {}) => {
  const path = String(url).replace('/quant/api', ''), data = options.body ? JSON.parse(options.body) : null;
  calls.push({ path, data, method: options.method || 'GET' }); let result = { items: [], total: 0 };
  if (path === '/universe-scopes') result = { scopeRef, scope: { symbols, symbolCount: symbols.length, start: data.start, end: data.end } };
  else if (path === '/market-preparation-plans') {
    const scope = { symbols, symbolCount: symbols.length, start: q.state.strategy.universe.start, end: q.state.strategy.universe.end, scopeRoot: root };
    plan = { planRef: { planId: id(2), planRoot: 'b'.repeat(64), format: 'atlas.quant.market_acquisition_plan', version: 1 }, universeScopeRef: scopeRef, profile: 'pooled_asset_1000_v1', scope, fields: ['open','high','low','close','vol','amount','adj_factor',...data.requiredFields], budget: { declaredRequests: data.requiredFields.length ? 3001 : 2001, rawResponseCeilingBytes: 1048576 }, blockedReasons: [], canStart: true, status: 'planned', providerCalls: 0, researchAdmissions: admissions, preferredResearchAdmission: admissions[0].admissionProfile };
    result = plan;
  } else if (path.endsWith('/requests?page=1&pageSize=50')) result = { items: [{ ordinal: 0, apiName: 'trade_cal', params: { exchange: 'SSE', start_date: plan.scope.start, end_date: plan.scope.end } }], total: 2001, page: 1, pageSize: 50, planRoot: plan.planRef.planRoot };
  else if (path.endsWith('/start')) {
    if (startFail) { startFail = false; throw Error('启动响应未知'); }
    job = { id: id(3), planId: id(2), status: 'queued', phase: null, result: null }; result = { job };
  } else if (path.startsWith('/market-preparation-plans/')) result = plan;
  else if (path.startsWith('/market-preparation-jobs/')) {
    if (jobGate) await jobGate;
    if (completed) job = { ...job, status: 'completed', phase: 'ready', result: { marketDatasetRef: marketRef, universeScopeRef: scopeRef, profile: 'pooled_asset_1000_v1', symbolCount: 1000, rowCount: 262000 } };
    result = { job, researchAdmissions: admissions, preferredResearchAdmission: admissions[0].admissionProfile };
  } else if (path.startsWith('/market-datasets/')) {
    assert.equal(path, `/market-datasets/${marketRef.datasetId}?datasetRoot=${marketRef.datasetRoot}`, 'source details use the exact frozen ref');
    result = { marketDatasetRef: wrongSource ? { ...marketRef, datasetRoot: 'e'.repeat(64) } : marketRef, universeScopeRef: scopeRef, scope: plan.scope, rowCount: 262000, sourceKind };
  } else if (path.includes('/statistical-quant/experiments') && !path.endsWith('/run') && ['POST','PUT'].includes(options.method)) {
    if (saveGate) await saveGate;
    saved = { id: id(5), version: (saved?.version || 0) + 1, strategy: data.strategy, universeScopeRef: data.universeScopeRef, marketDatasetBinding: data.marketDatasetRef ? { marketDatasetRef: data.marketDatasetRef, universeScopeRef: data.universeScopeRef, admissionProfile: data.admissionProfile, scope: { symbols: data.strategy.universe.symbols, start: data.strategy.universe.start, end: data.strategy.universe.end, symbolCount: 1000, scopeRoot: root } } : null };
    result = { experiment: saved };
  } else if (path.endsWith('/run')) result = { job: { id: id(6), status: 'queued', dataSource: data.dataSource } };
  else if (path === '/statistical-quant/experiments/' + id(5)) result = { experiment: saved };
  return { ok: true, status: 200, text: async () => JSON.stringify(result) };
};
const code = await build({ entryPoints: ['web/main.js'], bundle: true, write: false, format: 'iife', plugins: [{ name: 'no-init', setup(b) { b.onLoad({ filter: /\/web\/app\.js$/ }, async a => ({ contents: (await fs.readFile(a.path,'utf8')).replace('  init();', '  window.qa={state,workspace,parseRoute,render};'), loader: 'js' })); } }] });
const legacyRecord=JSON.stringify({job:{id:id(8),status:'queued'},startUnknown:true});w.localStorage.setItem('atlas-quant-market-preparation-v1',legacyRecord);
w.eval(code.outputFiles[0].text); const q = w.qa, s = q.state;
s.loading = false; s.session = { workspace: { id: 'owner_a' }, capabilities: { tushareHosted: true } };
s.strategy.universe = { symbols, start: '20250101', end: '20251231', selection: { version: 1, includeGroups: [{ id: 'g_1', name: '完整目录', filters: [{ field: 'exchange', value: 'SSE' }] }], excludeGroups: [], includeSymbols: [], excludeSymbols: [] }, subsetPolicy: 'all', snapshotHash: root, resolutionHash: root };
q.parseRoute(); q.render();
assert.equal(q.workspace.market.state.job,null,'unpartitioned records are retained but not restored');
assert.equal(w.localStorage.getItem('atlas-quant-market-preparation-v1'),legacyRecord);
const tick = () => new Promise(r => setTimeout(r, 35));
const route = async step => { w.location.hash = '#quant/easy/' + step; await tick(); };
const click = async action => { const button = w.document.querySelector(`[data-sq="${action}"]`); assert(button, action); assert(!button.disabled, action+' enabled'); button.click(); await tick(); };
await click('market-select'); assert.equal(s.dataSource,'ready_market');
await q.workspace.run(); assert(!calls.some(x=>x.path.endsWith('/run')),'unprepared source cannot submit a research');
await route('settings');
await click('market-plan');
assert.equal(plan.scope.symbols.length,1000); assert(w.document.querySelector('main').textContent.includes('2001'));
assert(!calls.some(x=>x.path.endsWith('/start')),'planning never starts provider work');
assert.deepEqual(calls.find(x=>x.path==='/market-preparation-plans').data,{scopeRef,profile:'pooled_asset_1000_v1',requiredFields:[]});
await click('market-requests'); assert(w.document.querySelector('main').textContent.includes('trade_cal'));
await click('market-start'); assert(w.document.querySelector('main').textContent.includes('启动响应未知'));
const initialStart = calls.filter(x=>x.path.endsWith('/start')).at(-1).data;
const stored = JSON.parse(w.localStorage.getItem('atlas-quant-market-preparation-v1:owner_a')); assert.equal(stored.startRequestId,initialStart.requestId); assert.equal(stored.startUnknown,true);
await click('market-start'); assert.deepEqual(calls.filter(x=>x.path.endsWith('/start')).at(-1).data,initialStart,'unknown control response retries same request, not a new provider intent');
assert(!w.document.querySelector('[data-sq="market-bind"]'));
assert(!w.document.querySelector('a[download]'),'queued preparation is not a downloadable complete source');
assert(!w.document.querySelector('[data-market-source-progress]'),'missing progress never invents counts');
const progress = { declaredRequests: 2001, receiptsSaved: 12, rawBytesSaved: 1048576, outcomeUnknown: 2 };
job = { ...job, status: 'running', phase: 'fetching_sources', sourceProgress: progress };
const startsBeforeProgress = calls.filter(x => x.path.endsWith('/start')).length;
await click('market-refresh');
const sourceProgressText = () => w.document.querySelector('[data-market-source-progress]')?.textContent || '';
assert(sourceProgressText().includes('已保存来源回执12 / 2001'));
assert(sourceProgressText().includes('1.00 MiB'));
assert(sourceProgressText().includes('仅表示来源阶段'));
assert(sourceProgressText().includes('包含已复用的缓存回执，不是新增供应商调用次数'));
assert(sourceProgressText().includes('2 项结果待核对，不会自动重复请求'));
assert(!sourceProgressText().includes('%'),'receipt counts are not research-wide percentage');
assert(w.document.querySelector('[data-sq="market-start"]').disabled,'unknown outcomes cannot start another preparation');
assert.equal(calls.filter(x => x.path.endsWith('/start')).length,startsBeforeProgress,'refreshing unknown outcomes never repeats provider intent');
const invalidProgress = [null, {}, [], { ...progress, declaredRequests: 0 }, { ...progress, declaredRequests: 2000 }, { ...progress, receiptsSaved: -1 }, { ...progress, receiptsSaved: 2002 }, { ...progress, receiptsSaved: '12' }, { ...progress, rawBytesSaved: -1 }, { ...progress, rawBytesSaved: 1.5 }, { ...progress, rawBytesSaved: Number.MAX_SAFE_INTEGER + 1 }, { ...progress, outcomeUnknown: true }, { ...progress, outcomeUnknown: -1 }, { ...progress, outcomeUnknown: 2002 }];
for (const sourceProgress of invalidProgress) {
  job = { ...job, sourceProgress }; await click('market-refresh');
  assert(!w.document.querySelector('[data-market-source-progress]'),'invalid progress stays absent: ' + JSON.stringify(sourceProgress));
}
job = { ...job, sourceProgress: { ...progress, receiptsSaved: 2001, outcomeUnknown: 0 } };
await click('market-refresh');
assert(sourceProgressText().includes('2001 / 2001'));
assert(!w.document.querySelector('[data-sq="market-bind"]'),'all saved receipts alone do not complete normalization or make research runnable');
assert(w.document.querySelector('.fin-progress').textContent.includes('正在准备完整行情'));
q.workspace.market.state.verified = false; q.render();
assert(!w.document.querySelector('[data-market-source-progress]'),'unverified cached progress is not presented as a fresh count');
completed=true; admissions[0].available=false; admissions[0].reason='RUNNER_OFFLINE'; await click('market-refresh');
assert(w.document.querySelector('.fin-progress').textContent.includes('完整行情已冻结'),'completed keeps the frozen-data status');
assert(sourceProgressText().includes('2001 / 2001'));
assert(sourceProgressText().includes('不代表模型拟合或研究完成'));
assert(w.document.querySelector('[data-sq="market-bind"]').disabled,'ready data alone does not grant compute availability');
assert(w.document.querySelector('main').textContent.includes('计算节点当前离线'));
assert(w.document.querySelector(`a[download][href="${sourceDownload}"]`),'completed source can be downloaded independently of compute availability');
assert(w.document.querySelector('main').textContent.includes('合成行情 · 仅供测试'),'fixture source is explicit before binding');
admissions[0].available=true; admissions[0].reason=null; await click('market-refresh'); await click('market-bind');
assert.equal(s.marketDatasetBinding.admissionProfile,'pooled_asset_1000_auto_candidate_v1');
assert.equal(s.strategy.model.estimator,'auto'); assert.equal(s.strategy.universe.symbols.length,1000);
assert.equal(s.marketDatasetBinding.marketDatasetRef.format,'atlas.quant.market_dataset');
assert.equal(w.document.querySelectorAll(`a[download][href="${sourceDownload}"]`).length,1,'same bound and completed source is not duplicated');
await q.workspace.save();
const firstSave = calls.filter(x=>x.path.includes('/statistical-quant/experiments')&&x.method==='POST').at(-1).data;
assert.deepEqual(firstSave.marketDatasetRef,marketRef); assert.deepEqual(firstSave.universeScopeRef,scopeRef);
assert.equal(firstSave.admissionProfile,'pooled_asset_1000_auto_candidate_v1');
assert(!Object.hasOwn(firstSave,'datasetRef')&&!Object.hasOwn(firstSave,'dataset'));
const draft = JSON.parse(w.localStorage.getItem('atlas-quant-statistical-draft-v2')); assert.deepEqual(draft.marketDatasetBinding.marketDatasetRef,marketRef);
// Date and filter mutations invalidate the old source without shrinking it or requesting data.
const oldStart = s.strategy.universe.start, oldSelection = structuredClone(s.strategy.universe.selection), beforeChange = calls.length;
s.strategy.universe.start='20250201'; s.dirty=true; assert.equal(await q.workspace.save(),null);
await q.workspace.run(); assert(!calls.slice(beforeChange).some(x=>x.method==='POST'),'mismatched source does not submit or reacquire');
s.strategy.universe.start=oldStart; s.strategy.universe.selection={...oldSelection,extra:'changed filter'};
assert.equal(await q.workspace.save(),null); s.strategy.universe.selection=oldSelection;
// Existing immutable experiment restores the binding in a browser without preparation history.
await route('report'); const open=w.document.createElement('button');open.dataset.sq='experiment-load';open.dataset.id=id(5);w.document.body.append(open);open.click();await tick();
assert.equal(s.dataSource,'ready_market'); assert.deepEqual(JSON.parse(JSON.stringify(s.marketDatasetBinding.marketDatasetRef)),marketRef);
assert.equal(s.strategy.universe.symbols.length,1000);
// Saved immutable source identity is checked without this browser's preparation history.
const preparation = q.workspace.market.state, oldPlan = preparation.plan, oldJob = preparation.job;
preparation.plan = null; preparation.job = null; preparation.sources = {};
await route('settings');
assert(w.document.querySelector('main').textContent.includes('合成行情 · 仅供测试'),'saved source retains fixture warning after a fresh detail read');
assert.equal(Object.keys(preparation.sources).length,1);
preparation.sources = {}; wrongSource = true; await route('report'); await route('settings');
assert(w.document.querySelector('main').textContent.includes('来源类型尚未核对'),'mismatched detail cannot certify source kind');
assert(!w.document.querySelector('main').textContent.includes('合成行情 · 仅供测试'));
wrongSource = false; sourceKind = 'provider'; await route('report'); await route('settings');
assert(w.document.querySelector('main').textContent.includes('来源类型：供应商冻结行情'));
sourceKind = 'fixture'; preparation.plan = oldPlan; preparation.job = oldJob;
// No automatic model downgrade when the mechanism is outside the registered auto profile.
s.strategy.model.family='trend'; s.dirty=true; const beforeUnsupported=calls.length; await q.workspace.run();
assert.equal(s.strategy.model.estimator,'auto'); assert(!calls.slice(beforeUnsupported).some(x=>x.path.endsWith('/run')));
s.strategy.model.family='mean_reversion';
// A delayed binding read cannot overwrite a newly chosen source.
await route('settings');let releaseJob;jobGate=new Promise(r=>{releaseJob=r;});
w.document.querySelector('[data-sq="market-bind"]').click();await tick();s.dataSource='upload';releaseJob();await tick();jobGate=null;
assert.equal(s.dataSource,'upload');assert(w.document.querySelector('main').textContent.includes('当前草稿保留')||q.workspace.market.state.error.includes('当前草稿保留'));
s.dataSource='ready_market';q.render();
// A late save belongs only to its submitted source; the new binding stays dirty.
let releaseSave;saveGate=new Promise(r=>{releaseSave=r;});s.dirty=true;
const pending=q.workspace.save();await tick();s.marketDatasetBinding={...s.marketDatasetBinding,marketDatasetRef:{...marketRef,datasetRoot:'e'.repeat(64)}};
releaseSave();await pending;saveGate=null;
assert.equal(s.marketDatasetBinding.marketDatasetRef.datasetRoot,'e'.repeat(64));assert.equal(s.dirty,true);
const late=calls.filter(x=>x.path.includes('/statistical-quant/experiments')&&x.method==='PUT').at(-1).data;
assert.equal(late.marketDatasetRef.datasetRoot,marketRef.datasetRoot,'submitted identity not contaminated');
s.marketDatasetBinding={...s.marketDatasetBinding,marketDatasetRef:marketRef};
await q.workspace.run();const run=calls.findLast(x=>x.path.endsWith('/run'));
assert.equal(run.data.dataSource,'ready_market');assert.deepEqual(run.data.marketDatasetRef,marketRef);assert(!Object.hasOwn(run.data,'datasetRef')&&!Object.hasOwn(run.data,'dataset'));
assert.equal(calls.filter(x=>x.path.endsWith('/start')).length,2,'research never starts another provider job');
// An owner switch neither restores another owner's unknown job nor accepts its late response.
await route('settings');const ownerRecord=w.localStorage.getItem('atlas-quant-market-preparation-v1:owner_a');
let releaseOwner;jobGate=new Promise(r=>{releaseOwner=r;});
w.document.querySelector('[data-sq="market-refresh"]').click();await tick();s.session.workspace.id='owner_b';q.render();
assert.equal(q.workspace.market.state.job,null);assert.equal(q.workspace.market.state.startUnknown,false);
assert.equal(q.workspace.market.state.plan,null);assert.equal(w.localStorage.getItem('atlas-quant-market-preparation-v1:owner_a'),ownerRecord);
assert(!w.document.querySelector('a[download]'),'another owner never sees the cached source link');
releaseOwner();await tick();jobGate=null;
assert.equal(q.workspace.market.state.plan,null,'old-owner response is fenced');
assert(!w.localStorage.getItem('atlas-quant-market-preparation-v1:owner_b'));
assert.equal(await q.workspace.save(),null,'unverified global draft binding cannot be saved by another owner');
s.session.workspace.id='owner_a';q.render();assert(q.workspace.market.state.plan,'returning owner can recover its own evidence');
s.session=null;q.render();assert(w.document.querySelector('main').textContent.includes('正在确认私有工作区身份'));
assert.equal(w.localStorage.getItem('atlas-quant-market-preparation-v1'),legacyRecord);
console.log(JSON.stringify({sourceStageReceiptProgress:true,invalidOrUnverifiedProgressAbsent:true,unknownOutcomesNeverRetry:true,allReceiptsDoNotFinishResearch:true,fixtureWarningBeforeBindingAndAfterReopen:true,exactSourceKindRead:true,mismatchedSourceUnknown:true,exactSavedSourceDownload:true,downloadIndependentOfCompute:true,invalidRefNoDownload:true,workspaceStoragePartition:true,ownerResponseFence:true,legacyEvidencePreserved:true,realDOM:true,httpDoubles:true,completePool:1000,explicitProviderStart:true,idempotentUnknownStart:true,separateSourceAndComputeProfiles:true,readyOnly:true,immutableReopen:true,scopeChangesInvalidate:true,noEstimatorDowngrade:true,lateBindingAndSaveIsolated:true,providerCalls:0}));
dom.window.close();
