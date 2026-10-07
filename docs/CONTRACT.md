# Atlas Quant v0.2 contract

The browser, edge, provider and numerical engine share the strategy format in [research-v02.json](../engine/examples/research-v02.json). `schemaVersion: 1` remains compatible with v0.1; v0.2 adds target selection, more registered model families, external field bindings and prediction output. The current public endpoint contract is [API.md](API.md).

## Research input

`atlas_quant.engine.run_research(strategy: dict, data: pandas.DataFrame, provenance: dict) -> dict`

The strategy contains universe, factors, preprocess, model, portfolio, costs, optional graph and dataBindings. Bounds are 3–50 symbols, 1–32 factors, 110,000 rows, 2,200 official sessions and eight calendar years. Targets are `forward_return` or `forward_excess_return`; manual model mode selects one family, automatic mode compares the selected finite family list. Eight families contain fourteen fixed configurations in total. Exact validation occurs independently at the edge and engine.

Data is keyed by unique `(trade_date, ts_code)`; OHLC are consistently adjusted research prices, `raw_close` is the raw close, volume is in hands, amount in thousands of CNY. Provider supplies the official `provenance.tradingDates`. Uploads lacking that calendar use the observed date union with an explicit warning. Missing sessions and values remain missing, never synthetic replacements.

External numeric fields require strict aliases, matching `__available_date` companions and `provenance.externalFields` mappings. Identity, unit, period, known time and source checks apply before time alignment. A field registry entry is not data coverage. Input details and PCD binding examples are in [DATA.md](DATA.md); four-store ownership is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Selection and output

Features are causal; labels use next-session open through horizon-end open. Folds split unique dates, purge labels crossing every boundary, and fit preprocessing only on training data. Three outer folds evaluate a nested selection procedure. Final development folds select a frozen model; terminal approximately 20% holdout never selects its parameters. [METHODOLOGY.md](METHODOLOGY.md) defines the exact procedure, finite grids and evidence status.

Result fields include `schemaVersion,status,engineVersion,strategy,provenance,selection,metrics,equity,trades,factors,warnings,validation,predictions`. Selection retains candidate results, split dates, scores, parameters, evidenceStatus and `holdoutUsedForSelection:false`. Validation includes cash/position audit, input fingerprints and limitations. Non-finite optional metrics serialize as JSON null.

`predictions` includes target definition, horizon, score interpretation, every holdout date/symbol score and rank, latest ranking, actual/predicted target when meaningful, earliest execution date and trailing historical risk. The unsupervised factor baseline has no estimated return; its `predictedTarget` is null. Incomplete future labels remain null. The final model is not refitted using holdout observations.

Headline metrics cover holdout only. Fills use fractional adjusted research units, next-session open and explicit commission/slippage/sell tax; missing data cannot fill. Daily cash plus marked positions must reconcile to equity. This is a custom research simulator, not a broker or full A-share exchange. `NO_VALIDATED_EDGE` remains a valid completed research outcome; even positive validation does not grant deployment qualification.

## Service and execution boundary

The Worker keeps workspace ownership, versions and leases in D1, with private upload/result objects in R2. Browser identity is an HttpOnly cookie, not an Atlas account assertion. Strategies, runs and code projects remain owner-isolated; community factor definitions are public and unreviewed.

The trusted Python runner polls outbound HTTPS, executes bounded DSL jobs in subprocesses, and returns results under the claimed lease. Private provider/PCD credentials come from external runtime configuration and never from public strategy JSON. Jobs default to 900 seconds, upload and result objects are each bounded at 24 MiB. Results are complete or explicitly rejected for size, not silently truncated. Local and hosted jobs use the same numerical implementation.

User Python runs separately in a browser Pyodide worker; the server does not execute saved user Python. AI review is an optional real Cloudflare binding call with original-source patch validation and explicit user application. Neither rule checks nor AI review claim to have completed a backtest. Deployment configuration, lifecycle, capability readback and recovery are in [OPERATIONS.md](OPERATIONS.md).

## Evidence

Numerical invariants and leakage tests, deterministic synthetic experiments, real provider probes, hosted completion and live availability are separate evidence. See [engine-v0.2-validation.json](engine-v0.2-validation.json), [source-validation.json](../data/source-validation.json) and the historical v0.1 artifacts. No repository snapshot implies continuous uptime, original-filing verification for vendor fields or profitable future performance.
