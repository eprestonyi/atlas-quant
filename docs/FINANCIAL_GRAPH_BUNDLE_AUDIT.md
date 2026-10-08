# Independent financial bundle/2 and dataset/3 paired audit

`scripts/audit-financial-graph-bundle.py` is a new standard-library verifier for
local `atlas.quant.financial_bundle/2` result directories or exact USTAR archives.
It uses the unchanged independent legacy forecast identity, SQLite indexing and
archive helpers, with separate new manifest/collection/snapshot validation. No
production engine, graph codec, model or provider is imported. Legacy financial
bundle/1 and dataset/1–2 auditors remain unchanged and reject the new formats.

The result transport retains the original forecast/report/coverage canonical
codec and validates every declared collection, chunk hash, order/count, document
recipe and full logical document hash. It verifies forecast-origin plans,
forecast/baseline counts and references, causal fit cutoffs, numerical forecast
identities and disabled execution. Cross-record indices are private temporary
SQLite files; no archived result is rewritten.

The snapshot has the exact schema/3 nine-key contract and transports complete
columns through `snapshotColumns` at `/numericInput/columns`. Financial numeric
tokens retain integer/float distinctions, signed zero and null. Both the actual
physical snapshot and its full logical expansion, including metadata and
provenance, remain within 24 MiB. No remote replacement data is consulted.

With a source sidecar, the independent dataset/3 auditor checks the full source
closure and original logical roots. The pair must have exactly matching dataset
identity, scope, financial commitment, provenance and every ordered numerical
column. Rehashing an altered snapshot cannot satisfy the unchanged source table.
Without the source sidecar the result is `INCOMPLETE_SOURCE`, CLI exit 2.

Auditor revision 2 also derives the complete forecast domain from the source's
verified calendar, full frozen symbol scope and declared strategy clock. It
reuses the independent standard-library asset clock/DSL-window auditor; no
engine parser, `build_samples`, model function, reported lookback or saved origin
plan supplies the expected domain. The start is `max(61, factorLookback + 1)`;
the holdout boundary is `floor((calendarLength - start) * (1 - holdoutFraction))`
within the eligible calendar. Observation steps stay anchored at that start,
then are filtered to holdout. Each expected origin retains next-session entry
and horizon-end dates, including null dates at the incomplete tail.

All frozen symbols require exact single-asset target definitions and ordered
origin rows in the main forecast, factor-free baseline and planned origins.
Forecast/report/baseline holdout declarations must match that independent clock.
Removing an entire stock, day or tail from all three collections and rebuilding
every inner hash cannot satisfy this check. `sourceForecastDomainVerified` is
false without the source sidecar and true only after this full comparison.
`sourceCoverage` records the derived row/date counts, warmup and holdout.

```sh
python3 -S scripts/audit-financial-graph-bundle.py /path/to/result.tar \
  --source-dataset /path/to/dataset.tar \
  --expected-bundle-id CALLER_AUTHORIZED_BUNDLE_SHA256 \
  --expected-dataset-root CALLER_AUTHORIZED_DATASET_SHA256 \
  --registry-pins /path/to/independently-authorized-registry-pins.json \
  --source-pins /path/to/independently-retained-source-pins.json \
  --output /path/to/new-paired-audit.json
```

The source pin formats are documented in `GRAPH_DATASET_AUDIT.md`. Output files
must be new. External root pinning and exact registry/source byte matches are
reported separately. An internally consistent pair cannot authorize its own
registry or originals.

`PASS` means transport, paired source closure and full source-derived forecast
domain passed. The report explicitly
keeps financial formula recomposition, provider/PDF authentication, research
fingerprint recomputation, model fitting/admission, callable-F reevaluation and
research-statistics recomputation false. All supplied model/statistics
collections are retained and hashed; deeper mathematical validation is a
separate gate. No hosted or production readiness is implied.
Input-validity flags and feature values are not independently recomputed
(`inputValidityRecomputed: false`); missing source observations never shrink the
expected domain. The separate fresh-source Python guard additionally regenerates
input validity; that stronger claim does not transfer to this stdlib audit.

## Retained small actual-F pair, 2026-10-08

The existing `graph-bundle-small-audit-20261008-01` pair was read once with Python
`-S`; the audit did not run F or any provider:

- Result bundle `393c61bb79bafc1147dc93947410a31f345d33278f8c4d78e4aa5daef9b10d23`.
- Source dataset `b02dc72d1d0776f4c873464cb57b9dbcacbb55756da5ea790d4ff3d449f04f41`.
- 41 forecast rows, 41 baseline rows, 262 exact snapshot rows; registry pins
  came from the retained caller-supplied fixture, not archive extraction.
- Full logical snapshot: 502,824 bytes,
  SHA-256 `0efe248e2ae3a0db120ec3035fe15dd90d27a302fdd0503e2c089822f2b84b90`.
- `/usr/bin/time -l`: 0.25 seconds and 38,961,152 bytes maximum RSS. This is
  measured resource use, not an active supervisor claim.

Evidence is retained in
`work/atlas-quant-graph-audit/private/graph-bundle-audit-small-20261008-01/`.
This fixture contains the earlier six diagnostic joint pairs; the audit does
not relabel it as the later expanded diagnostic design or a 50-security result.

Fifteen focused tests use clearly labeled no-fit transport fixtures. They check
paired exactness, independent root pins, old-reader rejection, self-rehashed
snapshot/commitment/provenance changes, unknown versions/codecs/collections,
archive trailers, unexpected files, altered chunks and Python `-S` CLI behavior.
The real retained actual-F pair above is separate evidence from those fixtures.

## Complete 50-security source-domain check, 2026-10-08

Revision 2 read the existing frozen `financial-graph-auto-50-20261008-01` result
and dataset archives with Python 3.13 `-S`, external registry/source pins and
caller-pinned roots. It performed no fit, provider call or source rewrite:

- Bundle `5fdc2c64d229a96a28c0c5cfa470b5fb2083193bf5bcf631585d559463feff73`.
- Dataset `cca665750bd7ba9d5eacc7ac7b0e54418397c35bf7f22fbb1981349d3cad3172`.
- PASS: 50 complete targets, 41 origin dates, 2,050 main and 2,050 baseline
  rows, 13,100 exact snapshot rows; source-derived holdout `20241105`, start
  index 61 and DSL lookback 0. All 1,750 rows with available target dates and
  300 incomplete-tail rows remain present. There are zero trades.
- Three separately rebuilt derivative result bundles preserved source scope,
  calendar and numerical columns. Deleting half the pool (25 targets; 1,025
  rows), one full origin date (2,000 rows), or all tail origins (1,750 rows)
  passed result-only transport/self-consistency but failed paired full-domain
  validation. Half-pool failed `RESULT_TARGETS`; day/tail failed
  `RESULT_COVERAGE`. All three rebuilt forecast/baseline/plan collections,
  recipes, document hashes, counts and bundle identities.
- Original archives and every original bundle file had identical SHA-256
  before and after the read-only audit/attack run.

New private receipts and derivative attack bundles are under
`work/atlas-quant-graph-audit/private/graph-source-domain-20261008-01/`:
`frozen-50-paired-audit.json`, `coordinated-attack-summary.json` and three
`attack-*-receipt.json` files. Earlier original receipts remain intact.

The focused financial suite now uses a complete year-calendar no-fit fixture;
it adds coordinated asset/day/tail/empty removal, tail endpoints, clock, target
and missing-baseline attacks, and observation-stride/warmup regressions. The
combined financial bundle, graph source and independent market audit suites
passed 145 tests. These are no-provider/no-F verification results, not another
actual model execution or research-statistics recomputation.
