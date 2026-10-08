# Financial runner protocol v1

Approved implementation target for the new financial workspace slice. Routes are under `/quant/api/runner/financial`; they do not interact with research `jobs` or research claim receipts. Feature flag `FINANCIAL_WORKSPACE_ENABLED` defaults false. Public `researchBinding` is always false in this slice. No route causes provider acquisition.

All runner requests require the existing operator Bearer authorization. JSON mutation bodies additionally contain `jobId` and `leaseToken` where shown. GET bodies are not used: protected downloads supply the opaque lease in `X-Financial-Lease`. Lease tokens, object keys and private registry records never enter browser responses. Root owns the Python consumer; the frontend agent owns the edge endpoints and their tests.

## Claim, heartbeat and input

`POST /heartbeat` without a job announces:

```json
{"capability":"financial-input/v1","engineVersion":"0.6.0","state":"ready"}
```

It writes the distinct `financial_runner` heartbeat. Existing `runner` readiness cannot enable financial preparation. Supported capability is an exact match, not just a high version. Every 20 seconds while working, add `jobId`, `leaseToken`, and `phase` (`checking_inputs`, `preparing_states`, `writing_evidence`). Response is `{ok:true,leaseValid:true,cancelRequested:false,leaseUntil:<ISO>}`. A valid heartbeat renews a 120-second lease up to the task's fixed wall deadline. It never extends the task deadline: validate/revise 60 seconds, prepare 300 seconds from claim. A lost/expired lease cannot be revived.

`POST /claim`:

```json
{"requestId":"<uuid>","capability":"financial-input/v1","engineVersion":"0.6.0"}
```

Response with work:

```json
{
  "claim":{"requestId":"<uuid>","status":"running","jobId":"<uuid>"},
  "job":{
    "id":"<uuid>","kind":"financial_validate","inputId":"<uuid>",
    "leaseToken":"<uuid>","leaseUntil":"<ISO>","deadline":"<ISO>",
    "inputUrl":"/quant/api/runner/financial/jobs/<uuid>/input"
  }
}
```

Kinds: `financial_validate`, `financial_revise`, `financial_prepare`. Empty is `{claim:{requestId,status:"empty",jobId:null},job:null}`. Durable claim receipt reserves a job once; repeating the same request ID returns the same lease/job or terminal receipt, including after response loss. Empty receipts are durable too. A new request ID is required for a new poll. Old research claims cannot see financial tasks; financial claims cannot see old research tasks. Root must persist a request ID before transport and not duplicate a local computation following an ambiguous claim response.

`GET /jobs/:jobId/input` returns only metadata and authorized reference descriptors:

```json
{
  "job":{"id":"<uuid>","kind":"financial_prepare","inputId":"<uuid>"},
  "source":{
    "url":"/quant/api/runner/financial/jobs/<uuid>/source",
    "sha256":"<64hex>","byteLength":12345,"representation":"uploaded_package_bytes"
  },
  "calendar":{
    "ref":"<uuid>","sha256":"<64hex>","byteLength":1234,
    "url":"/quant/api/runner/financial/jobs/<uuid>/registry/<uuid>"
  },
  "proofs":[{
    "ref":"<uuid>","sha256":"<64hex>","byteLength":1234,
    "url":"/quant/api/runner/financial/jobs/<uuid>/registry/<uuid>"
  }],
  "operation":{"expectedPackRoot":"<64hex>"},
  "limits":{"packageBytes":25165824,"resultBytes":67108864,"chunkBytes":524288,"manifestBytes":262144}
}
```

The source route streams the exact uploaded bytes for validate. For prepare/revise, it streams the parent's committed canonical package, with its own byte hash; raw original upload remains separately retained. No JSON decoder is run before the runner has verified the source byte length/SHA. The metadata response is at most 256 KiB. `operation` for revise contains the exact authenticated revision selection, `unitPolicy`, declarations plus server-established actor/timestamp; prepare contains expectedPackRoot. It does not contain `trusted:true`.

Each registry descriptor is server-selected from this input's authorized frozen refs, not a path supplied by the runner. Registry download is a bounded UTF-8 JSON record with `kind:"calendar"|"unit_proof"`, `payload` (complete core TradingCalendar or binding), `registryVersion`, `evidenceLevel` and immutable `scope`. SHA commits to exact bytes. The runner verifies those bytes and canonical payload equality/scope before enabling the core's out-of-band trusted option. A caller-supplied proof hash or calendar label cannot enable trust. See FINANCIAL_WORKSPACE_CONTRACT §3 for exact matching. The source and every registry reference are immutable for the lifetime of the task.

## Prepare and deterministic output

The Python consumer validates/revises via the real frozen-package core and prepares via `prepare_package`. It builds the read-model projections below directly from the computed package/result. It does not synthesize values for missing states. Coverage is per state × symbol with the exact bridge semantics. A valid package may prepare into all missing rows; that is a successful diagnostic result with `hasUsableStates:false`, not ready data or a failed transport.

All amounts in event/dependency projections use the preserved Decimal strings. Finite panel floats live only in the panel collection. No full result or complete event tree is stored in D1; D1 holds receipts, metadata and bounded query indexes.

## Bounded publication

`POST /publications/begin`:

```json
{
  "jobId":"<uuid>","leaseToken":"<uuid>",
  "manifest":{
    "format":"atlas.quant.financial-result","version":1,
    "kind":"prepared","inputId":"<uuid>",
    "roots":{"inputRoot":"<64hex>","packRoot":"<64hex>","preparedRoot":"<64hex>","calendarRoot":"<64hex>"},
    "summary":{},
    "collections":{
      "package":{"encoding":"bytes","rowCount":null,"byteLength":1234,"sha256":"<64hex>","chunks":[{"ordinal":0,"startRow":null,"rowCount":null,"byteLength":1234,"sha256":"<64hex>"}]},
      "coverage":{"encoding":"json_records","rowCount":1,"byteLength":500,"chunks":[{"ordinal":0,"startRow":0,"rowCount":1,"byteLength":500,"sha256":"<64hex>"}]}
    }
  }
}
```

`kind` is `validated`, `revised` or `prepared`, matching the task. Manifest ≤256 KiB; encoded `summary` ≤32 KiB. Root placeholders above represent SHA hex. `preparedRoot` is null for validate/revise. `summary` is the exact small DTO described below, not arbitrary extra objects. Unknown keys/collections are rejected.

Publication response: `{publicationId:<uuid>,manifestSha256:<64hex>,status:"staging",missing:[{collection,ordinal}]}`. A job has at most one publication; exact begin retry returns its original identity. A different manifest for that job is `409 PUBLICATION_CONFLICT`. `manifestSha256` hashes the Worker-retained manifest bytes; the returned identity pins all later calls and is distinct from Python financial roots. The runner need not guess JavaScript object serialization.

Every encoded chunk is ≤512 KiB; at most 512 chunks across a result; total bytes ≤64 MiB, including canonical package and all collections. One unsplittable record over 512 KiB fails explicitly with `FINANCIAL_RECORD_BYTE_BUDGET`; it is not truncated. A byte collection can split at arbitrary byte boundaries; concatenation exactly reproduces its declared bytes and overall SHA. JSON-record chunks are independent JSON arrays, nonempty and ≤500 rows, contiguous startRow/ordinal and exact row counts. No gzip/zip, URL/path, executable content or out-of-band fetch is accepted.

Allowed collections:

| Collection | Required | Encoding and meaning |
| --- | --- | --- |
| `package` | all kinds | canonical complete version-1 input package bytes, ≤24 MiB; decoded values/roots match the validated package |
| `panel` | prepared | actual prepared row objects with ts_code, trade_date, selected numeric fields and matching available-date columns |
| `events` | prepared | one core state event per record; `result.dependencies` removed only for transport, replaced with `dependencyStart` and `dependencyCount` at the event wrapper level |
| `dependencies` | prepared | `{eventId,index,dependency}` where dependency is one exact original core dependency dict; contiguous index 0..n−1 per event |
| `assignments` | prepared | exact core assignment objects |
| `coverage` | prepared | flattened actual bridge summary states, with symbol and stateId, no guessed counters |

Events retain `id`, `symbol`, `stateId`, `computedAsOf`, and all other original `result` keys. Removing the dependency array is transport-only: reconstructing the event replaces it in its original result object, removes the two transport keys, and verifies the original event ID and result lineageHash. Empty dependencies use start 0/count 0. An empty collection has zero bytes/rows and no chunks, not a fake empty record. A preparation with panel rows but all missing values still publishes those rows/events.

Validated/revised `summary`:

```json
{
  "input":{
    "source":{"kind":"provider","provider":"TUSHARE_PRO","snapshotRepresentation":"normalized_provider_table_snapshot"},
    "selection":{"symbols":["600690.SH"],"start":"20250101","end":"20251231","announcementStart":"20250101","selectedStateIds":["model_fin_cash_asset_share"],"scope":"consolidated","flowBasis":"ytd"},
    "unitPolicy":"verified_only","counts":{"snapshots":1,"sourceRows":1,"bindings":1},
    "evidence":{"availabilityEvidenceLevel":"vendor_reported_disclosure_dates","originalAsPublishedVerified":false,"revisionTimeVerified":false,"unitAssumptions":false}
  },
  "validation":{"status":"passed","issues":[],"missingPrerequisites":[]}
}
```

Prepared `summary` additionally has `preparation:{formulaVersion,policyVersion,qualityFlags,availabilityEvidenceLevel,originalAsPublishedVerified:false,revisionTimeVerified:false,resourceAccounting}` and `hasUsableStates:boolean`. Collection counts come from verified descriptors, not free text. The prepared task must retain the already committed package identity and input selection; the server rejects replacement roots that differ from its validated input. A trusted runner supplies computed financial roots; source object ownership/registry trust and byte integrity remain independently enforced at the edge.

`PUT /publications/:publicationId/chunks/:collection/:ordinal?manifestSha256=<hex>` sends exact bytes as `application/json`, with Bearer and `X-Financial-Lease`. Every collection is JSON bytes, including fragmented package content; package fragments are not parsed individually. Actual bytes are counted and hashed before accepting the R2 receipt. JSON-record collections are parsed only at bounded chunk size and schema/index checked. Exact retry returns 200 idempotent; different bytes fail. R2 staging keys are server-generated. D1 receipts never claim success before R2 write and receipt/index commit.

`GET /publications/:publicationId?manifestSha256=<hex>` with lease header reports received/missing chunk descriptors. It cannot expose another task's publication. Recovery never requires retransmitting acknowledged chunks.

`POST /complete` is compact:

```json
{"jobId":"<uuid>","leaseToken":"<uuid>","publicationId":"<uuid>","manifestSha256":"<64hex>"}
```

The edge verifies every declared chunk receipt and bounded byte/root/index consistency, record counts, event/dependency links, source identity and active uncancelled lease before one D1 transaction commits publication + input/preparation + terminal job. Incomplete uploads stay staging and return a precise error, never a partial preparation. A completed identical request returns `{ok:true,status:"completed",inputId,preparationId:<uuid-or-null>,idempotent:true}` after lost ACK. A different terminal payload returns conflict. This endpoint never accepts inline `panel`, `result`, `package` or `error` as a shortcut.

Source/package reconstruction streams chunks directly, and browser pages load only bounded indexed records. Internal canonical financial root auditing may be performed by the Python consumer and independent importer; the Worker must not claim it recomputed all Decimal financial semantics or read the entire 64-MiB result. Completion checks are structural/integrity checks, not a substitute for core/audit tests.

## Fail, cancellation and terminal recovery

`POST /fail`:

```json
{"jobId":"<uuid>","leaseToken":"<uuid>","error":{"code":"CALENDAR_REGISTRY_MISMATCH","message":"The frozen calendar does not match its authorized registry revision.","issues":[]}}
```

Error body ≤16 KiB, message ≤700 chars, ≤20 safe structured issues. No raw rows, credentials, tracebacks or arbitrary Python repr. Stable core reason codes are preserved. Identical retry returns the original terminal status; mismatched terminal requests conflict. `code:"CANCELLED"` acknowledges a cancellation request and commits cancelled, without a result. Expired leases become failed with `LEASE_EXPIRED`; a late publication cannot resurrect them. Queued cancellation is immediate; running cancellation stays cancel_requested until acknowledgment/expiry, and all reads remain owner checked.

`GET /jobs/:jobId/status` with the original lease header returns the durable compact terminal receipt for recovery after ambiguous completion/failure. It does not return result bodies or renew the lease. An identical completion acknowledgment can still be recovered after a task deadline if that completion was already atomically committed before expiry. Root should keep its local delivery spool until this receipt confirms completion/cancellation/failure.

Staged objects are not browser-visible. Retention may remove abandoned staging in bounded batches once no active lease or committed reference exists; it must preserve source/preparation evidence referenced by any completed object. No cleanup routine touches old research artifacts.
