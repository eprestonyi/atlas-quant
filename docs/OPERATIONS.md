# Hosted research service

The public site combines the embedded browser app, a Cloudflare Worker with D1/R2 and one trusted Python host polling over HTTPS. The server runs restricted research specifications, not contributed Python. Browser Python executes separately through Pyodide.

## Deployment outline

1. Install `engine/requirements.lock.txt` with Python 3.12 in a private runtime directory outside a checkout. On macOS, use Application Support for a persistent LaunchAgent.
2. Create a dedicated D1 database and bind it as `DB`; create a private R2 bucket bound as `ARTIFACTS`. Fresh installations apply `edge/schema.sql`; existing v0.1 installations also apply the idempotent SQL in `edge/migrations/`. Preserve existing workspaces, jobs and results.
3. Populate `data_fields`, registry metadata and `research_universes` using `registryStatements()` from `scripts/seed-registry.mjs` against the intended database. The checked-in data catalogs contain metadata and members, not a populated PCD fact matrix. Schema creation alone does not populate the catalog. `scripts/dev.mjs` seeds its local database automatically.
4. Run `npm ci`, `npm test`, `npm run check`, and `npm run build`. Deploy `dist/worker.mjs` as an ES module Worker with the required D1/R2 bindings and a randomly generated `RUNNER_SECRET`. Set `SOURCE_URL` to the public source repository. Production deployment configuration is operator-specific; this repository's local preview command is not a production deployment command.
5. For the operator's authorized hosted Tushare service, set `TUSHARE_PUBLIC_AUTHORIZED=true` and configure provider access on the private runner. Other deployments must determine their own provider authorization; a working token does not itself establish multiuser redistribution rights. Capability state is read back from `/quant/api/health` and `/session`.
6. Configure the real Workers AI binding described below if offering AI review. Rule review and browser Python do not require this binding.
7. Route `/quant/*` to the Worker. Atlas uses a service binding and the additive `edge/portal-entry.mjs` adapter, which imports the existing Portal module. Preserve the fresh live module, other bindings, settings, exports and assets. Compare readback hashes before declaring the integration complete.
8. Configure an hourly Worker schedule for cleanup. It removes expired rate buckets and batches of eligible raw uploads from terminal jobs older than 30 days. Reports, code and strategies remain separate records.
9. Save the private runner config below outside the checkout as an absolute, non-symlink file with mode `0600`. Start `python -m atlas_quant.runner --config /private/path/config.json` with `PYTHONPATH` pointing to the engine directory. Configure restart-on-failure and host startup appropriately.
10. Read back build version, capabilities and heartbeat, then submit and retrieve a complete experiment in a fresh workspace. Test an actual AI request separately when enabled. A configured binding or online heartbeat alone does not prove provider execution, correct computation or persistence.

```json
{
  "api_base": "https://your-host.example/quant/api",
  "runner_secret": "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS",
  "job_timeout": 900,
  "poll_seconds": 8,
  "delivery_dir": "/private/atlas-quant/delivery",
  "cache_dir": "/private/atlas-quant/cache",
  "allowed_proxy_hosts": ["your-host.example"]
}
```

For an authorized provider proxy, add `provider_access` with `proxyUrl` and a separate `serviceToken`. For authorized PCD reads, add `pcd_access` with `url` and `token`; the client accepts only its registered fixed HTTPS host and read methods. Credentials are injected only into trusted job execution, never queue specifications, browser code, logs, reports or Git. Direct local research can use `TUSHARE_TOKEN` instead. Do not relax host allowlists to accept arbitrary browser-supplied URLs.

## Real AI review

The edge calls `env.AI.run` with the registered model `@cf/qwen/qwen2.5-coder-32b-instruct`. A Workers AI binding named `AI` is required. In the deployment's Wrangler TOML this is:

```toml
[ai]
binding = "AI"
```

The equivalent JSON setting is `"ai": {"binding": "AI"}`. Binding configuration and account usage follow [Cloudflare's official Workers AI documentation](https://developers.cloudflare.com/workers-ai/configuration/bindings/). No model API key belongs in browser source.

The UI requests AI explicitly; code and limited strategy context are sent to the configured provider. Manual mode runs local server rules without a provider call. Missing binding returns 503; provider failure returns a sanitized 502. Successful transport returns provider/model/hash evidence, while malformed model output can still yield no useful patches. Apply changes only through user confirmation; AI review never executes Python or claims a backtest ran.

The service limits AI requests to 20 per workspace per day and 500 globally per day. These are request bounds, not an exact spending cap or a guarantee about provider availability. Test fixtures mock the transport while exercising the actual HTTP handler; they do not prove production AI execution. Verify one authorized real request separately.

## Failure and recovery

The runner encrypts completed results before delivery, retries the same payload idempotently, and drains pending deliveries before claiming new jobs. Keep its private delivery directory and runner secret stable across restarts. Do not delete a pending spool merely to clear an error. Rejected oversized or invalid results become small sanitized failures under the same lease; authentication and network failures remain retryable. Cancellation or an expired/interrupted lease cannot resurrect a terminal job.

Before sending a claim, the v0.4 runner durably writes an encrypted UUID intent to `delivery/claims/current.enc`. A lost response retries that UUID across process restarts; the Worker resolves it to the same job and unexpired lease. Before any input acquisition or computation, the intent changes to `executing`. On restart, a saved result is delivered first; an `executing` intent without a result is resolved under its original lease and ends as `RUNNER_INTERRUPTED`, without automatically repeating provider calls or computation. A pending claim that has not entered execution may safely resume its recovered job. Only a matching authenticated UUID receipt indicating an empty queue or the original job's terminal state clears the intent. Completion delivery obtains that terminal receipt before removing local recovery files. Protocol mismatch, corrupt intent or an unknown response preserves recovery state and prevents a new UUID.

The D1 `runner_claims` table retains at most one receipt per claimed job. Receipts have no automatic TTL: deleting a receipt while an offline runner still holds its UUID would permit that UUID to claim another job. Historical job/receipt archival needs an explicit protocol for retiring request IDs; it is not implemented by this release. Do not delete active receipts or local intents to unstick a queue.

Jobs default to 900 seconds, bounded at 30–900. Acquisition of frozen replay inputs is limited to 60 seconds and charged to the same job budget; queue reads also have a total deadline in addition to idle timeouts. Input envelopes are at most 26 MiB, and datasets/results each at most 24 MiB. Replay downloads its two immutable objects separately, with a bounded combined child-process envelope of 52 MiB. Compact UTF-8 JSON is used both for size validation and HTTP transmission. The host has one computation slot. Submissions have per-workspace and global queue bounds. Historical v0.2 timing measurements are not v0.4 performance or public latency promises.

The deployment uses a single trusted host. Shutdown, sleep, network loss or logout can interrupt availability; this is not an independently verified high-availability service. Before an engine update, drain active work and pending deliveries, update the Quant runtime and restart only that process. Preserve unrelated Atlas/News/PCD services. Portal rollback should use a fresh saved deployment snapshot and preserve storage.

## v0.4 compatible upgrade

1. Save the fresh deployed Worker modules/settings and runtime version, then read active jobs and local pending deliveries. Do not reconstruct a rollback from an old repository snapshot.
2. Apply the idempotent migration files as applicable: `0002_studio.sql`, `0003_statistical_quant.sql` and `0004_runner_claims.sql`. Verify the seven `quant_*` tables, the `runner_claims` receipt table, their keys and indexes, and existing workspace/job counts. `CREATE TABLE IF NOT EXISTS` does not repair incompatible experimental table definitions; production must start from the documented prior schema.
3. Set the D1 `meta` key `runner_maintenance` to `paused` (including its required `updated_at` timestamp), then deploy the v0.4 Worker with the same D1/R2 bindings. The v0.4 claim transaction checks this gate atomically. Existing job heartbeats, snapshot uploads and completion acknowledgements remain accepted. The prior v0.3 Worker does not implement this gate, so read back the new deployed bytes before treating claims as paused. Separately, version negotiation prevents pre-0.4 runners from claiming schema-2 jobs during normal operation.
4. Read the active queue again and drain the old runtime's current job and pending encrypted deliveries. Stop only the Quant runner, recheck the queue/spool after stopping it, replace its engine from the verified candidate, check imports/version and restart while the gate remains paused. Preserve the exact `api_base`, `runner_secret` and `delivery_dir`: the secret derives the encryption key and the API base forms authenticated associated data. Preserve a recoverable runtime backup. Check actual process state, candidate file hashes and unchanged private configuration before removing the maintenance key.
5. After resuming claims, execute and read back a real forecast-only job, its complete artifact, and a separate execution referencing that artifact. Check `predictionRefitPerformed:false`, artifact identity, source/data hashes and the entire cash ledger. Verify other Atlas services were preserved. Queued submissions made during the short maintenance window must resume normally.

Deploy the claim-receipt-compatible Worker before the new runner. Old v0.3 runners may still omit `requestId`; the Worker accepts that legacy path, without the new recovery guarantees. A new runner refuses a server response without its matching UUID receipt and preserves the unresolved intent. Maintenance blocks new mappings but permits recovery of existing mappings and terminal receipts, so pending delivery can drain.

Do not deploy a v0.3 Worker while a v0.4 runtime still needs claim receipts, `/runner/snapshot` or `/runner/replay`. A rollback must first stop new schema-2 submissions and drain task, delivery and claim-intent queues, or retain the compatible transport layer while rolling back the UI. Additive data stays in place; do not delete new tables or reports to simulate rollback.

Successful schema-2 research stores two distinct encrypted local delivery objects: a completion and its referenced input snapshot in `delivery/snapshots/`. The snapshot uploads first, then completion indexes its corresponding forecast. Lost acknowledgements retry the exact bytes and lease without computing again. Remove local objects only after an acknowledged terminal response; preserve them on identity conflicts or authentication failures. An empty snapshots directory is not pending work. Orphan `.tmp`/encrypted files require inspection, never blind deletion during an upgrade.

Complete artifacts and frozen inputs reside in private R2 under owner scope. D1 manifests keep compact diagnostics; full arrays remain in R2. Reads verify actual object SHA-256 against the stored manifest. A digest mismatch fails explicitly and must not be repaired by silently fetching today's provider data. Full result objects remain capped at 24 MiB; oversized experiments fail rather than silently truncate forecasts, control forecasts or the ledger.

For a safe operator restart, inspect `delivery/*.enc`, `delivery/*.tmp`, `delivery/snapshots/*` and `delivery/claims/*` as well as active queue work and claim receipts. Do not print their decrypted contents. The runner's `runner.lock` and empty snapshots/claims directories alone do not block a clean restart. A `.tmp` intent is not an acknowledged claim; inspect it together with `current.enc` and server receipts rather than deleting it during upgrade.

## v0.5 bundle upgrade

Apply additive `0005_artifact_bundles.sql` before deploying the v0.5 Worker: even the compatible legacy claim path queries the bundle tables. Preserve the existing database and all R2 objects. Pause new claims through `runner_maintenance`, drain running jobs and pending deliveries, deploy and read back the Worker, then upgrade the Quant runner while the gate remains paused. Resume only after checking source hashes, unchanged private configuration and actual process state.

The new runner writes authenticated encrypted per-file spool objects. A child returns a small spool handle; delivery uses the saved original manifest and chunk bytes. The 300-second total delivery budget is separate from the 900-second computation budget. Interrupted or uncertain acknowledgements retain recoverable bytes; do not delete a pending spool during upgrade. Inspect all delivery children, including bundle directories, not only the older completion/snapshot files.

Once any bundle is committed, draining jobs alone does **not** make a v0.4 Worker rollback compatible: that Worker cannot read bundle-backed reports or serve frozen bundle replay. Retain the v0.5 bundle read/replay layer when rolling back UI or computation, or restore a separately verified compatible Worker. Never remove committed data or overwrite it with a legacy report to make a rollback appear successful.

The scheduled cleanup removes only abandoned staging/verified/aborted uploads whose jobs are failed/cancelled and older than 30 days. It verifies the manifest and deletes every deterministic chunk key, including an R2 write whose D1 receipt transaction failed. Committed references block cleanup. User report and forecast JSON downloads stream the full logical document; local CLI bundle directories remain the available manifest/chunk export format.

## Local full stack

After building, run `node scripts/dev.mjs`; in another terminal run `.venv/bin/python scripts/dev-runner.py`. Preview at `http://127.0.0.1:8895/quant/`. Both use a fixed public development secret and loopback HTTP. Never expose the preview server to the Internet. Browser development state is ephemeral for the lifetime of Miniflare.

The development runner uses a 600-second job budget; the production runner and CLI default to 900 seconds. Local preview seeds the field and universe metadata. It does not automatically configure a paid AI binding, populate PCD observations or grant Tushare permissions. Synthetic research and uploads work through the local runner; provider calls require the operator's own environment/configuration. Browser Pyodide downloads its pinned runtime from the fixed CDN and has a separate package environment from the hosted engine.
