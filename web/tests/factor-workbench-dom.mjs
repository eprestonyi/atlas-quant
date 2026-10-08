/** Real UI events over declared API doubles; this is no provider or numerical evidence. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>', { url: 'http://localhost/quant/#quant/modes', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.structuredClone = structuredClone;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {} });
const symbols = Array.from({ length: 1000 }, (_, n) => `${600000 + n}.SH`);
let saved, savedPayload, scopeGate;
const scopeRequests = [];
w.fetch = async (url, options = {}) => {
  const path = String(url), body = options.body ? JSON.parse(options.body) : null;
  let result = { items: [], total: 0 };
  if (path.endsWith('/universes/resolve')) result = {
    selection: body.selection, symbols, symbolCount: 1000,
    members: symbols.map(ts_code => ({ ts_code, name: 'DOM 股票', area: '上海', industry: '材料' })),
    snapshotHash: 'a'.repeat(64), resolutionHash: 'b'.repeat(64),
    catalogSnapshot: { hash: 'a'.repeat(64), asOf: '2026-10-08', historicalMembershipVerified: false }, steps: [],
  };
  if (path.endsWith('/universe-scopes')) {
    if (scopeGate) await scopeGate;
    scopeRequests.push(body);
    result = { scopeRef: { scopeId: 'scope-' + scopeRequests.length, scopeRoot: 'c'.repeat(64), format: 'atlas.quant.universe_scope', version: 1 }, scope: { symbols, symbolCount: symbols.length, start: body.start, end: body.end } };
  }
  if (path.endsWith('/run')) return { ok: false, status: 409, text: async () => JSON.stringify({ error: { code: 'WHOLE_UNIVERSE_PROFILE_NOT_READY', message: '完整集合计算节点尚未就绪' } }) };
  if (path.includes('/statistical-quant/experiments') && ['POST', 'PUT'].includes(options.method)) {
    savedPayload = body;
    saved = body.strategy;
    result = { experiment: { id: 'workbench-test', version: 1, strategy: saved } };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(result) };
};
const result = await build({ entryPoints: ['web/main.js'], bundle: true, write: false, format: 'iife', plugins: [{ name: 'no-init', setup(b) {
  b.onLoad({ filter: /\/web\/app\.js$/ }, async args => ({ contents: (await fs.readFile(args.path, 'utf8')).replace('  init();', '  window.qa={state,studio,workspace,parseRoute,render,validateStrategy};'), loader: 'js' }));
} }] });
w.eval(result.outputFiles[0].text);
const q = w.qa, s = q.state;
s.loading = false;
s.session = { capabilities: { tushareHosted: true }, runner: { online: true } };
q.parseRoute(); q.render();
const tick = () => new Promise(resolve => setTimeout(resolve, 30));
const route = async path => { w.location.hash = `#quant/${path}`; await tick(); };
assert.equal(w.document.querySelectorAll('.sq-mode-card').length, 2);
assert(w.document.querySelector('a[href="#quant/easy/statistical"]'));
await route('easy/statistical');
assert.equal(w.document.querySelectorAll('.sq-module-card').length, 4);
assert(w.document.querySelector('a[href="#quant/easy/universe"]'));
await route('easy/universe');
await q.studio.flow.__test.resolve();
assert.equal(s.strategy.universe.symbols.length, 1000);
assert.equal(s.strategy.universe.subsetPolicy, 'all');
assert.equal(w.document.querySelectorAll('.sq-universe tbody tr').length, 40, 'pagination is display only');
assert(!w.document.querySelector('[data-rq-member]'));
assert(!w.document.querySelector('[data-v2="pool-take"]'));
assert(!w.document.querySelector('#rq-subset-codes'));
assert(!w.document.querySelector('[data-sq-config="universe.start"]'));
assert(!w.document.querySelector('.rq-source-row'));
assert(!w.document.querySelector('main').textContent.includes('/ 50'));
assert(!q.validateStrategy().some(x => /50|成员/.test(x)));
await route('easy/model');
assert.equal(w.document.querySelectorAll('[data-sq="family"]').length, 5);
assert(!w.document.querySelector('[data-sq-config="model.estimator"]'));
for (const family of ['trend', 'pair_reversion', 'event', 'fundamental']) {
  const card = w.document.querySelector(`[data-sq="family"][data-id="${family}"]`);
  assert(card.disabled, family + ' does not imply a thousand-member auto pipeline');
  assert.equal(card.querySelector('[data-mechanism-status]').dataset.mechanismStatus, 'blocked');
}
assert(!w.document.querySelector('[data-sq="family"][data-id="mean_reversion"]').disabled);
const fullUniverse = structuredClone(s.strategy.universe);
s.strategy.universe.symbols = symbols.slice(0, 50); s.strategy.model.estimator = 'hist_gradient_boosting'; q.render();
assert.equal(w.document.querySelectorAll('[data-sq="family"]').length, 5);
for (const family of ['trend', 'pair_reversion', 'event', 'fundamental']) assert(!w.document.querySelector(`[data-sq="family"][data-id="${family}"]`).disabled, family + ' has a conditional generic entry');
assert(w.document.querySelector('[data-sq="family"][data-id="pair_reversion"]').textContent.includes('明确两条篮子腿'));
assert(w.document.querySelector('[data-sq="family"][data-id="event"]').textContent.includes('普通日线行情不是事件源'));
w.document.querySelector('[data-sq="family"][data-id="trend"]').click(); await tick();
assert.equal(s.strategy.model.estimator, 'auto');
s.strategy.universe = fullUniverse; s.strategy.model.family = 'mean_reversion'; q.render();
await route('easy/settings');
assert(w.document.querySelector('.rq-source-row'));
assert(w.document.querySelector('[data-sq-config="universe.start"]'));
assert.equal(w.document.querySelector('[data-sq-config="research.observationDays"]').value, '1');
assert(w.document.querySelector('main').textContent.includes('最小可用粒度'));
await route('easy/validation');
assert(!w.document.querySelector('[data-sq-config="validation.innerFolds"]'));
assert(!w.document.querySelector('[data-sq-config="execution.enabled"]'));
await route('studio/validation');
assert(w.document.querySelector('[data-sq-config="validation.innerFolds"]'));
await route('easy/report');
s.strategy.execution.enabled = true;
s.strategy.model.estimator = 'ridge';
await q.workspace.save();
assert.equal(saved.execution.enabled, false);
assert.equal(saved.model.estimator, 'auto');
assert.equal(saved.universe.symbols.length, 1000);
assert.equal(savedPayload.universeScopeRef.scopeId, 'scope-1');
await route('easy/universe');
assert(w.document.querySelector('.sq-universe').textContent.includes('DOM 股票'), 'saving preserves metadata from the exact same frozen resolution');
await route('easy/report');
assert.equal(scopeRequests[0].expectedResolutionHash, 'b'.repeat(64));
assert(!Object.hasOwn(scopeRequests[0], 'symbols'), 'scope authority re-resolves selection');
await q.workspace.save();
assert.equal(scopeRequests.length, 1, 'unchanged frozen scope is reused');
s.strategy.universe.start = '20240201'; s.dirty = true;
await q.workspace.save();
assert.equal(scopeRequests.length, 2, 'changing research dates freezes a new scope');
assert.equal(savedPayload.universeScopeRef.scopeId, 'scope-2');
await assert.rejects(q.workspace.run(), /完整集合计算节点尚未就绪/);
await tick();
assert(w.document.querySelector('main').textContent.includes('运行准入未通过'));
assert(w.document.querySelector('main').textContent.includes('没有改成更小的股票子集'));
assert.equal(s.strategy.universe.symbols.length, 1000);
assert.equal(q.workspace.ui.activeId, 'workbench-test');
q.studio.flow.reset();
await route('easy/universe');
assert(w.document.querySelector('main').textContent.includes('已保存的冻结筛选集合'));
assert.equal([...w.document.querySelectorAll('.sq-universe table')].at(-1).querySelectorAll('thead th').length, 1, 'no metadata means code-only table, not invented current names');
assert.equal(w.document.querySelectorAll('.sq-universe tbody tr').length, 40);
const preservedUniverse = structuredClone(s.strategy.universe);
s.strategy.universe.subsetPolicy = 'explicit';
s.strategy.universe.symbols = symbols.slice(0, 20);
q.studio.flow.reset(); q.render();
assert(q.validateStrategy().some(x => x.includes('完整筛选集合')));
s.strategy.universe = preservedUniverse;
q.studio.flow.reset(); q.render();
// A delayed scope freeze belongs to the submitted study, even if another data binding is chosen.
let releaseScope;
scopeGate = new Promise(resolve => { releaseScope = resolve; });
s.strategy.universe.start = '20240301';
s.dirty = true;
const pendingSave = q.workspace.save();
await tick();
s.dataSource = 'ready_dataset';
s.datasetBinding = { datasetRef: { datasetId: 'other-dataset', datasetRoot: 'd'.repeat(64) }, admissionProfile: 'other-profile' };
releaseScope(); await pendingSave; scopeGate = null;
assert(!Object.hasOwn(savedPayload, 'datasetRef'), 'late binding must not contaminate older save');
assert(s.dirty, 'data source change is not marked saved by the old response');
s.dataSource = 'tushare'; s.datasetBinding = null;
for (const path of ['strategies', 'instruments', 'monitor']) {
  await route('easy/' + path);
  assert(!w.document.querySelector('[data-sq="run"]'));
  assert(!w.document.querySelector('[data-sq="forecast-execute"]'));
}
assert(w.document.querySelector('main').textContent.includes('独立 Atlas 自选股'));
assert(w.document.querySelector('main').textContent.includes('不改变训练票池'));
console.log(JSON.stringify({ modeModuleHierarchy: true, fullFilterSet: 1000, displayedMembers: 40, fiveMechanismsOnly: true, separateSettings: true, minimumDailyFrequency: true, automaticEstimator: true, forecastOnly: true, pendingModulesDoNotRun: true, apiDoubles: true }));
dom.window.close();
