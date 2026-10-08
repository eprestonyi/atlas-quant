# Frozen financial input → automatic F research

Status: implemented locally; no deployment or production enablement. This admission is distinct from immutable dataset composition. The source remains `atlas.quant.research_dataset/2`, profile `financial_snapshot_view_50_v1`; the new research admission is `financial_fundamental_auto_50_v1`. Existing version 1/2 Ridge imports retain their original identities and interpretation.

The new admission fixes fundamental mechanism, asset-price target and no execution. It uses the exact frozen securities and interval, at most 50 securities, 366 inclusive calendar days, 16 predictor factors, two inner and two outer temporal folds, and refitting no more often than every 20 trading sessions in the source calendar (a refit is checked only at an observation origin). Factors must reference registered, available financial states. Temporary external bindings and universe substitutions are rejected. The profile does not silently shorten data or choose a subset.

Eight predeclared estimator/regularization candidates are evaluated within the chosen mechanism. Training transforms only use training data. Inner purged temporal validation selects; outer folds estimate; terminal metrics do not select candidates. The factor-free baseline is included in the declared total fit budget. The one-standard-error simplicity rule is a heuristic for dependent folds, not a confidence interval or proof of no bias/overfitting. RL is not part of this supervised conditional-state fit. See [the exact contract](../contracts/financial-research-auto-v1.json).

A no-change winner remains a complete F artifact with diagnostics. It does not turn a negative finding into a missing result. Final fitted functions, feature marginals, descriptive fit statistics, IC availability and the bounded six-pair empirical joint tables use the existing factor-research contract. Arbitrary requested joint pairs are a later feature; six automatically selected pairs do not satisfy that whole requirement.

## Compatibility and admission

`financialResearchProfiles` is an additional runner capability. Dataset/snapshot/transport format declarations alone continue to admit the old Ridge profile but cannot admit auto. Queue selection and durable running-claim recovery must reject auto for an old runner; terminal delivery receipt recovery must remain available. Runner metadata must preserve this capability on heartbeat. The shared claim implementation is integrated separately by the queue owner.

Dataset details expose additive `researchAdmissions` and `preferredResearchAdmission`; the legacy `researchAdmission` remains. Scope eligibility and fresh runner availability are separate fields. A frozen interval beyond this auto profile remains explicitly ineligible instead of having its dates changed. `sampleStatus: not_checked` means structural admission has not established sufficient statistical samples.

The same spawned child validates encrypted source input, recomposes financial states against independently pinned registry bytes, fits F, and freezes the financial snapshot/result. Financial bundle version 1 and typed snapshot version 2 remain unchanged. A result archive always needs its separate dataset archive for complete source closure. Neither transport verification nor successful source recomposition establishes predictive edge.

## Local evidence, 2026-10-08

A new explicit job reused the existing synthetic frozen dataset `8df17a2f-7f94-4bb2-bbe3-717c51b40add`, root `22fbe8cd5d27fc667f9c41f7793b84ea0b1d10db654344a4e08e644b1cbea0c6`. This is **one security**, 262 rows in calendar year 2024, all 16 available financial states. It is not a 50-security capacity proof and not a provider/live-hosted acceptance.

- Job `6b6b9f19-f041-44d0-91a9-2cf7c24afe4d`; result bundle `a0fb49cc6be1a43e9aa31eca552dc6250e355090617b7534a1327af20215418b`.
- All 41 model rows and 41 baseline rows retained. Eight declared candidates, two branches, 19 feature diagnostics, six empirical joint tables and one callable final F. No-change won; `NO_VALIDATED_FORECAST_EDGE`; zero trades.
- Same-child source recomposition and F took 1.872 seconds. Child peak RSS 192,266,240 bytes. A 900-second process limit, 3-GiB sampled RSS stop and 500-MiB disk reserve were active; no separate per-fit timer is claimed.
- Result USTAR: 974,336 bytes, SHA `f3f4dfa4b9ed30831761d8bf5a429d041a6a86edf6acdac9979659fc664b8330`. Unchanged source USTAR: 1,625,088 bytes, SHA `f35a42aec4f6256aaef2551375b71ac57b1cb26034bbce8ac25495ef1f21ca13`.
- Separate source recomposition produced exact frozen research-row bytes. The independent stdlib-only auditor checked 919 result and 101,637 source assertions, maximum numerical identity error zero, external registry bytes matched. Its source audit does not rerun financial formulas or refit F.
- Evidence is private at `private/financial-auto-frozen-20261008-03`. Failed script attempts 01 (candidate serialization) and 02 (lock-path type) remain preserved; neither reached F. The successful run had a new job identity. No old job or provider request was repeated.

`benchmark-financial-auto.py` requires an existing explicitly synthetic dataset, expected root, independent registry-pin map, separately saved registry files and a new output directory. It disables Requests/urllib network calls in parent and spawn child, then saves predeclaration, encrypted local spool and both archives. The transport in this acceptance is local encrypted spool, not HTTP. Run `audit-financial-bundle.py` separately on the two saved archives and original authorized pins.

## Remaining release gates

A separate, explicitly synthetic 50-security × 366-day × 16-state source-complete run must test the maximum admitted resource combination before production auto enablement. Queue capability isolation, actual Worker binding → new runner → complete upload, browser selection/readback and current release CI remain integration gates. Production flags remain off; no alpha claim follows from these engineering checks.

## Maximum-scope counterexample and next design

A separate deterministic artificial source was predeclared at 50 securities, all 366 inclusive calendar days in 2024 (262 weekday sessions), 16 financial states. It contains 2021–2024 Q3 artificial quarterly statements, two disjoint 25-security packages, 75 normalized snapshots per package and one separately authorized synthetic calendar. Each package independently prepares successfully under the unchanged limits. The full dataset/2 composition fails `PREPARED_BYTE_BUDGET` before any F is fitted. Evidence: `private/financial-auto-50-source-20261008-01`; no provider calls, no model fit, no subset substitution.

The exact retained-byte measurements are:

| Item | Bytes |
| --- | ---: |
| Prepared package 0 | 33,888,059 |
| Prepared package 1 | 33,877,368 |
| Prepared sum, before market/input/joined rows | 67,765,427 |
| Existing entire closure limit | 67,108,864 |
| Expanded event objects, 4,000 events | 44,921,606 |
| Repeated dependency objects within events | 39,318,044 |
| Unique 5,350 dependency objects with dictionary keys | 9,173,629 |
| Repeated calendar objects | 1,284,000 |
| One unique calendar dictionary | 390 |
| Proposed referenced event graph lower bound | 15,338,281 |
| Prepared daily panels repeated across the two packages | 22,376,299 |
| 13,100 combined numeric research-row array, excluding provenance | 24,802,815 |
| Existing joined document limit | 25,165,824 |

This is an actual serialized-size failure, not solely a conservative allocation estimate. Removing 29.58 MB of event duplication alone would still leave little room for the original market/source components, repeated prepared panels and final joined rows. The daily prepared panels can be reconstructed from the existing 250 interval assignments and their exact referenced state events. Those panels should not be stored again in a compact source representation. The final joined-row document is also close to its current limit: only 363,009 bytes remain before provenance and the outer document. No upper limit was raised and no state, date or security was removed.

The next source format needs an explicit new version, not a changed interpretation of dataset/2. A compact prepared component should store content-addressed dependency/calendar dictionaries, state events referencing them, and interval assignments referencing events. It should stream reconstruction of the original logical prepared payload to verify its exact prepared root, including decimal strings, nulls, numeric types, event order and availability dates. Reconstructed daily panels should be derived as needed, retaining the same research-input values and fingerprints. This is a design proposal; no compact decoder or admission is implemented yet.

Admission for the new version must bound graph nodes, reference counts, total referenced expansion, part bytes, rows and process RSS independently; reject dangling/cyclic/duplicate or rehashed mismatched references; and preserve independently authorized registry pins. Golden exact reconstruction against dataset/2, adversarial reference/availability cases, full 50-security source composition, same-child F, and independent two-archive audit must all pass before enabling the larger combination. Old datasets/results remain readable and keep their original hashes.

Reproduction tools: `financial-auto-fixture.py` creates a new explicit artificial source and preserves failure/prepared evidence; `analyze-financial-source-size.py` reads those retained bytes and measures a possible lossless graph. The analysis does not publish a dataset, bypass admission, fit a model, or count a projected saving as successful capacity.
