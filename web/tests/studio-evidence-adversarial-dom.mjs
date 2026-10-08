/** Independent stale-evidence review. Transport is fake; parser/UI are real. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
import { describeExpression, validateExpression } from '../../edge/factor-language.mjs';

const dom = new JSDOM('<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>', {
  url: 'http://localhost/quant/#quant/studio/code', runScripts: 'outside-only',
});
const w = dom.window;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: true, addEventListener() {} });
w.structuredClone = structuredClone;
let releaseReview, reviewGate;
const tick = () => new Promise(resolve => setTimeout(resolve, 25));
const hash = text => createHash('sha256').update(text).digest('hex');
function holdReview() {
  reviewGate = new Promise(resolve => { releaseReview = resolve; });
}
w.fetch = async (url, options = {}) => {
  const path = String(url), input = options.body ? JSON.parse(options.body) : {};
  let output = { items: [], total: 0 };
  if (path.endsWith('/code/projects')) output.items = [{
    id: 'saved-rank', name: 'Existing rank project', version: 1,
    language: 'dsl', code: 'rank(close)',
  }];
  if (path.endsWith('/expressions/lint')) output = {
    valid: true, ...validateExpression(input.expression), expression: input.expression,
    codeSha256: hash(input.expression), deterministicFacts: describeExpression(input.expression),
  };
  if (path.endsWith('/code/review')) {
    const gate = reviewGate;
    if (gate) await gate;
    output = { review: {
      providerExecuted: false, language: input.language, codeSha256: hash(input.code),
      summary: 'EXPLICIT STATIC REVIEW TEST DOUBLE', findings: [], patches: [],
      ...(input.language === 'dsl' ? { deterministicFacts: describeExpression(input.code) } : {}),
    } };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(output) };
};
const app = await build({
  entryPoints: ['web/main.js'], bundle: true, write: false, format: 'iife',
  plugins: [{ name: 'independent-test-startup', setup(builder) {
    builder.onLoad({ filter: /\/web\/app\.js$/ }, async file => ({
      loader: 'js', contents: (await fs.readFile(file.path, 'utf8')).replace(
        '  init();', '  window.qa={state,studio,parseRoute,render};',
      ),
    }));
  } }],
});
w.eval(app.outputFiles[0].text);
const q = w.qa;
q.state.loading = false;
q.state.session = { capabilities: { tushareHosted: false }, runner: { online: false } };
q.parseRoute();
await q.studio.initialize();
q.render();
await tick();
const editor = () => w.document.querySelector('#v2-code-editor');
const facts = () => w.document.querySelector('#v2-dsl-facts');
const edit = code => {
  editor().value = code;
  editor().dispatchEvent(new w.InputEvent('input', { bubbles: true }));
};
async function click(action, id) {
  const selector = `[data-v2="${action}"]` + (id ? `[data-id="${id}"]` : '');
  const button = w.document.querySelector(selector);
  assert(button && !button.disabled, selector);
  button.click();
  await tick();
}

// A saved project replaces source while a review of a different source is pending.
edit('returns(close,20)');
holdReview();
await click('review-manual');
const project = w.document.querySelector('#v2-code-project');
project.value = 'saved-rank';
project.dispatchEvent(new w.Event('change', { bubbles: true }));
await tick();
assert.equal(editor().value, 'rank(close)');
releaseReview();
reviewGate = null;
await tick();
assert.equal(editor().value, 'rank(close)');
assert.match(facts().textContent, /待重新校验/);
assert.equal(facts().querySelector('.v2-dsl-formula'), null);
assert.equal(w.document.querySelector('[data-review-stale]').hidden, false);
await click('lint-code');
assert.match(facts().textContent, /同一个市场交易日/);
assert(!facts().textContent.includes('x[t−20]'));

// Exact-source edits, including whitespace, invalidate prior evidence immediately.
edit(' rank(close)');
assert.match(facts().textContent, /待重新校验/);
edit('rank(close)');
assert.match(facts().textContent, /同一个市场交易日/);

// A delayed DSL review cannot populate a Python editor with DSL evidence/source.
holdReview();
await click('review-manual');
await click('language', 'python');
edit('print("unchanged local Python")');
releaseReview();
reviewGate = null;
await tick();
assert.equal(editor().value, 'print("unchanged local Python")');
assert.equal(facts(), null);
assert.match(w.document.querySelector('.v2-review-panel').textContent, /dsl/);
await click('language', 'dsl');
assert.equal(editor().value, 'rank(close)');
assert.match(facts().textContent, /同一个市场交易日/);
edit('ts_rank(close,3)');
assert.match(facts().textContent, /待重新校验/);
assert.equal(facts().querySelector('.v2-dsl-formula'), null);
assert.equal(w.document.querySelector('[data-review-stale]').hidden, false);

console.log(JSON.stringify({
  delayedReviewAfterProjectSwitch: 'pass', languageIsolation: 'pass',
  exactSourceStaleness: 'pass', sourceNeverOverwritten: 'pass',
  transport: 'test_double', actualProvider: false, visualLayout: 'NOT_TESTED',
}));
w.close();
