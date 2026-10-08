# Financial forecast transport, version 1

Implementation contract, 2026-10-08. This document specifies a new, initially
offline codec. Hosted publication, queue negotiation and UI support must each
pass their own integration checks before this format can be admitted. This is
not a deployed capability and does not enable financial execution or replay.

## Identities and compatibility

The transport discriminator is `atlas.quant.financial_bundle/1`, represented by
`format: "atlas.quant.financial_bundle"` and integer `version: 1`. Its only kind
is `forecast`. A legacy `atlas.quant.bundle/1` reader must reject it. Legacy
writers, hashes, numeric encodings and execution behavior remain unchanged.

`bundleId` is SHA256 of the exact canonical manifest bytes. It is distinct from
`forecastArtifactId`, which remains the existing complete forecast-v1 logical
identity. The forecast document is still the artifact without its `artifactId`
field. Its complete reconstructed SHA256 must equal `forecastArtifactId`.

The dataset is a separately stored immutable closure, not a new document inside
the result bundle. Its manifest SHA256 is its `datasetRoot`; no second field is
allowed to describe a competing manifest hash. Dataset ownership is established
by the stored run admission, never by possession of a content hash or by a
runner-supplied grant.

## Manifest

The exact top-level keys are:

```json
{
  "format": "atlas.quant.financial_bundle",
  "version": 1,
  "kind": "forecast",
  "forecastArtifactId": "<64hex>",
  "predictionConfigHash": "<64hex>",
  "dataFingerprint": "<64hex>",
  "sourceEvidence": {
    "datasetRef": {
      "datasetId": "<uuid>",
      "datasetRoot": "<64hex>",
      "format": "atlas.quant.research_dataset",
      "version": 2
    },
    "admissionProfile": "financial_snapshot_view_50_v1"
  },
  "documents": {
    "forecast": {"codec": "forecast_json_v1", "parts": [], "sha256": "<64hex>", "byteLength": 2},
    "report": {"codec": "forecast_json_v1", "parts": [], "sha256": "<64hex>", "byteLength": 2},
    "coverage": {"codec": "forecast_json_v1", "parts": [], "sha256": "<64hex>", "byteLength": 2},
    "snapshot": {"codec": "financial_json_v1", "parts": [], "sha256": "<64hex>", "byteLength": 2}
  },
  "collections": [],
  "totals": {"chunkCount": 0, "chunkBytes": 0}
}
```

The empty layout above illustrates field names only; it is not a valid bundle.
Document layouts, collection paths, mandatory collections, origin coverage,
references, ordinal/start/count rules and the one report-to-forecast reference
use the existing 19-path layout. No arbitrary document reference, path, URL,
executable deserializer, compression or additional collection is accepted.

Dataset reference/profile combinations are registered together:

| Dataset version | Exact admissionProfile | Source closure |
| --- | --- | --- |
| 1 | `financial_compose_50_v1` | Existing frozen market plus prepared financial inputs |
| 2 | `financial_snapshot_view_50_v1` | Explicit snapshot scope view, including the registered marketOrigin evidence |

Version 2 is admitted only after the corresponding dataset reader and independent
auditor are present. Transport parsing alone must not imply support for restoring
an unknown dataset profile. Both cases retain the existing 50-security numerical
limits and explicit financial forecast-only restrictions.

## Exact encoding

The manifest itself uses `forecast_json_v1`: sorted keys, UTF-8, compact JSON,
finite numbers, and the existing integral-float-to-integer normalization. Its
metadata has only constrained integers, strings and layout literal strings.
Literal strings retain their contained characters exactly.

`forecast`, `report` and `coverage` documents use the unchanged
`forecast_json_v1`. This preserves all existing forecast IDs and numerical
semantics. The snapshot document, both its layout literals and `snapshotRows`
chunks, uses `financial_json_v1`: Python's finite compact, sorted-key,
ensure_ascii=false JSON encoding **without numeric normalization**. In
particular, `1`, `1.0`, `0.0` and `-0.0` retain distinct byte encodings. The entire
reconstructed snapshot must be byte-for-byte equal to the canonical input
snapshot supplied to the writer. A downstream JavaScript parse/stringify is
not an identity-preserving transport for these bytes.

Each chunk uses the codec fixed by its owning document; a caller cannot select
another codec per chunk. A validator checks exact canonical encoding, duplicate
keys, finite values, valid Unicode, depth, hash, byte length and item count before
accepting a chunk. The document hash is computed by streaming the original
literal and chunk bytes, never by serializing an assembled JavaScript object.

The financial snapshot is the existing exact object:
`schemaVersion:2`, `fingerprintVersion:"research_input_financial_v1"`,
`datasetRef`, a version-matched `sourceEvidenceClosure`, `rows`,
`provenance`, `dataFingerprint`, `sourceDataFingerprint`, and
`financialSourceCommitment`. Its datasetRef equals manifest.sourceEvidence's
reference. Its dataFingerprint equals the forecast and transport fingerprint.
The original domain roots and exact financial commitment are not rewritten.

## Forecast-only checks

The source strategy and report must describe schemaVersion 2, statistical_quant,
asset_price, fundamental/Ridge and explicit `execution.enabled:false`.
`research.executionOnly` must be false. Execution collections (`trades`,
`riskLedger`, `decisions`, `equity`) are present as required by the layout but
empty. This codec cannot be used to bypass the financial execution guard.

The writer receives a complete already-computed report, the canonical snapshot
bytes, a pre-fit coverage plan, and sourceEvidence. It does not fit a model,
prepare financial data, obtain registry authority or call a provider. It rejects
an artifact/strategy/snapshot identity disagreement before publishing a manifest.

The runtime restoring sources for a new F run must separately use the registered
dataset reader and `restore_dataset_for_research` in the actual F child process.
No serializable token or cached parent-process DataFrame grants financial
admission. This transport never calls `execute_forecasts`.

## Reader, sidecar and independent audit

The Python reader verifies transport integrity without materializing a whole
serialized report. Its public manifest/collection metadata cannot be mutated to
change the internal expected hashes. Document materialization is an explicit
bounded-runtime operation; streaming verification does not require it.

Transport integrity and complete source evidence are separate results:

* With no dataset sidecar: report `transportVerified:true` only if all transport
  checks pass, `sourceEvidenceClosed:false`, `recomposition:"not_performed"`,
  and overall `INCOMPLETE_SOURCE`. A CLI exits nonzero, without calling the
  intact numerical transport corrupt.
* With the exact dataset sidecar: verify its complete typed closure and manifest
  root, scope/profile, snapshot rows/provenance/financial commitment linkage.
  Wrong roots, missing parts, forged lineage or unsupported profiles fail.
* With no independent registry pins: preserve `registryTrustStatus:"unverified"`.
  Archive-contained registry bytes never authorize themselves.
* With separately supplied exact registry pins: use
  `registryTrustStatus:"external_registry_bytes_matched"`; this is not PDF
  authentication. The independent stdlib auditor does not import the numerical
  engine or silently claim it reran Decimal preparation or model fitting.

The standalone audit accepts `--source-dataset <typed archive or directory>` and the
existing `--registry-pins` local JSON mapping. Without the required sidecar it
must not return an unqualified complete financial forecast audit. Full numerical
and lineage checks retain explicit scope/limitations in machine-readable output.

## Storage, budgets and future hosted admission

The local directory layout remains `manifest.json` and
`chunks/<registeredCollection>/<ordinal>.json`. The directory name is not part
of identity. New exporters/importers must dispatch on the manifest discriminator;
old bundle/1 tools are not taught to accept new semantics implicitly.

Budgets remain 512 KiB manifest, 4 MiB target / 8 MiB hard chunk, at most 10,000
records per chunk, 256 chunks, 1,000,000 collection records and 256 MiB for
manifest plus chunks. The financial snapshot's original 24 MiB bound remains.
The separate dataset closure retains its 64 MiB budget, including origin evidence
and encoding overhead. A budget error never trims rows, origins, dependencies or
forecasts. Empty collections use zero chunks and zero records.

Hosted support requires a distinct negotiated transport capability
`atlas.quant.financial_bundle/1`. Begin/complete must compare sourceEvidence to
the immutable run→dataset admission record, not write new grants from it. Only
committed owner-scoped sidecars may be linked; source and registry dependencies
must be pinned against cleanup. Download presents two required private artifacts:
the financial result bundle and its source dataset closure. Owner/lease fencing,
durable request identity and immutable completed ACK semantics remain mandatory.

This initial implementation does not change hosted queue/API admission, start a
consumer, increase numerical limits or enable any execution endpoint.

The financial snapshot `sourceEvidenceClosure` must exactly match the referenced dataset version: `separate_research_dataset_v1` for dataset/1 and `separate_research_dataset_v2` for dataset/2. No legacy v1 bytes change.


## Hosted handler contract

`financialBundleRunnerApi(req, env, path, {assertRunDataset})` is called only
after runner authentication. The callback is a fixed server import, not request
data. It validates the immutable owner/run/dataset relation and current source
pins at begin, upload, finalize and first completion. Completed exact ACKs remain
recoverable. Routes are `POST /runner/financial-bundles/begin`, `POST
/runner/financial-bundles/finalize`, `POST /runner/financial-bundles/complete` and
`PUT /runner/financial-bundles/:bundleId/chunks/:collection/:ordinal`.

Completion accepts only the small existing `{id,leaseToken,stageId,bundleId}`
packet. The old `/runner/complete`, old begin/finalize/chunk routes and execution
replay refuse successful results of this format. A computation or input failure
with only `{id,leaseToken,error}` still uses the old `/runner/complete`; it does
not require a result stage. No large old completion body is decoded twice.

The existing `quant_bundle_*` tables and owner report/page/archive URLs are
reused with explicit stored-format dispatch; no migration 0009 is needed.
Metadata advertises the new format, its exact `sourceEvidence`, and
`executionEligible:false`. This codec alone does not activate worker routing or
ready-dataset admission.

The server callback returns the authorized parsed dataset manifest as well as
`sourceEvidence`. Finalization streams the original typed snapshot provenance
and row chunks into the dataset's exact `researchRows` payload recipe, then
compares its SHA256 and byte length to that component. A self-consistent rewrite
of all result hashes cannot replace an admitted source value or its numeric
type. This adds one bounded pass over snapshot chunks, without assembling the
entire dataset or claiming that the Worker recomputed financial formulas.

The private report transport advertises `sourceEvidence` and
`executionEligible:false`; a separate download of the source dataset is still
required for complete offline evidence. See [the independent audit command](FINANCIAL_BUNDLE_AUDIT.md).
