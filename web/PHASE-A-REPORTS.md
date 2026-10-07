# Phase A report UI

Status: real isolated Worker HTTP + DOM integration passed; final live-runner/browser chain remains separately recorded. This work does not deploy, increase numerical limits, or change the model. The authoritative API is `docs/BUNDLE_TRANSPORT_V1.md`; query identity is `bundleId`.

## Read path

- `app.js` loads `/runs/:id/report`. A 404 alone permits the legacy `/runs/:id` fallback. Old reports with `transport:null` retain their complete-array rendering.
- `quant-workspace/report-source.js` owns manifest-bound page/chart requests and on-demand details. It retains at most 12 response pages, follows server `nextOffset`, rejects a different bundle identity, and ignores responses from a previously opened run.
- `quant-workspace/reports.js` renders the light summary and independently paged forecast, target, fit, validation, baseline, trade, risk and decision collections. It never rebuilds `forecasts.rows` from pages.
- Forecast status includes invalid and immature records. “Latest” is a server grouping by stable target construction, not a filter over the browser's current cache. Exact ID lookup is inside a collapsed advanced section; no incomplete local list is presented as a full stock selector.
- Chart points are bounded separately from metrics. Metrics are retained from the completed report, not recomputed from a sampled curve.
- Complete report download is a direct private attachment link. The UI does not fetch, parse or stringify the complete bundle. Incomplete or unknown transport metadata cannot be treated as an executable completed report.

Loading, empty and failed states are distinct. Failed pages can retry; a late detail response cannot reopen a modal the user already closed. Filters and independent execution overrides are UI drafts, never mutations of source report objects.

## Adjacent product corrections

Research cards use the backend `latestRun` field, distinguish absent status from `latestRun:null`, and link to the actual forecast or execution run. Reading a saved execution comparison restores the execution editor kind and list query without overriding a user change made during the request.

Studio AI review exposes provider, model, language, review time and code hash in collapsed evidence. The request captures submitted code before awaiting the provider; later editor changes do not become falsely attributed to that review. No additional strategy or user data is sent by this UI change.

## Reproducible tests

From the repository root after installing the locked dependencies:

```sh
node web/tests/sharded-report-dom.mjs
node web/tests/statistical-dom.mjs
```

These use API doubles and real DOM events in jsdom. They cover identity pinning, bounded cache, nextOffset, error/retry, asynchronous details, all report sections, direct download links, immutable source objects, collapsed technical filters, comparison readback, latest run status and asynchronous AI review attribution. They are not browser layout or Worker/storage acceptance evidence.

Legacy computed reports can additionally be supplied without contacting any provider:

```sh
node web/tests/statistical-report-readback.mjs /absolute/private/report.json
```

The existing real Tushare pair report and the two existing synthetic factor-increment/coverage reports passed that readback in this task. Private reports and provider-derived prices are not embedded in public tests.

The new real HTTP test reads an isolated local Worker session, never prints its cookie, and makes only report GETs:

```sh
node web/tests/sharded-report-http.mjs /absolute/private/session.json
```

The session JSON supplies `baseUrl`, `cookie` and `runId`. Do not commit it. On 2026-10-08 this test passed against the actual 8916 Worker/D1/R2 fixture: six sections, 23 summary/page/detail/chart requests, a 53,885-byte summary, 19,700 forecast records, and a streamed 32,994,262-byte attachment. Attachment SHA256: `0c6eaf474bf1c4c8c3fd59c7d9bce7dc6e34fea5576c47715b941cd6e8f14a88`. No complete report JSON was parsed in the UI, no provider called, and no execution submitted; the execution button's output payload was captured and checked. This proves real read APIs and rendering, not the entire live computation lifecycle or visual acceptance.

## Visual and full-chain acceptance

`web/tests/report-preview.mjs` is an explicitly mocked, localhost-only visual server over an existing computed report. It is for visual inspection, not a Worker substitute:

```sh
node web/tests/report-preview.mjs /absolute/private/report.json 8906
```

Open `/quant/#runs/visual-fixture`. The task's root agent used the actual in-app browser at 1265×712 and found no layout overlap. After the implemented advanced-ID fold, root checked second-page details and target drilldown. At 390×844, the viewport was 390px and document/body 375px, with the 713px table scrolling inside its 305px container; there was no page overflow or obscured control. This remains fixture-backed browser evidence.

Final real-chain checks still required:

1. A real runner completes a bundle through authenticated Worker staging, validation and commit; open that real job without any fixture adapter.
2. Repeat the now-passing real HTTP six-tab/page/detail/filter checks through the real browser on that newly computed job. No full legacy endpoint should be fetched for a bundle.
3. Reload and navigate between distinct reports; verify no stale rows, modal responses or execution overrides leak between runs. Test 390px and keyboard-only controls in the actual browser.
4. Download from the report's attachment link and independently audit the complete artifact; compare original logical forecast identity and row coverage. A page preview count must never stand in for artifact completeness.
5. Submit independent execution from the same immutable forecast artifact and read its new report, trades and risk evidence without refitting the prediction model.

These remaining end-to-end checks must not be inferred from the passed HTTP or fixture-browser checks. No browser automation outside CUA was used.
