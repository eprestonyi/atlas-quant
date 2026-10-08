# Whole-filter market preparation (candidate; all production flags off)

This slice implements the independent `market-acquire/1` queue, frozen-source consumer and explicitly gated pooled research path. Production flags remain off; legacy research limits are unchanged. A complete filtered SH/SZ scope is prepared as one immutable market dataset; there is no stock truncation. BJ scopes are rejected in full. Current membership is not historical constituent membership.

The profile `pooled_asset_1000_v1` admits at most 1,000 securities, 366 natural days, 300,000 calendar-grid rows, 3,002 predeclared provider requests, 512 MiB raw receipts and 128 MiB normalized output. Each stock requires `daily` and `adj_factor`; requested daily-basic columns add `daily_basic`. Each represented venue has a separate `trade_cal` request. Both calendars must agree exactly. The initial normalized source supports later registered computation profiles without another provider read; the data profile does not silently choose a model.

## HTTP contract

The machine contract is `contracts/market-dataset-v1.json`. Scope/plan APIs are documented in `FILTER_UNIVERSE_ADMISSION.md`.

- `POST /market-preparation-plans/:id/start {requestId,planRoot}` creates one durable job. Same identity recovers the original result; changed payload conflicts.
- `GET /market-preparation-jobs/:id` and `POST .../cancel` are owner isolated. Completion returns `job.result.marketDatasetRef` and its exact `universeScopeRef`.
- Plan and job readback include `researchAdmissions` and `preferredResearchAdmission`. Availability requires the feature flag, a fresh research-runner heartbeat and explicit profile/transport capabilities; an available profile still needs source/configuration admission at submission. The candidate auto profile permits only mean-reversion asset-price, forecast-only, at most 16 factors, 2×2 folds and refit at least 20 sessions. Ridge permits mean reversion or trend. Other combinations are not implicitly downgraded.
- Runner routes are under `/runner/market-acquire`: `claim`, `heartbeat`, `/jobs/:id/input`, request `begin`/`receipt`/`unknown`, publication/chunk/complete, status and fail. They require the existing runner bearer secret plus exact job lease for content access. Raw routes use `X-Acquisition-Lease`; receipt PUT metadata uses base64url `X-Acquisition-Receipt`.

## One provider attempt and durable recovery

`python -m atlas_quant.market_acquisition --config /private/config.json` is an explicitly enabled, separate service. Configuration includes `market_acquisition_enabled:true`, `authorization_scope`, `market_delivery_dir`, optional `compute_lock_path`, and the authorized token or fixed private `provider_access`. The ordinary research and financial-preparation services remain provider-free for this route. Production CLI refuses fixture sources. No default configuration enables this service.

Every request is fixed in the immutable plan, including its authorization scope, exact endpoint/parameters/fields, response limit and maximum one actual attempt. Raw bodies are the exact bytes delivered by the configured endpoint. The portal proxy may have decoded and reserialized upstream Tushare JSON, so these are not asserted to be original upstream wire bytes.

The consumer saves an encrypted intent before `begin`; it saves `calling` before spawning the single bounded HTTP request. Response bytes are fsynced before normalization. A lost begin acknowledgement or a `calling` state without durable bytes becomes sticky manual review, never an automatic provider retry. Raw-receipt PUT and publication may be retried with exactly the saved bytes. Completion requires a matching terminal claim receipt before local cleanup. Failed/unknown evidence is retained. Separate AES/AAD namespaces prevent cross-service or cross-authorization spool reuse.

Provider attempts are serial, spaced at least one second, with a 30-second request deadline and a fixed 7,200-second job deadline. Leases are 120 seconds, renewed every 20 seconds; transient control failures cannot extend the original deadline. The server limits active jobs to one per owner/two globally and new request intents to 6,004 per UTC day. Already authorized cached receipts are scoped by request/authorization identity; fixture receipts remain forbidden in production, including cache hits.

## Normalization and publication

The adapter verifies explicit response fields, finite numeric data, symbol/date identity, duplicate rows and exact official calendar coverage. Missing sessions remain absent; missing daily-basic observations remain null. An entirely empty security or a missing adjustment factor rejects the complete dataset. OHLC use the existing provider convention `raw * adj_factor / first observed adj_factor per symbol`; raw close, amount, volume and factor remain observed fields. Volume is hands and amount is CNY thousands, matching the existing adapter policy.

The output manifest contains separate `rows`, `provenance` and `receipts` collections: at most 320 chunks, each at most 512 KiB; the manifest is at most 256 KiB. Rows are date/security ordered. D1 stores descriptors and identities; price rows remain in private R2. Completion rechecks all hashes, complete symbol coverage, raw calendar receipts, source metadata and the exact plan; it independently derives a normalized row-value root. Dataset creation, completed job state and committed publication are one D1 transaction. A cancelled or expired lease cannot publish.

This commit's tests use explicit synthetic responses, real spawned child/fsync/AES primitives and isolated Miniflare D1/R2. They perform zero provider calls and do not establish live data entitlement, production enablement or a hosted whole-pool forecast. A two-security synthetic source-to-F HTTP acceptance now covers the complete queue, frozen source, pooled child, publication and paired archives. The separate 1,000-security full transport acceptance is still pending; component benchmarks are not that evidence.

## Saved source and research admission

The integration saves `marketDatasetBinding` on every immutable experiment revision, including full scope and explicit computation profile. GET/list/export/copy retain this record; omitted fields on an update inherit it. Scope/date changes must match another explicitly supplied ready source, and financial/market sources cannot be combined. A lost concurrent update cannot add its source to the winning revision, even when strategy bytes are equal.

`GET /market-datasets` and its root-pinned detail route allow the same owner to reopen ready sources across browsers. Research jobs use `dataSource:ready_market`; old runners skip them. Matching runners must declare an exact `marketResearchProfiles` value and bundle/1 capability. The source input route `/runner/research-markets/:jobId/input` supplies bounded content-addressed manifest/plan/scope/part descriptors only to the active exact lease. Legacy upload, unsharded completion and execution-replay paths reject this source.

The API/source-binding slice is verified with synthetic D1/R2 data. It remains disabled by default; enabling the flag is not part of these commits.

## Exact source closure and download

The new, not-yet-deployed market dataset format includes `rawArchive` alongside
`rows`, `receipts` and `provenance`. It retains the exact bytes delivered by the
configured authorized endpoint. When that endpoint is the private portal proxy,
these are **not** asserted to be the original upstream Tushare wire bytes.

Raw responses remain whole and are concatenated without separators into at most
256 chunks of at most 4 MiB, with a 512 MiB aggregate limit. Each ordered receipt
has `rawLocation: {ordinal, offset, byteLength}`. Offsets must cover every chunk
exactly, with no gaps, overlap or unreferenced bytes. Every slice must match its
immutable, owner-authorized D1 receipt SHA and length. Normalized collections
keep their separate 128 MiB / 320 chunks / 512 KiB limits. The manifest remains
256 KiB and contains chunk descriptors rather than thousands of raw descriptors.
Only the raw publication PUT route accepts 4 MiB; other API limits are unchanged.

Complete reads all receipt metadata in one D1 query. Its maximum R2 read count
is 320 normalized chunks + 256 raw chunks + 2 calendar responses = 578, plus a
fixed number of D1 operations. The source download uses at most 576 R2 reads,
with one chunk in memory at a time and downstream cancellation propagation.

`GET /quant/api/market-datasets/:datasetId/download?datasetRoot=:exactRoot`
returns a private deterministic `atlas-market-<datasetId>.tar`, with exact
`manifest.json`, `plan.json`, `scope.json` and every normalized/raw part. It is
owner isolated and bound to the already committed root; downloading does not
fetch market data or run a model. It uses identity transfer with no-transform.

`MarketSourceReader(manifest_bytes, plan_bytes, scope_bytes, read_part,
expected_root=...)` provides the pure Python source path. `verify_integrity()`
recomputes all normalized prices, first-observed adjustment anchors, nullable
basic fields, calendars and missing sessions from the retained response bytes.
It does not grant provider authority. Public manifest/plan/scope properties are
copies; they cannot alter the internal pinned descriptors.

The independent standard-library CLI does not import the provider or research
engine:

```sh
python scripts/audit-market-dataset.py source.tar --expected-root <datasetRoot>
```

It verifies the exact source closure and independently recalculates normalization.
A PASS means integrity and reconstruction succeeded; it does not verify vendor
licensing, original wire bytes behind a proxy, or historical index membership.
A research result still requires its separate result bundle plus this source
archive for an independent full source check. The hosted consumer and server-bound numerical admission preserve the old bundle/1 byte format. No production readiness is inferred from source integrity alone.


## Frozen-source pooled research

The ordinary research runner only claims this route when explicitly configured
with `market_dataset_research_enabled:true` and a valid `compute_lock_path`.
Research, financial preparation, dataset composition and market normalization
must use the same private lock path in the deployment configuration. A claim
must advertise an exact registered `marketResearchProfiles` entry. No provider
or PCD credentials are placed in the market child; that child reconstructs
normalization from all frozen raw receipts before fitting one pooled model.
Encrypted source parts are written separately, so only a small per-lease
reference crosses spawn IPC. An interrupted calculation is not silently rerun;
a fully committed local manifest resumes delivery, while incomplete computation
becomes a retained terminal error. Local inputs are deleted only after the exact
terminal receipt is confirmed.

The parent enforces the 900-second total computation deadline, a 3 GiB RSS
ceiling, 300 seconds per fit and a 400 MiB temporary feature/sample cache. This
includes waiting for the shared compute lock. Memory-monitor failures stop a
still-running child. Neither supported estimator nor dates are substituted on
failure. Result transport keeps the existing 256 MiB / 256 chunk / 8 MiB hard
chunk budget. Registered server-side `ready_market` admission allows at most
80,000 forecast rows and 300,000 frozen input rows; a runner-supplied profile
cannot unlock these counts. Every snapshot row must match the source's server
row-value root. Source identity lives in
`report.provenance.marketSource`, independent of the currently edited draft.

A full source audit and result audit can be bound together:

```sh
python scripts/audit-market-dataset.py market-source.tar \
  --expected-root <HTTP-pinned-datasetRoot> \
  --result-bundle forecast-bundle.tar --output new-audit.json
```

This checks every original response slice, independently rebuilds normalized
rows, runs the existing numerical result auditor and compares every frozen
snapshot field and missing value. It authenticates content, not owner identity,
a vendor license, catalog membership or the server's private authorization.
The JS row-value digest is checked for internal agreement; complete numerical
row comparison is the independent cross-language proof, not a claim of identical
Python/JS floating-point JSON serialization.

## Reproducible loopback acceptance (zero provider)

The public harness contains explicit synthetic numeric responses and invented
security codes. Weekday sessions are fixture sessions, not an official exchange
calendar. `ALLOW_MARKET_FIXTURES=true` is confined to the isolated loopback Worker;
production continues to reject fixture receipts, including cache hits.

```sh
node scripts/preview-market.mjs --port 8938 --symbols 1000 --estimator auto \
  --output private/market-http-1000-auto
PYTHONPATH=engine .venv/bin/python scripts/market-http-acceptance.py \
  private/market-http-1000-auto/session.json --phase source
PYTHONPATH=engine .venv/bin/python scripts/market-http-acceptance.py \
  private/market-http-1000-auto/session.json --phase forecast
```

Source and research IDs are persisted separately; rerunning the harness reads a
completed result rather than fitting again. The 2,001 synthetic request attempts
retain the production one-second serial scheduling guard, approximately 33
minutes before publication. This measures durable scheduling and transport, not
real Tushare latency or availability. Session credentials remain in a 0600 file;
no installed service or production configuration is read. Use a new output path
for each predeclared independent case. Downloaded archives and failures remain
in that directory for independent auditing.
