# Automatic factor preprocessing, version 1

`preprocess.automatic = {"schema":"auto-factor-preprocess/1"}` opts into
this protocol. Its absence retains the original feature and scaling semantics,
including `atlas-model-function/1` artifacts. New functions use
`atlas-model-function/2`. Existing archives are not migrated.

## Constructed inputs and model inputs

For a declared factor, the engine evaluates its causal expression on each
security's session grid, applies the semantic transform, and then applies its
direction. Asset factors are aggregated using the origin's signed dollar
weights `q_j p_j / sum(abs(q_j p_j))`. Thus a negative factor direction or short
leg never causes a positive raw market cap to be logged after it became negative.

Pure named-index expressions are global. Their independent daily series is
evaluated once and broadcast. Same-date contradictory source observations fail.
Missing source dates remain missing. Global inputs are retained once per target,
including net-neutral baskets; they are not multiplied by net portfolio exposure.
Mixed index/asset expressions retain asset scope. An index's identity describes
the named series; it does not infer historical industry membership.

The resulting input is called `R_i` in the report. Portable F accepts these
constructed inputs. It does **not** execute source DSL or repeat log/return
transforms. The model's actual `X_i` applies the frozen training-fold clipping,
median imputation and, when enabled, median/IQR scaling to `R_i`.

## Fixed semantic rules

Only a raw single-field expression gets a type-based rule. Compound expressions
retain their explicit arithmetic, followed by fold preprocessing. Display names
never select a rule.

| Registered raw field | Transform | Invalid domain |
| --- | --- | --- |
| Total/free-float market cap and share counts | Natural log | Nonpositive → missing |
| Volume and amount | `log(1+x)` | Negative → missing; zero remains zero |
| PE, PB, PS | `1/x` | Zero → missing; negative ratios remain negative |
| Registered percent fields, including financial percent indicators | `x/100` | Nonfinite → missing |
| Adjusted open/high/low/close; named index close | One-session return divided by the previous 20 returns' sample standard deviation | Nonpositive prices, incomplete history or volatility ≤ `1e-8` → missing |
| Other raw fields and explicit compound DSL | Identity | Nonfinite → missing |

For the price rule, `r_t = p_t/p_(t-1)-1` and
`R_t = r_t / std(r_(t-20),...,r_(t-1), ddof=1)`. The current shock cannot enlarge
its own denominator. There is no forward fill. Bare `raw_close` is rejected in
automatic mode because its corporate-action jumps are not adjusted returns;
explicit compound DSL remains the user's declared quantity.

## Training populations and frozen parameters

Every fit receives only the existing maturity-purged training rows. Asset columns
fit their statistics on those pooled rows. Global columns use each actual
training date once, even when the number of stocks or valid targets differs
across dates. Duplicate global values on a date must agree, including missingness.
At least 10 observed rows, or 10 observed global dates, are required per input.

With winsorization enabled, the bounds are the population's 1st/99th percentiles.
Imputation uses the clipped population median. Standardization subtracts the
imputed population median and divides by its 75th−25th percentile. An IQR below
`10 * float64_epsilon` uses scale 1. No PCA is inserted by this protocol.

Each fit records `automaticPreprocessing`, `automaticFitRows`,
`automaticObservedRows`, clipping bounds, imputation medians, and actual
`scalerMean`/`scalerScale` vectors. In v2 the latter represent median/IQR;
`scalerMethod` explicitly records `median_iqr`. The vector field names retain
the portable evaluator's existing affine operation. Constant baselines carry no
unused vectors or fit counts.

The v2 construction declaration freezes `fitPopulation:asset_rows_global_dates`
and each expression, direction, scope, semantic transform and aggregation.
Source membership checks bind the exported vectors and declaration to the
actual fit audit. Parameter edits remain explicitly unvalidated derived F.

## Independent index sources and input identity

Reserved `ext_ctx_*` fields require a complete frozen source package:
`contextSources`, `contextSourceRoot`, `contextScope` and
`contextObservationClock`. Removing the package cannot downgrade a named-index
study to the old input contract. Ordinary user-defined `ext_*` values still use
their existing observed-row/PIT contract and cannot manufacture an independent
history.

Each source freezes the registered API/code, exact request and field projection,
ascending unique dated records, records SHA and evidence classification. The
validator checks finite values and units, request/calendar boundaries, every
existing stock row's broadcast value and available date, source SHA and the full
source root. Whole-number floats use JSON integer spelling, including `-0 → 0`;
other binary64 values are never rounded. Declared hashes must already match this
archive convention; a different supplied root is rejected, never rewritten.

Only after this validation does the engine restore selected context columns on
the complete research calendar. An index observation survives a day on which all
selected stocks have no row. Stock OHLC, volume and amount remain missing; absent
index observations remain missing. The audit records the independent observed
date counts and restored broadcast counts. A content hash does not authenticate
the provider or verify historical revisions; re-uploaded packages remain
`USER_PROVIDED_UNVERIFIED` or `SYNTHETIC_USER_UPLOAD_UNVERIFIED`.

Such snapshots use `fingerprintVersion: research_input_context_v1`. The existing
17-digit research-row/calendar fingerprint additionally commits to
`contextSourceRoot`, so changing a source-only date changes the research input
identity. The snapshot retains the complete provenance. Inputs without this
package keep their old `research_input_v1` bytes and hashes.

## Source archive transport

Ordinary `atlas.quant.bundle/1` adds one optional collection:
`snapshotContextSources` at `/provenance/contextSources` in the snapshot. Each
item is a complete source object, packed within the existing 8 MiB chunk bound;
up to 16 source objects use the existing total bundle budgets. The report holds
only `api`, `params`, `fields`, `sha256` and `rowCount` for each source. Creating
that summary does not mutate the full snapshot or original provenance.

The Worker binds these summaries to exact uploaded source records, checks the
original numerical tokens' SHA, and streams the collection to verify its root.
The private encrypted spool, resumable transport and owner-scoped native archive
retain every source chunk. Source records are excluded from report pagination.
The context fingerprint version and source collection must appear together.
Existing bundles with no context collection and the separate financial bundle
protocols keep their original layouts. Independent restoration and the standard
library archive auditor verify the source-to-stock broadcast relationship;
transport integrity alone is not proof of market-data authority.

## Unchanged output units

This protocol normalizes **inputs only**. Outputs remain entry/future state
changes divided by the origin's known gross absolute leg value. Restoring V and
`e = P−V` uses that original scale. It does not turn e into a standardized
forecast error or claim a normal distribution, forecast edge, or trading result.

## Verification boundary

`test_automatic_preprocessing.py` uses local micro fixtures for causal price
normalization, negative-domain behavior, per-leg order, neutral-basket global
inputs, missing dates, future shocks, date-weighted global statistics, and JSON
prediction parity. `tests/fixtures/model-function-v2-golden.json` contains real
local estimator fits and their source audits for independent JS checks. These
are engine tests, not provider coverage, a production run, or strategy evidence.

`test_context_restore.py` covers independent-grid restoration and malformed input
rejection. `test_context_bundle.py` and `context-bundle.test.mjs` cover a synthetic
16-source, 2,200-record-per-source archive, bounded manifests, encrypted recovery,
source hashes and legacy byte identity. The Miniflare bundle tests additionally
upload, finalize and download the complete private archive through the actual
Worker routes. None of these transport fixtures runs a provider or fits F.
