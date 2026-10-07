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

Jobs default to 900 seconds, bounded at 30–900; input envelopes are at most 26 MiB and datasets/results each at most 24 MiB. The host has one computation slot. Submissions have per-workspace and global queue bounds. A 50-symbol, 8-year, 32-factor synthetic experiment completed locally in about 289 seconds, but that measurement is not a public latency promise.

The deployment uses a single trusted host. Shutdown, sleep, network loss or logout can interrupt availability; this is not an independently verified high-availability service. Before an engine update, drain active work and pending deliveries, update the Quant runtime and restart only that process. Preserve unrelated Atlas/News/PCD services. Portal rollback should use a fresh saved deployment snapshot and preserve storage.

## Local full stack

After building, run `node scripts/dev.mjs`; in another terminal run `.venv/bin/python scripts/dev-runner.py`. Preview at `http://127.0.0.1:8895/quant/`. Both use a fixed public development secret and loopback HTTP. Never expose the preview server to the Internet. Browser development state is ephemeral for the lifetime of Miniflare.

The development runner uses a 600-second job budget; the production runner and CLI default to 900 seconds. Local preview seeds the field and universe metadata. It does not automatically configure a paid AI binding, populate PCD observations or grant Tushare permissions. Synthetic research and uploads work through the local runner; provider calls require the operator's own environment/configuration. Browser Pyodide downloads its pinned runtime from the fixed CDN and has a separate package environment from the hosted engine.
