/** Production asset graph over actual Worker responses; API reads below are UI doubles. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { Miniflare } from 'miniflare';
import { JSDOM } from 'jsdom';
import { loadWebAssets, buildWorkerSource } from '../scripts/worker-source.mjs';

test('production HTML module graph stays served under /quant and boots without source rebundling', async () => {
  const mf = new Miniflare({ modules: true, script: await buildWorkerSource({ assets: await loadWebAssets() }), compatibilityDate: '2026-08-01' });
  let dom;
  try {
    const origin = 'https://atlas.test', pageUrl = origin + '/quant/';
    const htmlResponse = await mf.dispatchFetch(pageUrl);
    assert.equal(htmlResponse.status, 200);
    const html = await htmlResponse.text(), document = new JSDOM(html).window.document;
    const entries = [...document.querySelectorAll('script[type="module"][src]')].map(el => new URL(el.getAttribute('src'), pageUrl).href);
    assert(entries.length > 0);
    const modules = new Map();
    // esbuild only parses imports here. Each import is resolved as a browser URL
    // and fetched from the actual Worker, never from the source filesystem.
    await build({ entryPoints: entries, bundle: true, write: false, format: 'esm', logLevel: 'silent', plugins: [{ name: 'served-module-graph', setup(b) {
      b.onResolve({ filter: /.*/ }, args => {
        const url = new URL(args.path, args.importer || pageUrl);
        assert.equal(url.origin, origin);
        assert(url.pathname.startsWith('/quant/'), 'Browser import escapes deployed asset prefix: ' + url.href);
        return { path: url.href, namespace: 'worker-http' };
      });
      b.onLoad({ filter: /.*/, namespace: 'worker-http' }, async args => {
        const response = await mf.dispatchFetch(args.path);
        assert.equal(response.status, 200, args.path);
        assert.match(response.headers.get('content-type'), /^(?:application|text)\/javascript\b/, 'Browser module requires JavaScript MIME: ' + args.path);
        assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
        const contents = await response.text();
        modules.set(args.path, contents);
        return { contents, loader: 'js' };
      });
    } }] });
    assert.equal(modules.size, entries.length, 'The published entry bundles shared JSON and validation; no unserved transitive modules remain');
    dom = new JSDOM(html, { url: pageUrl + '#quant/modes', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, reads = [];
    w.structuredClone = structuredClone; w.scrollTo = () => {}; w.matchMedia = () => ({ matches: false, addEventListener() {} });
    w.fetch = async (url, options = {}) => {
      assert(!options.method || options.method === 'GET', 'Boot must not mutate or run research');
      reads.push(String(url));
      const result = String(url).endsWith('/session') ? { workspace: { id: 'static-asset-test' }, capabilities: {}, runner: { online: false } } : { items: [], factors: [], models: [], templates: [], total: 0 };
      return { ok: true, status: 200, text: async () => JSON.stringify(result) };
    };
    // Execute exactly the bytes served by the production Worker, with normal
    // init() retained. No source transform or test-specific bundle hides imports.
    for (const entry of entries) w.eval(modules.get(entry));
    for (let i = 0; i < 50 && !w.document.querySelector('.sq-mode-card'); i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(w.document.querySelectorAll('.sq-mode-card').length, 2);
    assert(!w.document.querySelector('#app').textContent.includes('正在连接'));
    assert(reads.includes('/quant/api/session'));
    for (const asset of ['/quant/python-sandbox.html', '/quant/python-sandbox.js', '/quant/python-worker.js']) assert.equal((await mf.dispatchFetch(origin + asset)).status, 200);
    assert.equal((await mf.dispatchFetch(origin + '/edge/statistical-quant/validation.mjs')).status, 404, 'Private source routes stay unpublished');
  } finally { dom?.window.close(); await mf.dispose(); }
});
