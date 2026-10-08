# Complete-pool automatic-estimator capacity candidate

`pooled_asset_1000_auto_candidate_v1` is an explicitly experimental, local profile. It does not enable a public queue or widen the existing `pooled_asset_1000_v1` Ridge admission. Its scope is at most 1,000 symbols, 366 calendar days, 300,000 input/sample rows, 80,000 primary forecasts, 16 factors, `mean_reversion`, `asset_price`, `auto`, forecast-only, refit interval at least 20 sessions, and exactly two inner and two outer folds.

The estimator set is declared before fitting: no change, historical drift, two Ridge penalties, two Elastic Net penalties, and two histogram gradient-boosting configurations. The selected mechanism stays fixed. The factor-free baseline independently receives the same candidate and fold budget. Inner mean date-balanced error drives selection, with the documented one-standard-error complexity heuristic; dependent folds do not make that heuristic a confidence interval. Outer and terminal errors never choose the estimator. This does not promise zero bias, zero overfitting, or an economically useful edge.

Run with a new private directory:

```sh
PYTHONPATH=engine .venv/bin/python scripts/benchmark-filter-auto.py --output private/new-auto-capacity-evidence
```

`--plan-only` prints the admission without fitting. The separate auto entry point reuses the existing isolated-process supervisor. Guards remain 900 seconds overall, 300 seconds per fit, 3 GiB process RSS, 400 MiB total output, and 500 MiB free system reserve. No candidate, symbol, invalid origin, terminal tail, or baseline row is removed to meet a guard. Unknown/failed attempts retain their original evidence directory.

One predeclared local synthetic case completed on 2026-10-08:

| Measurement | Result |
| --- | --- |
| Input | 1,000 synthetic codes × 262 weekday sessions; 16 factors |
| Provider requests | 0 |
| Pooled samples | 201,000 |
| Fitting | 106 attempts; all eight candidates fitted at least 12 times |
| Rows in an individual pooled fit | 42,000–120,000 |
| Complete predictions | 41,000 primary and 41,000 baseline; each includes 35,000 mature and 6,000 tail origins |
| Functions and diagnostics | Three portable final functions, 20 feature diagnostics, six empirical joint tables |
| Supervisor elapsed | 34.04 seconds |
| Engine elapsed | 26.12 seconds |
| Largest individual fit | 0.81 seconds |
| Process peak RSS | 1,061,879,808 bytes |
| Complete output | 278,761,253 bytes before the small independent audit receipt |
| Independent standard-library audit | 808,165 checks; no engine imports; maximum identity error 0 |
| Trading | Disabled; zero trades |

The selected estimator was **no change**, with zero improvement over the no-change forecast. This is a capacity and artifact-integrity result, not alpha evidence. Runtime is one observation on a shared host, not an exclusive hardware limit or a promise for different data, mechanisms, periods, or feature counts.

Evidence is retained in `private/auto-1000-candidate-20261008-01`: admission, per-fit timing and source hashes, resource plan, full source/prediction bundle, supervisor receipt, and independent audit. Bundle identity: `b26692ab584bcb70dbddd2440111156f07935b55eefb6292dca77f9a1719ad31`. Forecast identity: `5a46b544af95026b27afcdb2f27e588a97fcc1e1ba4172bc37541edd13b5217a`.

Hosted enablement still requires matching source restoration, server admission, queue capability, isolated consumer, and complete bundle publication. This candidate does not change the financial dataset/2 source limits or prove event, fundamental, pair, PCA, execution, or monitoring support for 1,000 symbols.

## Complete pairwise diagnostics follow-up

The first measurement above is retained unchanged. A second predeclared synthetic capacity run, `private/auto-full-joints-1000-20261008-01`, exercises the same eight candidates, both research branches and all 106 fits after expanding descriptive joint tables. Quantile boundaries and terminal bin assignments are now computed once per input axis. The hard limit is 256 declared pairs, enough for every pair in the admitted 16-factor profiles with up to 20 total inputs; larger studies still state the omitted count. No diagnostics choose the estimator or rank which pairs to display.

All 190 pairs completed: supervisor 34.06 seconds, process peak RSS 1,052,606,464 bytes, output 278,993,939 bytes, with the same 900-second / 3-GiB / 400-MiB output / 500-MiB reserve guards. The independent standard-library bundle audit passed. Bundle identity is `51f096534310d911a71104f437da6422b442cd6c959c4d8ca6ce775bac9e282b`.

An independent file comparison matched every byte of 41,000 primary rows, 41,000 baseline rows, both sets of final model fits and candidate trials, planned origins, and 20 factor diagnostics against the earlier run. The original six joint tables also remain identical; only the additional 184 tables and declared budget changed. A separate numerical test compares every pair against an independent histogram calculation, including missing values, ties, constant axes and terminal outliers. Selection still returns no change with no forecast advantage. Existing saved reports keep their original six-pair evidence; they are not silently rewritten.
