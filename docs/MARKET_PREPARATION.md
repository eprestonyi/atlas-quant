# Whole-filter market preparation (candidate; all production flags off)

This slice implements the independent `market-acquire/1` queue and consumer. It does not enable hosted pooled research or increase the legacy research limits. A complete filtered SH/SZ scope is prepared as one immutable market dataset; there is no stock truncation. BJ scopes are rejected in full. Current membership is not historical constituent membership.

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

This commit's tests use explicit synthetic responses, real spawned child/fsync/AES primitives and isolated Miniflare D1/R2. They perform zero provider calls and do not establish live data entitlement, production enablement or a hosted whole-pool forecast. The ready-market F consumer, server-bound larger bundle admission and independent source-archive audit are separate follow-up integration gates.
