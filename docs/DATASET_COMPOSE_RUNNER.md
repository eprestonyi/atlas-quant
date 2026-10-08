# Provider-free dataset composition consumer

This is the isolated `research-dataset/1` consumer for
`financial_snapshot_view_50_v1`. It reads existing owner-authorized immutable
sources, restores the entire original market snapshot, derives the explicitly
selected scope, re-prepares the frozen financial packages, and publishes one
complete `atlas.quant.research_dataset/2` closure. It neither fetches providers
nor fits a prediction model. Financial execution remains disabled.

The public numerical contract is `SNAPSHOT_MARKET_VIEW_CONTRACT.md`. The HTTP
keys and ceilings are fixed in `contracts/hosted-datasets-v1.json`; the edge
implementation owns `HOSTED_DATASET_RUNNER_PROTOCOL.md`. The input's `planRoot`
is the server's opaque authorization-plan commitment. The consumer separately
checks every source descriptor, original document/chunk identity, exact registry
bytes, and financial roots. A plan digest alone never authorizes supplied proof.

## Entry and private state

Run `python -m atlas_quant.dataset_runner --config /absolute/private/config.json`.
The CLI requires a dedicated regular configuration file with mode 0600 or more
restrictive, HTTPS `api_base`, `runner_secret`, `dataset_enabled:true`, absolute
`delivery_dir` (the separate research root), and `dataset_delivery_dir`.
`financial_delivery_dir` and `acquisition_delivery_dir` may be supplied only to
check that their private roots do not overlap. `poll_seconds` is 3–60, default10.
`compute_lock_path` optionally selects the shared host compute slot; production
must configure the same private lock for research, preparation, and composition.
Unknown configuration fields, including `provider_access`, are rejected.

`dataset_delivery_dir` is independent of the other queues. Files use AES-GCM
with separate dataset key and AAD domains, 0700 directories, 0600 files, atomic
rename and fsync. The output child receives only frozen source bytes, the
original job identity, the local spool key/context, and optional lock path. It
receives no provider configuration or queue Bearer secret.
Frozen child inputs are separately encrypted with a bounded index before spawn;
only a small context crosses the process bootstrap pipe. A child import failure
therefore cannot block the parent while it sends a large source payload. Input
bytes, metadata, and the local index share the 64 MiB admission budget.

## Fixed limits and recovery

The job has a fixed 600-second deadline, a confirmed lease of at most120seconds,
and a20-second heartbeat cadence. Source input, lock waiting, composition,
verification, and delivery share that deadline. Transient heartbeat transport
failures may retry only within the last confirmed lease; they cannot extend it.
Cancellation, invalid lease, stop signals, or expiry terminate and join the child.
The operating system releases its compute lock on exit.

The complete profile admits at most50symbols,110000market rows,8financial inputs,
32components,256parts of at most512KiB, and64MiB including the manifest. The
original snapshot must itself be at most24MiB before any scope reduction. All
source/registry counts and advertised bytes are checked before payload reads;
registry descriptors are paginated64at a time, with256KiB per entry and32MiB total.
The registry must contain exactly the required external calendar/proof pins.

A durable UUID is saved before claiming. Idle maintenance or empty queues do not
create unbounded empty claims. Pending claims resume regardless of new-claim
availability. Input metadata is frozen locally before source reads; a changed
plan on recovery is rejected. Read-only source downloads can resume before the
child starts, but computation is at most once per durable claim:

- Before spawning, the state is durably `computing`.
- Each output part is separately encrypted and immutable. The fully verified
  canonical manifest is written last as the completion marker.
- A crash with a complete manifest resumes delivery of those exact bytes.
- A crash without that marker becomes `RUNNER_INTERRUPTED`, without recomputing.
- Lost begin, part, or complete responses retain the original job, lease, root,
  and publication identity. Missing parts are checked against the frozen graph.
- Files are removed only after same-lease terminal readback agrees with the
  original claim and, for success, the exact dataset root. Corrupt private
  evidence or conflicting identities stop progress and preserve the spool.

## Verification boundaries

The public generator `scripts/make-snapshot-view-fixture.py --output <new-dir>`
uses checked-in synthetic test sources and locked development dependencies. It
requires no private paths, credentials, network, or fit. Its empty forecast
artifact is explicitly a `SYNTHETIC_TRANSPORT_FIXTURE_NO_FIT`, suitable for
transport admission tests; it is not evidence that a forecast ran.

Tests cover exact full-source reconstruction, registry pre-admission, wrong-job
URLs, hashes/lengths/compression/redirects, encrypted domain isolation, real spawn
composition, shared-slot expiry, stop before spawn, original-claim recovery,
missing/duplicate output parts, and ACK-loss delivery without recomputation.
A local HTTP acceptance is separate evidence from these tests. This code alone
does not establish hosted readiness, production installation, or a successful F
research run. The source archive must accompany any future financial report;
a numerical result bundle alone does not prove financial source completeness.

The same generator's explicit `--long-scope` option prepares the fixed 2024
synthetic weekday calendar (262 sessions). It retains 85 initially missing TTM
observations instead of manufacturing earlier financial availability. Other
states, such as operating margin, have262observed values from the fixed earlier
synthetic disclosure. Generating this source still performs no F fit and gives
no indication of predictive advantage.

A local unreleased Worker acceptance on2026-10-08 completed composition through
the actual Python consumer and D1/R2. The HTTP-downloaded archive contained8parts,
16coverage entries and694272bytes. Offline recomposition from independently pinned
synthetic source bytes plus the owner-specific calendar grant reproduced every
part and the manifest. The standalone closure auditor passed47147checks. Its
separate limitations remain: it does not authenticate PDFs or independently
recalculate financial formulas; the core recomposition performed the numerical
comparison. No provider acquisition or prediction fit occurred. The first private
harness attempt failed during spawn import; its durable claim was settled without
recomputation, and a regression now covers bootstrap failure before unpickling.
