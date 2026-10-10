# v0.13 independent asset return research

## Contract and scope

New Easy/Studio research uses [asset-return-study/1](ASSET_RETURN_CONTRACT.md).
The selected collection shares factor definitions; every security has its own
fitted parameters, selection record, function and complete panel. Users choose
simple returns or origin-known volatility-scaled returns. Current-information
forecasts, matched-period associations and future-factor scenarios are explicit
separate uses. Only selected factors enter the model. No equal-weight price
aggregate or hidden own-price feature is inserted.

Portable F/4 has one output. Every source binding, editor, evaluator, archive
and report page retains the asset identity, response unit and factor timing.
Legacy saved price/basket studies keep their dual-output protocol and hashes.
New research cannot execute trades; it is admitted only by runners advertising
`returnStudyFormats: ["asset-return-study/1"]`.

## Acceptance

Synthetic tests cover independent assets, sparse calendars, response intervals,
training-only transforms, origin-known scales, future perturbation and leakage
rejection. Actual fitted scalar functions are compared against Python and JS
evaluation across all nine estimator families, both study modes and response
units. Zero-return selection remains visible alongside fitted research candidates.

Two predeclared new studies reuse the retained seven-stock baijiu snapshot:
matched-period simple returns with market/industry inputs, and next-session
volatility-scaled returns with the six originally selected factors. There are
no provider or AI requests. The previously observed historical test is not
fresh prospective evidence. Independent standard-library arithmetic checks the
complete raw-field panel, response scaling and per-security descriptive OLS;
archive integrity, actual browser behavior and production acceptance are
recorded separately. Failed envelope/order checks and original artifacts remain
preserved; their corrections only reassemble frozen values, without refitting.

## Deployment sequence

This release needs no D1 migration, dependency upgrade, configuration or queue
policy change. Pin the clean candidate commit, Worker bytes and engine tree;
require successful Linux push and PR checks for that exact commit. Fresh-read
the live v0.12 Worker/settings, six native services, configuration/plist bytes,
shared environment and empty job queues before any change.

Pause only the three active queues using a guarded update. Preserve the paused
dataset, dataset-graph and market-acquisition queues. Stop six services, prove
process absence and acquire their private service/compute locks. Exchange the
staged engine packages atomically, retaining the v0.12 packages. Deploy the
pinned Worker, start and verify all six processes and the research runner's
explicit new capability, then restore the original three active queues.

Use unique durable mutation intents and read back uncertain outcomes. Do not
replay prior release scripts, provider requests or completed model fits. Retain
at least 500 MiB free disk and every source/evidence archive. Perform bounded
authenticated production acceptance, verify downloads and publish only tracked
source plus synthetic fixtures. Private provider data and credentials remain
excluded from the public archive.

The complete Quant project, strategy research, execution and monitoring remain
separate work. Neither a high association R² nor a passing software test proves
a tradable forecast edge.
