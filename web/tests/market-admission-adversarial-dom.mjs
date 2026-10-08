/** Actual preparation clicks against HTTP doubles; no provider/F/service work. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
import { validateCapacity } from '../../edge/market-preparation/research.mjs';
import { validateExpression } from '../../edge/factor-language.mjs';
const id = n => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`;
const root = 'a'.repeat(64), profile = 'pooled_asset_1000_auto_candidate_v1';
const scopeRef = { scopeId: id(1), scopeRoot: root, format: 'atlas.quant.universe_scope', version: 1 };
const admission = { admissionProfile: profile, available: true, families: ['mean_reversion'], estimator: 'auto', targetKind: 'asset_price', executionEnabled: false, maxSymbols: 1000, maxCalendarDays: 366, maxFactors: 16, innerFolds: 2, outerFolds: 2, minRefitDays: 20 };
const code = await build({ entryPoints: ['web/main.js'], bundle: true, write: false, format: 'iife', plugins: [{ name: 'no-init', setup(b) {
  b.onLoad({ filter: /\/web\/app\.js$/ }, async f => ({ loader: 'js', contents: (await fs.readFile(f.path, 'utf8')).replace('  init();', '  window.qa={state,workspace,parseRoute,render};') }));
} }] });
const tick = () => new Promise(resolve => setTimeout(resolve, 25));
const deferred = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };
async function harness() {
  const dom = new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>', { url: 'http://localhost/quant/#quant/easy/settings', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, calls = [], planReads = [];
  w.structuredClone = structuredClone; w.scrollTo = () => {}; w.matchMedia = () => ({ matches: false, addEventListener() {} });
  let plan, q;
  w.fetch = async (url, options = {}) => {
    const path = String(url).replace('/quant/api', ''), input = options.body ? JSON.parse(options.body) : null;
    calls.push({ path, method: options.method || 'GET' });
    let out = { items: [], total: 0 };
    if (path === '/universe-scopes') out = { scopeRef, scope: { ...q.state.strategy.universe, symbolCount: 1000 } };
    if (path === '/market-preparation-plans') {
      const u = q.state.strategy.universe;
      plan = { planRef: { planId: id(2), planRoot: 'b'.repeat(64), format: 'atlas.quant.market_acquisition_plan', version: 1 }, universeScopeRef: scopeRef, profile: 'pooled_asset_1000_v1', scope: { symbols: u.symbols, symbolCount: 1000, start: u.start, end: u.end, scopeRoot: root }, fields: ['open', 'high', 'low', 'close', 'vol', 'amount', 'adj_factor', ...input.requiredFields], budget: { declaredRequests: 2001, rawResponseCeilingBytes: 1048576 }, blockedReasons: [], canStart: true, status: 'planned', providerCalls: 0, researchAdmissions: [structuredClone(admission)] };
      out = plan;
    } else if (path.endsWith('/start')) out = { job: { id: id(3), planId: id(2), status: 'queued', phase: null, result: null } };
    else if (path === '/market-preparation-plans/' + id(2)) out = planReads.length ? await planReads.shift().promise : plan;
    return { ok: true, status: 200, text: async () => JSON.stringify(out) };
  };
  w.eval(code.outputFiles[0].text); q = w.qa;
  q.state.loading = false;
  q.state.session = { workspace: { id: 'owner-a' }, capabilities: { tushareHosted: true } };
  q.state.dataSource = 'ready_market';
  q.state.strategy.universe = { symbols: Array.from({ length: 1000 }, (_, n) => `${600000 + n}.SH`), start: '20250101', end: '20251231', selection: { version: 1, includeGroups: [{ id: 'all', filters: [{ field: 'exchange', value: 'SSE' }] }], excludeGroups: [], includeSymbols: [], excludeSymbols: [] }, subsetPolicy: 'all', snapshotHash: root, resolutionHash: root };
  q.parseRoute(); q.render();
  w.document.querySelector('[data-sq="market-plan"]').click(); await tick();
  const start = () => { const button = w.document.querySelector('[data-sq="market-start"]'); button.disabled = false; button.click(); };
  return { w, q, calls, planReads, plan: () => plan, start, starts: () => calls.filter(x => x.path.endsWith('/start')).length, close: () => w.close() };
}
const outcomes = [];
for (const [name, mutate] of [
  ['refit_out_of_range', s => { s.model.refitDays = 127; }],
  ['hedge_on_asset_price', s => { s.factors = [{ id: 'local_hedge', expression: 'close', direction: 1, role: 'hedge' }]; }],
  ['missing_external_field', s => { s.factors = [{ id: 'local_ext', expression: 'rank(ext_custom) + close', direction: 1, role: 'predictor' }]; }],
]) {
  const h = await harness(); mutate(h.q.state.strategy); h.q.render();
  const strategy = structuredClone(h.q.state.strategy), before = JSON.stringify(strategy);
  if (name === 'missing_external_field') {
    validateCapacity(strategy, profile);
    assert(validateExpression(strategy.factors[0].expression).fields.some(f => !h.plan().fields.includes(f)));
  } else assert.throws(() => validateCapacity(strategy, profile));
  const readiness = h.q.workspace.market.researchAdmission().status;
  h.start(); await tick(); await tick();
  outcomes.push({ name, readiness, starts: h.starts(), unchanged: before === JSON.stringify(h.q.state.strategy), reason: h.q.workspace.market.state.error });
  h.close();
}
{
  const h = await harness(), a = deferred(), b = deferred();
  h.planReads.push(a, b); h.start(); await tick();
  h.q.workspace.market.routeChanged(); await tick();
  assert.equal(h.planReads.length, 0, 'A and B have both entered their independent GET reads');
  const offline = structuredClone(h.plan()); offline.researchAdmissions[0].available = false; offline.researchAdmissions[0].reason = 'RUNNER_OFFLINE';
  a.release(offline); await tick(); await tick();
  outcomes.push({ name: 'superseded_offline_A_pending_B', starts: h.starts(), reason: h.q.workspace.market.state.error });
  b.release(h.plan()); await tick(); h.close();
}
console.log(JSON.stringify({ cases: outcomes, actualProvider: false, actualF: false }));
assert(outcomes.every(x => x.starts === 0 && x.unchanged !== false), 'all rejected declarations and superseded reads must spend zero new preparation requests');
