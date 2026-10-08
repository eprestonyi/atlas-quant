# Explicit frozen-snapshot market views

Status: reviewed core contract; implementation in progress, 2026-10-08. This contract authorizes no
provider call, model fit, hosted admission, deployment or execution. The first
implementation is an offline adapter and a complete private dataset archive.
Existing dataset version 1 and `atlas.quant.bundle/1` retain their exact semantics.

## Why this is the first market input

Use an existing, same-owner, committed forecast bundle's non-financial frozen
snapshot. It already has immutable bytes, a source strategy and two numerical
fingerprints. A generic market upload also needs untrusted upload admission,
units/adjustment/calendar declarations and a new source contract. It can use a
future adapter, but is not the smallest first hosted path.

Explicit subrange selection is included in this first adapter. Requiring the
entire original two- or three-year study interval would often conflict with a
financial acquisition calendar limited to 366 civil days. No background operation
may silently intersect dates, select the first N securities, extend warmup dates,
fill observations, or change a prepared financial input's scope.

## Hosted reference DTO (proposed, no HTTP in this slice)

```json
{
  "marketSource": {
    "kind": "forecast_snapshot_view",
    "runId": "owner-scoped-run-uuid",
    "expectedBundleId": "64-lowercase-hex",
    "expectedSnapshotSha256": "64-lowercase-hex",
    "transform": {
      "kind": "snapshot_scope_view",
      "version": 1,
      "mode": "explicit_subset",
      "symbols": ["600000.SH"],
      "start": "20250101",
      "end": "20251231"
    }
  },
  "financialInputs": [{
    "inputId": "owner-scoped-input-uuid",
    "preparationId": "owner-scoped-preparation-uuid",
    "inputRoot": "64-lowercase-hex",
    "packRoot": "64-lowercase-hex",
    "preparedRoot": "64-lowercase-hex",
    "calendarRoot": "64-lowercase-hex"
  }]
}
```

`mode` is `exact` or `explicit_subset`. Exact means exactly the original universe
membership and interval; the caller must still provide the scope. Subset mode
requires a proper subset of symbols or a strictly narrower interval. The list is
sorted and unique, with 1–50 SH/SZ securities. Dates are inclusive YYYYMMDD and
must be within the original source strategy's interval. Every selected symbol
must have at least one actual source observation in the selected interval.

The server resolves source strategy, bundle/snapshot bytes and original scope
from the owner-scoped committed run. Clients cannot supply replacement source
metadata, URLs or credentials. Financial calendar/proof references come from the
same owner's immutable preparation and original input; client root assertions
must match. The market calendar grant is derived server-side from these selected
financial inputs and must match the market sessions exactly, not a client-supplied
arbitrary reference. Different selected calendar grants must be compatible;
otherwise preparation is blocked. An uploaded input is not a committed preparation.

Each financial package must use the exact target start/end and its symbols must
be a subset of the target market universe. A different scope requires a visible
financial revision and a new preparation with new roots. The adapter never
rewrites that package. Financial missing states remain missing and coverage is
not presented as adequate model training history.

## Pure Python seam

New `research_dataset/snapshot_view.py`:

```python
derive_market_snapshot_view(
    snapshot_bytes: bytes,
    bundle_manifest_bytes: bytes,
    transform: dict,
    *,
    expected_bundle_id: str,
    expected_snapshot_sha256: str,
    profile: DatasetProfile = DEFAULT_PROFILE,
) -> SnapshotMarketView

# Returned immutable byte fields; the receipt property returns a copy.
SnapshotMarketView.market_bytes: bytes
SnapshotMarketView.origin_bytes: bytes
SnapshotMarketView.receipt: dict

validate_snapshot_scope_origin(origin_bytes: bytes, *, profile=DEFAULT_PROFILE)
    -> SnapshotMarketView

compose_snapshot_dataset_components(
    view: SnapshotMarketView,
    financial_sources: list[FinancialSource],
    authorized_registry: dict[str, bytes],
    write_part,
    *,
    market_calendar_ref: str,
    profile: DatasetProfile = DEFAULT_PROFILE,
) -> DatasetPublication
```

The library is not an ownership checker. Its caller supplies authenticated source
pins and registry authority. Hosted admission must perform owner and committed
state checks before reading bytes. An archive's own references do not prove a
provider license, an authentic PDF, publication time or a server owner.

## Full-source validation before projection

1. Check bounded byte lengths and parse the original bundle manifest using the
   existing `bundle.validate_manifest`, with the expected bundle identity. Require
   a forecast bundle with the snapshot document, not an execution bundle.
2. Check exact snapshot document length/SHA against both the manifest and caller
   pin. Check the canonical source snapshot document against its declared
   snapshot skeleton; the only collection is its exact `rows` array, whose count
   must equal the source `snapshotRows` descriptor. Do not claim verification of
   unrelated forecast/report collection bytes which are not input dependencies.
3. Extract the full original `sourceStrategy` from the hash-bound forecast
   skeleton. Require original `research_input_v1` snapshot, no reserved
   `model_fin_*` columns or financial closure roots. A financial source cannot
   enter through this legacy adapter.
4. Run `runner_artifacts.restore_input` on the entire original source with that
   strategy and the manifest's data fingerprint. Its source and research
   fingerprints must both agree. A corrupt row outside the selected range must
   not be hidden by the filter.
5. Validate the explicit scope. Select only existing rows, preserving their
   original relative order, every supported field, numeric value, null and date.
   Do not rebuild source records using pandas JSON or the provider's rounded
   `_records` representation. Reject columns the market validator would discard.
6. Filter the original complete `tradingDates` list to the explicit interval.
   Composition then requires exact element-by-element equality with the chosen
   externally authorized market calendar. Coverage bounds must contain the
   target interval. Neither a provenance string nor business-day generation is
   an official calendar grant.
7. Recompute the derived market source fingerprint with the existing market
   numerical contract, and compute a new typed `marketRoot`. The raw source
   strategy and source metadata stay in the origin component. No old root is
   presented as the identity of the derived market view.

An older bundle may already have normalized `1.0` to `1` or negative zero to zero.
The adapter preserves the bytes it actually receives and verifies the original
research fingerprint. It does not restore discarded number kinds or claim access
to original provider wire bytes. Typed values in the new derived market are not
subsequently passed through the old bundle canonicalizer.

## Origin component and acyclic identities

New dataset format remains `atlas.quant.research_dataset`, with **version 2** and
registered profile **financial_snapshot_view_50_v1**. Version 1's profile and
closed graph remain unchanged. Version 2 retains the same manifest top-level
shape and adds exactly one `marketOrigin` component of type
`snapshot_scope_origin`, component version 1. Its semantic roots are
`sourceBundleId`, `sourceSnapshotSha256` and `marketRoot`.

Origin payload is exact canonical typed JSON:

```json
{
  "format": "atlas.quant.snapshot_scope_origin",
  "version": 1,
  "source": {
    "bundleId": "64-lowercase-hex",
    "manifestRawText": "original UTF-8 manifest bytes, decoded without rewriting",
    "snapshotSha256": "64-lowercase-hex",
    "snapshotRawText": "original UTF-8 snapshot bytes, decoded without rewriting",
    "sourceStrategy": {},
    "sourceStrategySha256": "SHA of existing bundle canonical strategy bytes"
  },
  "transform": {
    "kind": "snapshot_scope_view", "version": 1, "mode": "explicit_subset",
    "symbols": ["600000.SH"], "start": "20250101", "end": "20251231"
  },
  "receipt": {
    "sourceScope": {}, "targetScope": {},
    "sourceRows": 0, "selectedRows": 0, "removedRows": 0,
    "sourceTradingDatesSha256": "64-lowercase-hex",
    "selectedTradingDatesSha256": "64-lowercase-hex",
    "sourceDataFingerprint": "original source fingerprint",
    "sourceResearchFingerprint": "original research fingerprint",
    "derivedDataFingerprint": "newly calculated market source fingerprint",
    "marketRoot": "new typed market identity",
    "preservesRelativeRowOrder": true,
    "imputation": "none", "warmupExtension": "none"
  }
}
```

Raw-text strings round-trip back to the original UTF-8 bytes. Their JSON escaping
cost is fully charged to the shared 64 MiB limit. No unbounded base64/blob bypass,
third archive attachment, or remote-only origin reference is accepted.
`sourceStrategy` must exactly match the manifest-bound decoded strategy; its
redundant hash and every receipt field are recomputed, not trusted.

Derived market provenance copies original metadata while replacing only the
visible scope, trading dates and recalculated `dataFingerprint`. It adds
`marketDerivation` with transform kind/version/mode, original bundle/snapshot and
research/source fingerprints, and the explicit target scope. It does **not**
contain the origin root or output marketRoot. The origin receipt can consequently
contain the marketRoot without a hash cycle.

The new graph has `marketOrigin` with no dependencies and `marketDataset` depending
on both registryEvidence and marketOrigin. Existing financial input/prepared and
researchRows/schema/coverage edges remain unchanged. Every restore verifies the
origin, regenerates the market bytes, then recomposes financial data in-process;
serialized `model_fin_*` values never grant admission by themselves.

## Resource and compatibility gates

Existing limits remain: 50 symbols, 110,000 market rows, 64 MiB shared closure,
24 MiB original snapshot and market/joined inputs, 24 MiB aggregate financial
packages, 256 KiB dataset manifest, 512 KiB parts, 256 parts, 32 components and
dependency depth 3. The original bundle manifest retains its existing 512 KiB
maximum. Origin bytes are reserved before financial preparation, charged during
all component allocation and included in the final manifest total. All descriptor
and total checks complete before the first staging callback.

Version-aware readers, directory/archive import/export and the standard-library
source audit must verify the required origin component and regenerate its market
projection. Old version 1 archives remain valid and cannot accept this new type.
No arbitrary unknown component or codec is permitted.

`datasetRef` retains `{datasetId,datasetRoot,format,version}` with version 2 for
this profile. It references the complete dataset closure, not the marketRoot or
financialDatasetRoot. The hosted source result and schema must display original
and derived roots separately and expose retained/removed row/session counts.

A frozen financial snapshot retains schemaVersion 2 and fingerprintVersion
`research_input_financial_v1`. `sourceEvidenceClosure` is the fixed value
`separate_research_dataset_v1` or `separate_research_dataset_v2` matching both
`datasetRef.version` and the actual source manifest version. Old v1 bytes remain
unchanged; no arbitrary marker is accepted.

Financial research remains fundamental/Ridge/asset_price with explicit
`execution.enabled=false`. New typed financial snapshots cannot use legacy
`bundle.encode`, which changes integral-float and negative-zero identity. A
separately registered financial-result transport and its writer, reader, edge and
independent audit are required before hosted F use. The result package must bind
the datasetRef; complete source verification needs the dataset archive too.
Missing that archive is incomplete source evidence, never a full-source PASS.
Neither financial execution nor execution-only replay is enabled here.

## Acceptance without provider or fitting

- Small frozen SYNTHETIC legacy bundle fixture; exact and proper subset views.
  Preserve input raw bytes, high-precision values, numeric kinds as received,
  nulls, missing observations and source relative row order.
- Reject wrong original bundle/snapshot/source strategy, rows outside source
  scope, inconsistent row count/layout, financial source masquerade and a bad
  original fingerprint even when the bad row is outside the selected view.
- Reject a subset without explicit mode, exact-mode scope changes, absent
  symbols, an empty view, interval extension and a transformed/reordered source
  calendar. Do not create rows for missing security/session pairs.
- Rehashing a forged receipt or derived market cannot bypass origin recomputation;
  missing original bytes or any required component fails archive verification.
- Calendar/proof authority, exact prepared scope and declared-unit evidence remain
  enforced by existing composition. No missing historical filing is invented.
- Reduced byte/part budgets reject before preparation or callbacks; encoded origin
  overhead is counted. Repeated checks cannot mutate the internal trusted graph.
- Fresh-process archive round-trip/recomposition matches all roots, typed rows,
  missing masks, coverage and dependencies. Registry pins remain out-of-band.
- Existing version 1 tests and archive bytes remain unchanged. Financial initial
  execution, legacy entry and replay remain forbidden. No provider/network/F call.
