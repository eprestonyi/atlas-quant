# Offline financial dataset bridge

Local candidate, 2026-10-08. This slice connects actually prepared statement states to forecast-first research. It does not implement an HTTP endpoint, a hosted ready dataset, financial execution replay or a public evidence-complete bundle. The independently committed financial core remains separate from this candidate.

## Composition interface

```python
compose_financial_dataset(
    strategy,
    market_dataset,
    financial_packages,
    *,
    trusted_unit_proofs=False,
    budget=DatasetBudget(),
) -> FinancialDatasetResult
```

`market_dataset` contains `schemaVersion`, `rows`, and `provenance`. `financial_packages` contains complete immutable input packages, as dictionaries or encoded bytes. The bridge revalidates every root and recomputes every preparation; it never imports caller-prepared rows. The result contains:

- `data`: the composed DataFrame, with values and per-state `__available_date` columns;
- `provenance`: market and financial roots, calendar identity, formula metadata, unit policies, scoped evidence and source limitations;
- `financial_artifacts`: the exact packages, prepared Decimal events, assignments and compact coverage summaries needed for private audit.

`to_dataset()` serializes joined rows and provenance only. It deliberately does not embed full input packages or dependency ledgers and therefore is **not a closed financial research archive**. Deserializing it does not grant permission to use reserved financial states. A future immutable dataset must close the market, packages, reviewed proof references, calendar, preparation and coverage components before admission or export.

The existing market validation remains in force. The bridge never creates a missing market observation or fills OHLC. Package scope must use the exact research interval, a subset of the research securities, and the same complete trading sessions as the market dataset. Overlapping symbol/state sources and duplicate package roots are rejected. A frozen single-period report set is not presented as complete filing history or as the latest available filing on every research date.

## Budgets

The defaults remain 50 securities, 110,000 rows, at most 8 financial packages, 24 MiB of total package bytes, 24 MiB of joined output and 64 MiB of common serialized input/preparation/output accounting. Input sizes are checked before preparation; the remaining parent budget is passed into the statement adapter. Joined rows are charged incrementally. These are serialized byte limits, not a promise that Python RSS equals 64 MiB. Separate 300-security market capacity evidence does not expand this financial profile.

## Source and trust boundary

Only the sixteen IDs in `financial_statements.RECIPES` belong to the reserved `model_fin_*` namespace. A formula name alone passes only structural schema validation. `_prepare_data` additionally requires a weak-reference admission created after successful `compose_financial_dataset` recomputation and all budget checks.

This admission is an **in-process source check**, not PDF authenticity, publication-time, calendar-authority or licensing verification. Python library callers are trusted to resolve reviewed proofs and calendar sources out of band. An HTTP integration must never expose `trusted_unit_proofs` as a client option. Declared unit assumptions keep `verified: false`, declaration hashes and quality flags throughout the derived evidence; an admitted object does not upgrade them.

The admission checks the exact original DataFrame identity against the independent copy consumed by the engine, using row values/order, column names/order, dtypes and the complete provenance hash. It stores no strong reference to the frame and is removed automatically when the original frame is collected. It does not authorize a different run's object.

`DataFrame.copy()`, JSON round trips, forged `semanticKind`, a valid registered field name with no recomposition, field replacement and data/provenance edits fail with `FINANCIAL_RECOMPOSITION_REQUIRED`. Callers must recompose the immutable original inputs. Generic JSON upload also rejects reserved financial rows, registry entries, roots or selected fields before ingestion. Existing non-reserved PCD/EXT/FD/MODEL numeric uploads retain their previous unverified point-in-time contract.

No object ID, weak reference or temporary token enters the forecast artifact. Its data fingerprint is the existing full-precision market/factor/calendar bytes followed by canonical serializable `financialCompositionVersion`, `marketRoot`, `financialDatasetRoot` and `financialInputs`. Recomposition of the same inputs produces the same fingerprint; changing the evidence/policy closure changes it even when numeric values stay identical. The full financial dataset root binds original market content, prepared/package/calendar roots, selected states, unit policy, full-precision joined values and field evidence.

All current entry points require schemaVersion 2, `research.mode=statistical_quant` and explicit `execution.enabled=false` when the dataset carries financial states or financial closure roots. Initial execution, legacy and statistical-arbitrage routes fail with `FINANCIAL_FORECAST_ONLY_REQUIRED`; dropping the selected financial factors does not bypass the dataset-level check.

`execute_forecasts` explicitly returns `FINANCIAL_REPLAY_NOT_AVAILABLE` for these inputs, even when an admitted object is still alive. Future financial replay must rehydrate and audit the complete immutable component closure; the temporary source check is not a substitute. Existing bundle/1 readers and runner snapshot replay have not been upgraded for this financial fingerprint and evidence closure.

## Coverage semantics

`summarize_prepared` returns roots and one summary per state/security. Each includes `okRows`, `missingRows`, `firstAvailable`, `firstObserved`, `lastObserved`, `lastAvailable`, `latestPeriodEnd`, `latestAvailableDate`, `lastPreparedDate`, `latestAgeCalendarDays`, `periodEnds`, and day-weighted `reasonCounts`.

Availability is the original conservative effective trading date. Observed dates describe where the prepared panel contains a value; these are distinct. `latestAgeCalendarDays` is the natural-day difference between the end of the prepared interval and the latest available report period end. `status: available` means some numerical values exist, not that the selected research has enough training/validation observations. Complete publication versions, original-as-published status and revision timing remain unverified unless independently established by a future input contract.

## Fixed real-input acceptance

The private canary retained two companies' consolidated industrial 2024 annual statements and exact document-scoped unit bindings. No part of that evidence is generalized to other companies, periods or fields. One separately authorized SSE calendar read supplied 365 civil days and 243 open sessions for 2025. The SH/SZ calendar alignment uses Tushare's documented equivalence, not a separate SZSE response. Four separately authorized reads supplied daily and adjustment-factor rows for `600690.SH` and `000651.SZ`, 243 rows each. The earlier six statement requests were not repeated.

Six static balance-sheet states were prepared: cash/assets, current coverage, liabilities/assets, borrowings/assets, receivables/assets and goodwill/assets. All 486 security/session rows remain present. Per state, Haier has 187 non-null rows from 2025-03-31; Gree has 167 from 2025-04-29. Earlier observations remain missing. No TTM or growth state is manufactured from a single annual period.

The fixed study used 2025, the two securities, five-session asset-price predictions, fundamental Ridge, all six predictors and forecast-only execution. Its strategy bytes were retained before fitting. One offline fit produced 74 terminal prediction rows: 62 valid mature predictions over 31 scoring dates and 12 invalid tail rows. The preflight retained all 364 candidate origins, including 15 with unavailable fundamental inputs.

The result was `NO_VALIDATED_FORECAST_EDGE`. The six-state model's date-balanced joint MSE was approximately `0.0001854731`, versus `0.0001795851` for the separately fitted state-only model under the same targets, masks and candidate budget: a relative improvement of **−3.28%**. Relative improvement against no-change was **−10.85%**. These are prediction-error comparisons, not portfolio returns. No significance test or confidence interval was produced. There was no execution or profitability claim.

The complete private report passed the independent standard-library report audit with 372 identity checks. Private evidence includes the acquisition intents/raw responses, package/bindings/calendar, fixed strategy, exact implementation hashes, preparation, joined snapshot, pre-fit origins, full report and audit. These facts establish the local acceptance only; they do not establish hosted operation or public reproducibility of the private vendor/document evidence.

## Tests and remaining integration

Synthetic tests cover all sixteen registered states entering F, actual missing-input gates, declaration propagation, calendar/scope collisions, duplicate sources, budget preflight, weak-reference cleanup, reserved upload rejection, exact data/provenance mutation rejection and deterministic root-bound fingerprints. The live-object financial replay refusal has its own regression.

The candidate passed 609 Python tests, including 12 independently authored admission regressions; after merging the accepted Studio/DSL changes from main, all 696 Python tests passed. The independent future-filing perturbation test re-freezes and recomputes changed statement inputs and confirms that earlier X/y values stay exactly unchanged; it performs no model fit or provider request. The real-data report was not rerun after the forecast-only entry guard: a separate offline recomposition confirmed the same source commitment and data fingerprint with zero forecast calls.

Next integration must resolve server-authorized calendar/proof registries, preserve owner isolation and immutable references, recompute the package in a bounded worker, and publish a typed closure with paged dependencies. Only then can the UI attach a ready research dataset. The HTTP state machine is a separate workspace design; it is not implemented or published by this Python bridge.
