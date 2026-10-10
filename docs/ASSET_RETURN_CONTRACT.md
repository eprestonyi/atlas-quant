# Independent asset return equations — asset-return-study/1

This opt-in protocol preserves all saved legacy price/basket strategies, two-output functions and v0.12 identities. A research collection supplies shared factor definitions. Each security has its own training population, selected columns, transformation parameters, estimator and coefficients. There is no equal-weight price target and no pooled fit.

## Saved study

```json
{
  "schemaVersion": 2,
  "research": {
    "mode": "statistical_quant",
    "observationDays": 1,
    "returnStudy": {"schema": "asset-return-study/1", "mode": "forecast"}
  },
  "target": {
    "kind": "asset_return",
    "horizonSessions": 5,
    "normalization": {"kind": "none"}
  },
  "model": {"parameterSharing": "per_target"},
  "preprocess": {"automatic": {"schema": "auto-factor-preprocess/2"}},
  "execution": {"enabled": false}
}
```

Other existing strategy fields remain required/defaulted as before. `returnStudy` has exactly `schema,mode`; mode is `forecast` or `association`. The alternative normalization has exactly:

```json
{"kind":"trailing_volatility","windowSessions":20,"ddof":1,"horizonScale":"sqrt_h","minimum":1e-8}
```

Window is an integer 20–252. Horizon is 1–252; legacy price/basket horizons retain their previous limit. The volatility is sample standard deviation of the last W one-session simple returns **ending at the response interval's starting close**. The response denominator is sigma times sqrt(h), not a future realized volatility. This is a defined reference scale, not a claim of independent returns or a normal distribution. Insufficient/near-zero volatility invalidates that row; never invent a scale. No arbitrary user denominator enters training.

The new mode requires 1–50 securities, `per_target`, automatic preprocessing /2 and execution disabled. Basket configuration, pair_reversion and hedge-role factors are rejected here; legacy pair research remains a separate explicit path. No model or automatic factor semantics in a pre-existing saved study are migrated implicitly.

## Three distinct meanings

| Purpose | Training response | Factor timing | Meaning |
| --- | --- | --- | --- |
| `forecast` | P(i,t+h)/P(i,t) − 1 | X(i,t), available at origin close | Known-input prediction of subsequent return |
| `association` | P(i,t)/P(i,t−h) − 1 | Matched-period X(i,t) | Relationship between observed contemporaneous quantities |
| Future-factor scenario | Reuse an **association** F; no new fit | Caller explicitly supplies future-scenario factor R | Conditional scenario response, not a forecast from today's information |

Each response is optionally divided by the origin-known reference scale above. The interval start/end, factor date and label maturity are separate fields. Forecast targets use exactly close(t)→close(t+h), never legacy open(t+1)→open(t+1+h).

For association, price-derived reference market/industry inputs use the same h-session interval. Local-security price/valuation expressions that embed its contemporaneous target price are conservatively rejected; a same-period own-return identity cannot masquerade as a fitted relation. Referenced other-security/context factors retain their explicit source identity. All predictors must be selected factors: the engine does not inject hidden own-return/state-change features.

Economic descriptors in the new feature construction freeze actual effective transformations and timing. They are not misrepresented as old automatic /2's universal lag-1 defaults. Each descriptor retains `feature,expression,direction,scope,transform,aggregation,economicType,sourceUnit,clock` with `aggregation` fixed to `asset_direct` or `global_once`, and adds `timing: {kind: "origin_known"|"matched_period", horizonSessions: h}`. Association price/simple/log-return construction uses h; other already dimensionless expressions must explicitly match the declared interval or be rejected when their return window conflicts.

## Portable F/4

Top-level fields match the bounded JSON artifact layout of prior Fs, with `schema: "atlas-model-function/4"`. Hashing remains `sha256-canonical-f64-json/1`. The estimator has **one** output: constant value length 1, linear/basis coefficient matrix one row and intercept length 1, or histogram outputs length 1. No placeholder entry output exists.

`outputs` is `["asset_return"]` or `["volatility_standardized_asset_return"]` according to normalization. `scope` retains family, targetKind, horizonSessions, symbols, observationDays, researchStart/researchEnd and generalizationOutsideScopeValidated=false, adding `studyMode` (`forecast` or `association`). `symbols` has exactly one security. Training dates/counts retain the existing six fields and strict label maturity before fitting.

`featureConstruction` has exactly:

- `schema: "asset-return-features/1"`
- `factors`: shared selected factor definitions
- `preprocess`: frozen preprocessing configuration
- `targetSpecification`: the full new target object
- `inputs`: the effective descriptors described above

Only `factor:<id>` inputs are allowed. Economic factors are constructed before portable evaluation; rows passed to F contain R, and F applies its frozen training clipping/imputation/scaling to produce X. A security's coefficients and scaling cannot be silently applied to another security.

`identity` is exactly:

```json
{
  "response":"output[0]",
  "simpleReturn":"response * responseScale",
  "conditionalPrice":"originPrice * (1 + simpleReturn)",
  "responseScale":"one_or_origin_known_daily_volatility_times_sqrt_h"
}
```

Portable evaluation accepts `{rows, mode, originPrice?, originVolatility?}`. `mode` is required: a forecast artifact accepts only `forecast`; an association artifact accepts `association` or explicitly `future_scenario`. `future_scenario` requires originPrice and, for volatility-normalized targets, originVolatility. Optional prices must be positive and volatility must exceed the fixed minimum. Return-level recovery uses the frozen target normalization. Supplying future factors to a forecast F is rejected by mode; the API cannot prove the caller's numeric values were historically known and must not claim such verification.

The result has `artifactId,predictedResponse,outputUnit,mode,evidenceStatus,scenarioOnly`. `predictedResponse` is a one-dimensional vector. If the required normalization context is supplied, add `simpleReturns`; if originPrice is supplied, add `conditionalPrices`. These are conditional levels, not execution entry quotes. No `expectedEntry`, `normalizedChanges` pair, trade instruction or validated-strategy claim is produced.

## Report, complete panel, and coverage

Keep the report envelope `schemaVersion:2`, and its existing `forecasts` container for transport compatibility. Its artifact is `schemaVersion:1, studyProtocol:"asset-return-study/1"`, retaining `artifactId,predictionConfigHash,dataFingerprint,sourceStrategy,rows,totalRows,truncated,targetDefinitions,modelFits,hedgeFits,diagnostics,factorResearch`. Every model-fit/source record includes `targetId,targetSymbol`; hedgeFits is empty.

Rows use `schema:"asset-return-observation/1",forecastId,date,assetSymbol,targetId,modelFitId,featureDate,responseStartDate,responseEndDate,informationCutoff,status,invalidReason,inputValid,originPrice,responseScale,observedResponse,observedReturn,predictedResponse,predictedReturn,conditionalPrice,responseResidual,labelMaturedAt`. Residual means observed response minus predicted response. There are no fake entry fields. Missing target dates stay null at the calendar tail. Prediction status distinguishes unavailable labels from absent numeric predictions.

`factorResearch.panel` has `schema:"asset-return-panel/1",rowCount,complete:true,dates,symbols,rows`. It retains the complete date×security grid, including warmup/missing rows with reasons, factor R values, interval dates, origin scales and observed response. Columns are shared definitions; per-security dropped columns retain their reason in fit diagnostics. The panel is an optional **new chunked `researchPanel` collection**, never silently truncated to fit a summary. Resource admission rejects a too-large full study up front.

The pre-fit plan has `schemaVersion:1,studyProtocol:"asset-return-study/1",source:"samples_before_model_fitting",baselineRequired,holdoutStart,origins`; each origin carries date, targetId, responseStartDate, responseEndDate and inputValid. Publish this plan before fitting. Existing legacy coverage and document bytes remain unchanged.

Scoring and candidate selection are per security under a common calendar/declared budget. Training-only transform fitting, mature-label purging, chronological inner selection and outer validation remain. Zero-return and historical mean baselines remain explicit. Association fit quality is descriptive/generalization evidence, never predictive return edge. Future-scenario evaluation creates no new validation and never feeds a test result back into selection.

## Implementation boundaries and acceptance

- New Python `statistical_quant/return_study/` owns contract, samples, selection/reports and portable F/4 validation/evaluation. Dispatch only when the explicit return-study tag is present.
- Shared models may gain a strictly opt-in single-output argument; old default two-output paths and fixtures must remain byte-identical.
- Root owns edge admission, archive/bundle records/coverage/auditors and source binding. Portable JS runtime is separately implemented against this exact contract; UI owns per-security panels and the three meanings above.
- Tests cover distinct asset slopes/scalers, no aggregation, full panel preservation, exact h-session and volatility alignment, future-data perturbation, association target leakage rejection, per-security missing data, all supported estimators, zero baseline, single-output Python/JS parity, independent archive/source scope, and unchanged legacy identities. No prior provider or model job is replayed as part of implementation.


## Implemented selection and reporting boundaries

The complete research-session calendar is reindexed before temporal DSL evaluation. A missing quote cannot make `lag` skip a session. All `dates × symbols` cells remain in the panel; observationDays controls fitting/prediction origins, not panel completeness. Association rejects obvious mismatched or compounded price shifts (`returns`, price-derived `lag`/`delta`) as well as an own-security reference alias. This is conservative typed admission, not arbitrary algebraic proof.

Every security evaluates the same predeclared candidate dictionary separately. Auto plus `factor-model-search/1` evaluates all 22 candidates; finite specific-estimator configurations retain their declared subset. Each valid candidate receives a final development-only fit with a portable function and training-only scatter. A selected baseline never hides the best nonbaseline research candidate. Export/source validation errors fail the run rather than being disguised as a failed numerical fit.

There is no implicit factor-free fit with hidden state inputs. Zero response and historical mean are explicit candidate/reference models. Coverage has `baselineRequired:false`; each asset also reports the zero-response reference loss. No pooled collection price or pooled coefficient vector is constructed.

`diagnostics.assetModels` maps each target to its selected/research candidate and latest fit. `diagnostics.perTarget[].factorDiagnostics` carries that asset's distributions, temporal Pearson/Spearman, univariate descriptive fits, correlation/covariance and joint-frequency tables. Flat factor/joint collections retain targetId/targetSymbol for transport pagination. A one-security time series does not claim cross-sectional IC. Scoring is independently per asset; the collection summary does not label a pooled average as a portfolio forecast.

Before fitting, declare the full nested selection, candidate export and worst-case sequential-refit fit cap. Admission is bounded to 20,000 fit attempts and 1,800 seconds per research, with consumer process limits in addition. One reviewer wrapper is shared by the whole research, preserving its existing two-call maximum; only final development selection supplies the scalar-response packet. No outer/test result enters review. Further assets receive an explicit exhausted-budget receipt rather than silently gaining another allowance. Automatic review can remain disabled without changing the full candidate dictionary.
