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
