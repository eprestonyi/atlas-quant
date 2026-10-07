# Historical Atlas Quant v0.3 contract

**Architecture decision, 2026-10-08:** new core research must first generate horizon-specific expected-price or fixed-basket-state forecasts before an independent execution layer can backtest them. The accepted [forecast research contract](FORECAST_RESEARCH_CONTRACT.md) defines that requirement and its acceptance gates. The v0.3 behavior documented below does **not** implement it; its z-score rules and historical reports must not be represented as forecast-driven research.

The historical v0.3 product follows five steps: **universe → baseline and factors → observations and signals → execution and costs → validation and report**. The new strategy example is [stat-arb.json](../engine/examples/stat-arb.json); the endpoint contract is [API.md](API.md). `schemaVersion: 1` is retained. New research explicitly sends `research.mode: 'stat_arb'`; older strategies without a mode remain `legacy_long_only` and keep their original long-only behavior.

## Research input

`atlas_quant.engine.run_research(strategy: dict, data: pandas.DataFrame, provenance: dict) -> dict`

The strategy contains universe, factors, preprocess, research, statArb, portfolio, costs, optional graph and dataBindings. Residual research accepts 3–50 distinct Shanghai/Shenzhen A-share symbols and 0–32 additional factor exposures, up to 110,000 rows, 2,200 official sessions and eight calendar years. The directory also contains Beijing-exchange identities; their discovery does not establish execution support in this release.

Universe discovery preserves full membership. A version-1 selection expresses OR between include groups, AND within a group, OR between values of one condition, then explicit additions and final exclusions. Resolution returns all symbols, per-step counts, source metadata and hashes. Saved configurations preserve selection, resolutionHash, snapshotHash and subsetPolicy (`all` or `explicit`). Enqueue resolves again: stale hashes or symbols outside the set fail. A set above 50 requires further filtering or an explicitly selected research subset; it is never silently truncated. Current membership is not verified historical membership. [Set contract](UNIVERSE_SELECTION.md)

Data is keyed by unique `(trade_date, ts_code)`; OHLC are consistently adjusted research prices, `raw_close` is the raw close, volume is in hands, amount in thousands of CNY. Providers supply the official `provenance.tradingDates`. Uploads lacking that calendar use the observed date union with a warning. Missing sessions and values remain missing; provider failure never substitutes synthetic data.

External numeric fields require strict `pcd_`, `fd_`, `ext_` or `model_` aliases, matching per-row `__available_date` companions and `provenance.externalFields` mappings. Identity, unit, period, known time and source checks apply before alignment. MODEL source declarations do not independently verify the original training history. A field registry entry is not data coverage. See [DATA.md](DATA.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Residual research

Three templates are available: equal-weight basket common-component residual, PCA residual, and PCA residual plus style exposures. Fifteen factor packs organize optional added exposures; neither a template nor a pack is validated alpha. `market_residual` removes the equal-weight common component, not heterogeneous stock beta. `factor_residual` additionally supports explicit factor exposures and requires at least one factor.

Formation returns end strictly before the signal date; added factor exposures are observed at that formation cutoff. The engine builds `M = I - B pinv(B)` with an intercept and the selected exposures. It projects basket states onto the residual space and normalizes to the declared gross target. Added factors help define the hedge space, not a single-stock future-return target. Changing an exposure's sign does not change its span. Missing, constant, dependent, overly correlated or saturating exposures are dropped with reasons while preserving a residual degree of freedom.

A rolling residual-level z-score, descriptive AR(1) half-life and predeclared entry/exit/stop/maximum-age rules determine states. No cointegration test or mean-reversion proof is claimed. Observation, refit and rebalance intervals are separate. Risk exits bypass the ordinary rebalance band, but still need the next available open and all required basket legs.

Formation and factor warmup must be followed by at least 90 sessions. The first approximately 70% of eligible calendar dates precede the terminal approximately 30% reporting interval. Thresholds and methods are not selected using that interval; rolling loadings may update from past observations. The reporting portfolio starts flat while causal signal states may continue from earlier observations. When factors are added, the no-added-factor baseline uses the same calendar, costs and reporting interval, without parameter retuning. Repeated human changes after viewing outcomes still introduce selection bias. [Exact method](STAT_ARB.md)

Result fields include `schemaVersion,status,engineVersion,strategy,research,provenance,selection,metrics,equity,trades,factors,warnings,validation,execution,statArb`. `research.singleStockReturnForecast` is false and `predictions` is null. `statArb` retains formation loadings and projection diagnostics, residual states, exposure drift, baselineComparison and costComparison. The event preview `statArb.signals.rows` retains at most the latest 5,000 holdout events with explicit `totalRows,truncated`; trades and daily cash/position ledgers remain complete. Non-finite optional metrics serialize as null.

Headline metrics cover the holdout simulation only. Fills use fractional adjusted research units, prior-close instructions and a next-session-open atomic basket. Long positions cannot be sold on the acquisition date. Costs include commission, a per-order minimum, slippage, sell tax, transfer fees and a declared theoretical short-borrow scenario accrued per trading session. The zero-cost comparison uses the same signal schedule, not a claim of executable gross returns. Cash plus signed marked positions reconciles to equity.

`shorting` must be `theoretical`. Borrow inventory, actual loan terms, margin financing, forced buy-ins, board lots and limit-price queues are not verified or fully modeled. Ordinary cash-account execution is not established. `selection.qualified` and `deploymentQualified` are always false; evidenceStatus is `UNVALIDATED_THEORETICAL_STAT_ARB`. Positive historical performance, baseline improvement and half-life estimates do not establish profitability, arbitrage or deployment readiness.

## Historical compatibility

[research-v02.json](../engine/examples/research-v02.json) retains the `legacy_long_only` / `factor` route: 1–32 factors, two forward-return targets, eight model families and fourteen fixed configurations. Causal features and next-open labels use unique-date splits, purging, training-only preprocessing, nested walk-forward validation and a terminal approximately 20% holdout. Its single-stock scores, predicted targets, rankings and TopN long-only portfolio belong to that historical route. They are not outputs of residual research. `NO_VALIDATED_EDGE` remains a legitimate completed result. [Legacy method](METHODOLOGY.md)

## Service and execution boundary

The Worker keeps workspace ownership, versions and leases in D1, with private upload/result objects in R2. Browser identity is an HttpOnly cookie, not an Atlas account assertion. Strategies, runs and code projects remain owner-isolated; community factor definitions are public and unreviewed.

The trusted Python runner polls outbound HTTPS, executes bounded DSL jobs in subprocesses, and returns results under the claimed lease. Provider/PCD credentials come from external private runtime configuration, never public strategy JSON. Jobs default to 900 seconds; upload and result objects are each bounded at 24 MiB. An oversized report is rejected rather than silently dropping its trades or ledger. Event-preview limits are separately labeled above. Local and hosted jobs use the same numerical implementation.

User Python runs separately in a browser Pyodide worker; the server does not execute saved user Python. AI review is an optional real Cloudflare binding call with source-match patch validation and explicit user application. Neither rule checks nor AI review claim to have completed a backtest. Deployment, lifecycle and recovery are in [OPERATIONS.md](OPERATIONS.md).

## Evidence

Numerical invariants, leakage tests, synthetic experiments, real provider probes, hosted completion and current availability are distinct evidence. Tests in `engine/tests/test_stat_arb*.py` and `tests/stat-arb.test.mjs` cover the new route; prior [v0.2 numerical validation](engine-v0.2-validation.json) and [source validation](../data/source-validation.json) describe their recorded historical runs. Those old model benchmarks do not validate the new residual strategy. No repository snapshot implies continuous uptime, original-filing verification for vendor fields or profitable future performance.
