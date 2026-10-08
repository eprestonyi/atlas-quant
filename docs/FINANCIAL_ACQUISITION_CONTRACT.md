# Self-service financial acquisition — implementation contract

Revision 4, 2026-10-08. **Local edge/UI and injected-consumer acceptance complete;
not deployed or accepted as a working provider flow.** Initial base `bfe2855`, branch
`feat/quant-financial-acquisition`. No provider request is authorized by this
document itself.

## Product boundary and first profile

A fresh workspace currently has no authorized calendar and cannot upload its
first financial input. Unit declarations and the state catalog require an input,
so those screens cannot resolve the initial block. The new entry is “从 Tushare
创建财务输入”: choose a bounded scope, inspect the exact request/cache plan,
explicitly start acquisition, then open the existing input verification pages.
It creates source evidence, not financial model readiness or alpha.

`annual_statements_2_v1` accepts one or two unique SH/SZ securities, one completed
December-31 annual report, a research date interval of at most one calendar year,
and a declared announcement-history lower bound. Scope is `consolidated`, flow
basis `ytd`, report_type `1`, comp_type `1` (general-company statements). Empty responses and other
company/report types block publication with an explicit reason; there is no
fallback query or hidden expansion.

The approved seven-request cap permits only one exchange-calendar read.
The first implementation requires all selected symbols to share
one exchange and derives SSE for `.SH`, SZSE for `.SZ`. Mixed SH/SZ selection is
rejected with `MULTI_EXCHANGE_PROFILE_UNAVAILABLE`; it must not silently certify
an SSE calendar as SZSE evidence. Existing frozen mixed-exchange packages with reviewed evidence remain valid;
this acquisition profile does not infer that equivalence.

The exact requests are the three registered statement endpoints for each symbol
with `{ts_code,period,report_type:'1',comp_type:'1'}` plus one `trade_cal` request
with `{exchange,start_date:announcementStart,end_date:end}`. Each request includes
an exact, versioned field list. Maximum calls: `symbols.length * 3 + 1 <= 7`.
Each request has one attempt; no pagination, date splitting, retry, field repair
or automatic refresh is allowed. A full/truncated result is blocked rather than
silently treated as complete. A single annual period cannot provide TTM or every
one of the sixteen states; those definitions remain missing where history is
insufficient.

## Public HTTP contract

All routes below use the existing owner session. The independent capability
requires **both** `FINANCIAL_ACQUISITION_ENABLED=true` (default off) and the
existing `TUSHARE_PUBLIC_AUTHORIZED=true`; it never enables provider access by
itself. Existing upload/prepare capability is independent.

`GET /financial/acquisition-capabilities` returns:

```json
{
  "enabled": false,
  "audience": "canary",
  "maintenancePaused": false,
  "testFixtureMode": false,
  "runner": { "online": false, "capability": null },
  "profile": "annual_statements_2_v1",
  "limits": {
    "maxSymbols": 2,
    "sameVenue": true,
    "maxProviderRequests": 7,
    "maxResponseBytes": 4194304,
    "maxTotalBytes": 16777216,
    "maxCalendarDays": 366
  },
  "budget": {
    "utcDay": "2026-10-08",
    "maxActiveJobs": 8,
    "maxNewProviderRequestsPerUtcDay": 60,
    "newProviderRequestsUsed": 0,
    "newProviderRequestsRemaining": 60
  },
  "researchBinding": false,
  "unitVerified": false
}
```

`POST /financial/acquisition-plans` accepts only:

```json
{
  "requestId": "UUID",
  "profile": "annual_statements_2_v1",
  "name": "2024 年报输入",
  "symbols": ["600690.SH"],
  "period": "20241231",
  "start": "20250301",
  "end": "20251231",
  "announcementStart": "20250101",
  "selectedStateIds": ["model_fin_cash_asset_share"]
}
```

Response `{plan}` includes immutable `id,planRoot,version:1,ownerScope:true`, the
canonical selection, requests in deterministic order, and
`budget:{maximumProviderCalls,cachedRequests,newRequests,maximumResponseBytes}`.
Each request contains `requestKey,endpoint,params,fields`, and either
`cache:{status:'frozen',receiptId,sha256,byteLength,retrievedAt}` or
`cache:{status:'missing'}`. There is no provider activity on plan creation.
Reuse of the same owner/requestId with a different body is a 409.

`POST /financial/acquisition-plans/:id/start` accepts
`{requestId,expectedPlanRoot}`. This explicit start returns `{job}` with 202.
Availability and authorization are rechecked. Frozen hits are pinned, not
silently replaced by a newer response. If a planned miss has since acquired a
complete same-key cache receipt, it may reduce calls only after recording that
exact receipt in the job's immutable execution plan; no completed value changes.

`GET /financial/acquisition-plans/:id`, `GET /financial/acquisition-jobs/:id` and
`POST /financial/acquisition-jobs/:id/cancel` are owner-scoped. Job reads return
bounded per-request states and exact terminal errors, never provider credentials
or raw response bodies. `completed` adds `{inputId,calendarRef,inputRoot,packRoot}`
for the existing financial workspace. No `prepared`, `datasetReady` or attach
claim is inferred. Cancel never erases already acquired immutable evidence.

## Independent queue and transport

Use separate `financial_acquisition_plans`, `financial_acquisition_jobs`,
`financial_acquisition_claims`, `financial_acquisition_requests` and
`financial_acquisition_cache` tables. Existing research and `financial-input/v1`
claim queries do not touch these tables, including during rolling upgrades.

The provider-enabled process uses `/runner/financial-acquire/*` and declares
`financial-acquire/v1`; it is not the provider-free financial preparation
consumer. Reuse authentication, bounded byte helpers and exact descriptor
checking, not the original queue tables. One owner task is active at a time.
Task deadline 600 seconds, lease 120 seconds, heartbeat 20 seconds;
the deadline is fixed. All provider calls also have a 30-second deadline.

`POST /heartbeat` supplies no-job `canClaim` only when both flags, maintenance
and a queued acquisition permit it. Existing durable intents always resume.
`POST /claim {requestId,capability,engineVersion}` is atomic and idempotent and
returns `{claim,job}` with lease fields and a private `inputUrl`. Job metadata
contains only the immutable plan and receipt descriptors, not a large package.

Each planned provider request must complete a durable admission handshake:

1. `POST /jobs/:id/requests/:requestKey/begin {leaseToken,attemptId}` records an
   irreversible one-attempt intent before any network call. Retry of that exact
   intent reads its state; it never creates a second attempt.
2. The process writes/fsyncs a local intent, calls the injected provider adapter
   once, and fsyncs the raw response before delivery. An existing intent without
   a durable response is `outcome_unknown`. It cannot automatically call again.
3. `PUT /jobs/:id/requests/:requestKey/receipt` uploads bounded raw bytes, with
   descriptor metadata (`attemptId,sha256,byteLength,retrievedAt,httpStatus,sourceKind`)
   bound to the lease/attempt. Exact retries are safe. Different bytes conflict.
   R2 stores raw bytes; D1 stores descriptors, not numerical payloads.
4. Unknown provider outcomes stop this job with explicit retained partial
   receipt metadata. A user can inspect the failure; an operator must reconcile
   it before the same request key can be attempted again. A new plan does not
   bypass the unresolved intent.

Per-response limit 4 MiB, aggregate 16 MiB, source rows 20,000, calendar at most
366 daily records plus required completeness checks. No preflight or retry call
outside the displayed budget. Cancellation is checked between requests and
before publication; it cannot pretend to undo an already sent request.

## Cache and source identities

Request keys are SHA-256 over one canonical JSON contract:
`{requestVersion,profileVersion,provider,authorizationScope,normalizerVersion,endpoint,params,fields}`.
`authorizationScope` is a server-selected nonsecret dataset entitlement domain,
not a token or a client-supplied trust claim. Canonical object keys and explicitly ordered fields and
explicit profile version prevent unrelated snapshots from colliding.

Responses are immutable, not an overwriteable “latest” cache. Acquisition can
reuse a frozen receipt only inside the allowed entitlement domain and after
rechecking public provider authorization. Every owner gets its own plan/input
and calendar grant. Possession of a requestKey or receiptId grants no read access.
Tests must cover different owners, changed scopes, changed fields, changed
periods, and unresolved attempts. An operator import of existing frozen cache
must preserve its actual request arguments/fields and raw hash; it cannot relabel
a period request as an announcement-window request to manufacture a hit.

The raw receipt hash is a hash of actual provider-response bytes. Normalization
produces a separate `normalized_provider_table_snapshot` identity and retains
the raw-receipt reference; normalization does not restore precision lost by a
JSON/DataFrame parser. The existing Decimal/unit/core contracts remain unchanged.

## Calendar, units and completion

Calendar normalization checks the exact requested exchange/date range, daily
coverage, unique dates, valid `is_open` values and at least one actual session.
It creates the real core `TradingCalendar` and derives `calendarRoot`; the
registered payload is byte/hash-bound to its provider receipt. A synthetic
injected-client result stays explicitly synthetic and cannot self-certify a
production official calendar.

On successful acquisition, the provider process freezes an input package with
**no invented unit evidence** and default `verified_only`. All original vendor
disclosure/revision limitations remain false/unverified. The service creates an
exact **owner-scoped** calendar registry entry, not a wildcard grant; it does not
copy document proofs to other owners or periods. Existing reviewed proofs may
only be attached through their existing exact authorization/matching protocol.
First implementation attaches none by default.

Package publication reuses bounded input upload/storage limits, verifies raw
receipt closure, package byte hash and the calendar evidence root before atomically
exposing an **uploaded**, unvalidated `inputId`. Its declared inputRoot/packRoot are
not promoted to validated financial roots until the existing Python validator
recomputes them. The old financial queue then separately
validates and prepares it. Unit assumptions are only added through the existing
immutable revision UI after the actual inputRoot is known; `verified:false`
survives every derived result. Registry creation and input completion must be
idempotent after an ACK loss.

## File ownership and acceptance

- Edge: `edge/financial-acquisition/`, additive migration 0007, minimal worker
  dispatch. Existing financial/research queues remain unchanged.
- Core/consumer: `engine/atlas_quant/financial_acquisition/` with injected-client
  planning, raw receipts, normalization, spool and separate service. No importing
  provider credentials into `financial_runner`.
- UI: focused source-choice/plan/review/status module beside existing financial
  pages, with progressive loading/errors, retained inputs, keyboard equivalents;
  no enabled “ready” state before real consumer acceptance.
- Tests: true Miniflare ownership/queue/cache/limits/idempotency; pure injected
  provider one-attempt/unknown-ACK and frozen-response tests; full app DOM and
  actual browser acceptance later. No real provider request in this cycle until
  a separately approved exact scope is recorded.
- Source discovery: add an obvious GitHub source link to the new workspace. It
  must not point users to the known stale R2 zip.

Acceptance requires the actual independent consumer to take a reviewed plan,
reuse verified frozen receipts, create owner-specific calendar/input evidence,
and reach the existing financial UI. A fake response or merely enabled capability
is not completion. Public provider use remains default off until this succeeds.

## Provider parameter verification (2026-10-08)

The official [income](https://tushare.pro/document/2?doc_id=33),
[balancesheet](https://tushare.pro/document/2?doc_id=36) and
[cashflow](https://tushare.pro/document/2?doc_id=44) parameter tables each list
`period`, `report_type` and `comp_type` as inputs. Company type 1 is general
industrial/commercial; report type 1 is the latest consolidated report, not
an original-as-published history guarantee. The
[trade_cal contract](https://tushare.pro/document/2?doc_id=26) accepts both SSE and
SZSE. This review made no provider calls.

The existing `TushareClient.call` retries up to three reads and restricts calendars
to SSE. Acquisition must therefore use a separate one-attempt raw-response
adapter with the same reviewed endpoint/field contract, not call that retry
wrapper or change its production research behavior. Unsupported stocks are not
removed from the request and an empty bank response cannot complete the general
company profile. Exact owner grant IDs may reuse frozen calendar bytes; no
wildcard grant is created.

## Exact runner DTO, revision 3

All runner routes below are under `/quant/api/runner/financial-acquire` and require
existing runner bearer authentication. POST and PUT retain `Content-Type:
application/json` even for raw chunk bytes; bytes are never JSON re-encoded.

- `POST /heartbeat`: `{capability:"financial-acquire/v1",engineVersion,state,
jobId?,leaseToken?,phase?}`. No-job response `{ok:true,canClaim:boolean}` is
  advisory and does not create a durable claim. Valid phases are checking_plan,
  fetching_sources, normalizing, writing_evidence. Existing spools resume even
  when canClaim is false. Maintenance key is `financial_acquisition_maintenance`.
- `POST /claim`: `{requestId,capability,engineVersion}`. Response is `{claim:
{requestId,status,jobId},job:null|{id,kind:"financial_acquire",planId,leaseToken,
leaseUntil,deadline,inputUrl}}`. Claims are immutable, including empty results.
- `GET /jobs/:id/input`, header `X-Acquisition-Lease`: `{job:{id,kind,planId},
reviewedPlan,executionPlan,limits}`. The reviewed plan P includes the exact
  cache choices displayed before start. P.planRoot is SHA256 of canonical P
  **without planRoot**. executionPlan spreads P but refreshes requests.cache;
  executionPlanRoot is SHA256 of `{planRoot:P.planRoot,requests:resolvedRequests}`.
  Recompute both roots and each allowlisted request definition. Canonical JSON
  recursively sorts object keys, retains array order, UTF-8, no whitespace.
- limits are `{responseBytes:4194304,totalBytes:16777216,packageBytes:25165824,
calendarBytes:262144,chunkBytes:524288,manifestBytes:131072}`.
- `POST /jobs/:id/requests/:requestKey/begin`: `{leaseToken,attemptId}`. First
  intent returns `{state:"intent",attemptId,maySend:true}`. Exact replay returns
  maySend false; an ACK loss must not cause another provider read. A frozen
  existing result returns `{state:"received",receiptId,maySend:false}`.
- `PUT /jobs/:id/requests/:requestKey/receipt`: raw bytes, lease header and
  `X-Acquisition-Receipt` base64url JSON `{attemptId,sha256,byteLength,httpStatus,
retrievedAt,sourceKind:"provider"|"fixture"}`. Returns `{receiptId,requestKey,
sha256,byteLength,httpStatus,retrievedAt,sourceKind}`. Different metadata/bytes
  conflict. A completed response can still be saved after cancellation was
  requested, but no new intent or output input is allowed.
- `GET` the same receipt URL is plan/key/lease/authorization scoped. It returns
  raw bytes, identity/no-transform, SHA/length and `X-Acquisition-Receipt` metadata
  (the return descriptor above). It cannot grant access by arbitrary cache ID.
- `POST /jobs/:id/requests/:requestKey/unknown`: `{leaseToken,attemptId,reason}`.
  Returns `{state,manualReviewRequired}`; there is no public reset/retry route.
- `POST /jobs/:id/fail`: `{leaseToken,error:{code,message}}`; all unresolved
  intents become outcome_unknown. `GET /jobs/:id/status` with lease returns
  `{job,result}` and is allowed for its terminal state.

Output manifest:

```json
{
  "format": "atlas.quant.financial_acquisition_output",
  "version": 1,
  "jobId": "UUID",
  "executionPlanRoot": "SHA256",
  "package": {
    "sha256": "SHA256",
    "byteLength": 100,
    "chunks": [{ "ordinal": 0, "sha256": "SHA256", "byteLength": 100 }]
  },
  "calendar": {
    "sha256": "SHA256",
    "byteLength": 100,
    "chunks": [{ "ordinal": 0, "sha256": "SHA256", "byteLength": 100 }]
  },
  "calendarRoot": "SHA256",
  "inputRoot": "SHA256",
  "packRoot": "SHA256",
  "sourceReceipts": [
    {
      "requestKey": "SHA256",
      "receiptId": "UUID",
      "sha256": "SHA256",
      "byteLength": 100,
      "normalizedSnapshotId": "optional SHA256"
    }
  ]
}
```

`POST /jobs/:id/publication {leaseToken,manifest}` freezes this manifest and
returns `{manifestSha256,missing:{package:[ordinals],calendar:[ordinals]}}`.
GET `/jobs/:id/publication?manifestSha256=...` resumes; PUT
`/jobs/:id/publication/{package|calendar}/:ordinal?manifestSha256=...` writes
raw bounded parts with the lease header. POST `/jobs/:id/complete
{leaseToken,manifestSha256}` atomically exposes owner calendar and uploaded input,
returning `{job,result:{inputId,calendarRef,inputRoot,packRoot,researchBinding:false}}`.
Complete ACK replay returns the same IDs. R2 content keys are immutable and a
losing concurrent D1 writer never deletes another writer's content.

Fixture calendar references additionally start with `SYNTHETIC_FIXTURE:`; this
prefix is forbidden for provider receipts.

Calendar is the existing registry envelope `{kind:"calendar",registryVersion:1,
payload:<TradingCalendar>,scope:{calendarRoot},evidenceLevel:"provider_reported_calendar"}`.
Production kind is official only in the existing source-kind vocabulary; it is
still a vendor-reported calendar, not an additional independent audit. Reference
is `tushare:trade_cal:<exchange>:<start>:<end>:receipt-sha256:<SHA256>`.
Calendar sessions, coverage and evidence hash are checked against the frozen
raw response, not accepted from a client's trust flag. `sourceReceipts` stays in
input.metadata.acquisition; normalized snapshots retain their original schema.

Only isolated tests with explicit `ALLOW_ACQUISITION_FIXTURES=true` accept fixture
receipts/calendar kind fixture/evidenceLevel
`EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE`. Production refuses fixture
cache reuse even if a request key happens to match. Fixture authorization scope
must be separate; no fixture is a provider acceptance result.

The first UI holds at most 25 task summaries and shows a count of retained raw
receipts. A GET task response reads job and receipt state in one D1 batch to
avoid mixed terminal snapshots. Neither the browser nor D1 holds statement
arrays. Source package validation and state generation remain separate actions.

## Current local acceptance (2026-10-08)

- Twenty-one planner/actual Miniflare D1/R2 tests pass. They cover provider authorization
  false, default-off planning, unknown outcomes across owners/new plans, unchanged
  claim receipts, cancellation, expiry, raw-byte corruption and immutable cache.
  Independent tests cover exact-owner revocation, completed ACK recovery, concurrent
  admission at the last queue/daily slot, unknown outcomes consuming quota, and UTC
  rollover without resetting an unresolved logical request.
- The full Node suite passes 193/193 on main 63ab0e6 plus this implementation. New and
  existing financial DOM tests use real app modules/events with fake API transport;
  they verify input retention, source link, explicit start and no fake ready state.
- The independent Python consumer has completed one **synthetic** actual HTTP
  round trip: four injected source reads, zero provider requests, one retained
  claim/manifest recovery, 3911-byte package downloaded and revalidated with the
  numerical core. Three sessions remain all missing because units are unverified.
  It creates uploaded input only. Source receipt and package identities survived
  retry without new provider intent. Private evidence remains private.
- The actual isolated browser completed plan review, explicit start, immutable
  source acquisition, validation, strict preparation, unit declaration, and new
  preparation. The original input retained zero usable rows out of three; its
  declared revision had two usable rows and one missing row. The declarations
  remained unverified. Screenshots and actual D1/R2 readback are retained privately;
  28 R2 objects and all source/cache/chunk descriptors passed 61 hash/length checks.
  This browser session preceded the final admission-budget patch, which is covered
  by independent Miniflare/DOM tests. It is synthetic, not provider acceptance.
- Keyboard-only and narrow-screen acquisition acceptance, final production
  canary admission, and a production provider request remain unverified. No
  acquisition capability is enabled by a tracked production config; no deployment
  is included.

Run local checks with `node --test tests/financial-acquisition*.test.mjs` and
`node web/tests/financial-acquisition-dom.mjs`. `node
scripts/financial-acquisition-preview.mjs` starts a loopback-only, explicit-fixture
service without credentials. It writes a mode-0600 bootstrap to private/ and
accepts only an independently configured injected consumer for source acquisition.
The consumer implementation and its dedicated service are developed separately.

## Admission and global budgets (revision 4)

Production admission is explicit: `FINANCIAL_ACQUISITION_AUDIENCE` is `canary`
by default or explicitly `public`. Unknown values fail closed. Canary accepts
only exact UUIDs in `FINANCIAL_ACQUISITION_CANARY_OWNERS` (a private JSON array,
maximum 64, default empty = nobody). Public ignores this list but keeps every
other gate. No wildcard sentinel exists. Both require the feature flag, existing
Tushare public authorization, and the stable nonsecret entitlement scope. Each
job rechecks its current owner/scope/flags before cache reads and new publication;
revocation does not prevent retaining an already-issued raw response or recovering
an exact completed ACK.

`FINANCIAL_ACQUISITION_MAX_ACTIVE` defaults to 8 (range 1–32). The same
INSERT…SELECT that creates a job counts all queued/running/cancel_requested jobs
across owners; the existing one-active-job-per-owner constraint also remains.
`FINANCIAL_ACQUISITION_MAX_DAILY_REQUESTS` defaults to 60 (range 1–1000). The first
atomic provider intent counts all scopes and all intent states for that UTC day.
It inserts the exact captured UTC timestamp and compares the same day's window
in that statement. Unknown/failed/cancelled calls are not refunded. Cached bytes,
known-intent recovery, receipt delivery and completed ACK retries consume no new
intent. A quota rejection writes no request row and cannot return maySend=true.
Old unresolved keys remain sticky across midnight; resetting the daily budget
never resets an unknown result.

Invalid numeric settings deny new admission. `/financial/acquisition-capabilities`
returns current-owner availability, audience (never the private owner list),
maintenancePaused, and `{utcDay,maxActiveJobs,maxNewProviderRequestsPerUtcDay,
newProviderRequestsUsed,newProviderRequestsRemaining}` under budget. Counts are
advisory until the atomic admission. The independent consumer treats rejection
before a new intent as an unsent request, not an unknown provider outcome.

Routine drain uses `meta.financial_acquisition_maintenance='paused'`: it blocks
new start and claim but allows busy work and receipt recovery to finish. Clearing
the feature flag is a security revocation, **not** the normal drain procedure.
The loopback-only fixture preview explicitly uses public audience so fresh test
browser workspaces can exercise the flow; this is not a production binding.
