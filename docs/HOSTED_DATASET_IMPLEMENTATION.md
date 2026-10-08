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

## Remaining integration gates

Actual dedicated consumer → HTTP source delivery → core compose → publication
and private archive restore are in progress on isolated port 8932. The first
harness failed before child computation because its own top-level import tried
to recreate its evidence directory; that failure is retained, not called success.

Financial result codec dispatch, actual same-child financial F delivery and
source-result audit must be integrated and exercised with enough explicit
synthetic sessions. Desktop and actual 390px browser acceptance are still pending.
Neither mocked responses nor transport-only source metadata satisfy those gates.

## Local preview

`node scripts/hosted-dataset-preview.mjs` serves an isolated Miniflare instance at
`http://dataset.localhost:8932/quant/#quant/studio/datasets/source`. Dependencies
must already be installed; the script never installs them or calls a provider.
It writes its private owner/session configuration under ignored `private/`.
A browser owner may be granted only this explicit synthetic fixture through the
private file control described by the generated config; there is no public seed
route. The source fixture generator and optional model fixture live in the
separately integrated Python dataset consumer change.
