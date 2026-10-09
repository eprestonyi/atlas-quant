/** Actual report/edit DOM events over explicit HTTP doubles; no F/provider calls. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>', {
  url: 'http://localhost/quant/#quant/researches', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
w.structuredClone = structuredClone;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {} });
let response, saved, detailGate;
const calls = [];
w.fetch = async (url, options = {}) => {
  const path = String(url).replace('/quant/api', '');
  calls.push({ path, method: options.method || 'GET' });
  assert.equal(options.method || 'GET', 'GET', 'editing a report must not submit provider, F, or save requests');
  let value = { items: [], total: 0 };
  if (path.startsWith('/runs/')) value = response;
  if (path.startsWith('/statistical-quant/experiments?')) value = { items: [saved], total: 1 };
  if (path === '/statistical-quant/experiments/saved-research') {
    if (detailGate) await detailGate;
    value = { experiment: saved, runs: [{ ...response.job, experimentVersion: 1 }] };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(value) };
};
const app = await build({ entryPoints: ['web/main.js'], bundle: true, write: false, format: 'iife',
  plugins: [{ name: 'no-init', setup(b) {
    b.onLoad({ filter: /\/web\/app\.js$/ }, async f => ({ loader: 'js', contents: (await fs.readFile(f.path, 'utf8')).replace('  init();', '  window.qa={state,workspace,parseRoute,render};') }));
  } }],
});
w.eval(app.outputFiles[0].text);
const q = w.qa, s = q.state;
s.loading = false;
s.session = { workspace: { id: 'owner-a' }, capabilities: { tushareHosted: true }, runner: { online: true } };
const tick = () => new Promise(resolve => setTimeout(resolve, 30));
const root = 'b'.repeat(64), id = '11111111-1111-4111-8111-111111111111';
let ordinal = 0;
const protectedDraft = () => JSON.stringify({ strategy: s.strategy, source: s.dataSource, dataset: s.datasetBinding, market: s.marketDatasetBinding, activeId: q.workspace.ui.activeId, activeVersion: q.workspace.ui.activeVersion, scope: q.workspace.ui.universeScope, boundSource: q.workspace.ui.boundSource, local: w.localStorage.getItem('atlas-quant-statistical-draft-v2') });
async function showReport(job, report = null, transport = null) {
  response = { job: { id: 'report-' + ++ordinal, name: 'Original run v1', status: 'failed', strategy: structuredClone(s.strategy), error: { code: 'CAPACITY_MONITOR', message: '数据集请求或回执不符合固定协议。' }, ...job }, report, transport };
  w.location.hash = '#runs/' + response.job.id;
  await tick(); await tick();
}
async function assertBlockedContinuation() {
  const before = protectedDraft(), original = JSON.stringify(response), route = w.location.hash;
  assert(w.document.querySelector('[data-report-saved-edit]'));
  assert.equal(w.document.querySelector('[data-action="fork-run"]'), null);
  assert.match(w.document.querySelector('[data-report-source-hint]').textContent, /当前保存版本可能晚于本次运行/);
  assert.equal(w.document.querySelector('[data-report-source-hint]').tagName, 'DETAILS');
  assert.equal(w.document.querySelector('[data-report-source-hint]').open, false, 'editing explanation starts collapsed');
  // A stale previously rendered button must be guarded as well, before any mutation.
  const stale = w.document.createElement('button');
  stale.dataset.action = 'fork-run'; w.document.body.append(stale); stale.click(); await tick(); stale.remove();
  assert.equal(protectedDraft(), before);
  assert.equal(w.location.hash, route);
  assert.match(w.document.querySelector('#toast-root').textContent, /已保存版本/);
  assert.equal(JSON.stringify(response), original, 'historical run/error bytes stay unchanged');
}
for (const version of [1, 2, 3]) {
  s.strategy = w.AtlasQuantV4.defaultStrategy(); s.strategy.name = 'Unrelated unsaved draft';
  s.datasetBinding = null; s.marketDatasetBinding = null; s.dataSource = 'demo';
  const strategy = w.AtlasQuantV4.defaultStrategy();
  strategy.name = 'Saved research v2'; strategy.universe.symbols = ['600000.SH']; strategy.model.family = 'fundamental';
  strategy.model.estimator = version === 1 ? 'ridge' : 'auto';
  const ref = { datasetId: id, datasetRoot: root, format: 'atlas.quant.research_dataset', version };
  saved = { id: 'saved-research', version: 2, strategy, datasetBinding: {
    datasetRef: ref, admissionProfile: version === 1 ? 'financial_compose_50_v1' : version === 2 ? 'financial_fundamental_auto_50_v1' : 'financial_fundamental_graph_auto_50_v1',
    scope: structuredClone(strategy.universe), selectedStateIds: strategy.factors.map(f => f.id),
  } };
  await showReport({ dataSource: 'ready_dataset' });
  await assertBlockedContinuation();
  assert.match(w.document.querySelector('[role="alert"]').textContent, /资源或拟合进度监测未能完成/);
  assert.equal(w.document.querySelector('[role="alert"] details p').textContent, response.job.error.message);
  const before = protectedDraft();
  w.document.querySelector('[data-report-saved-edit]').click(); await tick(); await tick();
  assert.equal(w.location.hash, '#quant/researches');
  assert.equal(protectedDraft(), before, 'opening the list does not replace the draft');
  w.document.querySelector('[data-sq="experiment-load"]').click(); await tick(); await tick();
  assert.equal(s.dataSource, 'ready_dataset');
  assert.equal(s.datasetBinding.datasetRef.version, version);
  assert.equal(s.datasetBinding.datasetRef.datasetRoot, root);
  assert.equal(q.workspace.ui.activeVersion, 2, 'opens selected current v2, not the run v1');
  assert.equal(s.strategy.name, 'Saved research v2');
  assert.deepEqual([...s.strategy.universe.symbols], ['600000.SH']);
}
await showReport({ dataSource: 'ready_market' });
await assertBlockedContinuation();
// Source evidence still prevents fallback if a historical job omitted dataSource.
await showReport({ dataSource: undefined, status: 'completed', error: null }, { schemaVersion: 2, forecasts: {}, provenance: {} }, { format: 'atlas.quant.financial_bundle', version: 2 });
await assertBlockedContinuation();
await showReport({ dataSource: undefined, status: 'completed', error: null }, { schemaVersion: 2, forecasts: {}, provenance: { marketSource: { marketDatasetRef: { datasetId: id, datasetRoot: root } } } }, { format: 'unknown', version: 1 });
await assertBlockedContinuation();
// No reattachment after the user changes workspace while the explicit saved edit loads.
w.document.querySelector('[data-report-saved-edit]').click(); await tick(); await tick();
let release; detailGate = new Promise(resolve => { release = resolve; });
const before = protectedDraft();
w.document.querySelector('[data-sq="experiment-load"]').click(); await tick();
s.session.workspace.id = 'owner-b'; release(); detailGate = null; await tick();
assert.equal(protectedDraft(), before);
// Ordinary known-source reports retain the existing continuation entry.
await showReport({ dataSource: 'demo', error: { code: 'OTHER', message: '<script>untrusted</script>' } });
assert(w.document.querySelector('[data-action="fork-run"]'));
assert.equal(w.document.querySelector('[data-report-saved-edit]'), null);
assert.equal(w.document.querySelector('main script'), null);
assert(w.document.querySelector('[role="alert"]').textContent.includes('<script>untrusted</script>'));
console.log(JSON.stringify({ frozenDatasetVersions: [1, 2, 3], marketGuard: 'PASS', savedCurrentVersion: 'v2 explicitly selected', staleActionPreservesDraft: 'PASS', ownerFence: 'PASS', safeErrorCopy: 'PASS', writes: calls.filter(x => x.method !== 'GET').length, actualProvider: false, actualF: false }));
w.close();
