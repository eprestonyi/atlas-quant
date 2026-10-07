/** API doubles exercise bounded UI reads; this is not a worker or provider acceptance test. */
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createForecastReports } from '../quant-workspace/reports.js';
import { createForms } from '../quant-workspace/forms.js';
import { defaultStrategy } from '../quant-workspace/defaults.js';

const dom = new JSDOM('<main></main><div id="modal-root"></div>', {
  url: 'http://localhost/quant/#runs/run-a',
});
globalThis.document = dom.window.document;
const document = dom.window.document;
const bundleId = 'b'.repeat(64),
  artifactId = 'a'.repeat(64);
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
const fmt = (value, digits = 2) => (value == null ? '—' : Number(value).toFixed(digits));
const strategy = defaultStrategy();
strategy.universe.symbols = ['600000.SH'];
const target = {
  id: 'target-a',
  kind: 'asset_price',
  construction: 'single_asset',
  symbols: ['600000.SH'],
  quantities: [1],
  unit: 'adjusted_price',
};
const fit = {
  id: 'fit-a',
  fitDate: '20250101',
  trainStart: '20230101',
  trainEnd: '20241220',
  trainDates: 450,
  trainRows: 450,
  estimator: 'ridge',
  featureNames: ['state'],
  labelEndMax: '20241230',
  informationCutoff: '20241231',
};
const row = (n) => ({
  forecastId: `forecast-${n}`,
  targetId: target.id,
  modelFitId: fit.id,
  date: '20250102',
  entryDate: '20250103',
  targetDate: '20250110',
  informationCutoff: '20250102',
  labelMaturedAt: '20250110',
  horizonSessions: 5,
  currentState: 10,
  expectedEntry: 10.1,
  expectedFuture: 10.4,
  edgeGap: -0.4,
  expectedGrossBps: 300,
  realizedFuture: 10.3,
  forecastError: -0.1,
  status: 'valid',
});
const ledger = {
  date: '20250103',
  risk: { gross: 0.5, net: 0, annualVolatility: 0.1, factorExposures: {} },
  riskBreaches: [],
};
const report = {
  schemaVersion: 2,
  engineVersion: 'UI_TEST_DOUBLE',
  strategy,
  research: { predictionRefitPerformed: true },
  provenance: { synthetic: true },
  warnings: [],
  selection: { evidenceStatus: 'NO_VALIDATED_FORECAST_EDGE' },
  forecasts: {
    artifactId,
    totalRows: 10000,
    dataFingerprint: 'c'.repeat(64),
    predictionConfigHash: 'd'.repeat(64),
    diagnostics: {
      holdoutStart: '20250101',
      holdoutEnd: '20250131',
      metrics: {
        observations: 9900,
        rmse: 0.01,
        relativeMseImprovement: -0.2,
        weighting: 'equal_weight_daily_average',
        observedDates: 20,
      },
      factorIncrement: { status: 'not_applicable' },
    },
  },
  execution: {
    enabled: true,
    riskAdapter: { grossExposure: 1, netExposureLimit: 2 },
    unit: 'fractional_adjusted_research_units',
  },
  metrics: {
    totalReturn: -0.01,
    tradeCount: 1,
    maxDrawdown: 0.02,
    totalCosts: 8,
  },
};
const transport = {
  format: 'atlas.quant.bundle',
  version: 1,
  bundleId,
  complete: true,
  logicalArtifactId: artifactId,
  collections: {
    forecasts: { total: 10000 },
    targets: { total: 1 },
    modelFits: { total: 1 },
    perTarget: { total: 1 },
    finalTrials: { total: 1 },
    outerFolds: { total: 1 },
    trades: { total: 1 },
    riskLedger: { total: 1 },
    decisions: { total: 1 },
    equity: { total: 2000 },
  },
  downloadUrl: '/quant/api/runs/run-a/report/download',
};
const original = JSON.stringify(report);
function freeze(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value);
    for (const child of Object.values(value)) freeze(child);
  }
}
freeze(report);
let reports,
  requests = [],
  failedCollection = '',
  wrongIdentity = false,
  activeReport = report;
const gates = new Map();
const hold = (collection) => {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  gates.set(collection, promise);
  return () => {
    gates.delete(collection);
    resolve();
  };
};
const C = {
  state: { runId: 'run-a', reportTransport: transport },
  esc,
  fmt,
  pct: (v) => (v == null ? '—' : fmt(v * 100) + '%'),
  dateText: (v) => String(v || '—'),
  icon: () => '',
  toast: () => {},
  openModal: (title, body) =>
    (document.querySelector('#modal-root').innerHTML = `<h2>${esc(title)}</h2>${body}`),
  closeModal: () => (document.querySelector('#modal-root').innerHTML = ''),
  equityChart: (points) => `<div data-chart-points="${points.length}"></div>`,
  render: () => (document.querySelector('main').innerHTML = reports.render(activeReport)),
  api: async (path) => {
    requests.push(path);
    assert(!/\/export$|\/runs\/[^/]+$/.test(path), 'UI must never fetch a complete report');
    const url = new URL(path, 'http://localhost');
    const collection = url.searchParams.get('collection');
    assert.equal(
      url.searchParams.get('bundleId'),
      C.state.reportTransport.bundleId,
      'all requests pin transport identity'
    );
    if (gates.has(collection)) await gates.get(collection);
    if (failedCollection === collection) throw Error('分页连接中断');
    const identity = wrongIdentity ? 'wrong' : C.state.reportTransport.bundleId;
    const related = {
      targets: [target],
      modelFits: [fit],
      targetLabels: { [target.id]: '600000.SH' },
      riskEvents: { 20250103: { riskExitCount: 1, exitPendingCount: 2 } },
    };
    if (url.pathname.endsWith('/detail'))
      return {
        item: {
          forecasts: row(999),
          targets: target,
          modelFits: fit,
          riskLedger: ledger,
          finalTrials: { id: 'trial-a', folds: [{ score: 0.1 }] },
        }[collection],
        related,
        bundleId: identity,
      };
    if (url.pathname.endsWith('/chart'))
      return {
        points: [{ date: '20250103', equity: 1000 }],
        totalPoints: 2000,
        samplingMethod: 'bounded_test_points',
        range: ['20230101', '20260101'],
        bundleId: identity,
      };
    const offset = Number(url.searchParams.get('offset'));
    const items =
      {
        forecasts: [row(offset), row(offset + 1)],
        targets: [target],
        modelFits: [fit],
        perTarget: [
          {
            targetId: target.id,
            observations: 20,
            priceBias: -0.1,
            priceRmse: 0.2,
          },
        ],
        finalTrials: [{ id: 'trial-a', estimator: 'ridge', score: 0.2 }],
        outerFolds: [{ testStart: '20240101', testEnd: '20240201' }],
        trades: [
          {
            date: '20250103',
            symbol: '600000.SH',
            signedQuantity: -10,
            side: 'sell',
            price: 10,
            notional: 100,
            cost: 2,
            forecastId: 'forecast-999',
            exitReason: 'risk_limit_exit',
          },
        ],
        riskLedger: [ledger],
        decisions: [{ date: '20250103', action: 'exit_pending' }],
      }[collection] || [];
    const total = collection === 'forecasts' ? 10000 : items.length;
    return {
      items,
      total,
      offset,
      limit: 25,
      nextOffset: offset + items.length,
      hasMore: collection === 'forecasts' && offset + items.length < total,
      bundleId: identity,
      related,
    };
  },
};
reports = createForecastReports(C, createForms(C), {
  onExecution: async () => {},
});
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-sq]');
  if (button) reports.handle(button);
});
document.addEventListener('input', (event) => reports.onInput(event.target));
document.addEventListener('change', (event) => reports.onChange(event.target));
const tick = () => new Promise((r) => setTimeout(r, 20));
const click = async (selector) => {
  assert(document.querySelector(selector), selector);
  document.querySelector(selector).click();
  await tick();
};
const release = hold('forecasts');
C.render();
assert(document.querySelector('main').textContent.includes('正在读取这一页'));
assert(!document.querySelector('main').textContent.includes('没有匹配记录'));
release();
await tick();
assert.equal(requests.length, 1, 'first render reads only one forecast page');
assert(document.querySelector('main').textContent.includes('当前 1–2 条'));
assert(document.querySelector('main').textContent.includes('完整产物已提交'));
assert.equal(document.querySelector('a[download]').getAttribute('href'), transport.downloadUrl);
const recordLookup = document.querySelector('#sq-forecast-search').closest('details');
assert(recordLookup && !recordLookup.open, 'technical record ID lookup is collapsed initially');
assert.equal(recordLookup.querySelector('summary').textContent, '按记录编号定位');
assert(document.querySelector('#sq-forecast-status option[value="mature"]'));
assert(document.querySelector('#sq-forecast-status option[value="unmatured"]'));
await click('[data-sq="forecast-remote-page"][data-direction="next"]');
assert(
  new URL(requests.at(-1), 'http://localhost').searchParams.get('offset') === '2',
  'nextOffset is honored rather than assuming 25'
);
await click('[data-sq="forecast-remote-page"][data-direction="previous"]');
assert(document.querySelector('main').textContent.includes('当前 1–2 条'));
await click('[data-sq="forecast-row"]');
assert(document.querySelector('#modal-root').textContent.includes('forecast-999'));
assert(document.querySelector('#modal-root').textContent.includes('固定的篮子数量'));
assert(requests.some((p) => p.includes('/detail?')));
const releaseDetail = hold('forecasts');
document.querySelector('[data-sq="forecast-row"]').click();
await tick();
C.closeModal();
releaseDetail();
await tick();
assert.equal(
  document.querySelector('#modal-root').textContent,
  '',
  'late detail must not reopen a closed modal'
);
failedCollection = 'targets';
await click('[data-sq="forecast-tab"][data-id="targets"]');
assert(document.querySelector('main').textContent.includes('分页连接中断'));
assert(!document.querySelector('main').textContent.includes('没有匹配记录'));
failedCollection = '';
await click('[data-sq="forecast-remote-retry"]');
assert(document.querySelector('main').textContent.includes('target-a'));
for (const tab of ['models', 'validation', 'execution', 'provenance']) {
  await click(`[data-sq="forecast-tab"][data-id="${tab}"]`);
  assert(!document.querySelector('main').textContent.includes('undefined'), tab);
}
assert(document.querySelector('main').textContent.includes(bundleId));
await click('[data-sq="forecast-tab"][data-id="execution"]');
assert(document.querySelector('main').textContent.includes('风险退出 1 条交易腿'));
assert(document.querySelector('main').textContent.includes('等待退出 2 项'));
assert.equal(document.querySelector('[data-chart-points]').dataset.chartPoints, '1');
await click('[data-sq="forecast-row"]');
assert(document.querySelector('#modal-root').textContent.includes('forecast-999'));
const risk = document.querySelector('#sq-risk-filter');
risk.value = 'missing';
risk.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
await tick();
assert(requests.some((p) => p.includes('filter=missing')));
await click('[data-sq="forecast-tab"][data-id="forecasts"]');
for (let n = 0; n < 20; n++) await click('[data-sq="forecast-remote-page"][data-direction="next"]');
assert(
  reports.remote.diagnostics().cachedPages <= 12,
  'cache bounded independently from artifact length'
);
const dates = document.querySelector('#sq-forecast-date-from');
dates.value = '2025-01-01';
dates.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
await tick();
assert(requests.some((p) => p.includes('dateFrom=20250101')));
assert.equal(document.querySelector('#sq-forecast-date-from').value, '2025-01-01');
wrongIdentity = true;
const status = document.querySelector('#sq-forecast-status');
status.value = 'invalid';
status.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
await tick();
assert(document.querySelector('main').textContent.includes('分片身份不一致'));
wrongIdentity = false;
assert.equal(
  JSON.stringify(report),
  original,
  'remote interactions never mutate summary or original identity'
);
assert.equal(report.forecasts.rows, undefined, 'summary never became a reconstructed artifact');
const callsBeforeAbsentEvidence = requests.length;
assert.equal(reports.remote.page('baselineRows').unavailable, true);
assert.equal(
  requests.length,
  callsBeforeAbsentEvidence,
  'absent evidence does not issue a 404 request'
);
// Incomplete/unknown transport metadata never becomes a complete executable report.
C.state.reportTransport = { ...transport, complete: false };
C.render();
assert(document.querySelector('main').textContent.includes('尚未完整提交'));
assert(!document.querySelector('[data-sq="forecast-execute"]'));
C.state.reportTransport = { ...transport, logicalArtifactId: 'wrong' };
C.render();
assert(document.querySelector('main').textContent.includes('预测身份与报告传输身份不匹配'));
C.state.reportTransport = { ...transport, version: 2 };
C.render();
assert(document.querySelector('main').textContent.includes('尚未支持的传输版本'));
console.log(
  JSON.stringify({
    remotePagination: true,
    nextOffset: true,
    identityPinned: true,
    boundedCache: true,
    asyncDetails: true,
    directDownload: true,
    riskEvents: true,
    reportImmutable: true,
    apiDoubles: true,
    browserVisualAcceptance: false,
  })
);
dom.window.close();
