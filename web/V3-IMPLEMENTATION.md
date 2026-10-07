# Atlas Quant 0.3 frontend

The main flow is a five-page basket-residual research workflow. Each page has its own hash route, previous/next navigation, current configuration summary, and corresponding Studio workspace. The summary starts collapsed on narrow screens and retains the user's disclosure state.

| Route | Working controls |
| --- | --- |
| `#research/universe` | Source, universe search, include groups OR, filters AND, exclude groups, explicit symbol inclusion/exclusion, full-set resolution, 40-member pages, explicit research subset, dates |
| `#research/baseline` | Copy one of the three stat-arb research baselines; add/fork factor definitions, drag factor packs, group definitions |
| `#research/signals` | Residual method, formation/residual windows, PCA components, refit cadence, observation cadence, entry/exit/stop thresholds, maximum holding period, half-life diagnostic threshold, formation-only decorrelation |
| `#research/execution` | Trade check cadence, basket weight-distance band, gross exposure, initial capital, hypothetical borrow cost, commission/minimum/slippage/tax/transfer assumptions |
| `#research/review` | Frozen configuration review, static field/dependency validation, save/export/run |

`#lab` remains a compatibility entry to the new first step. Studio retains separate data, factors, observations, model fitting, validation, execution, reports and code routes. Legacy model candidates and original fees remain editable only when the loaded strategy is a legacy long-only research record. Missing `research` is normalized to `legacy_long_only`; new research explicitly uses `stat_arb`. Legacy zero commissions are preserved and new transfer/minimum charges are zero for old records.

## Complete universe and subset semantics

The frontend calls `/universe-options` and `/universes/resolve`; it does not reconstruct membership from card counts or recommendation metadata. Selecting CSI 1000 resolves all 1,000 members and selects none automatically. Inclusion groups are joined with OR, filters within a group with AND, array values within a filter with OR. Exclusions apply after inclusion.

Changing a set rule clears the previously confirmed research subset and resolution hashes. The user must explicitly check members, enter codes, select the complete set when it fits within 50, or click the clearly labeled code-order sample action. Code-order sampling is described as a computational limit, never as a performance or representativeness screen. Outside-set manual codes require explicit set inclusion and a new resolution.

Saved strategies retain `selection`, `resolutionHash`, `snapshotHash`, `catalogSnapshot`, and `subsetPolicy` (`all` or `explicit`). Watchlist and data imports create a new explicit universe and remove stale collection bindings. Saved template identity uses only `research.baseline.id/name`; actual performance comparisons use engine output.

## Reports

The dedicated stat-arb renderer consumes `result.statArb` and does not route through per-stock predictions. It shows:

- Net equity, cash benchmark, turnover, gross/net exposure drift and actual cost totals.
- Actual with/without-factor baseline comparison and zero-cost counterfactual, with declared same-window/same-cost flags.
- Residual events, half-life/AR(1), basket state and reason; 50-row pagination, explicit truncation disclosure and CSV export.
- Rolling model fits, retained/dropped hedge exposures, residual degrees of freedom, neutrality errors, PCA variance and actual loading matrices; 20-fit pagination.
- Trade legs, transfer fees, aggregate borrow costs, data provenance and audit export.

The UI distinguishes theoretical borrowing from verified inventory, AR(1) half-life from cointegration, current constituents from historical membership, and predeclared rules from evidence of effectiveness. Legacy `factorResearch` reports additionally expose actual holdout Rank IC/ICIR and quantile diagnostics without calling their long-short label spread an executable arbitrage portfolio.

## Verification completed before browser QA

- `node web/tests/research-flow.mjs`: 34 frontend fixture checks covering no implicit sampling, invalidation of stale subsets, independent routes, cadence separation, stat-arb bounds, legacy normalization, and report pagination/schema.
- `node web/tests/studio-contract.mjs`: 41 checks against the local API, including real factor/field catalogs, conditional PCD mapping and uploaded PIT readiness, code project versions, universe resolution and explicit subset application.
- `node web/tests/research-api.mjs`: 11 actual local API checks. CSI 1000 returned 1,000 members; Beijing AND filter returned 80; Shenzhen OR group returned 161; exclusions returned 80. Explicit 20-symbol configuration and template identity survived save/readback with both hashes.
- `node web/tests/research-dom.mjs`: 12 real DOM-event checks using the exact locked jsdom dev dependency. Input events persist immediately; observation 5 and rebalance 10 survive a background render before blur, actual next-step button clicks, hash navigation, and the saved request/readback. This test is included in CI.
- `node web/tests/report-readback.mjs <private-report-paths...>`: three existing, genuine TUSHARE_PRO stat-arb reports rendered successfully across all five report tabs (15 page renders). Each report had 1,296 residual observations and 27 model fits; the added-factor report exposed the actual baseline comparison. This test performs no provider request and writes no private report to public files.

The first test uses fixtures and proves UI behavior only. The API tests do not submit research jobs or contact Tushare. The DOM test is distinct from actual browser acceptance; the root task separately verified the 5/10 cadence edits in IAB. Actual report readback retained all three outcomes, including both negative-return baselines; theoretical borrowing remains unverified. No production deployment is performed by the frontend task.

All 15 factor pack titles/descriptions now state measurable exposures and timing; the 46 referenced underlying definitions, IDs, formulas, directions and versions are unchanged. The mean-residual method is labeled as equal-weight basket common-component removal and dollar neutrality, without implying market-beta neutrality.
