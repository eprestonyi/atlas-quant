# v0.11 factor research release

This release expands factor research. It does not enable strategy execution or
claim that a fitted candidate has a validated trading advantage.

## User flow

Statistical quantitative trading → factor research → Easy or Studio. The Easy
flow retains screening, one of five mechanisms, train/test and rolling windows,
factor input, and a model-first report. Include/exclude filters and the pool
directory support direct industry/index/theme search. Invalid mechanism inputs
are rejected before fitting.

The report leads with an actual saved nonbaseline research function and numeric
parameters, even when the separately identified selection winner is no-change.
Training fit, held-out predictions, contemporaneous exposure, future-change IC,
joint distributions, model comparison and frozen data sources remain separate.
Editing coefficients creates an unvalidated derived function; it does not
inherit the original validation result.

## Model protocol

[The fixed model-search protocol](FACTOR_MODEL_SEARCH.md) declares 22 candidates,
pooled or per-asset parameters, penalized joint and factorwise basis construction,
training-only preprocessing, chronological inner selection and outer evaluation.
The six reserve candidates are declared and budgeted before any results, and are
actually trained. AI review receives development evidence only. It cannot add
executable code, alter the candidate budget, or optimize on terminal outcomes.

The configured Codex reviewer uses gpt-6.1-sol at high, then max only when its
completed review requests revision, with at most two calls per research. Each
ephemeral app-server session verifies empty execution environments, disabled
agents/MCP capabilities and absence of loaded instruction files before receiving
the review input. Unexpected tool activity fails the review. Receipts distinguish
the verified configuration and observed events from a complete tool inventory.
Unknown calls are not retried. A failed reviewer leaves an explicit receipt and
the deterministic numerical selector remains available.

## Market inputs

China's official index sources retain their identities. SW industry definitions
cover all three levels; a classification entry is not evidence of observations.
US GICS classifications and issuer ETF mappings distinguish the classification,
the proxy instrument and the price-data provider.

Yahoo Finance through pinned yfinance is an additional provider. Its aliases
use `ext_ctx_yf_`; old Tushare ETF aliases are not retargeted. The input uses
Adjusted Close for returns and retains the original Close, dividends, splits,
volume, retrieval clock and response hashes. It does not invent traded amount.
US observations are available to the China grid only on a later calendar date,
with an explicit maximum staleness. The new source protocol is
`named-market-history/3`; older runners cannot reserve or recover these jobs.

Each ETF becomes available only after its own bounded history probe. The public
probe catalog lists the exact sampled interval and row count, not an assurance
of all historical periods or continuous service. Market records and research
snapshots stay owner-private and are excluded from the public source archive.
The adapter's software license does not grant redistribution rights to Yahoo
data. Free alternative providers need their own credentials, contracts and
source identities; no silent provider substitution is implemented.

## Release sequence and acceptance

1. Freeze a clean source commit and exact Worker/source archives. Require both
   push and pull-request Linux CI on that commit, including Python, API, DOM,
   build and complete synthetic reproduction checks.
2. Preserve the original Worker/settings, D1 catalog rows, six service configs,
   packages, shared environment and locks. Prepare the new packages, dependency
   environment and research-service configuration without altering live services.
3. After a fresh empty-queue check, pause only the previously active queues,
   stop all six services, hold their existing service/compute locks and exchange
   packages and the shared environment. Retain the old bytes at every step.
4. Publish the exact Worker with settings unchanged, apply the independently
   prepared catalog delta, start all six services and verify their new process
   identities, package/dependency hashes and explicit capabilities. Restore only
   the original active queues. Keep unrelated research feature flags closed.
5. Run one separately identified synthetic production acceptance research;
   verify actual candidate functions, bounded AI receipts, full archive download,
   function evaluation, derived-version persistence and owner isolation. Inspect
   the deployed screening and report UI on desktop and a narrow viewport.
6. Publish the exact tracked source archive and release tag. Verify full public
   download bytes. Raw market data, private evidence and credentials are excluded.

All mutations have durable intent and readback. An unknown result requires
inspection, never blind replay. Old user studies, provider probes and completed
F runs are immutable and are not rerun for release acceptance. Local tests,
actual provider probes, frozen-data model fitting and production acceptance are
reported as distinct evidence.
