/** Real DOM events and real application modules; API doubles only. These tests
 * establish state/interaction behavior, not browser visual or provider evidence. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
const defs = JSON.parse(await fs.readFile('edge/financial/definitions.json', 'utf8'));
const dom = new JSDOM(
  '<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',
  {
    url: 'http://localhost/quant/#quant/studio/financial',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  }
);
const w = dom.window;
w.matchMedia = () => ({ matches: false, addEventListener() {} });
w.structuredClone = structuredClone;
w.scrollTo = () => {};
const id = '11111111-1111-4111-8111-111111111111',
  newId = '22222222-2222-4222-8222-222222222222',
  calendar = '33333333-3333-4333-8333-333333333333',
  prepId = '44444444-4444-4444-8444-444444444444';
const root = 'a'.repeat(64),
  stateId = defs.items[0].id;
let fail = '',
  gate,
  release,
  lastUpload,
  lastRevision,
  pages = 0;
const calls = [];
let source = {
  id,
  name: '冻结财务输入',
  status: 'ready_to_prepare',
  revision: 1,
  calendarRef: calendar,
  uploadSha256: root,
  inputRoot: root,
  packRoot: root,
  calendarRoot: root,
  unitPolicy: 'verified_only',
  source: { provider: 'EXPLICIT_DOM_FIXTURE' },
  selection: {
    symbols: ['600690.SH'],
    start: '20240102',
    end: '20241231',
    announcementStart: '20230101',
    selectedStateIds: [stateId],
    scope: 'consolidated',
    flowBasis: 'ytd',
  },
};
const detail = () => ({
  input: source,
  activeJob: null,
  latestJob: null,
  latestPreparation: source.status === 'prepared' ? { id: prepId } : null,
});
w.fetch = async (url, options = {}) => {
  const path = String(url).replace('/quant/api', '');
  calls.push({ path, options });
  if (gate && path.includes(gate))
    await new Promise((r) => {
      release = r;
    });
  if (fail && path.includes(fail)) throw Error('测试网络中断，输入保留');
  const data = typeof options.body === 'string' ? JSON.parse(options.body) : null;
  let result = { items: [], total: 0 };
  if (path === '/financial/capabilities')
    result = {
      enabled: true,
      operations: {
        upload: true,
        validate: true,
        prepare: true,
        researchBinding: false,
      },
      runner: { online: true },
      limits: { packageBytes: 24 * 1024 * 1024 },
    };
  if (path === '/financial/definitions') result = defs;
  if (path.startsWith('/financial/calendars'))
    result = {
      items: [{ calendarRef: calendar, label: 'Explicit DOM calendar' }],
      total: 1,
    };
  if (path.startsWith('/financial/inputs?')) result = { items: [source], total: 1 };
  if (path === '/financial/inputs' && options.method === 'POST') {
    lastUpload = data;
    result = {
      input: { id },
      upload: { url: '/quant/api/financial/inputs/' + id + '/content' },
    };
  }
  if (path === `/financial/inputs/${id}/content`) {
    assert(
      options.body instanceof w.File,
      'upload must keep raw File rather than browser JSON parse'
    );
    result = { input: source };
  }
  if (path === `/financial/inputs/${id}` || path === `/financial/inputs/${newId}`)
    result = detail();
  if (path === `/financial/inputs/${id}/revisions`) {
    lastRevision = data;
    source = { ...source, id: newId, revision: 2, status: 'validating' };
    result = { input: source, job: { id: 'job', status: 'queued' } };
  }
  if (path === `/financial/preparations/${prepId}`)
    result = {
      preparation: {
        id: prepId,
        preparedRoot: root,
        selection: source.selection,
      },
      readiness: { prepared: true, researchBindingEnabled: false },
    };
  if (path.includes(`/preparations/${prepId}/coverage?`))
    result = {
      items: [
        {
          symbol: '600690.SH',
          stateId,
          status: 'available',
          okRows: 100,
          missingRows: 0,
          firstObserved: '20240102',
          lastObserved: '20241231',
          firstAvailable: '20231229',
          lastAvailable: '20231229',
          latestPeriodEnd: '20230930',
          latestAgeCalendarDays: 458,
          reasonCounts: {},
        },
      ],
      total: 1,
      nextCursor: null,
    };
  if (path.includes(`/preparations/${prepId}/events?`)) {
    pages++;
    result = {
      items: [
        {
          eventId: root,
          stateId,
          symbol: '600690.SH',
          computedAsOf: '20240102',
          status: 'ok',
          decimalValue: '0.123',
          periodEnd: '20230930',
          availableDate: '20231229',
        },
      ],
      total: 2,
      nextCursor: path.includes('cursor=') ? null : 'explicit-test-cursor',
    };
  }
  if (path.includes(`/events/${root}?`))
    result = {
      event: {
        eventId: root,
        stateId,
        symbol: '600690.SH',
        status: 'ok',
        decimalValue: '0.123',
        lineageHash: root,
        dependencyCount: 1,
        qualityFlags: ['USER_DECLARED_UNIT_ASSUMPTION'],
      },
    };
  if (path.includes(`/events/${root}/dependencies?`))
    result = {
      items: [
        {
          fieldId: 'balancesheet.total_assets',
          rawDecimal: '1.234567890123456789',
          rawUnit: 'CNY',
          evidenceLevel: 'user_declared_assumption',
          referencePreview: '显式测试假设',
        },
      ],
      total: 1,
      nextCursor: null,
    };
  return { ok: true, status: 200, text: async () => JSON.stringify(result) };
};
const built = await build({
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
            '  window.qa={state,workspace,parseRoute,render};'
          ),
          loader: 'js',
        }));
      },
    },
  ],
});
w.eval(built.outputFiles[0].text);
const q = w.qa;
q.state.loading = false;
q.state.session = {
  runner: { online: true },
  providers: { tushare: { available: true } },
};
const tick = () => new Promise((r) => setTimeout(r, 35));
const route = async (hash) => {
  w.location.hash = hash;
  await tick();
};
const click = (action) => {
  const el = w.document.querySelector(`[data-fin="${action}"]`);
  assert(el, action);
  el.click();
};
const input = (selector, value) => {
  const el = w.document.querySelector(selector);
  assert(el, selector);
  if (el.type === 'checkbox') el.checked = value;
  else el.value = value;
  el.dispatchEvent(new w.Event('input', { bubbles: true }));
};
q.parseRoute();
gate = '/financial/inputs?';
q.render();
const loading = q.workspace.routeChanged();
await tick();
assert(w.document.querySelector('main').textContent.includes('正在读取输入列表'));
assert(!w.document.querySelector('main').textContent.includes('尚无财务输入'));
gate = null;
release();
await loading;
await tick();
assert.equal(w.document.querySelectorAll('h1').length, 1);
assert(w.document.querySelector('main').textContent.includes('冻结财务输入'));
assert(!w.document.querySelector('#sq-step-picker'));
// Raw file selection survives a failed upload and route reads; request identity is retained.
input('#fin-name', '保留的输入名称');
input('#fin-calendar', calendar);
const file = new w.File(['{"explicit":"fixture"}'], 'fixture.json', {
  type: 'application/json',
});
Object.defineProperty(w.document.querySelector('[data-fin-file]'), 'files', {
  value: [file],
});
w.document.querySelector('[data-fin-file]').dispatchEvent(new w.Event('change', { bubbles: true }));
fail = '/content';
click('upload');
await tick();
assert(w.document.querySelector('main').textContent.includes('测试网络中断'));
assert.equal(w.document.querySelector('#fin-name').value, '保留的输入名称');
assert.equal(q.workspace.financial.state.file, file);
const requestId = lastUpload.requestId;
fail = '';
click('upload');
await tick();
await tick();
assert.equal(lastUpload.requestId, requestId);
assert.equal(w.location.hash, `#quant/studio/financial/${id}/source`);
// Each financial step has its own route. Draft changes survive asynchronous source refresh.
await route(`#quant/studio/financial/${id}/units`);
input('#fin-policy', 'allow_declared');
input('#fin-selection-scope', 'parent');
input('#fin-selection-flowBasis', 'quarter');
const firstUnit = w.document.querySelector('[data-fin-unit]'),
  fieldId = firstUnit.dataset.finUnit;
input(`[id="${firstUnit.id}"]`, defs.unitOptions[0].nativeUnit);
input(`[data-fin-statement="${fieldId}"]`, '这是由使用者明确记录的单位研究假设');
input('[data-fin-confirm]', true);
await q.workspace.financial.loadSource(id, { quiet: true });
assert.equal(
  w.document.querySelector(`[data-fin-statement="${fieldId}"]`).value,
  '这是由使用者明确记录的单位研究假设'
);
assert(q.workspace.financial.state.draft.confirmed);
await route(`#quant/studio/financial/${id}/states`);
const add = w.document.querySelector('[data-fin="state-add"]');
add.click();
await tick();
assert.equal(q.workspace.financial.state.draft.selection.selectedStateIds.length, 2);
assert(!q.workspace.financial.state.draft.confirmed);
await route(`#quant/studio/financial/${id}/units`);
input('[data-fin-confirm]', true);
click('revise');
await tick();
await tick();
assert.equal(lastRevision.unitPolicy, 'allow_declared');
assert.equal(lastRevision.selection.scope, 'parent');
assert.equal(lastRevision.selection.flowBasis, 'quarter');
assert.equal(lastRevision.declarations[0].inputRoot, root);
assert.equal(lastRevision.declarations[0].currency, defs.unitOptions[0].currency);
assert(!Object.hasOwn(lastRevision, 'trusted_unit_proofs'));
assert.equal(w.location.hash, `#quant/studio/financial/${newId}/source`);
// Actual coverage uses observed dates separately from disclosure dates and paged evidence.
source = { ...source, status: 'prepared' };
await route(`#quant/studio/financial/${newId}/coverage`);
await tick();
assert(w.document.querySelector('main').textContent.includes('20241231'));
assert(w.document.querySelector('main').textContent.includes('20231229'));
assert(!w.document.querySelector('[data-fin="attach"]'));
assert.equal(w.document.querySelectorAll('h1').length, 1);
click('events-next');
await tick();
assert(calls.some((x) => x.path.includes('cursor=explicit-test-cursor')));
click('event');
await tick();
assert(w.document.querySelector('main').textContent.includes('不是已验证单位'));
assert(w.document.querySelector('main').textContent.includes('1.234567890123456789'));
assert(w.document.querySelector('a[download][href*="/events/"]'));
// A delayed source response cannot replace a different route or rewrite its draft.
gate = `/financial/inputs/${newId}`;
const pending = q.workspace.financial.loadSource(newId);
await tick();
await route('#quant/studio/financial');
gate = null;
release();
await pending;
await tick();
assert.equal(w.location.hash, '#quant/studio/financial');
assert(!w.document.querySelector('.fin-step-tabs'));
q.workspace.financial.dispose();
dom.window.close();
console.log(
  JSON.stringify({
    dom: 'jsdom',
    rawFileUpload: true,
    idempotentRetry: true,
    independentFinancialSteps: 4,
    inputRetention: true,
    coverageAvailabilitySeparated: true,
    pagedLineage: true,
    noResearchBinding: true,
    browserVisualAcceptance: false,
    usesApiDoubles: true,
  })
);
