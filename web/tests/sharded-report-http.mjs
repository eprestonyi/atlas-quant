/** Read-only real Worker + DOM integration. The private session file is never logged. */
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { createForecastReports } from '../quant-workspace/reports.js';
import { createForms } from '../quant-workspace/forms.js';

const sessionFile = process.argv[2];
if (!sessionFile)
  throw Error(
    'Usage: node web/tests/sharded-report-http.mjs /absolute/private/session.json',
  );
const { baseUrl, cookie, runId } = JSON.parse(
  await fs.readFile(sessionFile, 'utf8'),
);
const origin = new URL(baseUrl);
assert(
  ['127.0.0.1', 'localhost', '[::1]', 'dataset.localhost'].includes(
    origin.hostname,
  ),
  'local isolated QA only',
);
assert(typeof cookie === 'string' && cookie.length > 0);
assert(typeof runId === 'string' && runId.length > 0);
const requests = [],
  errors = [];
let pending = 0;
async function api(path) {
  assert(path.startsWith('/runs/'), 'this test only reads report endpoints');
  const url = new URL('/quant/api' + path, origin);
  requests.push(url.pathname + url.search);
  pending++;
  try {
    const response = await fetch(url, {
      headers: { cookie },
      signal: AbortSignal.timeout(30000),
    });
    const text = await response.text();
    assert(
      Buffer.byteLength(text) <= 32 * 1024 * 1024,
      'bounded preview response',
    );
    if (!response.ok)
      throw Error(`HTTP ${response.status} ${path}: ${text.slice(0, 240)}`);
    return JSON.parse(text);
  } catch (error) {
    errors.push(error);
    throw error;
  } finally {
    pending--;
  }
}
const envelope = await api(`/runs/${encodeURIComponent(runId)}/report`);
const { report, transport } = envelope;
assert(
  ['atlas.quant.bundle', 'atlas.quant.financial_bundle'].includes(
    transport.format,
  ),
);
const financial = transport.format === 'atlas.quant.financial_bundle';
assert.equal(transport.complete, true);
assert.equal(report.forecasts.artifactId, transport.logicalArtifactId);
assert.equal(report.forecasts.rows, undefined, 'light summary has no rows');
const original = JSON.stringify(report);
function freeze(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value);
    for (const child of Object.values(value)) freeze(child);
  }
}
freeze(report);
const dom = new JSDOM('<main></main><div id="modal-root"></div>', {
  url: new URL(`/quant/#runs/${runId}`, origin).href,
});
const document = dom.window.document;
globalThis.document = document;
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
const fmt = (value, digits = 2) =>
  value == null || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toFixed(digits);
let reports, replay;
const C = {
  state: { runId, reportTransport: transport },
  api,
  esc,
  fmt,
  pct: (value) => (value == null ? '—' : fmt(value * 100) + '%'),
  dateText: (value) => String(value || '—'),
  icon: () => '',
  toast: () => {},
  equityChart: (points) => `<div data-chart-points="${points.length}"></div>`,
  openModal: (title, html) =>
    (document.querySelector('#modal-root').innerHTML =
      `<h2>${esc(title)}</h2>${html}`),
  closeModal: () => (document.querySelector('#modal-root').innerHTML = ''),
  render: () =>
    (document.querySelector('main').innerHTML = reports.render(report)),
};
reports = createForecastReports(C, createForms(C), {
  onExecution: async (id, config) => {
    replay = { id, config };
  },
});
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-sq]');
  if (button)
    Promise.resolve(reports.handle(button)).catch((error) =>
      errors.push(error),
    );
});
document.addEventListener('input', (event) => reports.onInput(event.target));
document.addEventListener('change', (event) => reports.onChange(event.target));
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
async function settled() {
  let idle = 0;
  const deadline = Date.now() + 35000;
  while (idle < 3) {
    if (Date.now() > deadline) throw Error('Report requests did not settle');
    await tick();
    idle = pending ? 0 : idle + 1;
  }
  if (errors.length) throw errors[0];
  assert(!document.querySelector('main').textContent.includes('undefined'));
  assert(
    !document.querySelector('main').textContent.includes('[object Object]'),
  );
}
async function click(selector) {
  const element = document.querySelector(selector);
  assert(element, selector);
  element.click();
  await settled();
}
async function select(id, value) {
  const element = document.getElementById(id);
  assert(element, id);
  element.value = value;
  element.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  await settled();
}
C.render();
await settled();
assert(
  document.querySelector('#sq-forecast-search').closest('details').open ===
    false,
);
await select('sq-forecast-scope', 'all');
const firstButton = document.querySelector('[data-sq="forecast-row"]');
assert(firstButton, 'real forecast records render');
await click('[data-sq="forecast-row"]');
assert(
  document
    .querySelector('#modal-root')
    .textContent.includes(firstButton.dataset.id),
);
C.closeModal();
const nextButton = document.querySelector(
  '[data-sq="forecast-remote-page"][data-direction="next"]',
);
if (nextButton && !nextButton.disabled) {
  await click('[data-sq="forecast-remote-page"][data-direction="next"]');
  await click('[data-sq="forecast-row"]');
  C.closeModal();
  await click('[data-sq="forecast-remote-page"][data-direction="previous"]');
}
for (const status of ['invalid', 'mature', 'unmatured', 'all'])
  await select('sq-forecast-status', status);
for (const tab of [
  'validation',
  'targets',
  'models',
  'execution',
  'provenance',
]) {
  await click(`[data-sq="forecast-tab"][data-id="${tab}"]`);
  assert.equal(reports.ui.tab, tab);
  const action = { targets: 'forecast-target-detail', models: 'forecast-fit' }[
    tab
  ];
  if (action && document.querySelector(`[data-sq="${action}"]`)) {
    await click(`[data-sq="${action}"]`);
    assert(document.querySelector('#modal-root').textContent.length > 50);
    C.closeModal();
  }
}
await click('[data-sq="forecast-tab"][data-id="execution"]');
if (report.execution?.enabled) {
  assert(
    Number(document.querySelector('[data-chart-points]').dataset.chartPoints) <=
      1000,
  );
  for (const filter of ['missing', 'all', 'events'])
    await select('sq-risk-filter', filter);
}
if (financial) {
  assert.equal(document.querySelector('[data-sq="forecast-execute"]'), null);
  assert.equal(document.querySelector('[data-sq-replay]'), null);
  assert.equal(replay, undefined, 'financial results cannot request execution');
  const sourceRef = transport.sourceEvidence.datasetRef;
  assert.equal(sourceRef.version, 2);
  const archiveLinks = [...document.querySelectorAll('a[download]')];
  assert(
    archiveLinks.some(
      (a) => a.href === new URL(transport.bundleDownloadUrl, origin).href,
    ),
  );
  const datasetLink = archiveLinks.find((a) =>
    a.href.includes(`/datasets/${sourceRef.datasetId}/archive`),
  );
  assert(
    datasetLink,
    'financial result exposes its separate complete dataset archive',
  );
  assert.equal(
    new URL(datasetLink.href).searchParams.get('datasetRoot'),
    sourceRef.datasetRoot,
  );
} else {
  const slippage = document.querySelector(
    '[data-sq-replay="costs.slippageBps"]',
  );
  slippage.value = '7';
  slippage.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await click('[data-sq="forecast-execute"]');
  assert.equal(replay.id, report.forecasts.artifactId);
  assert.equal(replay.config.costs.slippageBps, 7);
  assert.equal(
    replay.config.model,
    undefined,
    'replay payload cannot refit the model',
  );
}
assert.equal(
  JSON.stringify(report),
  original,
  'all interactions keep summary immutable',
);
assert(reports.remote.diagnostics().cachedPages <= 12);

// A streamed GET is checked without parsing or retaining the complete report.
const downloadUrl = new URL(transport.downloadUrl, origin);
assert.equal(downloadUrl.origin, origin.origin);
assert.equal(downloadUrl.searchParams.get('bundleId'), transport.bundleId);
const download = await fetch(downloadUrl, {
  headers: { cookie },
  signal: AbortSignal.timeout(60000),
});
assert(download.ok);
assert(download.headers.get('content-disposition')?.includes('attachment'));
const digest = createHash('sha256');
let bytes = 0;
for await (const chunk of download.body) {
  bytes += chunk.length;
  assert(bytes <= 512 * 1024 * 1024, 'bounded complete download test');
  digest.update(chunk);
}
assert(bytes > 0);
assert(
  requests.every((path) => path.startsWith(`/quant/api/runs/${runId}/report`)),
);
console.log(
  JSON.stringify({
    realWorkerHTTP: true,
    tabs: 6,
    requests: requests.length,
    summaryBytes: Buffer.byteLength(original),
    forecastRows: transport.collections.forecasts.total,
    logicalArtifactId: transport.logicalArtifactId,
    bundleId: transport.bundleId,
    completeDownloadBytes: bytes,
    completeDownloadSha256: digest.digest('hex'),
    immutableSummary: true,
    transportFormat: transport.format,
    executionPayloadOnly: !financial,
    financialExecutionDisabled: financial,
    financialTwoArchives: financial,
    providerCalls: 0,
    apiDoubles: false,
    browserVisualAcceptance: false,
  }),
);
dom.window.close();
