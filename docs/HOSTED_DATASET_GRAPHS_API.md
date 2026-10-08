# Hosted dataset graph API — implementation candidate, disabled

This document locks the next hosted graph interface. The source codec and local
model evidence exist; the routes below are being implemented and are not enabled
in production. `contracts/hosted-dataset-graphs-v1.json` is the machine contract.
Dataset/2 and financial_bundle/1 keep their original meaning and validators.

## Public control plane

All paths below are relative to `/quant/api`, require the current owner session,
and never fetch provider data. The graph namespace is separate from `/datasets`.

| Method | Path                                                                     | Result                                                                                                                                    |
| ------ | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/dataset-graphs/capabilities`                                           | `enabled, profile, composition, forecast, datasetFormats, financialResultFormats, researchBindingEnabled, providerRequired:false, limits` |
| GET    | `/dataset-graphs/sources/markets?page=1&pageSize=20`                     | Existing nonfinancial committed snapshot options: `items,total,page,pageSize`                                                             |
| GET    | `/dataset-graphs/sources/financial?page=1&pageSize=20`                   | Existing committed financial preparations: `items,total,page,pageSize`                                                                    |
| POST   | `/dataset-graphs/plans`                                                  | `{plan,idempotent?}`                                                                                                                      |
| GET    | `/dataset-graphs/plans/:planId`                                          | `{plan}`                                                                                                                                  |
| POST   | `/dataset-graphs/plans/:planId/start`                                    | `{preparation,idempotent?}`                                                                                                               |
| GET    | `/dataset-graphs/jobs/:jobId`                                            | `{preparation,activeJob,latestJob,datasetRef}`                                                                                            |
| POST   | `/dataset-graphs/jobs/:jobId/cancel`                                     | Existing cancellation state response                                                                                                      |
| GET    | `/dataset-graphs?page=1&pageSize=20`                                     | Graph datasets only: `items,total,page,pageSize`                                                                                          |
| GET    | `/dataset-graphs/:datasetId?datasetRoot=...`                             | Root-pinned detail described below                                                                                                        |
| GET    | `/dataset-graphs/:datasetId/manifest?datasetRoot=...`                    | Exact canonical manifest bytes and SHA header                                                                                             |
| GET    | `/dataset-graphs/:datasetId/coverage?datasetRoot=...&page=1&pageSize=20` | `items,total,page,pageSize,datasetRoot`                                                                                                   |
| GET    | `/dataset-graphs/:datasetId/download?datasetRoot=...`                    | Complete deterministic private source tar                                                                                                 |

Pagination is bounded by the existing page policy; coverage response bytes remain
at most 256 KiB. Downloads stream exact bytes and include every declared part.
Missing or corrupt evidence fails the download; no partial archive is called
complete. List/detail remain readable when new composition is disabled.

Plan creation accepts exactly:

```json
{
  "requestId": "11111111-1111-4111-8111-111111111111",
  "name": "Explicit graph source",
  "profile": "financial_snapshot_graph_50_v1",
  "marketSource": {
    "kind": "forecast_snapshot_view",
    "runId": "22222222-2222-4222-8222-222222222222",
    "expectedBundleId": "64 lowercase hex",
    "expectedSnapshotSha256": "64 lowercase hex",
    "transform": {
      "kind": "snapshot_scope_view",
      "version": 1,
      "mode": "exact",
      "symbols": ["600000.SH"],
      "start": "20240101",
      "end": "20241231"
    }
  },
  "financialInputs": [
    {
      "inputId": "33333333-3333-4333-8333-333333333333",
      "preparationId": "44444444-4444-4444-8444-444444444444",
      "inputRoot": "64 lowercase hex",
      "packRoot": "64 lowercase hex",
      "preparedRoot": "64 lowercase hex",
      "calendarRoot": "64 lowercase hex"
    }
  ]
}
```

These are the existing `marketSource` and `financialRef` shapes. Every financial
ref must resolve to a same-owner committed preparation and its exact original
canonical package. The consumer downloads that package and the currently active
registry bytes and recomputes the preparation. `inputPackage` is not a public
request field; client-supplied raw prepared rows or trust booleans are rejected.
If a selection is unsuitable, the user first creates and prepares an explicit
financial input revision. A graph source cannot bypass those source guards.

`start` accepts `{requestId,expectedPlanRoot}`. It rechecks the same frozen source
and registry descriptors. The returned plan includes `id,name,planRoot,profile,
marketSource,financialInputs,originalScope,scope,marketCalendarRef,selectedStates,
knownSourceBytes,limits,checks,createdAt`. `checks.semantic` stays pending until
actual raw recomposition succeeds. No dates or members are silently reduced.
The profile allows 1–50 SH/SZ symbols and at most 366 inclusive calendar days,
with the existing 64 MiB complete physical closure and 24 MiB logical panel caps.

## Dataset and immutable research binding

A successful source publication returns:

```json
{
  "datasetId": "55555555-5555-4555-8555-555555555555",
  "datasetRoot": "64 lowercase hex",
  "format": "atlas.quant.research_dataset",
  "version": 3
}
```

Detail includes `datasetRef,name,status,scope,summary,researchAdmissions,
preferredResearchAdmission,researchBindingEnabled,archiveUrl,
sourceEvidenceClosure:'separate_research_dataset_v3'`. Coverage items retain the
actual core coverage fields, including symbol, state, missing counts/reasons and
observed versus publication dates. They are not inferred from an enabled flag.
`ready` confirms source closure; model sample adequacy remains `not_checked`.

The existing experiment create/update/run API accepts `datasetRef` and
`admissionProfile:'financial_fundamental_graph_auto_50_v1'`. Its immutable
`datasetBinding` returns the same fields as before, with version 3, exact scope,
selectedStateIds and stateDefinitions. Editing the draft cannot change a stored
run's source. This profile requires fundamental/asset_price/auto, predictors
only, the exact frozen universe, 2×2 validation folds, refit >=20 sessions and
`execution.enabled:false`. Unsupported combinations fail explicitly.

A research runner must independently declare all four capabilities:
`atlas.quant.research_dataset/3`, `financial_column_snapshot_v1`,
`atlas.quant.financial_bundle/2`, and
`financial_fundamental_graph_auto_50_v1`. The legacy tuple is neither implied nor
required. The claim binds `dataSource:'ready_dataset'`, exact `datasetRef`,
`admissionProfile`, `sourceEvidence:{datasetRef,admissionProfile}` and
`resultTransport:{format:'atlas.quant.financial_bundle',version:2}`.

Report summary/pages/detail/chart and native result download retain their current
user URLs. The server dispatches by **both format and version**, so bundle/2 is
never fed into the old parser. Function resolution retains its original
owner/source/artifact checks after the exact parser dispatch. Source and result
archives are two separate required files for independent paired audit. Forecast
replay and execution remain unavailable.

## Composition consumer

The isolated provider-free capability is `research-dataset-graph/1`; its fixed
prefix is `/quant/api/runner/dataset-graphs` and job kind is
`dataset_graph_compose`. Claim/heartbeat/lease/source/registry/publication shapes
follow the machine contract. Source metadata and authorized registry descriptors
remain bounded and paginated. The output must be dataset/3 with the graph profile;
no consumer-selected type unlocks a legacy stage. Durable intent and exact-byte
publication recovery do not recompute a completed package.

The graph composition and research gates are independently
`RESEARCH_DATASET_GRAPHS_ENABLED` and `FINANCIAL_GRAPH_RESEARCH_ENABLED`, both
false by default. Legacy flags do not activate either graph capability. Schema
storage can be shared only when every claim/read/publication route checks the
persisted profile and owner; namespace alone is not authorization.

## Implemented validation boundary

The edge retains the original column bytes. At result finalization it reconstructs
`researchColumns` by streaming the frozen column chunks and exact metadata, then
compares its length and SHA with the committed source component. Physical column
size and expanded logical row size both keep the existing 24 MiB limit. This is
source-byte verification; Python still recomposes and verifies the financial
formula and trusted registry before fitting.

After that identity check, the server derives the complete asset target set and
observation/holdout/refit clocks from the immutable source scope, verified source
calendar and declared strategy. Main forecasts, the independent factor-free
baseline and pre-fit planned origins must each retain the entire product of
assets and report dates, including invalid and tail rows. Targets and hedge-fit
references also must match that full domain. Rehashing a shortened report does
not authorize omission. These graph checks do not replace or relax legacy
format validation.

Focused reproducible tests (no provider calls):

- `tests/dataset-graph-manifest.test.mjs`: 78 manifest/closure cases from public
  pure-core inputs, including physical and expanded budget boundaries.
- `tests/dataset-graph-control.test.mjs`: 7 actual D1/R2 owner/profile, public
  route and archive cases, with graph flags independent from legacy flags.
- `tests/graph-runner-claims.test.mjs`: 28 actual D1/R2 capability, durable claim,
  owner/relation, cancellation/expiry and old-runner isolation cases.
- `tests/financial-graph-bundle.test.mjs`: 16 source-byte, mutation, domain and
  complete Worker upload/download cases. Its checked-in Python generator makes
  one tiny explicitly synthetic auto F, then every transport test reuses the
  frozen output. Full native tar contents and portable F resolution are checked.

The actual consumer HTTP process, browser workflow and independent paired
archive auditor remain separate acceptance steps. These unit/integration tests
are not evidence of production deployment, real provider acquisition or an
investment advantage.
