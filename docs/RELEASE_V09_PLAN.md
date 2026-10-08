# v0.9 candidate release plan

Status: **not deployed**. This is the release plan for the factor-first rebuild,
not a production acceptance record. The last accepted production release is
[v0.8](RELEASE_V08.md). All new production dataset, market-research and capacity
flags remain closed until the corresponding gates below are complete.

## Product and evidence gates

The current product contract is [FACTOR_RESEARCH_REFRAME.md](FACTOR_RESEARCH_REFRAME.md):
mode → statistical quant → factor research, with separate future strategy,
execution and monitoring workspaces. The universe page freezes the complete
filtered set; dates, data and horizon belong to research settings. A page of
members is not a sample. The report publishes a conditional function and its
frozen evidence; numerical edits create unvalidated derived functions.

| Gate | Current evidence | Remaining release condition |
|---|---|---|
| Complete filter and Easy flow | Actual local browser saved 1,000 members, then 12 after explicit filters; no subset picker | New production build and authenticated browser readback |
| Automatic selection | Eight predeclared candidates, date-ordered nested validation, state-only comparator and preserved tails | Exact final commit CI; no claim of unbiased or universally optimal selection |
| Portable F and diagnostics | Native function download agrees with Python evaluation; joint, marginal and conditional tables checked | Same behavior on deployed assets and independent owner workspace |
| Market full-pool transport | The declared correction run passed actual loopback HTTP for all 1,000 securities: 41,000 main and baseline predictions each, 190 input pairs, and two frozen archives. Independent paired source/result audit passed. The first rejected run remains preserved separately. Final reply resource checks and fresh progress-clock observation passed no-fit regression checks | Exact release candidate CI and authenticated deployed readback; this synthetic run proves transport and coverage, not provider authenticity or predictive advantage |
| Existing financial dataset/2 | Actual browser composition, auto research and native dual downloads passed for one synthetic security | Candidate runtime and authenticated hosted readback using frozen input |
| Financial dataset/3 | Supervised 50-security F and full-domain audit passed. Separate routes, claims, publication and actual local browser composition/research passed with one synthetic security. Native paired archives passed 16,231 result and 93,797 source checks; portable F agrees with Python. The original monitor failure remains retained | Exact release candidate CI, service installation and authenticated deployed readback; neither the original 50-security run nor completed browser correction is repeated |
| Source authenticity | Exact receipt, source and authorized registry identities retained | Synthetic acceptance never substitutes for provider or disclosure authentication; reuse previously frozen real evidence for canary |

The graph dataset/3 and financial bundle/2 hosted interface is implemented and
accepted in an isolated local HTTP/browser workflow; its production gates remain
closed. No serialized `modelAdmissionRegistered` flag grants authority.
A fresh same-process restoration must recompute financial inputs
from retained source and compare the full logical data before fitting.
Publishing the general workbench does not implicitly publish graph admission.
An unavailable profile must remain unavailable in the capability response/UI.

Five mechanism cards do not imply five full-pool compute profiles. The accepted
1,000-security auto profile currently applies to asset-price mean reversion.
Trend auto at that size, basket/pair sources, event PIT sources and financial
scope projection require their own profiles and acceptance. New acquisition
must check whether the requested mechanism can consume its result before making
provider requests; receipt reconciliation and existing source downloads remain
available even when a new computation is unavailable.

The first 1,000-security F's manifest and 17 received chunks survived its rejection,
but the old runner discarded its local result spool after acknowledging failure.
That evidence is incomplete and cannot be presented as a reusable full model
archive. Rejected deliveries must now retain encrypted original results and source
references outside the automatic retry queue. A later correction acceptance is a
new declared run, never a rewrite of that failed job or another source acquisition.
That single correction run completed as `b3b1c435-fb7e-454c-a852-d4344c914507`;
its declared compute allowance is exhausted. Neither it nor the source acquisition
may be repeated for UI checks. The result preserves 35,000 mature observations,
6,000 tail observations and zero trades. The selected model was the no-change
baseline: no forecast advantage was validated. The actual browser resolved the
frozen function, evaluated explicit inputs and downloaded its JSON; independent
Python evaluation agreed. Its complete 1,000-member scope remains available in
a bounded expandable list, with actual 390px layout verification.

## Six services, five compute consumers

| Persistent service | Role | Provider configuration | Shared compute slot |
|---|---|---|---|
| Research runner | Existing research plus explicitly admitted frozen dataset/market F | Preserve existing configuration; new frozen-source paths do not fetch providers | Required for new dataset/market computation |
| Financial preparation | Validate and prepare financial inputs | None | Same canonical private path |
| Financial acquisition | Existing v0.8 durable requests and receipts | Existing authorized configuration | Network acquisition is not model computation |
| Dataset composition | New independent dataset queue and encrypted recovery directory | None | Same canonical private path |
| Graph dataset composition | Independent dataset/3 queue, exact graph capability and encrypted recovery directory | None | Same canonical private path |
| Market acquisition | New independent requests, receipts and market normalization | Authorized market configuration only | Normalization uses the same canonical private path |

The services share the existing runner authentication model; separate processes
and spool namespaces do not imply independent security credentials. Preserve
the exact API base, secret and existing spool locations. New services get
separate private configuration, delivery directories and process identities.
All five compute consumers must use the **same explicit canonical private
lock path**. No parallel worker may bypass it to improve a benchmark.

Graph composition runs as `python -m atlas_quant.graph_dataset_runner`, requires
explicit `graph_dataset_enabled:true` and a separate absolute
`graph_dataset_delivery_dir`, and declares only `research-dataset-graph/1` for
that queue. Its pending publication cannot block the legacy dataset consumer.
The research service separately enables the exact graph F capability tuple;
neither the graph composition flag nor the legacy dataset capability enables it.
This topology is a deployment plan, not proof of installed services.

On macOS retain Standard process scheduling (omit `ProcessType`), bounded
numerical threads and restart behavior. The dataset, graph dataset and market services are
additions to the three v0.8 services. The earlier four-service v0.9 installation
plan is therefore incomplete and must not be replayed unchanged.

## Ordered deployment and recovery

1. Freeze an exact candidate commit, version, source archive, Worker bytes and
   Python tree. Run fresh Linux CI and the applicable actual transport/browser
   gates against this candidate. Older successful CI is not a later commit's
   acceptance. Keep every failed run and capacity boundary.
2. Fresh-read the deployed Worker modules/settings, D1 schema and queue state,
   all current service identities/configuration hashes and encrypted pending
   deliveries. Snapshot them privately. Pause new claims using the matching
   queue maintenance protocols and drain active work without discarding spools.
3. Export D1 and verify restoration/integrity, original table schemas and row
   counts. Apply only missing additive migrations in order: `0008` datasets,
   `0009` full filter scopes, `0010` derived functions, `0011` market acquisition.
   Read back each result. If a request outcome is unknown, read schema and
   migration state before attempting another mutation. Never apply fresh
   `schema.sql` over production or delete tables for rollback.
4. Deploy compatible Worker readers/receipts with new feature flags closed.
   Read the actual deployed bytes and bindings back. Install the exact private
   runtime, preserve old configuration and archives, then add the three new
   services. Keep `ALLOW_MARKET_FIXTURES` absent/false in production. The local
   preview enables fixtures explicitly and is not a production configuration.
5. Verify the shared slot, private paths, actual process identities and fresh
   exact transport/profile capabilities. Resume the existing queues only after
   compatible readback. Opening a new profile requires its own canary gate;
   heartbeat availability alone does not establish successful computation.
6. Reuse retained real frozen sources for authenticated hosted canary and
   independent downloads. Do not repeat the four v0.8 provider requests, old
   financial F jobs or an uncertain registration. A new research run needs a
   predeclared new purpose and durable identity. Read-only compatibility checks
   consume existing reports instead of fitting again.
7. Open only accepted capabilities, verify new-owner browser flow, old reports,
   native full archives and source downloads, then publish the exact source
   archive/tag. Record the final public download hash and runtime/Worker hashes
   in a separate release acceptance document.

Keep the 500 MiB free-disk floor, source/expanded-document guards, 900-second
research deadline, per-fit/resource monitors and the 256 MiB result-bundle
ceiling. A capacity failure is a failure of that declared profile; no silent
sampling, omitted diagnostics/forecast tails, raised legacy guard or automatic
provider/F replay is an acceptable recovery.

If deployment must roll back, stop new admissions and drain work first. Retain
readers for every already committed format and claim receipt. Restore a
compatible saved Worker/runtime, not an old incompatible binary. Additive
storage, source evidence, derived functions and user reports remain retained.
