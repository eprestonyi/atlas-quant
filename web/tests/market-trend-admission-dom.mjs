/** Real five-choice/binding events with server-generated capability DTOs and HTTP doubles. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
import { marketResearchAdmissions, AUTO_PROFILE, TREND_AUTO_PROFILE } from '../../edge/market-preparation/admissions.mjs';
import { marketBindingErrors } from '../quant-workspace/research-data-binding.js';
const id = n => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`, root = 'a'.repeat(64);
const ref = { datasetId: id(1), datasetRoot: root, format: 'atlas.quant.market_dataset', version: 1 };
const scopeRef = { scopeId: id(2), scopeRoot: 'b'.repeat(64), format: 'atlas.quant.universe_scope', version: 1 };
const fields = ['open', 'high', 'low', 'close', 'vol', 'amount', 'adj_factor'];
let trendEnabled = false, marketEnabled = true, runnerTrend = true, runnerFresh = true, transportSupported = true, omitTrend = false, detailGate = null, badRoot = false;
const declaration = () => marketResearchAdmissions({ MARKET_RESEARCH_ENABLED: String(marketEnabled), MARKET_TREND_AUTO_ENABLED: String(trendEnabled), DB: { prepare: () => ({ first: async () => ({ updated_at: new Date(Date.now() - (runnerFresh ? 0 : 121000)).toISOString(), value: JSON.stringify({ marketResearchProfiles: [AUTO_PROFILE, ...(runnerTrend ? [TREND_AUTO_PROFILE] : [])], transportFormats: transportSupported ? ['atlas.quant.bundle/1'] : [] }) }) }) } });
const dom = new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>', { url: 'http://localhost/quant/#quant/easy/model', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, calls = [];
w.structuredClone = structuredClone; w.scrollTo = () => {}; w.matchMedia = () => ({ matches: false, addEventListener() {} });
let saved, original, q, queuedDetailReads = null, returnedFields = fields;
w.fetch = async (url, options = {}) => {
  const path = String(url).replace('/quant/api', ''), input = options.body ? JSON.parse(options.body) : null;
  calls.push({ path, method: options.method || 'GET', input });
  assert(!path.startsWith('/market-preparation-'), 'existing source reuse does not create/read acquisition plans or jobs');
  assert(!path.endsWith('/run'), 'this acceptance does not submit an F run');
  let out = { items: [], total: 0 };
  if (path.startsWith('/market-datasets/')) {
    assert.equal(path, `/market-datasets/${ref.datasetId}?datasetRoot=${ref.datasetRoot}`);
    const capabilities = await declaration();
    if (omitTrend) capabilities.researchAdmissions = capabilities.researchAdmissions.filter(x => x.admissionProfile !== TREND_AUTO_PROFILE);
    out = { marketDatasetRef: badRoot ? { ...ref, datasetRoot: 'c'.repeat(64) } : ref, universeScopeRef: scopeRef, scope: original.marketDatasetBinding.scope, fields: returnedFields, rowCount: 262000, sourceKind: 'fixture', ...capabilities };
    if (detailGate) await detailGate;
    if (queuedDetailReads) {
      const response = await new Promise(resolve => queuedDetailReads.push({ resolve, out }));
      out = response;
    }
  } else if (path === '/statistical-quant/experiments/' + id(3)) {
    if (options.method === 'PUT') {
      saved = { ...saved, version: input.version + 1, strategy: input.strategy, marketDatasetBinding: { ...saved.marketDatasetBinding, marketDatasetRef: input.marketDatasetRef, admissionProfile: input.admissionProfile, universeScopeRef: input.universeScopeRef } };
    }
    out = { experiment: saved };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(out) };
};
const app = await build({ entryPoints: ['web/main.js'], bundle: true, write: false, format: 'iife', plugins: [{ name: 'no-init', setup(b) { b.onLoad({ filter: /\/web\/app\.js$/ }, async f => ({ loader: 'js', contents: (await fs.readFile(f.path, 'utf8')).replace('  init();', '  window.qa={state,workspace,parseRoute,render};') })); } }] });
w.eval(app.outputFiles[0].text); q = w.qa; const s = q.state;
s.loading = false; s.session = { workspace: { id: 'owner-a' }, capabilities: { tushareHosted: true } };
delete s.strategy.validation.testStart; // This fixture retains its pre-existing fraction split.
s.strategy.universe = { symbols: Array.from({ length: 1000 }, (_, n) => `${600000 + n}.SH`), start: '20240101', end: '20241231', selection: { version: 1, includeGroups: [{ id: 'all', filters: [{ field: 'exchange', value: 'SSE' }] }], excludeGroups: [], includeSymbols: [], excludeSymbols: [] }, subsetPolicy: 'all', resolutionHash: 'b'.repeat(64), snapshotHash: 'b'.repeat(64) };
s.strategy.factors = [{ id: 'declared_close', expression: 'rank(close)', direction: 1, role: 'predictor' }];
original = { id: id(3), version: 1, strategy: structuredClone(s.strategy), universeScopeRef: scopeRef, marketDatasetBinding: { marketDatasetRef: ref, admissionProfile: AUTO_PROFILE, universeScopeRef: scopeRef, scope: { symbols: [...s.strategy.universe.symbols], start: s.strategy.universe.start, end: s.strategy.universe.end, symbolCount: 1000, scopeRoot: scopeRef.scopeRoot } } };
saved = structuredClone(original);
q.parseRoute(); q.render();
const tick = () => new Promise(resolve => setTimeout(resolve, 35));
const route = async step => { w.location.hash = '#quant/easy/' + step; await tick(); await tick(); };
const click = async selector => { const el = w.document.querySelector(selector); assert(el && !el.disabled, selector); el.click(); await tick(); await tick(); };
const open = async () => { const el = w.document.createElement('button'); el.dataset.sq = 'experiment-load'; el.dataset.id = id(3); w.document.body.append(el); el.click(); await tick(); el.remove(); };
const trendCard = () => w.document.querySelector('[data-sq="family"][data-id="trend"]');
await open(); await route('model');
assert.equal(q.workspace.market.state.plan, null);
assert.equal(w.document.querySelectorAll('[data-sq="family"]').length, 5);
assert(!w.document.querySelector('[data-sq-config="model.estimator"]'));
assert(trendCard().disabled); assert(trendCard().textContent.includes('暂不可用'));
for (const mutate of [() => { trendEnabled = true; runnerTrend = false; }, () => { runnerTrend = true; runnerFresh = false; }, () => { runnerFresh = true; transportSupported = false; }, () => { transportSupported = true; marketEnabled = false; }, () => { marketEnabled = true; omitTrend = true; }]) {
  mutate(); q.workspace.market.routeChanged(); await tick();
  assert(trendCard().disabled, 'missing exact fresh server proof keeps trend disabled');
  assert.equal(s.strategy.model.family, 'mean_reversion');
}
omitTrend = false; q.workspace.market.routeChanged(); await tick();
assert(!trendCard().disabled, 'both flags, exact fresh runner profile and transport permit the candidate entry');
for (const family of ['pair_reversion', 'fundamental', 'event']) assert(w.document.querySelector(`[data-sq="family"][data-id="${family}"]`).disabled);
await click('[data-sq="family"][data-id="trend"]');
assert.equal(s.strategy.model.family, 'trend'); assert.equal(s.strategy.model.estimator, 'auto');
assert.equal(s.marketDatasetBinding.admissionProfile, AUTO_PROFILE, 'choosing a mechanism does not mutate the frozen compute binding');
assert(marketBindingErrors(s, { run: true }).length);
await route('model');
assert(w.document.querySelector('main').textContent.includes('合成行情 · 仅供测试'));
assert(w.document.querySelector('[data-sq="market-rebind"]'));
const before = JSON.stringify(s.marketDatasetBinding);
trendEnabled = false; await click('[data-sq="market-rebind"]');
assert.equal(JSON.stringify(s.marketDatasetBinding), before, 'late flag revocation blocks binding on a fresh read');
assert(q.workspace.market.state.error.includes('尚未开放'));
trendEnabled = true; q.workspace.market.routeChanged(); await tick();
let release; detailGate = new Promise(resolve => { release = resolve; });
w.document.querySelector('[data-sq="market-rebind"]').click(); await tick();
const oldStart = s.strategy.universe.start; s.strategy.universe.start = '20240201'; release(); detailGate = null; await tick();
assert.equal(JSON.stringify(s.marketDatasetBinding), before, 'scope edit during fresh read preserves old binding');
s.strategy.universe.start = oldStart; q.render();
badRoot = true; await click('[data-sq="market-rebind"]');
assert.equal(JSON.stringify(s.marketDatasetBinding), before, 'different returned root cannot rebind');
badRoot = false; q.workspace.market.routeChanged(); await tick();
returnedFields = fields.filter(x => x !== 'close');
await click('[data-sq="market-rebind"]');
assert.equal(JSON.stringify(s.marketDatasetBinding), before, 'source missing a factor field cannot rebind');
assert(q.workspace.market.state.error.includes('close'));
returnedFields = fields; q.workspace.market.routeChanged(); await tick();
// A ready rebind read is superseded by a route read whose result is still pending.
// Neither the former cached declaration nor A may authorize the new binding.
queuedDetailReads = [];
w.document.querySelector('[data-sq="market-rebind"]').click(); await tick();
q.workspace.market.routeChanged(); await tick();
assert.equal(queuedDetailReads.length, 2);
queuedDetailReads[0].resolve(queuedDetailReads[0].out); await tick();
assert.equal(JSON.stringify(s.marketDatasetBinding), before, 'superseded ready A cannot bind while newer B is pending');
const latest = queuedDetailReads[1].out;
latest.researchAdmissions.find(x => x.admissionProfile === TREND_AUTO_PROFILE).available = false;
latest.researchAdmissions.find(x => x.admissionProfile === TREND_AUTO_PROFILE).reason = 'RUNNER_OFFLINE';
queuedDetailReads[1].resolve(latest); queuedDetailReads = null; await tick();
assert(w.document.querySelector('[data-sq="market-rebind"]').disabled);
assert.equal(JSON.stringify(s.marketDatasetBinding), before);
q.workspace.market.routeChanged(); await tick();
await click('[data-sq="market-rebind"]');
assert.equal(s.marketDatasetBinding.admissionProfile, TREND_AUTO_PROFILE);
assert.deepEqual(JSON.parse(JSON.stringify(s.marketDatasetBinding.marketDatasetRef)), ref);
assert.equal(s.strategy.universe.symbols.length, 1000);
assert.equal(marketBindingErrors(s, { run: true }).length, 0);
assert(w.document.querySelector('main').textContent.includes('趋势条件预测 · 自动选择'));
await q.workspace.save();
const savedRequest = calls.findLast(x => x.method === 'PUT');
assert.equal(savedRequest.input.admissionProfile, TREND_AUTO_PROFILE);
assert.deepEqual(savedRequest.input.marketDatasetRef, ref);
assert.equal(savedRequest.input.strategy.model.family, 'trend');
assert.equal(savedRequest.input.strategy.universe.symbols.length, 1000);
assert.equal(original.version, 1); assert.equal(original.strategy.model.family, 'mean_reversion'); assert.equal(original.marketDatasetBinding.admissionProfile, AUTO_PROFILE);
assert.equal(saved.version, 2);
s.marketDatasetBinding = null; q.workspace.market.state.sources = {};
await open(); await route('model');
assert.equal(s.marketDatasetBinding.admissionProfile, TREND_AUTO_PROFILE);
assert(w.document.querySelector('main').textContent.includes('趋势条件预测 · 自动选择'));
assert.equal(q.workspace.market.state.plan, null);
assert(calls.filter(x => x.method !== 'GET').every(x => x.method === 'PUT' && x.path.startsWith('/statistical-quant/experiments/')));
console.log(JSON.stringify({ exactServerCapabilityMatrix: true, defaultFalse: true, fiveMechanismsOnly: true, frozenSourceRebind: true, noNewSourcePlanOrJob: true, sourceAndScopePinned: true, missingSourceFieldBlocks: true, revokedFlagBlocks: true, supersededReadBlocks: true, savedVersionReopen: true, originalMeanVersionUnchanged: true, providerCalls: 0, actualF: false, httpDoubles: true }));
w.close();
