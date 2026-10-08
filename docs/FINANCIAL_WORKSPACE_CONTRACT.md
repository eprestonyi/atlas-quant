# Financial workspace HTTP and UI contract

Status: **root approved gates 2–4 for implementation, 2026-10-08**. No implemented HTTP route, financial queue, hosted preparation or UI readiness is claimed merely by this contract. The runner wire protocol is fixed in [FINANCIAL_RUNNER_PROTOCOL.md](FINANCIAL_RUNNER_PROTOCOL.md). Python core commit `7409f8b` exists; the independent dataset bridge is being implemented by history. There are no provider calls in this HTTP workflow.

This specification uses the existing `/quant/api` prefix and workspace cookie. All routes below are new proposed routes. Unknown object keys are rejected rather than silently projected away. Dates are `YYYYMMDD`; timestamps are timezone-qualified ISO 8601; hashes are lowercase 64-character SHA-256 hex. Examples containing `<...>` illustrate types, not literal accepted values.

## 1. Minimum real vertical slice and ownership

The first complete slice is: import a frozen statement package → authenticate its calendar/proof references against the operator registry → run the real Python validator → run the real Python preparation → read bounded coverage and dependency evidence → explicitly bind selected states to a research draft. An actual research run must then compose those inputs with its frozen market data through the Python bridge and preserve the evidence closure in its private export. No client-supplied prepared panel, `preparedRoot`, `semanticKind` or successful-looking test fixture bypasses this path.

Two capabilities are distinct. `preparation` may be enabled when validation/preparation and evidence readback work. `researchBinding` remains false until schema admission, dataset composition, runner resolution, reproducible evidence export and replay have passed together. A prepared source can be useful while research binding is unavailable; the UI says so and does not show an enabled attach button.

Responsibilities:

| Owner | Files and responsibility |
| --- | --- |
| Frontend agent | `web/quant-workspace/financial/*` for source list, import, unit assumptions, preparation, coverage and evidence; bounded API client; DOM behavior tests |
| Frontend agent | `edge/financial/{api,validation,inputs,registry,read-model}.mjs`, additive financial SQL migration, HTTP ownership/limits/readback tests; small router/capability integration only after root approval |
| Root | Dedicated financial runner adapter and claim/delivery/timeout/cancellation protocol; trusted registry bootstrap; schema-2 immutable input bindings; existing run/export integration |
| History | `engine/atlas_quant/financial_statements/dataset.py`, pure composition tests and exact bridge DTO; Python core remains its responsibility |
| Independent reviewer | Trust, tampering, isolation, missing-state, budget and full replay counterexamples |

Do not merge financial tasks into the old `jobs` table or old claim selector. Do not start the financial UI with invented API success responses. Tests may use explicitly named transport doubles; acceptance requires the real Worker, D1/R2 and Python path.

## 2. Capability and immutable registry

`GET /financial/capabilities` is an authenticated, lightweight read:

```json
{
  "apiVersion": "financial-workspace/v1",
  "enabled": true,
  "operations": {"upload": true, "validate": true, "prepare": true, "researchBinding": false},
  "runner": {"online": true, "capability": "financial-input/v1", "lastSeen": "<ISO8601>"},
  "limits": {
    "packageBytes": 25165824, "snapshots": 128, "sourceRows": 20000,
    "bindings": 20000, "calendarSessions": 10000, "selectedStates": 16,
    "panelRows": 110000, "preparedBytes": 67108864,
    "pageSize": 25, "maxPageSize": 100, "pageBytes": 262144,
    "maxActiveTasksPerWorkspace": 1, "maxPendingUploadsPerWorkspace": 3,
    "workspaceRetainedFinancialBytes": 268435456
  }
}
```

The initial host quota is 256 MiB of owned source, staging and prepared evidence, with at most three pending uploads and one queued/running financial task per owner. Reserve declared upload/preparation capacity transactionally and release unused reservation only after a terminal receipt. Do not discard referenced completed evidence to satisfy quota; return an explicit quota error. Expired incomplete uploads are eligible for bounded cleanup after 24 hours, never while a live validation lease references them.

These ceilings intersect, rather than replace, the core's panel/event/calendar and common-parent budgets. Capabilities are derived from actually installed routes, supported runner capability and operator configuration. Before the response arrives, the UI displays loading; a network failure is not an empty source list or unavailable data. Unsupported/offline preparation returns a recoverable explicit state, not an indefinitely queued job.

`GET /financial/definitions` returns `{formulaVersion,policyVersion,items,fields,unitOptions}` from a build export of the actual Python contracts and recipes. Each item has `id`, `name`, `expression` equal to its stable state ID, `definition` equal to `asdict(RECIPES[id])`, derived `requiredFields`, and `availability:{status:"definition_only"}`. The definition contains `anchor_field`, numerator triples `(period_operator,field_id,quarter_offset)`, denominator triple, `subtract_one` and `applicable_company_types`. The UI can explain point/quarter/TTM dependencies from these real definitions; it cannot silently rewrite formulas. `fields` and `unitOptions` are exported from the exact installed core version, not hard-coded guessed currencies or scales. Definitions never imply data coverage. Preparation references that same formula/policy version.

`GET /financial/calendars?page=1&pageSize=25&dateFrom=...&dateTo=...` lists authorized immutable calendar revisions:

```json
{
  "items": [{
    "calendarRef": "cal_<uuid>", "registryVersion": 1,
    "calendarRoot": "<64hex>", "label": "SSE provider calendar / 2025",
    "coverageStart": "20250101", "coverageEnd": "20251231", "complete": true,
    "sessionCount": 243, "appliesTo": ["SSE", "SZSE"],
    "evidenceLevel": "provider_reported_calendar",
    "equivalenceBasis": "provider_documentation", "revision": 1
  }],
  "page": 1, "pageSize": 25, "total": 1
}
```

Counts in the example describe history's retained calendar receipt, not a currently hosted registry. Only registry loading makes a reference available. `calendarRoot` uses the core's complete calendar-evidence identity; the exact canonical `TradingCalendar` payload is separately retained. Calendar authenticity and completeness are different. A provider statement about SH/SZ equivalence must remain visible; no independently fetched SZSE evidence is invented.

`GET /financial/unit-proofs?inputId=<id>&page=1&pageSize=25` lists only proofs authorized to this owner and matching a bounded coordinate in that source. It returns `proofRef`, `bindingHash`, `kind`, `fieldId`, symbol/period/snapshot scope, review version and evidence label. It does not expose other owners' rows or make all source amounts public. Both registry namespaces are operator-written, immutable and versioned; **there is no browser write endpoint**. Revocation blocks new use but preserves immutable historical replay with the recorded registry disposition.

## 3. Bounded source import

The UI accepts the existing `atlas.quant.financial-input` version-1 JSON package, not arbitrary CSV, provider credentials or a URL to fetch. It uploads a `File` body directly and does not parse a complete package into long-lived browser state. Name, byte size and the server's receipt suffice for the import view.

### Create an upload receipt

`POST /financial/inputs` (JSON, maximum 16 KiB):

```json
{
  "requestId": "<uuid>", "name": "My frozen statements", "byteLength": 1048576,
  "calendarRef": "cal_<uuid>", "proofRefs": ["proof_<uuid>"]
}
```

`name`: 1–80 characters; `byteLength`: integer 1–24 MiB; `proofRefs`: at most 256 unique authorized registry references, additionally bounded by the request-byte limit. A proof registry may expose a preapproved immutable `proofSetRef` in a later version; v1 does not silently truncate a large list. Absence of proof refs permits declarations only. Response `201`:

```json
{
  "input": {"id": "fi_<uuid>", "name": "My frozen statements", "status": "uploading", "revision": 1},
  "upload": {"method": "PUT", "url": "/quant/api/financial/inputs/fi_<uuid>/content", "maxBytes": 25165824}
}
```

`requestId` is scoped to owner and exact creation body. An exact retry returns the original receipt; a different body with the same ID returns `409 REQUEST_ID_CONFLICT`. No signed public storage URL is returned.

### Upload exact bytes

`PUT /financial/inputs/:id/content`, `Content-Type: application/json`, raw file bytes. The endpoint preserves bytes, counts actual streamed bytes independently of Content-Length, rejects a mismatch with declared length, validates UTF-8 and computes `uploadSha256`. It does not claim that `JSON.parse` proves duplicate-key or financial correctness. A bounded staging object becomes visible only after receipt commit. Exact-byte retry is idempotent; different bytes for the same receipt return `409 INPUT_IMMUTABLE`. No partial object is a usable input.

Response `200`: `{ "input": { "id", "status":"uploaded", "uploadSha256", "byteLength", "revision":1 } }`.

R2 paths are generated by the server and contain the owner/source identity; a browser never supplies a storage key. Uploads are private and all responses use `Cache-Control: no-store`. Source bytes and exact proof/calendar snapshots remain retained while any preparation, experiment or exported replay references them.

### Validate without preparing a panel

`POST /financial/inputs/:id/validate`:

```json
{"requestId":"<uuid>","expectedUploadSha256":"<64hex>"}
```

Response `202`: `{ "input": {"id":"fi_<uuid>","status":"validating"}, "job": {"id":"fj_<uuid>","kind":"financial_validate","status":"queued"} }`.

The financial runner reads exactly the uploaded bytes and frozen authorized registry entries. A bounded duplicate-key-rejecting JSON inspection extracts only the fields needed for registry matching; it grants no trust and publishes no validated state. Trust resolution occurs before calling any core function with trusted proofs. After resolution, `decode_package` validates the original bytes with the strictly derived trusted-caller option and rejects malformed roots, credentials, unknown top-level flags, unsupported source semantics and size/shape violations. Do not pass `trusted_unit_proofs=True` merely to inspect a package before registry checks:

1. Package `raw.calendar` must match the registry's complete canonical TradingCalendar payload, including evidence reference and coverage, not merely have the same open dates. It must have sufficient coverage. **Do not replace the uploaded calendar and reuse the old inputRoot.** A mismatch needs a newly created package and new declarations.
2. All unverified bindings must be valid `DeclaredUnitBinding` records. A user cannot set `verified:true` on an assumption.
3. Every document/global binding must match the complete canonical binding and hash of a supplied authorized registry proof; registry proof scope must match the package's provider, field, symbol, period, normalized snapshot and normalized row identity. A declared proof ref is not sufficient by itself. Fixture bindings are rejected by the public route.
4. A package with `trusted_unit_proofs`, `trustedUnitProofs`, a caller-selected reviewer identity or self-certified official calendar is rejected. The trusted-caller boolean is created only inside the runner after the above exact checks; it never appears in the public DTO.
5. On success, retain exact original package bytes plus canonical package and resolved proof/calendar snapshots. `uploadSha256`, `inputRoot`, `packRoot` and `calendarRoot` are separate identities, each labelled. A normalized row hash is never labelled a raw HTTP/PDF hash.

Unknown units or insufficient earlier report periods do not make a structurally valid package invalid. The validator returns them as known limitations or leaves numerical coverage `not_computed`; preparation can then return honest missing states. Invalid registry trust, corrupt roots, unsupported package structure and resource overflow block preparation.

`GET /financial/inputs/:id` is the refresh/readback authority. Its compact DTO is:

```json
{
  "input": {
    "id":"fi_<uuid>","name":"My frozen statements","revision":1,
    "status":"ready_to_prepare","uploadSha256":"<64hex>",
    "inputRoot":"<64hex>","packRoot":"<64hex>","calendarRoot":"<64hex>",
    "calendarRef":"cal_<uuid>","unitPolicy":"verified_only",
    "source":{"kind":"provider","provider":"TUSHARE_PRO","snapshotRepresentation":"normalized_provider_table_snapshot"},
    "selection":{"symbols":["600690.SH","000651.SZ"],"start":"20250101","end":"20251231","announcementStart":"20250101","selectedStateIds":["model_fin_cash_asset_share"],"scope":"consolidated","flowBasis":"ytd"},
    "counts":{"snapshots":6,"sourceRows":7,"bindings":32},
    "evidence":{"availabilityEvidenceLevel":"vendor_reported_disclosure_dates","originalAsPublishedVerified":false,"revisionTimeVerified":false,"unitAssumptions":false},
    "coverageStatus":"not_computed","createdAt":"<ISO8601>","updatedAt":"<ISO8601>"
  },
  "validation":{"status":"passed","issues":[],"missingPrerequisites":[]},
  "activeJob":null,"latestPreparation":null
}
```

Example counts are illustrative except where noted above. A missing root before validation is `null`, not a made-up zero hash. `GET /financial/inputs?page=1&pageSize=25` returns `{items,total,page,pageSize}` using this compact input summary plus actual `latestJob`/`latestPreparation` status; it never loads source bytes. Owner-local ordering is deterministic (`createdAt DESC,id DESC`).

## 4. Explicit configuration/declaration revision

Validated source content is immutable. Editing selected states, period, policy or declarations creates a new input revision with `parentId`; it never changes the original pack or a previously attached experiment.

`POST /financial/inputs/:id/revisions` (maximum 128 KiB):

```json
{
  "requestId":"<uuid>","expectedPackRoot":"<64hex>",
  "selection":{"symbols":["600690.SH","000651.SZ"],"start":"20250101","end":"20251231","announcementStart":"20250101","selectedStateIds":["model_fin_cash_asset_share"],"scope":"consolidated","flowBasis":"ytd"},
  "unitPolicy":"allow_declared",
  "declarations":[{
    "fieldId":"balancesheet.total_assets","inputRoot":"<64hex>",
    "nativeUnit":"CNY","currency":"CNY","positiveOutflow":null,
    "statement":"For this exact frozen input I interpret this field as CNY yuan."
  }]
}
```

Declaration authorship is the current authenticated workspace actor; `declaredAt` is the server timestamp. Optional display attribution is not an authentication credential. Core `kind=user_declared_assumption` and `verified=false` are server constants. Accepted units/currency/sign values come from the core's supported contract, exposed as registry metadata; the UI does not offer unsupported choices or default an unknown unit to CNY. Declarations are a **complete replacement set** (explicit empty array removes old declarations); reviewed proofs remain fixed from the parent. Other policies/selection changes also require this revision endpoint.

The new job `financial_revise` revalidates its parent, constructs declarations scoped to its unchanged raw `inputRoot`, calls `freeze_package` and validates the new pack. Calendar/source edits are not supported by revision; import a new source for those. Response `202` returns the new input in `validating` plus job. Conflicting declarations remain visible errors/missing results under core rules; no last-write-wins value selection. The normal UI shows the proposed change and requires “保存声明并生成新版本”. It never applies assumptions by selecting a formula card.

## 5. Separate financial tasks and preparation

Add independent tables: `financial_inputs`, `financial_jobs`, `financial_claims`, `financial_preparations`, `financial_registry_entries`. All input/job/preparation rows carry owner; registry grants are explicit. Foreign reference checks join owner through every link, not just the first source. There is no automatic claim via the old research `jobs` table.

Financial kinds are `financial_validate`, `financial_revise`, `financial_prepare`. Dedicated runner routes under `/runner/financial/` use the existing independent operator credential and require capability exactly `financial-input/v1`; browser cookies cannot call them. Proposed verbs mirror durable research delivery: `heartbeat`, `claim`, `input`, `complete`, `fail`. A capability-aware `claim` reserves one financial task under a durable UUID request ID and bounded lease. It is idempotent across lost responses; old runner claims cannot see this table. The initial financial resource profile has a 120-second renewable lease, 20-second heartbeat and wall deadlines of 180 seconds for validate/revise and 600 seconds for prepare, charged from acknowledged claim. These are new financial limits, not a change to existing research timeout. Cooperative cancellation is checked between bounded batches, with an outer-process deadline as the final guard. Lease heartbeat/cancel/expiry and completion acknowledgment must use the existing durable protocol principles, without claiming that copying an in-memory job is sufficient. Runner lease tokens, storage keys and registry private objects are never included in browser job DTOs.

`POST /financial/inputs/:id/prepare`:

```json
{"requestId":"<uuid>","expectedPackRoot":"<64hex>"}
```

Response `202`: `{ "input":{"id":"fi_<uuid>","status":"preparing"}, "job":{"id":"fj_<uuid>","kind":"financial_prepare","status":"queued"} }`.

The runner resolves the validated immutable source, verifies all bytes/roots and trusted registry scope again, then calls `prepare_package`. It cannot accept a caller's panel or preparedRoot. Success commits bounded sidecars and a manifest transactionally, returning `preparationId`, computed `preparedRoot` and coverage. A partial numerical body is never published as prepared.

`GET /financial/jobs/:id` returns `{job:{id,kind,status,inputId,phase,createdAt,updatedAt,error,resultRef}}`. `phase` is one of `queued`, `checking_inputs`, `preparing_states`, `writing_evidence`; no invented percent/ETA. Errors use stable codes plus safe text and bounded reason details. `POST /financial/jobs/:id/cancel` requests cancellation. Queued tasks cancel immediately; running tasks show `cancel_requested` until acknowledged. Completion and cancellation are mutually exclusive atomic terminal transitions; no silent late-success overwrite. A cancelled task leaves validated input intact and no selectable preparation.

Input state transitions:

```text
uploading -> uploaded -> validating -> ready_to_prepare -> preparing -> prepared
                         |                ^                 |
                         -> blocked       |                 -> ready_to_prepare + lastJob failed/cancelled
```

For first-time validation, malformed input reaches `blocked`; cancellation or a transient transport/runner failure returns it to `uploaded` with the terminal last job preserved. A cancelled/failed provisional revision reaches `blocked` with its exact job reason and leaves its parent unchanged; resubmission creates a new revision request. A failed preparation returns to `ready_to_prepare` if no committed preparation exists, or to `prepared` with its prior committed preparation still selected; it never overwrites or hides old evidence. A blocked structural source requires a new import/revision, not mutation of bytes.

`prepared` means computation completed, including missing outcomes. `usableStates=0` remains a valid diagnostic preparation with `dataReadiness=no_usable_states`; it is not readiness or a zero-filled panel. Retrying with the same request ID never starts a second computation. A new request may retry a transient failure; it does not cause a provider fetch. A ready existing preparation is reused for the same complete source/config/core-policy identity, or a new implementation version is explicitly recorded rather than overwritten.

## 6. Bounded preparation read models

`GET /financial/preparations/:id` returns `{preparation,collections,readiness}`. The preparation contains source ID, all four roots, versions, unit policy, counts, resource accounting, availability limitations and exact immutable selection. `readiness` separates `prepared:true`, `hasUsableStates`, `marketComposition:"not_checked"`, `sampleCoverage:"not_checked"`, and `researchBindingEnabled`; no aggregate green badge implies predictive value.

The compact DTO is:

```json
{
  "preparation": {
    "id":"fp_<uuid>","inputId":"fi_<uuid>","status":"prepared",
    "inputRoot":"<64hex>","packRoot":"<64hex>","preparedRoot":"<64hex>","calendarRoot":"<64hex>",
    "formulaVersion":"financial_states_v1","policyVersion":"statement_asof_v1",
    "unitPolicy":"verified_only","selection":{"stateIds":["model_fin_cash_asset_share"],"symbols":["600690.SH"],"start":"20250101","end":"20251231"},
    "counts":{"panelRows":243,"stateEvents":2,"assignments":2,"coverageItems":1},
    "resourceAccounting":"conservative_serialized_expansion_v1_not_rss",
    "qualityFlags":[],"availabilityEvidenceLevel":"vendor_reported_disclosure_dates",
    "originalAsPublishedVerified":false,"revisionTimeVerified":false,"createdAt":"<ISO8601>"
  },
  "collections":{"coverage":{"total":1},"events":{"total":2}},
  "readiness":{"prepared":true,"hasUsableStates":true,"marketComposition":"not_checked","sampleCoverage":"not_checked","researchBindingEnabled":false}
}
```

Counts here are illustrative, not a claimed canary result. `readiness` represents this immutable preparation plus current service capability; it cannot certify sufficient nested-fold history or a profitable model.

The manifest does not embed all events, dependencies, assignments or prepared rows. These routes require `preparedRoot=<64hex>` to pin every later response:

| Route | Supported filters | Bounded result |
| --- | --- | --- |
| `GET /financial/preparations/:id/coverage` | `stateId`, `symbol`, `status=all|usable|missing`, `cursor`, `limit` | state × symbol summaries |
| `GET /financial/preparations/:id/events` | `stateId`, `symbol`, `periodEnd`, `status=all|ok|missing`, `dateFrom`, `dateTo`, `cursor`, `limit` | projected state events, not complete dependency trees |
| `GET /financial/preparations/:id/events/:eventId` | identity pinned | projected event, dependency count, links |
| `GET /financial/preparations/:id/events/:eventId/dependencies` | `cursor`, `limit` | dependency projections with exact values/versions |
| `GET /financial/preparations/:id/events/:eventId/download` | identity pinned | complete event fields as private JSON attachment (transport key order is not an identity) |

Every page returns `{items,total,limit,nextCursor,preparedRoot,allMatchingItemsReturned}`. The last boolean is true only when this response contains all matching items from the start, not merely the last page, and never claims that provider history is complete. An empty actual collection has `total:0`, `items:[]`, `nextCursor:null`, `allMatchingItemsReturned:true`; an unavailable query is an error, not that empty shape. Cursors are opaque, scoped to owner/preparation/root/filters and server-controlled offset. Unknown filters or root mismatch reject; page size 1–100, default 25, encoded page ≤256 KiB. If fewer rows fit, return the next cursor, not a false end marker. No arbitrary query requiring unbounded scans.

Coverage item:

```json
{
  "stateId":"model_fin_cash_asset_share","symbol":"600690.SH",
  "okRows":180,"missingRows":63,"firstAvailable":"20250331",
  "firstObserved":"20250331","lastObserved":"20251231",
  "lastAvailable":"20250331","latestPeriodEnd":"20241231",
  "latestAvailableDate":"20250331","lastPreparedDate":"20251231",
  "latestAgeCalendarDays":365,"reasonCounts":{"NO_DISCLOSED_PERIOD":63},
  "qualityFlags":[],"unitEvidenceLevels":["source_document"],
  "originalAsPublishedVerified":false,"revisionTimeVerified":false
}
```

These coverage numbers are hypothetical and must only be replaced by computed output. `firstObserved`/`lastObserved` are the first/last prepared nonmissing sessions. `firstAvailable`/`lastAvailable` are the minimum/maximum actual per-row available_date among valid rows, not observation dates or the report-period end. The bridge status is `available|missing`; available means at least one valid value in that source, not enough training history. Age is defined as the civil-date difference `lastPreparedDate − latestPeriodEnd`, labelled “报告期末距样本末日（自然日）”; it is neither a trading-session count nor the age of the latest disclosure. `latestAvailableDate` comes from the latest selected event and is separately displayed. If there is no qualifying period/event, the corresponding fields are null. The server/bridge computes these fields; the frontend does not infer them from the currently loaded page.

Projected event: `{eventId,stateId,symbol,computedAsOf,asOf,availableDate,periodEnd,status,decimalValue,floatValue,decimalPrecision,rounding,reasonCodes,qualityFlags,unitVerified,declarationHashes,dependencyCount,lineageHash}`. `eventId` maps exactly to core event `id`, `computedAsOf` remains the event origin, and `asOf`/`availableDate` are the actual `result` fields. `floatValue` is the finite numeric conversion used by the prepared panel, not a replacement for `decimalValue`. Preserve null and explicit unavailable status. Ordinary UI shows formula, values, periods and reasons; raw IDs/JSON live under advanced evidence.

A dependency projection retains field, report period, source version/availability, original numeric representation where retained, normalized/derived Decimal value, unit/currency/basis, source snapshot and binding hashes. Long source references are bounded previews with `referenceTruncated:true` and an explicit complete-event download; omitted text is not called complete evidence. Exact event download never reconstructs an approximation from the displayed page. No dataset-wide forecast or state value is inferred from the currently loaded page.

`GET /financial/inputs/:id/download?packRoot=...` streams the validated complete package (private attachment). Raw uploaded bytes, if different formatting, are separately available via `/source-download?uploadSha256=...`. A standalone preparation export contains canonical package + prepared evidence + versioned manifest and complete hashes; its finalized archive protocol must be shared with the existing bundle work before enabling its button. It cannot be labelled self-contained if it omits registry proof/calendar evidence needed to validate trust scope.

## 7. Research binding and actual dataset bridge

History's agreed Python entry point is:

```python
compose_financial_dataset(strategy, market_dataset, financial_packages,
    *, trusted_unit_proofs=False, budget=None)
# FinancialDatasetResult(data: DataFrame, provenance: dict,
#                        financial_artifacts: tuple)
```

Each sidecar contains the canonical package, actual `AdapterResult` and compact state × symbol summary. The bridge revalidates/prepares inputs, checks calendar/symbol/date alignment and rejects collisions with reserved `model_fin_*` columns or preexisting claimed financial roots. It only joins actual market rows; it cannot fabricate prices or extend source history. `provenance.financialInputs` and `externalFields` stay compact; evidence sidecars remain private and complete.

Financial component metadata for the future common dataset composition path (not a new run-request shortcut):

```json
{"financialInputs":[{
  "inputId":"fi_<uuid>","preparationId":"fp_<uuid>",
  "inputRoot":"<64hex>","packRoot":"<64hex>","preparedRoot":"<64hex>",
  "calendarRoot":"<64hex>","stateIds":["model_fin_cash_asset_share"]
}]}
```

These are auxiliary financial-evidence references, not a second market dataset identity. The market preparation/admission work owns the common immutable `datasetRef`; financial source pages do not create parallel market-upload endpoints. At composition, that dataset reference resolves the frozen market rows and its manifest/root, while `financialInputs` supplies the statement evidence closure. The final composed dataset fingerprint binds both. Browser/Worker handling of larger pooled market inputs remains governed by the separate dataset preparation contract and must not parse a 64-MiB market JSON through these 24-MiB financial endpoints.

Selection is explicit and a subset of actually prepared state definitions. Future composition selects these financial components into the common immutable dataset pipeline. Only a ready `datasetRef:{datasetId,datasetRoot,format:"atlas.quant.research_dataset",version:1}` may be submitted as a prepared research data source; the UI cannot bypass dataset admission by sending component refs directly to run. It may add the corresponding registered DSL expressions only after an explicit user action, and never overwrites existing factors or saves/runs automatically. This first slice has no attach action, because researchBinding is always false. Saving uses the normal experiment optimistic version. Ownership and all roots are rechecked at save and run, and evidence identities participate in the prediction/data identity. A matching client hash is not sufficient if the referenced object is inaccessible or has different contents.

During run, root resolves immutable packages plus registry evidence and composes them with the **frozen** market dataset. It compares recomputed prepared roots and reports per-state coverage, missing training features and declarations. Provider revision-history limitations stay in the source/report even when arithmetic and no-future-leak tests pass. Fundamental family support is enabled only for registered native statement states backed by this validated bridge. Arbitrary uploaded `model_*` columns remain untrusted external fields.

The report/export contract must preserve package + calendar/proof registry snapshots + preparation evidence closure as private bundle collections. Replay loads them; it never fetches providers, guesses units or relies solely on mutable database rows. Before this closure and root's real-run acceptance exist, `researchBinding=false` and the UI remains a preparation/evidence workspace.

## 8. Product pages and progressive detail

- Main state step: an “财务状态” source entry, showing actual attached source count and status. Sixteen definitions are a formula library, not sixteen ready datasets.
- Studio route `#quant/studio/financial`: source list with real latest job/preparation, loading/error/empty states separated; upload button only when supported. Existing eight-step research navigation remains intact.
- Source route `#quant/studio/financial/:id`: four explicit panels/steps — input receipt, calendar/units, definitions/configuration, preparation/coverage. Back/next preserve local edits; reload restores server state and immutable refs.
- Calendar/units: selected approved calendar summary; verified proofs versus user assumptions clearly separated. Default `verified_only`. Unsupported calendar/source/proof errors link to the precise missing prerequisite; no “trust anyway” checkbox.
- Definitions: search/filter the 16 actual formulas by raw dependencies/period requirements. Select a definition to request its missing diagnostics too; unavailable history is not hidden, but never shown ready. Static ratios from one annual period and quarter/TTM states have distinct required-history text.
- Coverage: compare state × company, reason counts and actual first availability. Paginate on the server. Optional lineage drawer fetches one event and one dependency page. Advanced IDs/JSON stay collapsed by default; no huge summary before mobile controls.
- Attach: preview selected states and existing-factor changes, confirm explicitly; disabled with concrete reason until research-binding capability is real.

All drag actions have “加入/移除/上移/下移” button equivalents. Keyboard focus moves to the new page title or dialog, not an arbitrary rerendered field. Late responses are keyed by source/root/request; editing, navigation or selected-source changes cannot display old coverage as current. Errors retain source choice and typed declarations. At 390px, horizontal scrolling belongs to an explicit table container, never the entire page.

## 9. Error and security contract

Ordinary HTTP failures retain the repository envelope `{error:{code,message}}`. Validation/job detail includes bounded `issues:[{code,path,message,remediation}]` and `missingPrerequisites`, without secrets, raw amounts or source data in audit logs. Known financial domain reason codes are preserved; the UI translates known codes but displays unknown codes safely rather than treating them as success.

| HTTP | Code examples | Meaning |
| --- | --- | --- |
| 400 | `INVALID_INPUT`, `INVALID_JSON`, `UNKNOWN_PROPERTY`, `FINANCIAL_PACKAGE_INVALID` | invalid DTO/package, duplicate keys or unsupported schema |
| 401 | `SESSION_REQUIRED` | establish current workspace |
| 404 | `NOT_FOUND` | missing or another owner's object/reference; no ownership leak |
| 409 | `INPUT_IMMUTABLE`, `ROOT_MISMATCH`, `REQUEST_ID_CONFLICT`, `JOB_ACTIVE`, `INPUT_NOT_VALIDATED` | stale/incompatible state, preserve edits and refresh safely |
| 413 | `PACKAGE_BYTE_BUDGET`, `PREPARED_BYTE_BUDGET`, `SOURCE_ROW_BUDGET`, `WORKSPACE_STORAGE_BUDGET` | bounded-resource refusal; no truncated success |
| 422 | `CALENDAR_EVIDENCE_REQUIRED`, `CALENDAR_REGISTRY_MISMATCH`, `CALENDAR_COVERAGE`, `UNIT_PROOF_NOT_AUTHORIZED`, `UNIT_PROOF_SCOPE_MISMATCH` | required evidence does not qualify; numerical missing values remain in successful preparations when appropriate |
| 429 | `RATE_LIMITED`, `WORKSPACE_ACTIVE_TASK_LIMIT` | retry under returned policy, no duplicate job |
| 503 | `FINANCIAL_CAPABILITY_UNAVAILABLE`, `FINANCIAL_RUNNER_OFFLINE`, `MAINTENANCE` | explicit service gap; existing evidence remains readable |

Mutations require same-origin JSON and existing CSRF/origin policy, including raw package PUT which is still JSON. New owner-scoped IDs never substitute for ownership checks. The API accepts no credential, network URL, local filesystem path, executable object, arbitrary SQL predicate, R2 key or client trust boolean. Shared registry existence and inaccessible source existence are not disclosed. Cancellation/download/page endpoints enforce the same ownership as detail.

## 10. Acceptance gates and implementation order

1. Contract/root approval: approve route namespace, bridge/schema DTO and explicit registry bootstrap policy. No UI calls a proposed endpoint beforehand.
2. Edge ingress + registry + independent financial tables: actual Miniflare owner isolation, streamed byte cap, missing Content-Length, duplicate-key preservation for Python, request retry, immutable upload and registry-mismatch tests.
3. Root financial runner: actual validate/revise/prepare jobs with claim-loss recovery, capability mismatch/old runner exclusion, expiry/cancel, atomic evidence publication and terminal readback. No provider calls.
4. Real UI + HTTP: upload a hand-verifiable multi-period fixture labelled synthetic; strict unknown-unit result, explicit declaration revision, mixed coverage, pagination, individual lineage, private download and reload. No successful transport double is counted as real evidence.
5. Frozen real canary: use retained statements and history's one frozen calendar receipt, with authorized exact document proof registry. Six static states may be available; other ten retain honest history gaps. Actual coverage counts are derived, never copied from this contract's illustrative examples.
6. F composition + export: root/history complete pure bridge and evidence closure; then same-source frozen market data, forecast-only fixed research, no provider refetch, independent replay audit. If sample coverage is insufficient, preserve that outcome and do not tune intervals after seeing it.
7. Browser acceptance: actual desktop plus verified 390px innerWidth, keyboard alternatives, route reload, delayed response/source switching, errors/input preservation. CUA screenshots are separate from DOM and HTTP results.

The immediately implementable slice is 2–4 after root adds the dedicated runner contract. A source can reach `prepared` before gate 6, with research binding explicitly disabled. Enabling the final attach button requires gate 6 rather than a cosmetic flag.
