/** UI fixtures verify saved-model presentation, not research quality or live providers. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM } from 'jsdom';
import { createForms } from '../quant-workspace/forms.js';
import { createForecastReports } from '../quant-workspace/reports.js';
import { renderSavedModel, modelFormula } from '../quant-workspace/report-model.js';

const golden = JSON.parse(fs.readFileSync('engine/tests/fixtures/model-function-golden-v1.json', 'utf8'));
const linear = golden.cases.find(x => x.name === 'ridge').artifact;
const constant = golden.cases.find(x => x.name === 'historical_drift').artifact;
const forest = golden.cases.find(x => x.name === 'hist_gradient_boosting').artifact;
const dom = new JSDOM('<main></main><div id="modal-root"></div>', { url: 'http://localhost/quant/' });
globalThis.document = dom.window.document;
const main = document.querySelector('main');
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const number = (x, n = 2) => x == null ? '—' : Number(x).toFixed(n);
const fits = [linear, constant, forest].map((functionArtifact, n) => ({ id: `fit-${n}`, functionArtifact, estimator: functionArtifact.provenance.estimator, fitDate: `2025010${n + 1}`, ...functionArtifact.training, params: functionArtifact.provenance.parameters }));
const report = {
  engineVersion: 'UI_FIXTURE', selection: { evidenceStatus: 'NO_VALIDATED_FORECAST_EDGE' },
  execution: { enabled: false },
  forecasts: { artifactId: 'a'.repeat(64), factorResearch: { diagnostics: { features: [], dependence: {} } }, modelFits: fits, rows: [], targetDefinitions: [], diagnostics: { metrics: {} } }
};
let activeReport = report, apiCalls = [], detailGate = null, rejectDetail = false, wrongFit = false;
const C = {
  state: { runId: 'run-a', reportTransport: null }, esc, fmt: number, pct: x => x == null ? '—' : `${number(x * 100)}%`, dateText: x => x || '—', icon: () => '',
  render: () => { main.innerHTML = reports.render(activeReport); },
  openModal: (_title, body) => { document.querySelector('#modal-root').innerHTML = body; }, closeModal() {}, download() {}, toast() {},
  api: async path => {
    apiCalls.push(path);
    const url = new URL(path, 'http://localhost');
    const bundleId = url.searchParams.get('bundleId');
    if (path.includes('/pages?')) {
      assert.equal(url.searchParams.get('limit'), '25');
      assert.equal(url.searchParams.get('collection'), 'modelFits');
      return { bundleId, items: fits.map(({ functionArtifact, ...fit }) => fit), total: fits.length, offset: 0, hasMore: false };
    }
    const id = url.searchParams.get('id');
    assert.equal(url.searchParams.get('collection'), 'modelFits');
    if (detailGate) await detailGate;
    if (rejectDetail) throw Error('fixture read interrupted');
    return { bundleId, item: wrongFit ? fits[1] : fits.find(x => x.id === id) };
  }
};
const F = createForms(C), reports = createForecastReports(C, F);
document.addEventListener('change', event => reports.onChange(event.target));
document.addEventListener('click', event => { const el = event.target.closest('[data-sq]'); if (el) reports.handle(el); });
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const select = (id, value) => { const el = document.getElementById(id); assert(el); el.value = value; el.dispatchEvent(new dom.window.Event('change', { bubbles: true })); };
const before = JSON.stringify(report);
C.render();
assert.equal(reports.ui.tab, 'models');
const saved = main.querySelector('[data-saved-function]');
assert(saved && !saved.closest('details'), 'actual F is immediately visible without opening an audit modal');
assert.equal(saved.dataset.savedFunction, linear.artifactId);
assert(main.querySelector('.sq-model-primary > header #sq-model-fit'), 'fit picker shares the model header');
assert.equal(main.querySelector('.sq-model-primary > .sq-panel-body').firstElementChild.className, 'sq-saved-model', 'model formula starts the primary panel body');
assert.equal(linear.identity.scale, 'origin_known_gross_absolute_leg_value');
assert(saved.querySelector('[data-model-scale]').textContent.includes('scale = Σ |qⱼ pⱼ,ₜ|'));
assert(saved.querySelector('[data-model-scale]').textContent.includes('P = Σ qⱼ pⱼ,ₜ'));
assert(!saved.textContent.includes('波动率'), 'known gross value must not be labeled a volatility scale');
assert(saved.textContent.includes(String(linear.estimator.intercepts[1])));
for (const coefficient of linear.estimator.coefficients[1]) assert(saved.textContent.includes(String(Math.abs(coefficient))), 'formula contains exact saved coefficient precision');
assert.equal(main.querySelector('.sq-model-hyperparameters dt').textContent, 'alpha');
for (const value of linear.transforms.scaleScale) assert(main.textContent.includes(String(value)));
assert(main.textContent.includes('20日状态偏离'));
assert(main.textContent.indexOf('模型参数') < main.textContent.lastIndexOf('拟合数据'), 'function and parameters precede fit data');
assert(!main.querySelector('[data-sq="forecast-tab"][data-id="execution"]'));
assert(main.querySelector('[data-sq="forecast-tab"][data-id="correlations"]'));
assert(main.querySelector('[data-sq="forecast-tab"][data-id="joints"]'));
assert.equal(apiCalls.length, 0, 'local frozen artifact needs no API or fitting');
select('sq-model-fit', fits[1].id);
assert(main.querySelector('[data-saved-function]').textContent.includes(String(constant.estimator.value[1])));
assert(!main.textContent.includes('输入标准化'), 'constant model does not claim to apply transforms');
select('sq-model-fit', fits[2].id);
assert.equal(main.querySelectorAll('.sq-model-formulas .sq-model-equation').length, 2);
assert(main.querySelector('.sq-model-equation').textContent.includes(`Σ(m = 1…${forest.estimator.outputs[1].trees.length})`));
assert(!main.querySelector('.sq-model-equation').textContent.includes('β'));
let treeRows = main.querySelector('.sq-saved-model table tbody').rows;
assert.equal(treeRows.length, forest.estimator.outputs[1].trees[0].length, 'default future tree displays every actual node');
assert(treeRows[0].textContent.includes(String(forest.estimator.outputs[1].trees[0][0][2])));
select('sq-model-tree-output', '0'); select('sq-model-tree-index', '1');
treeRows = main.querySelector('.sq-saved-model table tbody').rows;
assert.equal(treeRows.length, forest.estimator.outputs[0].trees[1].length);
assert(treeRows[0].textContent.includes(String(forest.estimator.outputs[0].trees[1][0][2])));
assert.equal(JSON.stringify(report), before);
main.innerHTML = renderSavedModel(C, F, { ...fits[0], functionArtifact: { ...linear, inputSchema: [{ name: '<img src=x onerror=alert(1)>' }, ...linear.inputSchema.slice(1)] } });
assert(!main.querySelector('img'), 'feature names remain escaped');
main.innerHTML = renderSavedModel(C, F, { ...fits[0], functionArtifact: { ...linear, identity: { ...linear.identity, scale: 'unknown' } } });
assert(!main.querySelector('[data-model-scale]'), 'unrecognized scale semantics never receive a guessed definition');
assert.equal(modelFormula({ estimator: { kind: 'unknown' } }), null);
assert(renderSavedModel(C, F, { status: 'invalid', invalidReason: 'MISSING_MODEL_DATA' }).includes('MISSING_MODEL_DATA'));

// Sharded reports read a bounded fit page and just the selected exact function.
C.state.reportTransport = { format: 'atlas.quant.bundle', version: 1, bundleId: 'b'.repeat(64), logicalArtifactId: report.forecasts.artifactId, complete: true, collections: { modelFits: { total: 3 } } };
C.state.runId = 'remote-a';
C.render(); await tick(); await tick();
assert.equal(apiCalls.length, 2);
assert.equal(main.querySelector('[data-saved-function]').dataset.savedFunction, linear.artifactId);
const beforeTransportChange = apiCalls.length;
C.state.reportTransport = { ...C.state.reportTransport, bundleId: 'e'.repeat(64) };
C.render();
assert(!main.querySelector('[data-saved-function]'), 'a replaced transport cannot reuse the previous bundle function');
await tick(); await tick();
assert.equal(apiCalls.length, beforeTransportChange + 2);
assert(apiCalls.slice(-2).every(path => new URL(path, 'http://localhost').searchParams.get('bundleId') === 'e'.repeat(64)));
let release; detailGate = new Promise(resolve => { release = resolve; });
select('sq-model-fit', fits[2].id); await tick();
assert(main.textContent.includes('正在读取模型与参数'));
// Switching reports invalidates an in-flight function without adopting its result.
C.state.runId = 'local-b'; C.state.reportTransport = null;
activeReport = { ...report, forecasts: { ...report.forecasts, artifactId: 'c'.repeat(64), modelFits: [fits[1]] } };
C.render(); release(); detailGate = null; await tick();
assert.equal(main.querySelector('[data-saved-function]').dataset.savedFunction, constant.artifactId);
assert.equal(document.querySelector('#modal-root').innerHTML, '', 'automatic model reading never opens a modal');

// Failed reads stay failed until an explicit retry; mismatched model IDs cannot populate F.
rejectDetail = true; C.state.runId = 'remote-failure'; C.state.reportTransport = { ...C.state.reportTransport, format: 'atlas.quant.bundle', version: 1, bundleId: 'd'.repeat(64), logicalArtifactId: report.forecasts.artifactId, complete: true, collections: { modelFits: { total: 3 } } }; activeReport = report;
C.render(); await tick(); await tick();
assert(main.textContent.includes('fixture read interrupted'));
const callsAfterFailure = apiCalls.length; C.render(); await tick(); assert.equal(apiCalls.length, callsAfterFailure);
rejectDetail = false; wrongFit = true; main.querySelector('[data-sq="forecast-model-retry"]').click(); await tick();
assert(main.textContent.includes('拟合记录身份不一致'));
assert(!main.querySelector('[data-saved-function]'));
wrongFit = false; main.querySelector('[data-sq="forecast-model-retry"]').click(); await tick();
assert.equal(main.querySelector('[data-saved-function]').dataset.savedFunction, linear.artifactId);
assert.equal(JSON.stringify(report), before);
console.log(JSON.stringify({ actualSavedFunctionFirst: true, exactCoefficientPrecision: true, frozenTransformParameters: true, trueTreeTopology: true, boundedReads: true, staleReadIsolation: true, explicitRetryOnly: true, mismatchedFitRejected: true, originalImmutable: true, providerOrFitRuns: 0 }));
dom.window.close();
