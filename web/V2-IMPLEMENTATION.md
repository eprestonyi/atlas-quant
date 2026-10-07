# Atlas Quant frontend v0.2

The default interface is a plain-language research composer. Quant Studio is a separate set of independent workspaces, while the strategy/session/run APIs and old saved records continue to use the existing state bridge.

## Files

- `studio.js`: ordinary home/composer, curated stock pools, eight Studio screens, paginated catalog and field discovery, private factor builder, research groups, exact PCD mapping, code projects and review, prediction preview.
- `studio.css`: independent fluid workspace layout, responsive cards and panels, mobile navigation; no fixed-width graph is needed to configure a strategy.
- `app.js`: existing session, strategy persistence, data import, queue/report actions and modal behavior; v2 route bridge, 50-symbol / 32-factor / 110,000-row limits, 24 MiB file limit, external field preservation, prediction reports and training diagnostics.
- `index.html`: loads v2 assets and the parent-owned isolated Python runtime before the app.
- `python-runtime.js`, sandbox and worker: owned and implemented separately from this frontend work.

## Working behavior

Ordinary users select a stock pool, pick a research goal, add or drag curated packs, adjust horizon/holdings/dates, then save or run. Default new work uses Tushare. Synthetic data requires an explicit source choice in Studio. Provider failure does not switch to synthetic data.

Pool selection displays complete membership separately from the recommended research subset. Current snapshots do not claim historical membership. The home prioritizes the six provider-backed index pools; the complete paginated catalog includes industry, area, exchange and intersections. Full member selection is searchable and renders only 40 members per page, with explicit checkboxes and a 50-symbol limit.

Studio has independent routes for data, factor research, labels, models, validation, portfolio, reports and code. Factor and field catalogs use server-side filters and 24 items per page. Schema definitions and mapping requirements remain visible and distinct from executable recipes. PCD bindings require explicit field/alias/unit/entity/record IDs and never silently infer security mappings.

Groups are saved in `graph.groups` as research organization. The engine still receives a flat feature set; no unsupported group weighting or independent branch execution is offered.

The code area supports DSL, Python and editable strategy JSON. DSL is checked by the server; Python uses the isolated runtime with `data` input and `result` output. Projects persist through the private API with optimistic versioning. Rule checks and actual AI calls are distinguished. Suggestions show exact before/after snippets and are applied only by an explicit button; ambiguous or stale matches are refused.

Reports add frozen holdout forecasts, latest/all views, date/code filters, 50-row pagination and CSV export. Baseline ranking scores are not formatted as return forecasts. MAE, RMSE and direction accuracy only appear when returned by the engine. Historical volatility/downside are labeled observed historical risk, not forecast confidence. Training-only correlated feature pairs are displayed separately from holdout evidence.

Financial DB means four logical authority stores: PCD (including D2 original company disclosures), MKT, EXT (external research and estimates) and MODEL. The legacy FD selector is labeled financial-indicator adapter, not a fifth database. Imported numeric `fd_` / `pcd_` / `ext_` / `model_` values and availability dates are preserved and checked.

## Verification and evidence boundary

`node --check web/app.js` and `node --check web/studio.js` passed. `node web/tests/studio-contract.mjs` exercises the real localhost APIs from a source VM harness, creating a private test code project. It verifies route rendering, private pack/group behavior, metadata retention, unavailable PCD rejection, valid/invalid DSL, private project creation and update, rule review, and bounded pool member rendering. With `ATLAS_FORECAST_REPORT` pointing to an engine report, it also checks actual forecast output and pagination; The final v0.2 run recorded 43 checks, including the later contextual mapping and four-store alias cases, with an actual engine report supplied. This number counts source-VM/local-API assertions, not 43 browser automation cases.

This VM harness is not browser visual evidence. Parent QA separately controls the real browser. Child computer use was unavailable because its CUA inventory had no browser; no substitute UI automation was used. The parent reported successful isolated Python execution in the real browser.

## Remaining scope boundaries

- Current universe membership snapshots and deterministic research subsets are explicit, not historical constituent reconstruction or return-based recommendations.
- Thousands of PCD field-derived definitions do not mean thousands of populated or validated investment factors. Runtime coverage, publication timing and mappings remain required.
- Live provider availability and AI execution are determined by runtime responses; the interface never fabricates their results.
- Touch devices use explicit add/remove controls and group selectors to retain normal vertical scrolling. Mouse/pen supports dragging.
- Browser workspace cookies identify private records. Clearing site data can lose access, so strategy, code and report exports remain available.

## Contextual data readiness repair

Catalog availability is immutable. A separate contextual indicator enables a numeric PCD recipe only when its required aliases have exact matching field IDs, explicit units, and entity/record mappings for current study symbols. Upload readiness requires actual numeric columns, matching availability-date columns and complete `provenance.externalFields` PIT metadata. Future dates, missing proof, incorrect PCD identities and text values are refused. The UI labels configured mappings and uploaded PIT inputs as pending runtime validation, never verified market coverage. The same gate controls card buttons, drag-and-drop, field building and private custom factors. EXT and MODEL user-upload aliases are accepted under the same PIT requirements; choosing hosted Tushare does not make those uploaded series available.
