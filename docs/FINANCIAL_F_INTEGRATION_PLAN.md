# Frozen statement states into F — next vertical slice

Status: original predeclared plan with an offline acceptance update, 2026-10-08. The separately authorized one calendar read and four market reads have completed; the six earlier statement reads were not repeated. The fixed forecast-only research completed once with no validated forecast advantage. Financial core commit `7409f8b` remains independently testable. The subsequent bridge is a local candidate, with no hosted admission, financial execution replay or closed public evidence export. See [the implemented bridge contract](FINANCIAL_DATASET_BRIDGE.md) for the precise current boundary. Sections below preserve the acquisition and research decisions fixed before those reads.

## 1. One separately budgeted calendar read

Use the existing `TushareClient` and approved private runtime access, in an isolated acquisition process. Persist the intent before the request; no credentials in the intent or output. The six statement reads in the 2024 canary are complete and must not be repeated.

```json
{
  "api_name": "trade_cal",
  "params": {"exchange": "SSE", "start_date": "20250101", "end_date": "20251231"},
  "fields": "exchange,cal_date,is_open,pretrade_date"
}
```

One actual HTTP attempt, no retry, no pagination, no alternative host/year/exchange. Because the shared client currently retries transient errors, the isolated session must refuse a second `post` before network access. Use the existing client timeout `(10,35)`, a 50-second outer deadline, a 256-KiB streamed-response cap and a 365-row cap. Missing/unknown outcome is recorded and does not authorize another attempt.

Do not filter `is_open`: require exactly the 365 unique civil dates of 2025, each `exchange=SSE`, valid `is_open` in `{0,1}`, and valid `pretrade_date`. Check the predecessor chain for dates whose predecessor lies inside the response; the first predecessor outside the requested year is retained without inventing earlier evidence. Missing, duplicate or out-of-range dates block `complete=True`. Freeze request hash, retrieval time, transport outcome, raw response bytes/hash, normalized table bytes/hash and sorted open-session hash in private 0600 files.

The [official trade_cal documentation](https://tushare.pro/document/2?doc_id=26) lists these four fields, defaults to SSE, and explicitly states that the three exchanges have the same trading calendar. Therefore one SSE response can support a **provider-declared equivalent calendar** for this SH/SZ canary. Metadata must still say `responseExchange=SSE`, `appliesTo=[SSE,SZSE]`, `equivalenceBasis=provider_documentation`, and retain the documentation citation/hash; it is not a separately fetched SZSE response or independent exchange authentication. The documentation does not establish intraday release timing.

This interval is fixed before observing any model performance. It covers the actual retained disclosure dates (Haier: 2025-03-28; Gree: 2025-04-28), their next trading sessions and the remainder of the same calendar year. No extension into 2026 to improve a validation result is included.

## 2. Produce the actual six-state daily panel

Revalidate the unchanged canary manifest, six normalized snapshots and 32 exact document bindings. Use the two frozen balance-sheet rows, report period `20241231`, report type `1`, company type `1`; do not modify snapshot rows/columns or substitute new hashes into existing proofs. The private PDF/wire evidence stays private.

Create a complete `TradingCalendar` only after the calendar checks pass. Its evidence reference identifies the raw calendar response plus the SH/SZ equivalence policy. Then use the existing `freeze_package` / `prepare_package` path with:

- universe `600690.SH`, `000651.SZ`; research and announcement-history interval `20250101–20251231`;
- `unitPolicy=verified_only`, trusted caller resolving only these already reviewed `DocumentUnitBinding` objects;
- selected states `model_fin_cash_asset_share`, `model_fin_current_coverage`, `model_fin_liability_asset_share`, `model_fin_borrowings_asset_share`, `model_fin_receivable_asset_share`, `model_fin_goodwill_asset_share`.

The exact fields are supported by this period. Before each symbol's later `ann_date`/`f_ann_date` reaches its strictly next session, values remain missing. Afterwards values carry as-of the **frozen report set**, with `periodEnd=20241231`, source event IDs and age visible. This is not a claim that no newer 2025 filings existed; `completeHistoricalVersionsVerified`, `originalAsPublishedVerified` and `revisionTimeVerified` remain false. No TTM, year-on-year or average-asset state is enabled from one annual period.

Retain `packRoot`, `preparedRoot`, calendar root, Decimal events, assignments, per-state/per-security coverage, missing reasons and independent audit output. Exact covered-session count is unknown until the sole calendar response passes verification.

## 3. Minimum engine/DSL bridge

The implemented pure `financial_statements/dataset.py` composition function accepts a frozen market dataset and frozen financial packages; it does not accept caller-supplied prepared outputs, fetch callbacks or credentials. It revalidates and recomputes those outputs, checking symbol/date scope, root consistency, compatible calendars, uniqueness and row-level available dates. It joins only on existing market `(ts_code,trade_date)` rows. It does not forward-fill prices or manufacture market observations. Financial as-of carry is already explicitly performed by the statement core.

The existing DSL accepts `model_fin_*` through its strict external numeric-field grammar. The bridge keeps the sixteen registered state IDs and represents them as `semanticKind=native_statement_state`, with actual required statement fields. Fundamental-family validation recognizes only registered IDs. Engine admission additionally requires the exact object produced by recomposition and unchanged values/provenance; metadata alone never establishes that origin. Arbitrary `model_*` uploads are not verified financial states, and financial states are not model forecasts with a training-history claim.

For each joined field, retain `source`, `path`, `dataType`, exact `__available_date`, formula/version, `packRoot`, `preparedRoot`, calendar root, unit policy, evidence levels, declaration hashes and quality flags. Required validation stays: non-null numeric values are finite, availability is present and no later than the signal session, and every non-null joined value maps to its original event/assignment. One corrupted root or unmatched value rejects composition before fitting.

No unbounded metadata arrays belong in the small catalog or forecast manifest. A compact dataset binding can be:

```json
{
  "financialInputs": [{
    "packRoot": "<64hex>", "preparedRoot": "<64hex>", "calendarRoot": "<64hex>",
    "unitPolicy": "verified_only", "selectedStateIds": ["model_fin_cash_asset_share"]
  }]
}
```

The private dataset must actually retain and resolve those immutable evidence artifacts. References alone do not make an export reproducible. Initially the local CLI may save the frozen package and audit ledger as a private sidecar alongside the market snapshot and report. Before hosted admission or claiming one downloadable self-contained bundle, the transport contract needs explicit financial-evidence collections/reference closure, owner isolation, chunk/resource budgets and export/import audit support. Do not place a potentially 24-MiB financial package in the existing small report metadata.

## 4. First F acceptance and remaining market-data gap

Fix one acceptance configuration before execution: two symbols above, 2025 interval, `asset_price`, horizon 5, `fundamental`, Ridge, forecast-only, all six states as predictor factors, unchanged nested validation and the state-only baseline. Keep every invalid/tail origin and any negative or unavailable increment. This demonstrates the pipeline; two companies and one fixed report period cannot establish general predictive value.

The known old frozen market artifact inspected during planning contains `000001.SZ` and `600000.SH` through 2025-09-30, not the two canary companies. It cannot supply this research. First inspect bounded existing frozen artifacts for an exact matching SH/SZ 2025 price dataset. If none exists, stop at the correctly prepared financial panel and propose a **separate** predeclared market-data acquisition (two securities × daily/adj_factor = four reads), reusing the frozen calendar. Those four reads are not included in the one-calendar-read plan. No synthetic price fallback and no repeated statement requests.

Once genuine matching market inputs exist, perform sample/coverage preflight before fitting. If the fixed year does not supply enough valid nested-fold dates, retain the panel and exact insufficient-data result; do not shorten gaps, reduce tests or select a longer interval after seeing model performance. Compare same-target/full-coverage baseline, preserve the complete forecasts and perform the independent report audit.

## 5. UI/API contract and acceptance gates

The source card should show six actually prepared states with per-security coverage and exact report period, not sixteen universally ready catalog items. Missing history, missing market data, incomplete calendar and unit assumptions must be separate reasons. The report lists formula, current source period/age and evidence level; on-demand detail resolves a state event to its Decimal dependencies and exact source snapshot/document scope.

A future owner-scoped API admits unverified declarations or references to operator-reviewed proofs; it never accepts a client trust boolean. Preparation produces immutable input/derived IDs, and saved experiments reference them. Running or replaying resolves those IDs rather than refetching. Only inputs with selected states and adequate coverage receive a ready status; disclosure/units verification and forecast quality are independent.

Required tests: no pre-disclosure values, correct later-date/next-session mapping, calendar response gaps/duplicates, no SH/SZ evidence overstatement, document-scope/root tampering, deterministic offline replay, exact state-to-event mapping, declared assumption propagation, no automatic TTM from the annual canary, missing market rows remain missing, recipe metadata cannot self-upgrade upload trust, and large evidence stays within the common resource budget. Real canary acquisition, daily preparation, F fitting and hosted publication remain distinct evidence gates.
