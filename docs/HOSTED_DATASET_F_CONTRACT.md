# Hosted frozen dataset → fundamental forecast: contract proposal

Implementation contract, 2026-10-08. Explicit subset views, dataset version 2 and
profile financial_snapshot_view_50_v1 are approved; implementation remains
default off and local-only until acceptance. This does not enable
research binding, add an HTTP endpoint, call a provider or fit F. Checked against
main `189a207` and the current `research_dataset` core. Proposed names below are
not callable APIs. The two decisions in §9 need approval before implementation.

## 1. Scope and facts already enforced by the core

The user selects a **completed, same-workspace forecast bundle with a complete
non-financial snapshot**, an exact authorized calendar, and one to eight committed
financial preparations. Composition verifies and recomputes their frozen contents.
A ready dataset can then be used only by schema 2 `statistical_quant`,
`target.kind=asset_price`, `model.family=fundamental`, `model.estimator=ridge`, and
**explicit** `execution.enabled=false`. No provider fallback, financial execution,
execution replay, automatic date expansion, or reduced training requirement exists.

The existing pure core already supports `atlas.quant.research_dataset/1`, exact
canonical component bytes, strict private USTAR, recomposition in a new process,
and a separate `research_input_financial_v1` snapshot. It does not implement hosted
ownership, jobs, run admission or a financial result transport.

Important implementation facts:

- `financial_statements/dataset.py` requires every financial package's start/end to
  equal the target dataset interval. Its securities may be a subset of the market
  universe. Overlapping `(symbol,state)` sources fail rather than overwrite.
- `research_dataset/compose.py` requires market trading dates to equal the selected
  interval of the **separately authorized** complete calendar. A correct hash or
  a source string saying `official` is not that authorization.
- Every original `preparedRoot` must equal actual recomputation. Changing a
  financial package's selection requires a new immutable revision and preparation.
- The registered DataFrame is process-local. Parent-process composition followed by
  JSON/pickle/DataFrame copying cannot authorize F in the child.
- `edge/statistical-quant/validation.mjs` does not currently count registered
  `model_fin_*` states as fundamental inputs. The new path needs an exact shared
  registry check, not a blanket prefix exception.
- `bundle/1` uses a different numeric canonicalizer from the financial core. It
  collapses integral floats and negative zero. Merely permitting snapshot schema 2
  would corrupt identity; the existing version must continue rejecting it.

## 2. User-visible sequence

A small independent “研究数据集” area has five pages:

1. **冻结行情**: paginated own completed forecast sources; show original members,
   dates, row count, snapshot completeness, synthetic/source label and retained
   evidence. Ineligible sources remain visible with an explicit reason.
2. **研究范围与日历**: original scope is visible and retained. The default is the
   full source. A user may explicitly request a smaller view only if §9A is
   approved. Show before/after members/dates, and inspect the exact owner calendar derived from the selected financial inputs.
   No calendar means blocked; there is no implicit provider request.
3. **财务状态**: choose committed preparations. Show original scope, selected states,
   actual observed coverage, disclosure availability dates, units policy and
   missing reasons. Date mismatch links to the existing revision flow; it does not
   silently rewrite or re-prepare the selected package.
4. **核对与组成**: review immutable refs, view transformation, known byte budgets and
   unresolved checks. An explicit button queues composition. Progress and errors
   survive refresh; no “ready” label before atomic publication.
5. **覆盖与使用**: show dataset completeness separately from state coverage and F
   sample sufficiency. “创建基本面预测研究” copies a supported configuration with the
   dataset scope locked. Users still select genuine prepared predictor states,
   horizon and supported validation settings. A second explicit action runs F.

Ordinary pages show names/dates/coverage; hashes and registry IDs are expandable.
Selection controls work without dragging; async responses cannot replace newer
inputs or steal routes. Back/forward preserve drafts. Downloads are direct private
attachments, not browser `fetch().json()` or full-file Blob buffers. The existing
financial-input pages remain source/preparation pages, not a second research-run API.

## 3. References and public API

All paths are relative to `/quant/api`. Existing session, owner and CSRF rules apply.
All request objects reject unknown keys. UUIDs and 64-hex hashes are assertions to
compare to stored facts, never permission grants.

### 3.1 Capabilities and source discovery

`GET /dataset-capabilities`:

```json
{
  "enabled": false,
  "profile": "financial_snapshot_view_50_v1",
  "composition": {"online": false, "capability": "research-dataset/1"},
  "forecast": {"online": false, "admissionProfile": "financial_snapshot_view_50_v1"},
  "datasetFormats": ["atlas.quant.research_dataset/2"],
  "financialResultFormats": [],
  "researchBindingEnabled": false,
  "providerRequired": false,
  "limits": {"symbols":50,"marketRows":110000,"financialInputs":8,
    "closureBytes":67108864,"partBytes":524288,"parts":256}
}
```

`GET /datasets/sources/markets?page=1&pageSize=25` returns bounded candidates:
`{items:[{sourceRef,name,scope,rowCount,snapshotBytes,sourceLabel,synthetic,
eligibility:{status:'eligible'|'blocked',reasonCodes:[]}}],total,page,pageSize}`.
Only own committed **forecast** bundles qualify; execution bundles, old single-JSON
reports, incomplete snapshots and already-financial snapshots are blocked in v1.
The bundle kind and snapshot fingerprint discriminator are both checked.

`GET /datasets/sources/financial?...` returns own committed preparations, their
exact `financialRef`, original selection, compact coverage and registry status.
The market calendar is derived deterministically from the selected financial
inputs’ existing same-owner grants. Their sessions must agree for the selected
interval and later match the frozen market view. A browser cannot supply a
replacement calendar/proof reference. No raw evidence or full state panel is
returned in these lists.

### 3.2 Immutable review plan

`POST /dataset-plans` accepts:

```json
{
  "requestId":"UUID",
  "name":"年报状态与冻结行情",
  "profile":"financial_snapshot_view_50_v1",
  "marketSource":{
    "kind":"forecast_snapshot_view",
    "runId":"UUID",
    "expectedBundleId":"SHA256",
    "expectedSnapshotSha256":"SHA256",
    "transform":{
      "kind":"snapshot_scope_view","version":1,"mode":"exact",
      "symbols":["600690.SH"],"start":"20250101","end":"20251231"
    }
  },
  "financialInputs":[{
    "inputId":"UUID","preparationId":"UUID",
    "inputRoot":"SHA256","packRoot":"SHA256",
    "preparedRoot":"SHA256","calendarRoot":"SHA256"
  }]
}
```

`marketSource.transform.mode` is `exact`, or the separately approved
`explicit_subset` (§9A). Full
scope is always transmitted so reviewed membership cannot depend on a default.
The server derives original scope/sourceStrategy from the committed bundle, never
from client metadata. Every chosen symbol must have original snapshot rows; ranges
cannot expand, empty results are rejected, and the complete original snapshot is
verified before projection so corrupt out-of-view rows cannot be hidden.
Financial refs intentionally contain **no mutable stateIds, symbols, calendar
payload, proofs, raw JSON or trust switch**: the server derives them from the exact
committed preparation/input. To change them, create a financial revision first.

Return `{plan}` with server ID, immutable `planRoot`, original/target scope,
source descriptors, registry refs/byte hashes, transformation version, known
source bytes, conservative remaining budget, `checks` and `blockedReasons`.
Use separate `checks.metadata='passed'` and `checks.semantic='pending'`; a reviewed
plan is not a completed numerical validation. Roots hash canonical metadata with
bounded integers/strings, not decoded financial amounts. Owner+requestId+bodyHash
is idempotent; same key/different body is 409. Plans are read-only descriptions;
start rechecks source availability and pins it atomically.

`POST /dataset-plans/:id/start {requestId,expectedPlanRoot}` returns
`{preparation:{id,status:'queued',planId,planRoot,deadlineSeconds:600}}` with 202.
A plan has no provider endpoint; source failure cannot switch to live fetching.

`GET /dataset-preparations/:id` returns a single consistent D1 snapshot:
`{preparation,activeJob,latestJob,datasetRef:null|ref,error,phase,progress}`.
`POST /dataset-preparations/:id/cancel` never erases completed evidence.

### 3.3 Dataset readback and F

The sole run reference remains:

```json
{"datasetId":"UUID","datasetRoot":"SHA256",
 "format":"atlas.quant.research_dataset","version":2}
```

`GET /datasets/:id?datasetRoot=...` returns immutable scope/schema/roots, bounded
coverage summary, source refs and assumptions, plus independently stated:
`status`, `sourceClosure.status`, `stateCoverage.status`,
`researchAdmission:{profile,configurationEligible,sampleStatus:'not_checked'}`.
A ready all-missing dataset remains diagnostically readable; it is not F-ready.

`GET /datasets/:id/coverage?datasetRoot=...&page=1&pageSize=25` is backed by bounded
published row indexes, not an R2 scan on every page. Each row separates
`firstObserved/lastObserved` from `firstAvailable/lastAvailable` and reports
`okRows,missingRows,latestPeriodEnd,reasonCounts,unitVerified,qualityFlags`.
`GET /datasets/:id/manifest` and `/download` pin the same datasetRoot.
Downloads use identity/no-transform and fixed lengths; corruption or cancellation
must abort the stream, not return an apparently complete truncated 200.

The existing singular route `POST /statistical-quant/experiments/:id/run` gains
one mutually exclusive body:

```json
{"version":3,"dataSource":"ready_dataset","datasetRef":{
 "datasetId":"UUID","datasetRoot":"SHA256",
 "format":"atlas.quant.research_dataset","version":2},
 "admissionProfile":"financial_snapshot_view_50_v1"}
```

No simultaneous dataset JSON, URL, provider/source parameters or upload fallback.
Enqueue validates exact dataset scope, allowed model/target/execution, registered
financial predictor IDs, current registry authority and runner capability. It
creates an immutable run→dataset link in the same transaction as the job. The
actual pre-fit sample plan is computed in the child after reconstruction; insufficient
samples fail before fitting with coverage reasons and without changing the strategy.
Saved draft validation can recognize registered financial field names, but a name
alone never allows a direct provider/upload run. Copying an experiment retains its
source restriction. All execution, replay, legacy and alternate transport entry
points reject financial forecasts explicitly.

## 4. Queue, consumer and fixed budgets

Use independent tables/namespace for `dataset_compose` tasks with capability
`research-dataset/1` + profile `financial_snapshot_view_50_v1`. Neither old financial nor
old research claim SQL sees them. A provider-free consumer is responsible only
for restore-source → scope-view → reprepare/compose → freeze → deliver.

Mechanical protocol mirrors the proven dedicated queues: durable claim UUID,
120-second lease, 20-second heartbeat, **fixed 600-second total deadline**, exact
lease/fence on every mutation, idempotent begin/part/complete/fail, terminal receipt
recovery before new work. Do not allocate durable empty claims on every idle poll;
no-job heartbeat has advisory canClaim, actual claim remains atomic. Maintenance
blocks new starts/claims while allowing current delivery to finish.

- `/runner/datasets/jobs/:id/input` returns only pinned plan/descriptors/limits.
- `/sources/:sourceId/parts/:ordinal` reads only a registered job dependency; IDs
  are logical references, not R2 keys/URLs. Exact bytes and hashes are verified.
- `/registry/:ref` resolves one authorized entry in O(1), with descriptor lists
  fetched in bounded batches. Do not repeat an all-proof read per proof.
- Publication begin supplies bounded canonical manifest bytes/descriptors;
  parts are raw bytes ≤512KiB. Complete verifies all actual receipts and closure,
  then atomically publishes ready, dependency pins and job completion.
- Interrupted computation without a complete manifest is failed, not silently
  recomputed. Durable complete output resumes only missing delivery pieces.

The existing research runner may accept ready-dataset F jobs only after claiming
all three explicit capabilities: dataset/2 with the origin component, financial snapshot codec, and the new
financial result transport (§6). An engine version number or bundle/1 alone is
insufficient. Legacy claim paths must exclude dataset-linked jobs even when callers
omit capability fields. The F child fetches/validates the closure and calls
`restore_dataset_for_research` **inside that same process**, then preflight and F.
Its existing 900-second total budget includes retrieval, restore, composition,
training and serialization; a separate composition job does not increase it.

A host-level compute slot must be shared by dataset, financial prepare and F
services. Independent service locks do not establish a one-compute-at-a-time bound.
F has no provider-enabled fallback process or provider credential requirement.

Existing hard ceilings remain: 50 stocks, 110k rows; 1–8 financial packages with
24MiB aggregate package bytes; market and joined bytes 24MiB each; manifest256KiB;
registry entry256KiB and sum32MiB; 32 components/depth3; 256×512KiB pieces;
**64MiB complete source/derived closure**. Hosted coverage indexing additionally
limits the coverage component to 8MiB; an over-budget index rejects publication
rather than truncating rows. No byte ceiling is a claim about RSS.
Origin evidence in §6 must also fit that parent ceiling; a file omitted because
it is too large makes the task fail, not become an apparently complete dataset.

## 5. Persistent relationships and authority

Allocate the next additive migration only after the current acquisition migration
has landed. Proposed responsibilities:

| Table | Persistent facts |
| --- | --- |
| `quant_dataset_plans` | owner/request identity, exact immutable source/view spec and planRoot |
| `quant_dataset_jobs` / `quant_dataset_claims` | independent lease/deadline/status and durable receipt |
| `quant_dataset_stages` / `quant_dataset_parts` | immutable staged manifests/actual R2 receipts; no arrays in D1 |
| `quant_research_datasets` | owner/id, datasetRoot, status and compact summary |
| `quant_experiment_datasets` | datasetRef/profile pinned to each saved experiment version |
| `quant_dataset_coverage` | bounded indexed coverage rows for real pagination |
| `quant_dataset_dependencies` | exact source bundle/input/preparation/publication/registry refs, including original bytes |
| `quant_run_datasets` | run+dataset+profile, actual numerical/config/resource roots and source closure identity |

Start pins sources in the same D1 transaction that queues the task. Complete
atomically writes ready+root+dependency links+job terminal state; an abort leaves
no half-ready dataset. Initial implementation offers no physical delete API for
referenced sources. Existing cleanup must check these pins before deleting market
snapshots, financial chunks, registry records or dataset components. Archive is a
UI action only and does not erase a pinned dependency. Failed/cancelled evidence
is retained under explicit storage limits; no receipt TTL reset is introduced.

Current owner and active registry status are checked at plan start, finalization,
and every new F admission. The consumer receives the exact pinned registry bytes
from those authorized relationships, never from the archive's own assertions.
Revocation blocks new admission, but does not silently rewrite prior roots or make
a historical archive claim current authority. Raw evidence downloads remain private
and scoped to their original owner. A digest or datasetRoot does not grant access.

## 6. Result transport and complete source attachments

**Approved format direction:** use a distinct `atlas.quant.financial_bundle/1` result
format, with explicit snapshot codec `financial_json_v1`. Keep the registered
forecast/report/coverage layout where applicable, but use financial canonical
bytes for the new snapshot and its rows. Forecast artifact identity and F numeric
semantics do not change. The exact shared codec schema is supplied by the result-codec owner before integration.

Writer, reader, strict edge parser/scanner, completion, HTTP archive and independent
stdlib auditor must agree on that discriminator. Old bundle/1 accepts only its
existing snapshot/encoding. New completion requires the exact run→dataset relation,
profile, snapshot datasetRef/financial commitment, unchanged forecast artifact ID,
complete planned origins and zero execution rows. Missing new capabilities block
admission rather than selecting the old single-JSON completion path.

The existing dataset/1 archive already contains complete normalized market,
financial packages, registry evidence, prepared panel/events/dependencies,
research rows/schema/coverage. It does **not** contain original upstream wire
receipts, annual-report PDFs or the original source bundle snapshot identity.
The user must see these distinct evidence levels; absence is never labelled closed.

The independent reviewer recommends a versioned dataset extension instead of a
third companion archive. **Propose `atlas.quant.research_dataset/2`**, with a fixed
`marketOrigin` component in the same typed root/dependency closure. Keep
existing dataset/1 bytes, whitelist, CLI and readers unchanged. Do not extend the
old version's accepted components silently. The hosted ref retains the same four
keys but explicitly says version 2; this version change has root approval.

The new component contains the original source bundle manifest bytes, exact
original snapshot document bytes, and a versioned projection receipt. The agreed
core proposal is typed canonical JSON with raw UTF-8 strings for the two original
documents; decoding those strings must reproduce their original bytes exactly.
No numeric reserialization of the original snapshot is permitted. Escaping
overhead is measured as stored, not excluded from the parent budget. The proposed
new dataset profile is `financial_snapshot_view_50_v1`; the old
`financial_compose_50_v1` profile keeps all existing v1 rules. Snapshot
bytes are reconstructed from the pinned source snapshotRows chunks before any
normalization. The original manifest and snapshot document SHA remain verifiable;
the manifest's forecast skeleton retains the original source strategy. The projection
receipt binds original bundle/snapshot/scope/strategy identity to the new exact
scope and marketRoot. If the old bundle already normalized numeric tokens, the archived source preserves
those existing bytes; it cannot restore the previous float/integer distinction.
The origin component is an earlier typed dependency of the
market component; no circular reference to outer datasetRoot is stored inside it.
The outer datasetRoot then covers the complete derivation evidence.

All actual serialized origin bytes, including any framing/escaping, count against
the same 64MiB closure, component, depth and part budgets. Reserve their exact size
before giving the remaining budget to composition. Component graph design must preserve depth ≤3 (origin and registry are
independent roots; market depends on both). The original **complete** forecast
bundle remains pinned and separately downloadable, but unrelated old forecast
arrays need not be duplicated inside the dataset. This component proves original
market snapshot derivation; it is not a renamed complete original forecast bundle.

The report therefore offers two required private attachments:

1. Financial forecast numerical package (new result transport).
2. Research dataset and its complete normalized/market-origin source closure
   (explicit dataset/2, including the origin component).

The source complete historical forecast bundle is an optional separate download.
A reader given only attachment 1 cannot claim data-source closure. Given both,
it must verify dataset/2's origin component and projection as well as financial
recomposition. Missing origin cannot be downgraded to a complete ready dataset.
PDFs/wire responses remain separately disclosed as included/unavailable; no new
fetch is authorized. Registry pins still require independent authority; hashes
alone cannot certify official evidence. Independent audit and numerical recompose
remain separately labelled.

## 7. Errors and completeness semantics

Errors retain immutable refs and actionable reason codes. Typical blocked reasons:

| Code | UI action / meaning |
| --- | --- |
| `DATASET_SOURCE_NOT_ELIGIBLE` | choose a committed complete non-financial forecast source |
| `DATASET_SOURCE_CHANGED` | refresh the plan; pinned identity unavailable, never refetch |
| `DATASET_CALENDAR_REQUIRED` / `DATASET_CALENDAR_MISMATCH` | select authorized exact calendar or retain explicit gap |
| `DATASET_SCOPE_MISMATCH` | show both scopes; create a financial revision or explicit market view |
| `DATASET_STATE_COLLISION` | overlapping symbol/state packages need explicit combined input |
| `DATASET_REGISTRY_REVOKED` | new admission stopped; original archive unchanged |
| `DATASET_BUDGET` | show failing byte/count boundary; no dropped rows/dependencies |
| `DATASET_RESEARCH_PROFILE` | restrict target/model/execution and exact frozen scope |
| `DATASET_RUNNER_UNAVAILABLE` | plan preserved, no alternate source or old runner |
| `INSUFFICIENT_DATA` / existing preflight codes | dataset complete but F sample eligibility failed before fit |

State machine: preparation `queued→running→validating→ready`, or explicit
`blocked|failed|cancelled`. The dataset ID is exposed as ready only at successful
commit. Progress phases are descriptive, not fabricated percent estimates.
`sourceClosure`, `stateCoverage`, `sampleEligibility`, `forecastCompletion`, and
`validatedEdge` are separate labels. A negative/no-edge forecast is completed
research, not a data preparation failure.

## 8. Small-module split and acceptance gates

| Owner slice | Files / responsibility | Gate |
| --- | --- | --- |
| Source adapter/core | `research_dataset/source_bundle.py`, `view.py`, versioned origin codec; reuse existing compose/reader/snapshot | old snapshot verified before projection; exact/narrowed views, no future/date/unit repair |
| Hosted edge | `edge/datasets/{sources,plans,jobs,registry,publication,integrity,read-model,archive}.mjs`; additive migration | actual D1/R2 ownership, CAS, references, rollback, bounded reads |
| Dataset consumer | `dataset_runner/{client,service,spool}.py`; shared compute slot | actual spawn, no credentials/network-provider, deadline/ACK restoration |
| Financial result codec | dedicated Python/edge format dispatch and stdlib audit | float/int/−0 byte fixtures, original bundle/1 unchanged, origin/dataset cross-binding |
| Run adapter | narrow `runner-claims`, statistical persistence/validation/enqueue and child changes | old runners skip; exact dataset recompose+preflight in child; every execution route blocked |
| UI | `web/quant-workspace/datasets/{sources,scope,financial,review,detail}.js` + data-source adapter | independent pages, input retention, paging, keyboard and actual desktop/narrow viewport |

No new provider/F call is needed during design. First implementation verification:

- Frozen short synthetic closure: real HTTP compose/readback/archive/recompose;
  all-missing and declared states retained, F fails insufficient samples.
- A fixed longer synthetic fixture: one full permitted forecast, all baseline/tail
  origins retained, new transport readback and joint source audit; no tuning or
  rerun to produce a favourable result.
- Existing private real sources: recompose and compare already-frozen fingerprints,
  no provider and no repeated F; acknowledge missing raw/PDF evidence.
- Cross-owner IDs/hashes, revoked registry during each phase, rehashed forged
  components, missing panel rows, source mutation, arbitrary model_fin_ uploads,
  cancellation/expiry/SQL abort, lost claim/chunk/complete ACK.
- 64MiB/+1, 256/257 parts, 32/33 components, proof query counts, actual serialized
  and runtime resources; checksum-only tests are not numerical recomposition.
- CI preserves old bundle/1 IDs, archive strictness, old queues and old UI flows.

Research binding remains false until all slices are complete and a real authenticated
HTTP source→compose→F→download→fresh-process verification chain passes.

## 9. Approved architecture; codec fields require shared fixtures

A. **Explicit market subset view** is recommended so a two/three-year source can be
used with a one-year authorized financial preparation. Selection is user-visible,
original source is verified/retained, and new scope creates new market/dataset roots.
Exact mode remains default. Financial packages are never automatically trimmed.
Projection fields and identity semantics must be shared with the core owner.

B. **New financial result format plus dataset/2 origin component** preserves two
required attachments and binds the original market derivation into datasetRoot.
Approve exact discriminators and versioned component layout before writer/UI code.
Existing dataset/1 and bundle/1 stay unchanged. Any alternative must preserve the
same complete original-source bytes, joint budgets and independent verification;
adding schemaVersion2 to an old whitelist is not acceptable.

Coordination on 2026-10-08: the source-core owner accepted the marketSource DTO
and immutable financial ref fields in §3, resolving calendar/proof refs server-side
into `FinancialSource`. The result-codec owner is drafting the exact
`sourceEvidence` manifest linkage; API implementation must consume that registered
schema rather than infer it from this prose. datasetRoot already equals the actual
canonical manifest SHA; a separately named manifestSha256 must equal it, never be
a second interchangeable identity.
