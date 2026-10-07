# Statistical Quant 0.4 interface

Implementation contract, 2026-10-08. This file specifies the rebuild; an endpoint or model is not considered implemented merely because it appears here. `schemaVersion=2`, `research.mode=statistical_quant`. Historical v1 configurations retain their original semantics.

## Input

```json
{
  "schemaVersion": 2,
  "name": "Conditional basket value research",
  "universe": {"symbols": ["000001.SZ", "600000.SH"], "start": "20230101", "end": "20250930"},
  "research": {"mode": "statistical_quant", "observationDays": 1},
  "factors": [],
  "preprocess": {"winsorize": true, "standardize": true, "decorrelation": "drop_correlated", "correlationThreshold": 0.9},
  "target": {"kind": "frozen_basket", "horizonSessions": 5, "basket": {"method": "pair_ols", "symbols": ["000001.SZ", "600000.SH"], "formationDays": 126}},
  "model": {"family": "pair_reversion", "estimator": "auto", "trainWindow": 504, "refitDays": 20},
  "validation": {"holdoutFraction": 0.2, "minTrainDates": 80, "innerFolds": 2, "outerFolds": 2},
  "execution": {"enabled": true, "side": "long_short", "shorting": "theoretical", "minEdgeBps": 10, "maxPositions": 5},
  "portfolio": {"initialCapital": 1000000, "grossExposure": 1, "maxWeight": 0.3, "rebalanceDays": 1, "rebalanceThresholdBps": 25},
  "costs": {"commissionBps": 2.5, "slippageBps": 3, "sellTaxBps": 5, "transferBps": 0.1, "minCommission": 5, "borrowAnnualBps": 300},
  "dataBindings": {}
}
```

Ranges are inclusive. Numeric booleans, nonfinite numbers, unknown enums and unsupported configuration keys fail validation.

| Field | Contract |
|---|---|
| universe | 1–50 unique SH/SZ symbols; increasing dates, at most 8×366 calendar days; provider requires nonfuture end; existing versioned universe selection metadata may remain |
| factors | 0–32 unique DSL definitions; `{id,expression,direction,role,version?}`; direction ±1; role `predictor` (default), `hedge`, `event` |
| research.observationDays | integer 1–60, default 1 |
| preprocess | winsorize/standardize booleans default true; decorrelation `none`/`drop_correlated` default latter; threshold .5–1 default .9; fitted on training rows only |
| target.kind | `asset_price` or `frozen_basket` |
| target.horizonSessions | integer 1–60, default 5; holding interval from the next official session open to h sessions later open |
| target.basket | required for frozen_basket; method `pair_ols`, `pca_residual`, `fixed`; symbols explicit unique subset of universe |
| pair_ols | exactly 2 symbols; price-level OLS with intercept over past formation window, first symbol quantity +1, second −beta; intercept is a feature, not a tradable leg; no cointegration claim |
| pca_residual | 3–20 symbols for bounded first release; components integer 1–min(10,N−2), default min(2,N−2); PCA returns through previous session, intercept and optional hedge exposures; each projection column converted to frozen quantities using formation-cutoff prices |
| fixed | 1–20 symbols; quantities object has exactly these keys, finite values between −1e6 and 1e6, at least one nonzero |
| basket.formationDays | integer 60–504, default 126; fixed baskets do not estimate a hedge |
| model.family | `mean_reversion`, `pair_reversion`, `trend`, `fundamental`, `event`; pair_reversion requires pair_ols target |
| model.estimator | `auto`, `no_change`, `historical_drift`, `ridge`, `elastic_net`, `hist_gradient_boosting`; default auto; finite grids only |
| model.trainWindow | integer 120–1260, default 504; prior observed training dates, labels must already be mature |
| model.refitDays | integer 1–126, default 20; calendar sessions; refit only on observation dates |
| validation | holdoutFraction .1–.4 default .2; minTrainDates integer 40–252 default 80; innerFolds 2–3 default 2; outerFolds 2–3 default 2; minTrainDates <= trainWindow |
| execution | enabled boolean default true; side long_only/long_short default long_short; shorting theoretical only; minEdgeBps 0–10000 default 10; maxPositions integer 1–50 default 5 |
| portfolio | initialCapital 10000–1e9 default 1e6; grossExposure .1–2 default 1; maxWeight .01–1 default .3; rebalanceDays integer 1–60 default 1; rebalanceThresholdBps 0–10000 default 25 |
| portfolio risk | netExposureLimit 0–2 default 2; sizingMode `fixed`/`volatility_target` default fixed; targetAnnualVolatility .01–1 default .1; volatilityLookback integer 20–252 default 60; factorExposureLimits default [] with at most 32 unique `{factorId,maxAbsExposure}` selected-factor references, maxAbsExposure 0–5 |
| costs | commission 0–100, slippage 0–200, sellTax 0–100, transfer 0–100, minCommission 0–1000, borrowAnnualBps 0–10000; defaults in example |

Asset targets produce one forecast per selected symbol with quantity 1. Frozen basket quantities are stored under `targetDefinitions`; the same prediction's current state and entry/exit labels use the identical vector. Formation is causal at each historical sample origin, including inner validation origins; a hedge estimated on a validation window must not be retroactively reused for its training rows.

Only predictor/event factors enter conditional prediction; hedge factors are allowed only for PCA basket construction. Fundamental requires selected predictor expressions containing an actual fundamental field (`fd_`, `pcd_`, or supported daily_basic valuation/financial columns). Event requires at least one role:event expression with PIT `ext_`/`pcd_`/`fd_` inputs and enough observed nonzero-event training dates; names or catalogue entries do not supply events. Unsupported data fails without a synthetic fallback. Model families select state features and economic hypotheses; estimator grids supply distinct fitted functions. Mean-reversion families report state-effect diagnostics, not a presumed negative coefficient. AR1/OU are not implemented estimators.

## Targets and validation

At origin close t, predict two normalized level changes relative to known current state S and gross scale G>0:

```text
y_entry = (q'open[t+1] - S[t]) / G[t]
y_exit  = (q'open[t+1+h] - S[t]) / G[t]
V_entry = S[t] + G[t] * F_entry(X[t])
V_exit  = S[t] + G[t] * F_exit(X[t])
edgeGap = S[t] - V_exit
expectedGrossPnl = V_exit - V_entry
expectedGrossBps = expectedGrossPnl / G[t] * 10000
```

The two targets use the same training observations; estimator outputs do not imply an estimated joint predictive distribution. No-change baseline predicts both levels as S and hence zero remaining gross edge. Training uses only labels ending strictly before a validation/prediction date. Candidate selection uses purged inner chronological folds; outer development folds assess the selection procedure. Terminal reporting dates are calendar-fixed before observing outcomes. Final candidate selection uses development only; predeclared rolling refits thereafter may use matured past holdout labels and are labeled sequential out-of-sample, not never-trained-on terminal data.

Forecast scoring uses entry/exit normalized squared loss, bias and absolute error plus remaining-change error. Selection, outer development metrics and terminal metrics first average eligible target rows within each date, then weight dates equally; `weighting=equal_weight_daily_average` and `observedDates` make this explicit. Missing cross-sectional coverage therefore cannot give a well-covered date more statistical weight. Per-target price-unit errors are separate from pooled normalized errors. Metrics use `biasSign=predicted_minus_realized`; aggregate uncertainty reports the opposite convention explicitly as `realized_minus_predicted`, matching the per-row forecast error. diagnostics.aggregateUncertainty provides an aggregate circular-date-block bootstrap interval for paired no-change versus model squared-loss improvement when enough mature dates exist; otherwise it gives an unavailable reason. Its loss-improvement point estimate uses the same date-balanced estimand as the terminal headline. It is not a per-forecast prediction interval, does not refit models inside bootstrap samples, assumes suitable weak temporal stability, and does not correct repeated research selection. Significance/alpha certification remains false; row.uncertainty remains null. All forecast origins, including invalid/tail/untraded cases, remain in the private artifact; no preview truncation in engine output. Hard resource limits fail explicitly rather than silently discard forecasts.

When predictor/event factor columns are supplied, `diagnostics.factorIncrement` independently selects and fits a state-only baseline using the identical frozen targets, labels, usable-input/event mask, time folds and candidate budget. Its date-balanced paired loss comparison estimates the incremental out-of-sample predictive contribution where both models produce valid, mature forecasts; it is not causal attribution, an alpha test, or an ablation of hedge factors. `bothModelValidOnly=true` and `coverage` report each model's valid, mature, invalid and model-unavailable row counts, matched rows/dates and valid-but-unmatched rows. An identical input mask does not imply identical output coverage; `outputValidityMasksIdentical` states whether model failures changed that coverage. Unavailable forecasts remain visible and are not treated as zero error. A changed hedge vector would be a different target and is not silently used as a feature comparison. The report retains full `baselineRows`, `baselineModelFits`, `baselineValidation`, and per-date losses, including unfavorable results. No independent factor-increment confidence interval is provided; the two models' no-change comparison intervals cannot be subtracted to construct one. With no predictor/event factors this diagnostic is explicitly not applicable.

`inputCoverage` records observed feature counts and invalid input reasons. A fundamental study requires actual observed fundamental predictor values at its usable origins; all-null columns cannot be labeled a conditional model even if a constant baseline could run. An event study additionally requires observed nonzero events. A rolling terminal fit that loses enough usable training inputs produces invalid forecasts with `model_unavailable`; all origins remain, and no alternative estimator is silently substituted. Such fits have `status:invalid` and a safe reason in `modelFits`.

## Result

Envelope: `schemaVersion:2,status,engineVersion:'0.4.0',strategy,research,provenance,forecasts,validation,selection,metrics,equity,trades,execution,factors,warnings`.

`forecasts` contains `schemaVersion:1,artifactId,predictionConfigHash,dataFingerprint,rows,totalRows,truncated:false,targetDefinitions,modelFits,diagnostics,sourceStrategy`. Each row contains:

```text
forecastId,date,targetId,modelFitId,informationCutoff,
entryDate,targetDate,horizonSessions,currentState,scale,
expectedEntry,expectedFuture,edgeGap,expectedChange,
expectedGrossPnl,expectedGrossBps,realizedEntry,realizedFuture,
forecastError,labelMaturedAt,status,invalidReason,uncertainty
```

`status` is valid/invalid. `uncertainty` is null when uncalibrated. Missing future calendar/labels remain null and are not invented. `targetDefinitions` entries include id, kind, symbols, quantities, unit, construction, formationStart/End and hedge audit. `modelFits` include training interval, labelEndMax, estimator/parameters and preprocessing audit. `validation` includes outer folds, final development trials, terminal diagnostics and no-change comparison; `selection` names the finite-grid choice and makes no profitability assertion.

The immutable `forecasts.diagnostics` is the complete evidence source. The outer `validation` object is a compact summary: its outer folds omit nested trials, final trials omit fold arrays, and `factorIncrement` omits baselineRows/baselineModelFits/baselineValidation while supplying baselineTotalRows, baselineModelFitCount and completeArtifactPath. `validation.completeArtifactPath` is `forecasts.diagnostics`; the corresponding factor path is `forecasts.diagnostics.factorIncrement`. Likewise, `selection.trials` omits fold arrays. These envelope projections avoid duplicating large arrays; they never trim the underlying artifact or alter its hash. Consumers inspecting complete training or baseline evidence must follow the artifact path.

Resource ceilings are 110,000 input/sample rows and 25,000 complete terminal forecast origins. The hosted runner additionally rejects a complete serialized result above 24 MiB with `RESULT_SIZE`; the artifact is never truncated to fit. These are independent ceilings, so a large factor/hedge audit or position ledger can reach the byte limit before the row limit. Reduce the universe, date range or observation density to keep the complete study within budget.

Execution consumes only valid forecasts identified by forecastId. Entry is allowed only at the stated entryDate; unavailable entry is canceled instead of shifting targetDate. Frozen-position expiry exits may wait for the first tradable open; they never invent a fill. Long T+1, atomic basket availability, fees, borrow cost, forecast expiration and cash-plus-signed-position NAV apply. Every trade references forecastId and targetId; risk exits also have exitReason. Portfolio sizing is a separate module with declared gross and per-symbol target limits. Actual drift is reported.

Theoretical shorts accrue the declared borrow rate on net short closing notional over 252 sessions. Short-sale proceeds enter unsegregated research cash; borrow availability, collateral segregation, margin calls, financing-rate changes and cash interest are not modeled. The ledger is a self-financing research accounting identity under these assumptions, not evidence of a broker-executable financing arrangement.

The independent risk adapter also enforces absolute net exposure and optional predicted annual volatility / factor-exposure bounds by scaling the entire frozen quantity vector. Admission limits use NAV after the known opening fees, so commission does not silently create an immediate oversized position. Covariance uses only the declared complete past simple-return window through the previous close, annualized by 252, with 10% diagonal shrinkage and PSD eigenvalue clipping. Factor exposures are previous-close cross-sectional z-scores within the selected research universe; these are not industry or market-beta neutrality. Missing required observations and constant factor cross-sections are unavailable risk inputs, never substituted with zero risk. Risk breaches or unavailable required risk inputs can request early ticket exits, subject to the same atomic basket, T+1 and fill constraints. These exits retain their original forecast linkage and frozen leg proportions. Default risk settings retain fixed sizing with no new factor constraint.

Daily volume and one-price bar information are used only as ex-post matching availability. A conservative adverse one-price-bar rule blocks buys above the previous mark or sells below it; it is not a reconstruction of historical exchange price limits or queues. Pre-ranked order slots cannot be replaced after an order fails this ex-post check. Sizing still uses open reference prices, fractional adjusted research quantities, and no intraday order-book capacity model.

Artifact hashes use sorted JSON with finite integer-valued floats normalized to integers and signed zero normalized to zero. Frozen input fingerprints use full-precision numeric CSV and the official calendar, also normalizing signed zero. This makes an otherwise unchanged artifact and snapshot stable across Python and JavaScript JSON transport; it does not permit price rounding or changed calendars.

`execution.enabled=false` completes a first-class forecast-only run with `metrics:null,equity:[],trades:[]`; it still returns complete forecasts/diagnostics. `execute_forecasts(strategy,data,artifact,provenance)` reruns only the independent execution layer with immutable predictions and frozen input data. Prediction configuration and data fingerprint must match; only execution/portfolio/costs (and the descriptive name) may differ. The server keeps dataset plus calendar/provenance privately in R2 separately from the report, binds it to the forecast artifact, and never refetches data for an execution-only job. Queue orchestration is coordinated separately from this numerical contract.

Errors use safe `ResearchError.code` including INVALID_STATISTICAL_QUANT, INCOMPATIBLE_TARGET, MISSING_MODEL_DATA, INSUFFICIENT_FORECAST_DATA, FORECAST_BUDGET, FORECAST_ARTIFACT_MISMATCH. A poor prediction result is still a completed research run, not a fabricated passing strategy.
