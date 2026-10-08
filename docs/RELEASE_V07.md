# v0.7 candidate — auditable financial workspace

Status: **initial production deployment verified; hosted preparation and registry-read correction acceptance in progress**.
This is an incremental release of the ongoing Atlas Quant rebuild, not the
completion of financial-to-F research or the whole community platform.

Four independent pages accept an immutable financial package, validate its
registered calendar/proofs, create explicit unit/reporting-basis revisions,
and prepare the sixteen actual core financial states. Prepared results retain
missing rows and expose coverage, event dependencies and private downloads.
User-declared units remain unverified. A dedicated financial consumer and queue
keep these operations separate from forecasting and execution.

## Candidate checks

After merging the accepted v0.6 release, financial dataset bridge and capacity
core, the candidate passed 867 Python tests, 167 Node tests, seven frontend/DOM
commands, formula-definition parity and the JavaScript check. Candidate build:
`0.7.0-a83bd2c1fcbe`, 1,490,381 bytes, SHA-256
`1fb3c6d63dea3eee404d5a9ca57f4bd1aa7ab940d7f5640f7549044ccd2dcffe`.

Actual local HTTP and the production Python consumer completed validation,
all-missing preparation, explicit unit revision and the revised preparation.
The consumer used real subprocesses, leases, encrypted recovery and chunk
publication. Independent readback from local D1/R2 and seventeen HTTP GETs
recomputed 304 checks for the strict input and 309 for the revised input. Six
missing rows became five valid rows and one missing row under the explicit
synthetic assumptions; an independent Decimal calculation confirmed 2/10=0.2.
There were no provider requests or F-model fits in this acceptance.

Actual browser interactions covered file upload, validation, unit edits,
immutable revision, formula selection, preparation and dependency inspection.
At an observed 390-pixel viewport, the page did not overflow horizontally; its
tables retained their own horizontal scrolling. A native event download
contained both complete dependencies and passed its original event and lineage
hashes. Dark-theme progress and active-step contrast defects found during
visual review were fixed and re-inspected. A localhost cookie collision between
different preview ports was isolated using `financial.localhost`; production
authentication was not changed.

Independent negative cases reject missing coverage/panel/assignment records,
wrong event references, inconsistent usable-state flags and changed dependency
order. A true TCP download test found that automatic gzip could hide a
truncated JSON attachment. Fixed-length package streams now explicitly retain
identity encoding/no-transform; the corrupted R2 case fails the download.

Applying migration 0006 twice to the exact published v0.6 schema preserved all
seeded old tables, rows, claim receipts and R2 report bytes. New and old queues
cannot claim or finalize each other's tasks. These are isolated migration tests,
not proof that a production migration has already occurred.

Idle heartbeats now expose read-only queue availability. An idle consumer does not
create new durable empty claims; existing recovery intents ignore that advisory
and retain their original identity. Registry metadata cannot overwrite proof
identity fields.

## Release boundaries

The production flag remains off until the new schema, consumer, private
registry scope and hosted readback are verified. `researchBinding` remains
false. Financial preparation does not attach a component to a forecast, run an
execution, authenticate historical as-published revisions, or establish alpha.
A unified dataset reference and its typed evidence archive are the next stage.

Public research remains limited to fifty securities. The merged capacity core
and sorted-snapshot optimization remain behind their existing default-off
admission gates. No new 300-stock benchmark, historical model fit or paid data
acquisition is part of this release.

Implementation and reproducible developer workflow:
[FINANCIAL_WORKSPACE_IMPLEMENTATION.md](FINANCIAL_WORKSPACE_IMPLEMENTATION.md).

## Initial hosted acceptance and correction

PR #11 merged as `35266330750016bf435b6e1b0db452b485b82532`. The initial
Worker `0.7.0-a83bd2c1fcbe` and both Python 0.7.0 consumers were read back;
queues resumed, prior research remains readable and the original research
configuration was preserved. An additive migration created eight financial
tables after a 93,236,050-byte private database backup passed SQLite restore
and integrity checks.

The existing two-company provider package completed hosted validation, with
957 independent D1/R2/source/proof checks. No provider acquisition or model fit
was repeated. Validation took about 178 seconds: repeated all-proof lookups
made each proof download do quadratic registry work. The correction batches
metadata lookups in groups of 64 and authorizes each body by its own reference,
retaining owner, active state, kind, length and SHA checks. Its hosted preparation
acceptance remains pending. New-user calendar onboarding and financial forecast
attachment are separate unfinished work.

The registry correction passed all 171 Node tests and static checks. Candidate
`0.7.0-44af664ce6dd`, 1,491,290 bytes, SHA-256
`a9e7bd8758973d3a8859847d1fe456a4cc2eb7cdf956baed1e8ca5789375cf9f`.
The Python engine and frontend are unchanged from the accepted initial release.
An unrelated cancellation test counted tiny chunks legitimately read before
Node-to-workerd cancellation arrived. The test now gates the first storage read
and cancels inside workerd against the native FixedLengthStream; it precisely
asserts one in-flight read without sleeps or a relaxed threshold. The production
archive implementation was not changed.
