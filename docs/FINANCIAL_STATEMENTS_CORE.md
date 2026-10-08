# Native financial statement core

Date: 2026-10-08. Scope: an independently callable numerical core and bounded, injected-client adapter, verified with hand-calculated fixtures and fake provider responses. **No production provider wiring, catalog registration, strategy engine integration, UI, deployment or live provider request is included in this implementation/test evidence.** Existing `fina_indicator` aliases and frozen reports remain unchanged. This is the first vertical slice of [the approved plan](FINANCIAL_FACTORS_PLAN.md), not completion of its entire roadmap.

The subsequent [frozen input/prepare slice](FINANCIAL_INPUT_PACKAGE.md) adds one shared offline computation path, bounded input packages and an explicit `allow_declared` unit policy. The original `verified_only` policy remains the default. Declarations are never verified proofs; their quality flags and scope follow every dependent result. Earlier test counts below remain dated checkpoints rather than the new combined-suite count.

## Public contract

Import from `atlas_quant.financial_statements`:

```python
store = build_store(records, calendar)
raw = store.value(symbol, field_id, period_end, as_of,
                  scope="consolidated", basis="ytd")
q = quarter(store, symbol, field_id, period_end, as_of,
            scope="consolidated", flow_basis="ytd")
annual = ttm(store, symbol, field_id, period_end, as_of,
             scope="consolidated", flow_basis="ytd")
states = compute_states(store, symbol, as_of, ids=None,
                        period_end=None, scope="consolidated", flow_basis="ytd")
```

- `StatementRecord` is immutable. It carries symbol, endpoint, report period, announcement dates, report/company types, fiscal-year end, raw numeric fields, per-field `UnitEvidence`, `SourceRef`, and the retained update flag. `requested_fields` records the response projection (default: the supplied value keys). An unrequested field creates no observation; an explicitly requested absent field is `FIELD_MISSING`, and an explicit null is `NULL_VALUE`. Independent sparse projections therefore do not erase one another. Numeric strings, booleans and nonfinite values are rejected as malformed input. Raw Decimals accept at most 100 coefficient digits, absolute stored exponent 100, and absolute adjusted exponent 100; larger/smaller magnitudes fail with `ContractError` rather than overflow or round to zero.
- `SourceRef` requires provider, immutable snapshot identity, timezone-qualified retrieval timestamp and `kind=fixture|provider`. Retrieval time is provenance, not a substitute publication date.
- `UnitEvidence` requires native unit, currency, verified flag, reference and `kind=fixture|source_contract|source_document`. The core consumes a caller-supplied unit contract; it does not independently authenticate the evidence reference. A provider record cannot use fixture evidence. Scoped provider evidence is retained as immutable `UnitScope`; absent scope, mismatched scope and conflicting proofs remain missing. Currency support in this slice is CNY; explicit CNY/1,000-CNY/10,000-CNY units normalize by registered scales. Unknown unit or currency is missing, even if a ratio would appear to cancel the dimension. Capex cash requires explicit positive-outflow sign evidence.
- `TradingCalendar` requires sorted unique sessions, coverage start/end, completeness declaration, source reference and `kind=fixture|official`. A provider record cannot use the test calendar as official evidence. The core validates the supplied contract but does not fetch or independently verify exchange holidays.
- `ValueResult.value` is `Decimal` or `None`; `status` is `ok|missing`. `to_dict()` serializes `decimalValue` as a string, plus precision/rounding, unit, period, availability, reason codes, dependencies, formula/policy versions and lineage hash. It does not claim a finite decimal quotient is exact. The separate DataFrame adapter explicitly converts valid derived Decimals to finite engine floats while retaining the audit string and recording the conversion.
- `ContractError` is reserved for malformed input or an unknown API argument/field. A well-formed record with unsupported/missing financial semantics produces a missing result, with reasons such as `UNIT_UNVERIFIED`, `PERIOD_NOT_AVAILABLE`, `CONFLICTING_DISCLOSURES`, `UNSUPPORTED_REPORT_PERIOD`, `UNSUPPORTED_COMPANY_TYPE` or `DENOMINATOR_NONPOSITIVE`.

## Policies and evidence

`statement_asof_v1` uses the later valid `ann_date`/`f_ann_date`, then the first strictly later covered trading session. No valid announcement means no usable observation. Any announcement before its report period, an incomplete calendar, announcement outside coverage, or unavailable next session cannot manufacture an availability date. Observation dates must themselves be covered sessions.

Within each symbol/field/period/scope/basis coordinate, select the latest disclosed version available at the requested `as_of`. Identical raw content is deduplicated; conflicting same-date values are missing. Update flags and input order do not establish publication order. A later revision of an old period cannot replace the newer report period used as the latest anchor. A latest null is not filled from an older version.

Only calendar quarter ends and a December 31 fiscal-year end are supported. Report-type mapping separates consolidated/parent and cumulative/single-quarter records. Ambiguous report type 11 is deliberately unsupported in this contract version. Adjusted and original rows retain their source report type; they are not assigned invented timestamps.

`calendar_quarter_v1` computes Q1 from YTD Q1, and subsequent quarters from current YTD minus the preceding quarter's YTD in the same calendar year. Explicit single-quarter mode uses its own series. `four_calendar_quarters_ttm_v1` sums four actual quarters and requires every dependency. There is no fallback from an incomplete cumulative series to a different single-quarter series, nor a daily rolling sum over forward-filled statements.

Every quarter dependency selects its own report-period version at the same `as_of`, with identical scope, currency and declared flow basis. A revision to Q1 may change the derived Q2 in later observations; both actual source versions remain in the dependency evidence. This is a reproducible policy, **not independent proof that the provider preserves every original as-published revision or that all cumulative revisions form a fully restated statement set**. `PROVIDER_ORIGINAL_AS_PUBLISHED_VERSIONS_UNVERIFIED` remains in every serialized result.

Unit normalization moves a registered power-of-ten exponent exactly, before any rounding, so two different high-precision disclosures cannot collide merely because a later formula uses 34 significant digits. Derived arithmetic uses a local 34-significant-digit `ROUND_HALF_EVEN` context independent of the process-global Decimal context. `decimalPrecision` describes that computation policy, not an assertion that raw source values were rounded to 34 digits. Dependencies preserve original Decimal strings, source snapshot and raw record hash, field/unit evidence, report type, report period, announcement and availability. Adding known future disclosures changes neither past selected dependencies nor past result lineage hashes. The result hash does not contain unrelated future rows from the complete store.

Saved results also include a compact calendar contract: session-list hash, coverage, completeness declaration, source kind/reference and a root over that metadata. Field dependencies carry their mapping version, summarized in `mappingVersions`. Identical numerical results from distinct calendar evidence therefore have distinguishable lineage. The full calendar sessions remain in the frozen source store/snapshot, not repeated in every result. Statement-future perturbation tests hold this calendar contract fixed; replacing the calendar snapshot intentionally changes the evidence hash.

Each formula declares an anchor field. If `period_end` is omitted, the latest available report period for that anchor is selected; the calculation does not backtrack to an older complete set merely because other inputs are missing. Cross-table dependencies must match that period. Average assets are explicitly the mean of the ending balance and the same quarter-end one year earlier, matching the TTM interval.

## Implemented fields and states

There are **15 raw field contracts**, all within the existing read-only endpoint field whitelists:

| Endpoint | Native fields | Kind |
| --- | --- | --- |
| income | revenue, operate_profit, n_income, n_income_attr_p | Period flow |
| balancesheet | total_assets, total_liab, total_cur_assets, total_cur_liab, money_cap, accounts_receiv, goodwill, st_borr, lt_borr | Period-end stock |
| cashflow | n_cashflow_act, c_pay_acq_const_fiolta | Period flow |

Aliases are new `fd_income_*`, `fd_balance_*`, `fd_cashflow_operating` and `fd_cashflow_capex_cash`. Existing 21 `fd_` indicator aliases are untouched. These contracts accept explicit evidence-bearing observations; their existence does not establish provider history coverage.

All 16 states currently apply to company type 1 (general industrial/commercial). Financial institutions are explicitly unsupported for these recipes. Outputs are numerical input states, without a buy/sell direction, ranking strategy or profitability claim.

| State suffix after `model_fin_` | Formula | Hand fixture result |
| --- | --- | --- |
| revenue_quarter_yoy | Quarter revenue / prior-year same-quarter revenue − 1 | 180/150−1 = 0.2 |
| revenue_ttm_yoy | TTM revenue / prior-year TTM revenue − 1 | 600/500−1 = 0.2 |
| operating_margin | TTM operating profit / TTM revenue | 120/600 = 0.2 |
| parent_net_margin | TTM parent-attributable profit / TTM revenue | 60/600 = 0.1 |
| cash_revenue_ratio | TTM OCF / TTM revenue | 90/600 = 0.15 |
| profit_cash_asset_gap | (TTM total net profit − TTM OCF) / average assets | (80−90)/1000 = −0.01 |
| cash_assets_ratio | TTM OCF / average assets | 90/1000 = 0.09 |
| capex_revenue_ratio | TTM cash paid for long-lived assets / TTM revenue | 30/600 = 0.05 |
| cash_less_capex_assets | (TTM OCF − TTM cash paid for long-lived assets) / average assets | (90−30)/1000 = 0.06 |
| assets_yoy | Assets / prior-year same-quarter assets − 1 | 1100/900−1, rounded under the declared policy |
| cash_asset_share | Money capital / assets | 110/1100 = 0.1 |
| current_coverage | Current assets / current liabilities | 330/110 = 3 |
| liability_asset_share | Total liabilities / assets | 440/1100 = 0.4 |
| borrowings_asset_share | (Short borrowing + long borrowing) / assets | (55+55)/1100 = 0.1 |
| receivable_asset_share | Accounts receivable / assets | 55/1100 = 0.05 |
| goodwill_asset_share | Goodwill / assets | 22/1100 = 0.02 |

Required denominators must be positive; valid negative numerators/results are preserved. Two borrowing fields are not labeled all interest-bearing debt. OCF minus purchase cash is named by its formula, not called verified FCFF/FCFE.

## Verification and current boundary

From this worktree, using the existing sibling Python environment without adding symlinks:

```sh
PYTHONPATH=engine ../atlas-quant/.venv/bin/python -m pytest -q engine/tests/test_financial_statements.py
```

The initial 48-test run covered all 16 independently hand-calculated positive results, one missing-dependency counterexample per state, delayed disclosure/next session, cumulative quarters, cross-year TTM, revisions and past hashes, duplicate/conflicting/null records, unknown units/currencies/sign, unit conversion, scope/basis/industry, denominator, calendar/fiscal boundaries and global-precision independence. Independent review then reproduced and drove fixes for precision collisions, Decimal overflow/underflow, sparse response projections and missing calendar/mapping lineage. Static contract checks verify every dependency is whitelisted and no old alias is overwritten. After those fixes, **52 core tests + 12 independent adversarial tests + 15 existing connector tests = 79 passed**; this is a recorded checkpoint, not a claim that future additions have been run.

```sh
PYTHONPATH=engine ../atlas-quant/.venv/bin/python -m pytest -q \
  engine/tests/test_financial_statements.py \
  engine/tests/test_financial_adversarial.py \
  engine/tests/test_connectors.py
```

Fixtures explicitly use a synthetic weekday calendar and invented financial values. This is numerical/contract evidence only. Raw provider observations, real exchange-calendar evidence, complete historical versions, catalog/UI availability and forecasting integration are not yet established by this module. No field has been marked publicly runnable.

## Implemented injected-client adapter

`adapter.py` contains `load_statement_states`; `unit_bindings.py` owns source-proof scopes. Neither constructs a network client, reads credentials, changes runner behavior, or alters `load_financial_history`/the 21 existing `fd_*` indicator aliases. The caller supplies an object with `call(endpoint, params)`, a strategy universe, explicit history bound, calendar and unit bindings.

```python
from atlas_quant.financial_statements.adapter import (
    AdapterBudget, AdapterError, load_statement_states,
)

result = load_statement_states(
    client, strategy, selected_ids, calendar, unit_contract,
    announcement_start="20210101",
    budget=AdapterBudget(),
    source_kind="provider", source_provider="TUSHARE_PRO",
    # Pin this when replaying a frozen normalized snapshot.
    retrieved_at="2026-10-08T00:00:00Z",
)
# result.panel: ts_code, trade_date, selected state columns and __available_date
# result.snapshots: normalized table snapshots, not wire responses
# result.state_events: Decimal/audit result once per disclosure transition
# result.assignments: symbol + [from, through] + state -> event IDs
# result.provenance and result.coverage: source/assumption metadata and missing counts
```

The implementation materializes `selected_ids` once, so generators cannot be consumed during dependency resolution and then return an empty successful state set. It resolves only required table endpoints and uses the existing endpoint field whitelist. An explicitly requested financial or metadata column absent from a response is `RESPONSE_COLUMNS`, whereas a present null remains a missing disclosed value. The core can separately accept sparse declared projections.

Requests contain `ts_code`, `start_date` and `end_date`. For these three endpoints they are treated as announcement bounds, and non-null `ann_date` must actually be within the requested interval. This deliberately fails closed on a conflicting provider response. The official balance-sheet example has an apparent date-range contradiction, documented in [the source evidence review](TUSHARE_STATEMENT_EVIDENCE.md); the adapter does not silently use report period instead. Missing announcement dates cannot produce a usable state. A calendar must be complete over the declared history/research bounds and explicitly official for provider input.

The declared `announcement_start` is a lower bound on the history requested, not proof that every required older period or original version exists. A short initial history stays missing for quarter/TTM dependencies. The adapter never fills a first observed statement backward. New disclosure states take effect at the core's first strictly later covered market session and are carried forward only from that point.

An interval reaching the configured response threshold or a `TUSHARE_TRUNCATED` error is split recursively; a still-truncated single date is an explicit failure. The default `response_row_limit=1000` is a conservative **local guard, not an independently verified official endpoint cap**. `AdapterBudget` also limits requests (128), source rows (20,000), interval days (366), panel rows (110,000), state events (10,000), normalized source bytes (32 MiB), and serialized state-event evidence bytes (32 MiB). There is no cache/network retry layer in this module. Exceeding a budget raises `AdapterError(code, partial)` with completed request/snapshot metadata and `panelPublished=False`; it never silently truncates and publishes a successful panel.

### Snapshot and numeric boundary

`_safe_rows` receives `DataFrame.to_dict("records")`. Its canonical snapshot representation is `normalized_provider_table_snapshot`; individual row hashes use `normalized_provider_table_row`. The snapshot hash includes normalized rows, fields, endpoint, request, source declaration and retrieval timestamp. **It is not a raw HTTP response, original JSON numeric token, original disclosure PDF, or provider-signed hash.** Client JSON decoding and pandas may have converted a source number into binary float already. Decimal arithmetic cannot reconstruct lost source precision.

The result explicitly records `wireEvidence.availability="UNKNOWN"`, `wireBytesAvailable=False`, `wireNumericLexemesAvailable=False`, the normalized representation, and the numeric conversion limitation. Only successful Decimal states are converted to finite IEEE754 floats; overflow or nonzero underflow fails. Raw/derived Decimal strings and scoped dependencies remain in the event audit. A future exact lexical adapter must be a separate input contract, not an upgraded claim on this one.

### Enforced unit-proof scope

The adapter accepts registered field IDs mapped to a binding or a list of same-scope bindings. Bare `UnitEvidence(verified=True)` is rejected before any read. There are no default provider-verified units.

- `FixtureUnitBinding(evidence, field_id, provider, scope="fixture")`: explicit hand/test evidence, prohibited for provider input.
- `GlobalUnitBinding(evidence, field_id, provider, document_hash, scope="global")`: requires `kind="source_contract"`, an explicit global declaration and a nonempty SHA256 of the reviewed source contract. None has been installed from the currently reviewed Tushare documentation.
- `DocumentUnitBinding(evidence, field_id, provider, symbol, period_end, ann_date, f_ann_date, report_type, company_type, source_snapshot, normalized_row_hash, document_hash)`: requires `kind="source_document"`. Every coordinate and both normalized source hashes must match the exact observation. A PDF's SHA256 must also be present. `normalized_row_hash(endpoint, row)` is exposed for preparing bindings from the adapter's frozen normalized rows; it never claims wire identity. Because source snapshots include request/retrieval metadata, a binding must be replayed against that same frozen snapshot, not automatically transferred to a new fetch.

Mismatches become `UNIT_SCOPE_MISMATCH`; contradictory matching proofs become `UNIT_SCOPE_CONFLICT`. The final dependency retains field/provider/security/period/report/company/announcement coordinates, observed normalized snapshot/row hashes, matching status, document hashes, binding hashes and `statement_unit_scope_v1`. A changed document proof changes the lineage hash even if the numerical result is identical. Proof authenticity and the actual PDF-to-value comparison remain external review responsibilities; a syntactically valid hash alone is not authentication. For failed matches the candidate binding identities remain available rather than turning into unexplained missing coverage.

### Disclosure evidence is not historical-version verification

`availabilityPolicy="point_in_time_asof"` names the algorithm, not a data-quality certification. Provider-derived field metadata and adapter provenance explicitly contain:

```json
{
  "availabilityEvidenceLevel": "vendor_reported_disclosure_dates",
  "originalAsPublishedVerified": false,
  "revisionTimeVerified": false
}
```

Fixture metadata instead says `synthetic_disclosure_dates`. The provider path aligns returned values by the provider's stated announcement dates; it does not establish that a particular revised value was historically available on those dates. Unit matching against an exact PDF can support that exact unit observation, but does not itself verify publication timing or upgrade the entire historical series. A later evidence contract could upgrade only an explicitly matched document/disclosure version. Retrieval time, future-perturbation stability tests and `update_flag` cannot eliminate unobserved vendor back-revision bias. Downstream catalog/UI integration must display this research-data assumption rather than claim PIT verification.

### Adapter verification and remaining integration

The injected fake-provider suite exercises all 16 hand-calculated formulas through the complete table adapter, bounded interval splits, unresolved truncation, missing columns versus nulls, revisions/cross-year announcements, sparse histories, five exhausted budgets with retained metadata, exact symbol/date boundaries, the selected-ID generator regression, and the normalized/wire provenance boundary. Scope tests additionally reject wrong security, report period, disclosure date, report/company type, provider, snapshot, row and field bindings; verify document hashes change lineage; and retain unit conflicts. No live provider request is part of those tests. Final local checkpoint on 2026-10-08: **52 core + 12 independent adversarial + 20 adapter + 16 binding-scope + 15 unchanged connector tests = 115 passed** (0.90 seconds); module compilation also passed. These counts describe the executed local suite, not live-provider acceptance.

```sh
PYTHONPATH=engine ../atlas-quant/.venv/bin/python -m pytest -q \
  engine/tests/test_financial_statements.py \
  engine/tests/test_financial_adversarial.py \
  engine/tests/test_financial_adapter.py \
  engine/tests/test_financial_unit_bindings.py \
  engine/tests/test_connectors.py
```

Production calendar/source authentication, any narrowly authorized real-observation comparison, provider client wiring, snapshot persistence/cache, catalog/engine/UI opt-in and end-to-end research acceptance are separate integration gates. The sibling evidence review may record its own explicitly authorized real canary; that does not turn these hand/fake tests into provider evidence or make all 16 states globally runnable.
