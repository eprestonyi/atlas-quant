# Hosted dataset implementation checkpoint

This branch implements the owner-scoped dataset/2 control plane and UI. Both
`RESEARCH_DATASETS_ENABLED` and `FINANCIAL_DATASET_RESEARCH_ENABLED` default off.
No provider was called and nothing was deployed by this change.

## Implemented

- Explicit original forecast snapshot selection and range derivation, exact
  financial preparation references and server-derived calendar grants.
- Dedicated research-dataset/1 claim/lease/publication table and endpoints. The
  legacy runner cannot claim this queue. Fixed 600-second wall time includes
  transport; leases cannot extend it.
- Bounded manifests, source/registry reads and staged parts; one D1 JSON join
  checks all grant descriptors without per-grant network round trips.
- Atomic ready publication with full byte hashes, coverage coordinates and source
  pins. The Worker does not recompute Decimal formulas: trusted composition and
  the subsequent F child independently restore/recompose the typed closure.
- Owner-only deterministic USTAR download, exact dataset references on each saved
  experiment version, and ready_dataset admission restricted to asset-price,
  fundamental/Ridge, forecast-only. The saved version has a D1 binding, not only
  browser-local state.
- Four distinct source/scope/financial/review pages, actual task polling, paged
  coverage, explicit errors and retained choices. Financial report UI uses bounded
  pages, exposes two separately named required archives, and blocks execution.

## Evidence completed at this checkpoint

- `node --test tests/hosted-dataset-control.test.mjs tests/hosted-dataset-publication.test.mjs`:
  14 actual Miniflare D1/R2 tests, including corrupted part, cancellation,
  internally rehashed missing coverage, false counts, malformed registry closure,
  exact ACK recovery, saved-version bindings and cross-owner denial.
- A true local TCP archive download with a removed middle R2 piece fails instead
  of ending as an apparently successful truncated response. Valid archive members
  are read back and hashed individually.
- `node --test tests/statistical-quant.test.mjs`: 20 existing regressions pass.
- `node web/tests/dataset-workspace-dom.mjs` and
  `node web/tests/sharded-report-dom.mjs`: real DOM events with explicit HTTP
  doubles. Covers four pages, loading states, idempotent retry, input retention,
  source binding, bounded financial report pages and separate source archives.
  These tests are not browser visual acceptance.

The checked-in dataset fixture is real offline core output from explicitly
synthetic market/statement inputs. Its original transport has no model fit and
empty forecast rows, explicitly marked as such. Its six-session scope cannot
satisfy model-training requirements and must not be treated as a forecast result.

## Actual consumer and HTTP integration

Actual dedicated consumer → HTTP source delivery → core compose → publication
and private archive restore passed on isolated port 8932. The short fixture
produced 8 parts / 16 coverage rows. Its 694,272-byte HTTP archive was recomposed
byte-for-byte and passed 47,147 independent stdlib checks. The first harness
failure before computation is separately retained, not counted as success.

The original temporary instance was quiescently exported before replacement:
all D1 SQLite snapshots pass integrity checks, all 12 R2 objects were retained,
and 11 D1 content descriptors match their actual bytes. The next instance uses
an additional explicit 262-session synthetic source to test meaningful sample
requirements; the six-session fixture is never used to justify model admission.

Typed financial result codec dispatch is integrated. The parent integration
passed all 217 Node tests and 190 focused Python tests. A real provider-free
composition consumer prepared the 262-session source. The separate real research
runner restored/recomposed that dataset inside the F child and published 41
forecasts plus 41 baseline forecasts, with 35 mature dates and zero trades.
The fixed fundamental/Ridge/operating-margin configuration reported
`NO_VALIDATED_FORECAST_EDGE`; it was chosen before the run for transport acceptance,
not selected for return. This is synthetic computational evidence, not market alpha.

The result and dataset archives were downloaded through actual owner-scoped HTTP
and passed 880 and 101,637 independent stdlib checks respectively. A separate
read-only real-HTTP/DOM test visited all six report chapters and details, issued
17 report requests, retained the immutable 50,729-byte summary without full rows,
and streamed the 173,991-byte complete private report. It confirmed both required
archive links and absence of financial execution controls. Its JSON download
SHA-256 was `0e0ad4710fb315d977cc9d83aff3e50fc850892549908a6b6fafaa1730622b0b`.
No API doubles, provider requests or new computations were involved in this readback.

The parent separately used the actual in-app browser to complete source → explicit
2024 scope → prepared financial input → compose → dataset coverage on a new
browser workspace. That workspace's generated dataset is distinct from the HTTP
consumer owner's dataset. Model binding/reload and narrow-screen visual acceptance
remain separate browser gates until their outcomes are recorded. DOM tests are
not represented as visual acceptance.

A cached offline heartbeat found during browser testing was repaired with an
explicit “刷新节点状态” action. It reads only capabilities, preserving the name,
scope, selected preparations, reviewed plan and both idempotency request IDs.
A real DOM regression covers offline → online and an unknown start response
followed by refresh and retry with the same ID. Dataset creation drafts currently
survive in-app navigation, but are not persisted across a full page reload.
Saved research versions and completed datasets are durably server-side.

## Local preview

`node scripts/hosted-dataset-preview.mjs` serves an isolated Miniflare instance at
`http://dataset.localhost:8932/quant/#quant/studio/datasets/source`. Dependencies
must already be installed; the script never installs them or calls a provider.
It writes its private owner/session configuration under ignored `private/`.
A browser owner may be granted only this explicit synthetic fixture through the
private file control described by the generated config; there is no public seed
route. The source fixture generator and optional model fixture live in the
separately integrated Python dataset consumer change.

The optional `DATASET_FIXTURE_DIRECTORY` points to an offline generated synthetic
fixture. `FINANCIAL_DATASET_RESEARCH_ENABLED=true` enables only this local
instance's F admission for integration; neither variable changes any cloud
binding. The helper assigns a unique object prefix and exact owner grant per
source, and verifies the explicit fixture calendar label before insertion.

The preview uses persistent ignored D1/R2 storage. `DATASET_PREVIEW_RESUME=true`
reuses its original private owner/session configuration, while each private seed
operation obtains fresh Miniflare D1/R2 bindings after hot reload. The host repair
was preceded by a quiescent SQLite backup plus immutable R2 blob copy: all 29
objects and 27 D1 descriptors were checked, then the original cookie, completed
F job, bundle ID and dataset root were re-read successfully through HTTP. It did
not fabricate a fresh-stub export after a poisoned binding failed. No private
session, raw financial input or licensed data is included in the public fixture.

## v0.9 release and rollback checklist (plan, not deployment evidence)

This checklist is based on the current source. It does not authorize provider
requests, a new model run, production writes, or an unrecorded retry. Record each
actual release SHA/build ID, timestamp and readback separately from local evidence.

### Before changing production

1. Pin the integrated Worker/UI, dataset consumer, research runner, codecs and
   audit CLI to the reviewed release. Preserve the previous Worker version and
   bindings, service configurations, encrypted spool directories and shared
   compute-lock configuration. Check free disk and retain a verified D1 export
   plus the R2 object descriptors needed to restore existing records; never print
   session cookies, runner secrets or provider credentials.
2. Apply the existing migration chain through `edge/migrations/0008_hosted_datasets.sql`
   **before routing traffic to the new Worker**, even while both flags remain off.
   New ordinary research claim queries reference `quant_run_datasets`; feature
   flags do not remove that schema dependency. Migration 0008 is additive and
   idempotent, with ten new tables and their indexes; it does not rewrite previous
   jobs. Verify table/index definitions, foreign-key checks and preserved old row
   counts. Keep the separate 0006-only migration test and current-Worker 0007/0008
   tests distinct. Do not treat an unverified migration command exit as readback.
3. Preserve both flags absent or exactly `false` initially:
   `RESEARCH_DATASETS_ENABLED` and `FINANCIAL_DATASET_RESEARCH_ENABLED`.
   Only the exact string `true` enables them. Composition can be opened separately;
   financial research requires **both**. There is no dataset canary-owner allowlist
   in this implementation: enabling a flag exposes that capability to every
   otherwise eligible authenticated workspace. Do not describe it as canary-only.
4. Verify the installed provider-free composition service advertises
   `research-dataset/1`, uses its own queue and encrypted recovery spool, and shares
   the same compute lock as the research service. Its fixed deadline is 600 seconds,
   lease 120 seconds and heartbeat 20 seconds; none should be extended to hide a
   failed acceptance. Verify the research service's financial mode is explicitly
   configured and that its heartbeat advertises all three exact capabilities:
   `atlas.quant.research_dataset/2`, `financial_json_v1`, and
   `atlas.quant.financial_bundle/1`. A fresh heartbeat is an availability signal,
   not proof of successful computation. Old research runners must not acquire a
   dataset-linked job, and neither old research nor financial-prepare/acquisition
   consumers may acquire a dataset composition job.

### After deployment, before enabling new work

- Authenticated `GET /quant/api/dataset-capabilities` must report `enabled:false`
  and `researchBindingEnabled:false`; UI must show the closed boundary without a
  fake ready state. Existing ordinary research lists, reports, source downloads,
  financial input workspaces and their owner isolation must still work. Confirm
  unknown capabilities or missing migrations do not silently downgrade the new
  source to an ordinary upload.
- Read existing completed test identities through their actual owner session.
  Record expected source bundle/snapshot hashes, dataset root, exact source scope,
  financial preparation roots and registry grants. A different workspace must not
  read them or gain access by copying hashes. Reusing data means exact owner grants,
  not wildcard proof access or regenerated source values.
- Confirm the browser serves the pinned new assets, four preparation pages,
  explicit subset boundaries, real loading/error states, Chinese state names,
  and the node-status refresh that preserves both request IDs. Verify saved
  research reload restores the D1 dataset binding and the complete selectable
  state catalog, while its scope and fundamental/Ridge/asset-price profile remain
  fixed and execution remains disabled. Full-page reload of an unfinished compose
  draft is currently not supported; state preservation is limited to in-app
  navigation until the plan is saved server-side.
- Any newly authorized acceptance must be separately bounded and logged. Reuse
  complete frozen sources; do not request a provider or rerun F merely to check a
  deployment. A ready dataset establishes validated composition and byte closure,
  not sufficient training coverage, verified units, historical as-published data,
  or predictive advantage. Before opening research, the integrated synthetic
  consumer → same-child restore/F → financial publication evidence must remain
  reproducible with unchanged thresholds and negative outcomes retained.

### Staged enablement and acceptance

Open composition first only after its consumer and recovery path are healthy.
Check actual queued/running/completed transitions, coverage including missing
states, fixed deadline, private archive hashes and source ownership. Then enable
financial research only after the research runner declares the full protocol set.
A completed F must store `atlas.quant.financial_bundle/1`, exact dataset/2
`sourceEvidence`, zero executions, and the same saved experiment-version binding.
Report pages must stay bounded, retain all forecast outcomes and expose **both**
the financial result archive and the full dataset closure archive. Downloaded
bytes must pass the format-dispatched independent audit and source reconstruction;
old bundle/1 readers must reject the new format rather than reinterpret it.
Record desktop and actual verified-width narrow-screen outcomes independently of
DOM tests. Publication, authenticated readback, browser operation and service
continuity are separate acceptance entries, not interchangeable success labels.

### Pause, abort and rollback boundaries

- For a normal drain, set `meta.dataset_maintenance='paused'` to stop new compose
  starts/claims while existing lease-bound work and delivery can finish. Pausing
  `meta.runner_maintenance='paused'` stops **all** new research claims, including
  ordinary research; it is not a financial-only switch. It does not prevent the
  public API from queuing new research, so queued dataset jobs must be explicitly
  inventoried and retained or cancelled before any older Worker is restored.
- Feature-off is an admission/source-access cutoff, **not** a safe drain switch.
  Turning composition or financial-research flags off during a running job may
  reject remaining source reads or first publication. Prefer draining first; for
  an urgent cutoff, retain the exact claim, lease, fixed deadline, stage and spool,
  then record its cancellation/failure. Do not issue a new request ID to conceal
  an unknown delivery, revive an expired lease or recompute a completed result.
  Exact already-committed acknowledgements remain recoverable through their
  defined protocol; do not generalize that exception to unfinished publication.
- After flags are off, this release still provides owner-scoped reads and archives
  for committed datasets/results. Verify those readbacks before calling rollback
  complete. Keep all 0008 tables, immutable R2 parts, dependencies, registry grants,
  per-version experiment bindings and run bindings. **Do not down-migrate/drop
  tables, restore an old whole database over newer rows, or delete staged receipts
  as routine rollback cleanup.**
- A pre-v0.9 Worker/runtime is not guaranteed to understand new dataset-linked
  queued jobs or financial results. Do not restore it with such jobs available
  to its old claim path. Prefer the reviewed v0.9 reader with both flags off;
  if code rollback is necessary, first drain/cancel and verify all active/queued
  new-protocol jobs, stop incompatible consumers, retain recoverable spools, and
  independently prove the chosen fallback cannot claim or rewrite these records.
  New-format archive/reader availability is a separate compatibility requirement.
- Re-enable only after the blocking issue is understood and pinned sources,
  ownership, queue/lease states, capabilities and complete byte identities are
  re-read. A retry must use the existing durable request/manifest identity where
  the protocol requires it; a fresh compute requires an explicit new run decision.
