/** Real application + DOM events, explicit HTTP doubles; not browser acceptance. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
import { validateStatisticalQuant } from '../../edge/statistical-quant/validation.mjs';
const graph = process.argv.includes('--graph');
const sourceProfile = graph ? 'financial_snapshot_graph_50_v1' : 'financial_snapshot_view_50_v1',
  autoProfile = graph ? 'financial_fundamental_graph_auto_50_v1' : 'financial_fundamental_auto_50_v1',
  apiRoot = graph ? '/dataset-graphs' : '/datasets',
  capPath = graph ? apiRoot + '/capabilities' : '/dataset-capabilities',
  planPath = graph ? apiRoot + '/plans' : '/dataset-plans',
  jobPath = graph ? apiRoot + '/jobs' : '/dataset-preparations',
  routeRoot = '#quant/studio/datasets/' + (graph ? 'graph/' : '');
const dom = new JSDOM(
    '<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',
    {
      url: 'http://dataset.localhost/quant/' + routeRoot + 'source',
      runScripts: 'outside-only',
      pretendToBeVisual: true,
    },
  ),
  w = dom.window;
w.matchMedia = () => ({ matches: false, addEventListener() {} });
w.structuredClone = structuredClone;
w.scrollTo = () => {};
const root = 'a'.repeat(64),
  datasetId = '11111111-1111-4111-8111-111111111111',
  stateId = 'model_fin_cash_asset_share',
  scope = { symbols: ['600000.SH'], start: '20240101', end: '20241231' },
  ref = {
    datasetId,
    datasetRoot: root,
    format: 'atlas.quant.research_dataset',
    version: graph ? 3 : 2,
  },
  financialRef = {
    inputId: datasetId,
    preparationId: datasetId,
    inputRoot: root,
    packRoot: root,
    preparedRoot: root,
    calendarRoot: root,
  },
  detail = {
    datasetRef: ref,
    name: '合成固定来源',
    ...(graph ? { sourceEvidenceClosure: 'separate_research_dataset_v3' } : {}),
    scope,
    status: 'ready',
    summary: {
      marketRows: 243,
      availableStateCoverage: 1,
      stateCoverage: 1,
      selectedStateIds: [stateId],
    },
    researchAdmission: { profile: 'financial_snapshot_view_50_v1' },
    researchAdmissions: [
      { profile: 'financial_snapshot_view_50_v1', estimator: 'ridge', configurationEligible: true, runnerAvailable: true },
      { profile: autoProfile, estimator: 'auto', configurationEligible: true, runnerAvailable: false, limits: { symbols: 50, factors: 16, calendarDays: 366, innerFolds: 2, outerFolds: 2, minRefitDays: 20 } },
    ],
    preferredResearchAdmission: null,
    researchBindingEnabled: true,
    archiveUrl:
      '/quant/api/datasets/' + datasetId + '/archive?datasetRoot=' + root,
  };
const calls = [];
let planFailures = 1,
  startFailures = 1,
  compositionOnline = false,
  compositionEnabled = true,
  coverageRoot = root, planGate = null,
  lastPlan = null,
  detailGate = null, savedExperiment = null, saveGate = null,
  releaseInitial;
const initialGate = new Promise((r) => (releaseInitial = r));
w.fetch = async (url, opts = {}) => {
  const path = String(url).replace('/quant/api', ''),
    data = opts.body ? JSON.parse(opts.body) : null;
  calls.push({ path, data });
  let value = { items: [], total: 0 };
  if (path === capPath) {
    await initialGate;
    value = {
      enabled: compositionEnabled,
      profile: sourceProfile,
      composition: { online: compositionOnline },
      researchBindingEnabled: true,
    };
  } else if (path === '/financial/definitions')
    value = { items: [{ id: stateId, name: '现金资产占比' }] };
  else if (path.startsWith(apiRoot + '/sources/markets'))
    value = {
      items: [
        {
          name: '明确合成行情',
          scope,
          synthetic: true,
          rowCount: 243,
          sourceLabel: 'fixture',
          sourceRef: {
            kind: 'forecast_snapshot_view',
            runId: datasetId,
            expectedBundleId: root,
            expectedSnapshotSha256: root,
          },
          eligibility: { status: 'eligible' },
        },
      ],
      total: 1,
    };
  else if (path.startsWith(apiRoot + '/sources/financial'))
    value = {
      items: [
        {
          name: '已准备合成输入',
          financialRef,
          selection: { ...scope, selectedStateIds: [stateId] },
          unitPolicy: 'allow_declared',
          hasUsableStates: true,
        },
      ],
      total: 1,
    };
  else if (path === planPath) {
    lastPlan = data;
    if (planGate) await planGate;
    if (planFailures-- > 0) throw Error('明确网络失败，选择保留');
    value = { plan: { id: datasetId, planRoot: root, profile: sourceProfile, knownSourceBytes: 2048 } };
  } else if (path.includes('/start')) {
    if (startFailures-- > 0) throw Error('启动响应未知，保留原请求标识');
    value = { preparation: { id: datasetId, status: 'queued' } };
  } else if (path.startsWith(jobPath + '/'))
    value = {
      preparation: { id: datasetId, status: 'completed' },
      datasetRef: ref,
    };
  else if (path.startsWith(apiRoot + '/' + datasetId + '/coverage'))
    value = {
      items: [
        {
          symbol: '600000.SH',
          stateId,
          okRows: 183,
          missingRows: 60,
          firstObserved: '20240401',
          lastObserved: '20241231',
          latestPeriodEnd: '20231231',
        },
      ],
      total: 1, datasetRoot: coverageRoot,
    };
  else if (path.startsWith(apiRoot + '/' + datasetId + '?')) { if (detailGate) await detailGate; value = detail; }
  else if (path.startsWith('/statistical-quant/experiments') && ['POST', 'PUT'].includes(opts.method)) {
    if (saveGate) await saveGate;
    value = {
      experiment: savedExperiment = {
        id: datasetId,
        version: 1,
        strategy: validateStatisticalQuant(data.strategy),
        datasetBinding: {
          datasetRef: data.datasetRef,
          admissionProfile: data.admissionProfile,
          scope,
        },
      },
    };
  } else if (path === '/statistical-quant/experiments/' + datasetId) value = { experiment: savedExperiment };
  else if (path.endsWith('/run'))
    value = { job: { id: datasetId, status: 'queued' } };
  return { ok: true, status: 200, text: async () => JSON.stringify(value) };
};
const code = await build({
  entryPoints: ['web/main.js'],
  bundle: true,
  write: false,
  format: 'iife',
  plugins: [
    {
      name: 'no-init',
      setup(b) {
        b.onLoad({ filter: /\/web\/app\.js$/ }, async (a) => ({
          contents: (await fs.readFile(a.path, 'utf8')).replace(
            '  init();',
            '  window.qa={state,workspace,parseRoute,render};',
          ),
          loader: 'js',
        }));
      },
    },
  ],
});
w.eval(code.outputFiles[0].text);
const q = w.qa;
q.state.session = { workspace: { id: 'financial_owner_a' }, capabilities: { tushareHosted: true } };
const tick = () => new Promise((r) => setTimeout(r, 35));
async function route(hash) {
  w.location.hash = hash;
  q.parseRoute();
  q.render();
  await q.workspace.routeChanged();
  await tick();
}
const click = (a) => {
    const el = w.document.querySelector(`[data-ds="${a}"]`);
    assert(el, a);
    el.click();
  },
  input = (selector, value) => {
    const el = w.document.querySelector(selector);
    el.value = value;
    el.dispatchEvent(new w.Event('input', { bubbles: true }));
  };
q.parseRoute();
q.render();
q.workspace.routeChanged();
await tick();
assert(w.document.querySelector('main').textContent.includes('正在读取'));
assert(!w.document.querySelector('main').textContent.includes('没有可组成'));
releaseInitial();
await tick();
await tick();
click('choose-market');
await tick();
assert(w.location.hash.endsWith('/scope'));
assert.equal(w.document.querySelectorAll('[data-ds-symbol]').length, 1);
click('scope-next');
await tick();
click('toggle-financial');
click('review');
await tick();
input('#ds-name', '保留当前数据集名');
click('plan');
await tick();
assert(w.document.querySelector('main').textContent.includes('明确网络失败'));
assert.equal(w.document.querySelector('#ds-name').value, '保留当前数据集名');
const firstRequest = lastPlan.requestId;
click('plan');
await tick();
assert.equal(lastPlan.requestId, firstRequest);
assert.equal(lastPlan.marketSource.transform.mode, 'exact');
assert(!Object.hasOwn(lastPlan, 'marketCalendarRef'));
assert.equal(w.document.querySelector('[data-ds="start"]').disabled, true);
const frozenPlan = JSON.stringify(q.workspace.datasets.state.plan),
  frozenSelection = JSON.stringify({
    name: q.workspace.datasets.state.name,
    symbols: q.workspace.datasets.state.symbols,
    start: q.workspace.datasets.state.start,
    end: q.workspace.datasets.state.end,
    selected: q.workspace.datasets.state.selected,
  });
async function refreshNode(online) {
  compositionOnline = online;
  const before = calls.length;
  click('refresh-capabilities');
  assert(
    w.document
      .querySelector('main')
      .textContent.includes('正在读取准备节点状态'),
  );
  await tick();
  assert.deepEqual(
    calls.slice(before).map((x) => x.path),
    [capPath],
  );
  assert.equal(JSON.stringify(q.workspace.datasets.state.plan), frozenPlan);
  assert.equal(
    JSON.stringify({
      name: q.workspace.datasets.state.name,
      symbols: q.workspace.datasets.state.symbols,
      start: q.workspace.datasets.state.start,
      end: q.workspace.datasets.state.end,
      selected: q.workspace.datasets.state.selected,
    }),
    frozenSelection,
  );
  assert.equal(w.document.querySelector('[data-ds="start"]').disabled, !online);
}
await refreshNode(true);
click('start');
await tick();
assert(w.document.querySelector('main').textContent.includes('启动响应未知'));
const firstStartRequest = calls.find((x) => x.path.includes('/start')).data
  .requestId;
await refreshNode(false);
await refreshNode(true);
// Re-check after a heartbeat refresh keeps the original plan idempotency key.
click('plan');
await tick();
assert.equal(lastPlan.requestId, firstRequest);
click('start');
await tick();
await tick();
assert(w.document.querySelector('main').textContent.includes('已生成数据集'));
assert.equal(
  calls.filter((x) => x.path.includes('/start')).at(-1).data.requestId,
  firstStartRequest,
);
click('open-job-dataset');
await tick();
await tick();
assert(w.document.querySelector('main').textContent.includes('2024-04-01'));
assert(
  w.document.querySelector('a[download]').href.includes((graph ? 'download' : 'archive') + '?datasetRoot='),
);
assert(w.document.querySelector('[data-ds="bind"]').disabled,'a Ridge-capable runner does not silently substitute for auto');
if (graph) assert(!w.document.querySelector('[data-ds="bind-ridge"]'), 'graph has no declared Ridge entry');
else assert(!w.document.querySelector('[data-ds="bind-ridge"]').disabled,'Studio Ridge is an explicit separate action');
assert(w.document.querySelector('main').textContent.includes('计算节点尚未就绪'));
detail.researchAdmissions[1].runnerAvailable = true;
detail.preferredResearchAdmission = detail.researchAdmissions[1];
await q.workspace.datasets.routeChanged(true); await tick();
click('bind');
await tick();
assert.equal(q.state.dataSource, 'ready_dataset');
assert(w.location.hash.endsWith('/state'));
assert(w.document.querySelector('main').textContent.includes('现金资产占比'));
const initialStrategy = JSON.parse(JSON.stringify(q.state.strategy));
await route('#quant/easy/settings');
assert(w.document.querySelector('[data-sq-config="validation.testStart"]'));
assert(!w.document.querySelector('[data-sq-config="universe.start"]'), 'frozen source dates stay read only');
assert(!w.document.querySelector('[data-sq-config="universe.end"]'));
assert(!w.document.querySelector('[data-sq-config="target.horizonSessions"]'));
await route('#quant/easy/model');
assert.equal(w.document.querySelector('[data-sq-config="target.horizonSessions"]').value,'5');
assert.equal(w.document.querySelector('[data-sq-config="research.observationDays"]').value,'1');
assert(!w.document.querySelector('main').textContent.includes('h=1 不是下一日收盘'));
assert.deepEqual(JSON.parse(JSON.stringify(q.state.strategy)),initialStrategy,'moving controls does not migrate the frozen protocol');
await route('#quant/easy/state');
assert.doesNotThrow(() => validateStatisticalQuant(initialStrategy));
assert.equal(Object.hasOwn(initialStrategy.factors[0], 'name'), false);
assert.throws(
  () =>
    validateStatisticalQuant({
      ...initialStrategy,
      factors: [{ ...initialStrategy.factors[0], name: '现金资产占比' }],
    }),
  /因子包含尚未支持的字段：name/,
);
q.state.strategy.factors[0].id = 'custom_cash_id';
q.render();
let selected = w.document.querySelector('[data-sq-dataset-state]');
assert.equal(selected.checked, true);
selected.checked = false;
selected.dispatchEvent(new w.Event('change', { bubbles: true }));
assert.equal(q.state.strategy.factors.length, 0);
selected = w.document.querySelector('[data-sq-dataset-state]');
selected.checked = true;
selected.dispatchEvent(new w.Event('change', { bubbles: true }));
assert.equal(q.state.strategy.factors.length, 1);
assert.deepEqual(
  JSON.parse(JSON.stringify(q.state.datasetBinding.datasetRef)),
  ref,
);
assert.equal(q.state.strategy.model.estimator, 'auto');
assert(w.location.hash.includes('/easy/state'));
assert.equal(q.state.strategy.execution.enabled, false);
await route('#quant/universe');
assert(!w.document.querySelector('[data-sq-config="universe.start"]'));
assert(
  w.document.querySelector('main').textContent.includes('已冻结的研究范围'),
);
await route('#quant/model');
assert(
  w.document.querySelector('main').textContent.includes('基本面条件预测'),
);
assert(!w.document.querySelector('[data-sq-config="model.family"]'));
const savedVersion = await q.workspace.save();
assert(savedVersion, 'real server validator must accept the UI save payload');
assert.equal(q.state.dirty, false);
const saved = calls.find((x) => x.path === '/statistical-quant/experiments');
assert.deepEqual(saved.data.datasetRef, ref);
assert.equal(saved.data.admissionProfile, autoProfile);
assert.equal(lastPlan.profile, sourceProfile, 'composition protocol remains independent');
assert.doesNotThrow(() => validateStatisticalQuant(saved.data.strategy));
assert.equal(Object.hasOwn(saved.data.strategy.factors[0], 'name'), false);
assert.equal(q.state.datasetBinding.stateDefinitions[0].name, '现金资产占比');
if (!graph) {
// Old persisted Ridge versions remain Ridge and keep their original source/admission.
savedExperiment.strategy.model.estimator = 'ridge';
savedExperiment.datasetBinding.admissionProfile = 'financial_snapshot_view_50_v1';
const openOld = w.document.createElement('button');openOld.dataset.sq='experiment-load';openOld.dataset.id=datasetId;w.document.body.append(openOld);openOld.click();await tick();
await route('#quant/easy/model');
assert.equal(q.state.strategy.model.estimator,'ridge');
assert.equal(q.state.datasetBinding.admissionProfile,'financial_snapshot_view_50_v1');
assert(!w.document.querySelector('[data-sq-config="model.estimator"]'), 'Easy has no estimator picker even for a preserved Ridge version');
// Binding refresh is owner- and draft-fenced; it cannot replace edits made in flight.
await route('#quant/studio/datasets/dataset/' + datasetId + '?root=' + root);
let releaseDetail; detailGate = new Promise(resolve=>{releaseDetail=resolve;});
click('bind');await tick();const keptStrategy=JSON.stringify(q.state.strategy);
q.state.session.workspace.id='financial_owner_b';releaseDetail();await tick();detailGate=null;
assert.equal(JSON.stringify(q.state.strategy),keptStrategy);
assert.equal(q.state.strategy.model.estimator,'ridge');
q.render();
assert.equal(q.workspace.datasets.state.detail, null, 'another owner cannot see the completed private source');
q.state.session.workspace.id='financial_owner_a';q.render();
// Explicit Studio selection can create a new Ridge draft; default automatic entry remains distinct.
click('bind-ridge');await tick();
assert.equal(q.state.strategy.model.estimator,'ridge');assert(w.location.hash.includes('/studio/state'));
assert.equal(q.state.datasetBinding.admissionProfile,'financial_snapshot_view_50_v1');
} else {
  // A saved graph binding reopens with its exact source and estimator; no v2 fallback.
  const reopen = w.document.createElement('button');
  reopen.dataset.sq = 'experiment-load'; reopen.dataset.id = datasetId;
  w.document.body.append(reopen); reopen.click(); await tick();
  assert.equal(q.state.datasetBinding.datasetRef.version, 3);
  assert.equal(q.state.datasetBinding.admissionProfile, autoProfile);
  assert.equal(q.state.strategy.model.estimator, 'auto');
  assert.equal(q.state.datasetBinding.workspaceId, 'financial_owner_a');
  assert(w.document.querySelector(`a[href="${routeRoot}dataset/${datasetId}?root=${root}"]`));
  const beforeInvalidSave = calls.length;
  q.state.datasetBinding.admissionProfile = 'financial_fundamental_auto_50_v1';
  assert.equal(await q.workspace.save(), null, 'a legacy auto profile cannot save a graph source');
  assert.equal(calls.length, beforeInvalidSave);
  q.state.datasetBinding.admissionProfile = null; q.state.strategy.model.estimator = 'ridge';
  assert.equal(await q.workspace.save(), null, 'an undeclared graph Ridge/null tuple is also rejected');
  assert.equal(calls.length, beforeInvalidSave);
  q.state.datasetBinding.admissionProfile = autoProfile; q.state.strategy.model.estimator = 'auto';
  // A successful old save never replaces a binding or edited draft changed in flight.
  const retainedBinding = structuredClone(q.state.datasetBinding), retainedName = q.state.strategy.name;
  let releaseSave; saveGate = new Promise(resolve => { releaseSave = resolve; });
  const pendingSave = q.workspace.save(); await tick();
  q.state.datasetBinding.datasetRef = { ...q.state.datasetBinding.datasetRef, datasetRoot: '6'.repeat(64) };
  q.state.strategy.name = 'subsequent graph draft'; q.state.dirty = true;
  releaseSave(); await pendingSave; saveGate = null;
  assert.equal(q.state.datasetBinding.datasetRef.datasetRoot, '6'.repeat(64));
  assert.equal(q.state.strategy.name, 'subsequent graph draft');
  assert.equal(q.state.dirty, true);
  q.state.datasetBinding = retainedBinding; q.state.strategy.name = retainedName;
  const graphDetail = routeRoot + 'dataset/' + datasetId + '?root=' + root;
  // New composition and F research gates are independent: frozen sources remain readable.
  compositionEnabled = false;
  await route(routeRoot + 'review'); click('refresh-capabilities'); await tick();
  assert.equal(q.workspace.datasets.state.cap.enabled, false);
  await route(graphDetail);
  await q.workspace.datasets.routeChanged(true); await tick();
  assert(w.document.querySelector('[data-ds="bind"]') && !w.document.querySelector('[data-ds="bind"]').disabled);
  assert.equal(w.document.querySelector('a[download]').getAttribute('href'), `/quant/api/dataset-graphs/${datasetId}/download?datasetRoot=${root}`);
  // The source namespace must not reinterpret a valid legacy reference as graph.
  ref.version = 2;
  await q.workspace.datasets.routeChanged(true); await tick();
  assert(w.document.querySelector('main').textContent.includes('来源版本不一致'));
  assert(!w.document.querySelector('[data-ds="bind"]'));
  ref.version = 3; coverageRoot = 'f'.repeat(64);
  await q.workspace.datasets.routeChanged(true); await tick();
  assert(w.document.querySelector('main').textContent.includes('覆盖根'));
  assert(!w.document.querySelector('[data-ds="bind"]'));
  coverageRoot = root;
  await q.workspace.datasets.routeChanged(true); await tick();
  // An unrelated auto profile cannot enable a graph source, even if both booleans are true.
  detail.researchAdmissions[1].profile = 'financial_fundamental_auto_50_v1';
  await q.workspace.datasets.routeChanged(true); await tick();
  assert(w.document.querySelector('[data-ds="bind"]').disabled);
  detail.researchAdmissions[1].profile = autoProfile;
  await q.workspace.datasets.routeChanged(true); await tick();
  // Discard a late private binding response after identity changes, preserving the existing draft.
  let releaseDetail; detailGate = new Promise(resolve => { releaseDetail = resolve; });
  const beforeOwner = JSON.stringify(q.state.strategy);
  click('bind'); await tick();
  q.state.session.workspace.id = 'financial_owner_b'; q.render();
  releaseDetail(); await tick(); detailGate = null;
  assert.equal(JSON.stringify(q.state.strategy), beforeOwner);
  assert.equal(q.workspace.datasets.state.detail, null);
  assert(!w.document.querySelector('[data-ds="bind"]'));
  q.state.session.workspace.id = 'financial_owner_a';
  compositionEnabled = true;
  await route(routeRoot + 'review'); click('refresh-capabilities'); await tick();
  input('#ds-name', 'graph plan whose response arrives after namespace switch');
  let releasePlan; planGate = new Promise(resolve => { releasePlan = resolve; });
  click('plan'); await tick();
  const graphState = q.workspace.datasets.state;
  await route('#quant/studio/datasets/source');
  releasePlan(); await tick(); planGate = null;
  assert.equal(graphState.plan, null, 'late graph plan response cannot apply after switching protocol');
  assert.equal(q.workspace.datasets.state.market, null, 'legacy form does not inherit graph source');
  assert.equal(q.workspace.datasets.state.plan, null);
  assert.equal(w.location.hash, '#quant/studio/datasets/source');
  assert(calls.filter(x => x.path === planPath).every(x => Object.keys(x.data.financialInputs[0]).sort().join(',') === Object.keys(financialRef).sort().join(',')));
  assert(!calls.some(x => /tushare|acquisition/.test(x.path)), 'composition never calls a provider');
}
q.workspace.datasets.dispose();
q.workspace.financial.dispose();
dom.window.close();
console.log(
  JSON.stringify({
    realDOM: true, graphNamespace: graph,
    httpDoubles: true,
    loadingNotEmpty: true,
    independentSteps: 4,
    idempotentPlanRetry: true,
    inputRetention: true,
    heartbeatRefreshRetainsPlanAndBothRequestIds: true,
    coverageObservedDates: true,
    explicitForecastOnlyBinding: true, automaticFinancialAdmission: true, noRidgeFallback: true, compositionSeparate: true, legacyRidgePreserved: !graph, freshBindingOwnerFence: true, graphSavedBindingReopened: graph, graphSaveResponsePreservesChangedDraft: graph, versionAndCoverageRejected: graph, namespaceFence: graph,
    realServerValidationOnBindAndSave: true,
    browserVisualAcceptance: false,
  }),
);
