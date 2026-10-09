# v0.9.1: research workflow and explicit test dates

Candidate, not deployed. Production remains the frozen v0.9 release. This update
has no database migration and does not open additional compute or data profiles.

The five-page factor workflow now follows statistical quant → factor research →
mode selection. A single universe filter contains recommendations, set operations
and results. Mechanism, research window and factor inputs are separate pages.
Saved reports open on the actual fitted function and numeric parameters.

`validation.testStart` is an optional new contract shared by the browser, Worker,
Python, market coverage validator, independent auditor and code-review context.
Existing configurations without it retain their original fraction-based split
and prediction identity. New configurations require a compatible Python runner;
shipping the browser first would cause old runners to reject their jobs.

## Candidate verification

- Independent review reproduced and fixed a recommendation re-click expanding
  an already narrowed universe. The regression checks 1,000 → 80 → 40 → 39
  members using the real set resolver and no provider/model calls.
- Independent review checked old normalized configuration hashes, explicit-date
  identities, label maturity, holiday boundaries, coverage and archive checks.
- Real isolated Worker browser: saved 80-member filtered study with one factor,
  changed the test start to 2025-01-02, saved version 2, refreshed and read both
  date and full scope back. No job was submitted.
- Report visuals use a SHA-verified frozen historical report without refitting.
  The separate Ridge/tree golden fixture is UI/numerical evidence only.
- New filter and model-first DOM regressions are included in Linux CI.
- Input volatility normalization and broader global/sector/reference feature
  bindings remain a separate design, not capabilities delivered by this patch.

## Paired release gates

1. Freeze the final commit and pass both candidate CI contexts. The base main
   includes the independently reviewed PR24 offline pair work beyond deployed
   v0.9; its shared-engine changes remain in this candidate's validation scope.
   Pair Stage2B still rejects the new date field explicitly and is not hosted.
2. Read and preserve current authenticated Worker bytes/settings, D1 state,
   maintenance values, active jobs, runtime trees and pending deliveries. A
   public health response alone is insufficient for this step.
3. Pause admissions and drain existing work; preserve original trees. Install
   compatible Python before accepting new date-bearing jobs. Keep the existing
   configuration, shared compute lock, credentials and data intact.
4. Publish exactly the verified Worker with the original bindings and flags;
   check full bytes, version, UI and API readback, then restore the prior queue
   state. No provider acquisition or historical F canary is replayed.
5. Publish matching source and record the fresh production receipt. The prior
   v0.9 archive and receipt remain immutable.

The original v0.9 release drivers are historical, version-pinned tools and must
not be resumed or repointed for this release. If authentication is unavailable,
retain the candidate and report that deployment is blocked before any mutation.
