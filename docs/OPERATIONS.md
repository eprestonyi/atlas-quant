# Hosted research service

The public site has three pieces: the embedded browser app, a Cloudflare Worker with D1/R2, and one trusted Python host polling the queue over HTTPS. The Python host executes only the restricted research specification, never arbitrary contributed code.

## Deployment outline

1. Install the pinned Python requirements in a private runtime directory outside a checkout. On macOS, use Application Support for a persistent LaunchAgent.
2. Create a dedicated D1 database, apply `edge/schema.sql`, and bind it as `DB`. Create a private R2 bucket and bind it as `ARTIFACTS`.
3. Run `npm ci && npm run build`; deploy `dist/worker.mjs` as an ES module Worker. Bind a randomly generated `RUNNER_SECRET`, and set `TUSHARE_PUBLIC_AUTHORIZED=false` unless the data provider has authorized the intended multiuser service. `SOURCE_URL` is the public source repository URL.
4. Route `/quant/*` to that Worker. Atlas uses a service binding and the small `edge/portal-entry.mjs` adapter. That adapter imports the existing Portal module; it is not a replacement Portal application. Preserve the fresh live module, all existing bindings, settings, exports and assets. Compare readback hashes before declaring the integration complete.
5. Configure an hourly Worker schedule for its cleanup handler. It removes expired rate buckets and batches of eligible raw uploads from terminal jobs older than 30 days. Reports and strategies remain separate records.
6. Save the runner configuration below as an absolute, non-symlink file with mode `0600`. Start `python -m atlas_quant.runner --config /private/path/config.json` with `PYTHONPATH` pointing to the engine directory. Configure the process manager to restart failures and start on host login/boot as appropriate.
7. Verify `/quant/api/health`, then submit and read back a completed experiment through a fresh browser workspace. An online heartbeat alone does not prove calculation or persistence.

```json
{
  "api_base": "https://your-host.example/quant/api",
  "runner_secret": "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS",
  "job_timeout": 600,
  "poll_seconds": 8,
  "delivery_dir": "/private/atlas-quant/delivery",
  "cache_dir": "/private/atlas-quant/cache",
  "allowed_proxy_hosts": ["your-host.example"]
}
```

For an authorized private provider proxy, add `provider_access` with `proxyUrl` and a separate `serviceToken`. These credentials are injected only into the trusted runner process for Tushare jobs. They must not appear in queue specifications, browser code, logs, reports, or Git. Direct local research can instead use the `TUSHARE_TOKEN` environment variable.

## Failure and recovery

The runner encrypts completed results before attempting delivery, retries the same payload idempotently, and drains pending deliveries before claiming new jobs. Keep its private delivery directory and runner secret stable across restarts. Do not delete a pending spool merely to clear an error. A rejected oversized/invalid result becomes a small sanitized failure for the same lease. Authentication and network failures remain retryable.

The host has one computation slot. Public submissions are bounded per browser workspace and by a global queue limit. The current deployment uses a Mac mini; its shutdown, sleep, network loss, or logout can interrupt research availability. This is a public beta deployment, not an independently proven high-availability service.

For a code update, first wait for active work and pending delivery to drain, update the engine, and restart only the Quant process. Keep existing Atlas/News/PCD services untouched. For Portal rollback, use the saved original deployment and preserve data storage rather than re-uploading an old local checkout.

## Local full stack

Run `node scripts/dev.mjs` after building; in another terminal run `.venv/bin/python scripts/dev-runner.py`. Both use a fixed, public development secret and loopback HTTP. Never expose that development server to the Internet. Browser development state is ephemeral for the lifetime of Miniflare.
