# Factor time coordinates

Status: implementation in progress; the new protocol has not yet passed acceptance or been released. Existing `asset-return-study/1` and function v4 retain their frozen meanings.

## One conditional equation, independently timed inputs

For asset i and research-session origin t, the response is

`Y(i,t,h) = (P(i,t+h) / P(i,t) - 1) / s(i,t,h)`

where s is either 1 or a strictly origin-known trailing volatility multiplied by sqrt(h). The conditional equation is

`Yhat(i,t,h) = F_i(R_1(t+delta_1), ..., R_m(t+delta_m))`.

Each R is an economic feature, with its own transformation and observation window. Its offset is applied **after** constructing that feature. Thus a 20-session market return at offset 0 covers [t-20,t]; a 5-session return at offset +5 covers [t,t+5]; a 20-session return at offset -5 covers [t-25,t-5]. An offset must not silently change a return's denominator or window. Financial TTM/year-on-year windows retain their fiscal-period meaning.

The new study declaration is `{"schema":"asset-return-study/2"}`. There is no mutually exclusive forecast/association mode. Factor instances have a unique `id`, a `baseFactorId`, and `timing:{anchor:'target_origin',offsetSessions:k}` with integer k in [-252,252]. Different instances of the same base factor can coexist. Frozen function v5 descriptors additionally declare `offsetClock:'research_sessions'`. The target always covers t to t+h.

This is a conditional statistical model. Historical realized future inputs are allowed, but a score conditioned on those inputs is not an origin-known forecast score. Hyndman and Athanasopoulos explicitly distinguish ex-ante forecasts from ex-post predictions using later realized predictors; ex-post estimation still must not train on the evaluation period's response. Their discussion also explains that conditional intervals omit uncertainty in assumed future predictors. [Regression forecasting](https://otexts.com/fpp3/forecasting-regression.html), [dynamic regression](https://otexts.com/fpp3/dynamic-regression.html).

Distributed lags do not require every input to have the same lag. The official ARDL interface supports variable-specific lag sets and a common hold-back sample when comparing different lag lengths. Its `causal` flag excludes lag zero; it does not establish economic causality. [Statsmodels ARDL](https://www.statsmodels.org/stable/generated/statsmodels.tsa.ardl.ARDL.html). Leads also occur in specialized estimators, such as Stock and Watson's dynamic OLS for cointegrating relations under their stated assumptions; this does not turn a realized lead into an available forecast input. [Original paper](https://www.princeton.edu/~mwatson/papers/Stock_Watson_Ecta_1993.pdf), [DynamicOLS documentation](https://arch.readthedocs.io/en/latest/unitroot/generated/arch.unitroot.cointegration.DynamicOLS.html).

## Observation time and information time

An economic interval, source observation date, availability bound, and retrieval/vintage date are distinct. A positive offset can describe information already known at t, such as a scheduled calendar event; a negative economic offset does not make an unpublished financial statement available at t. For each factor instance the frozen panel and observation must carry its own evidence, rather than inherit one blanket origin-known label.

Version 2 records use `inputEvidence['factor:<instance>']` with `valueOrigin`, `evaluationDate`, `sourceDate`, `availableAt`, `availabilityBasis`, `knownAtOrigin`, and `status`. In this implementation `availableAt` is a conservative session-policy bound, **not a provider publication timestamp**. Financial/model-financial/external inputs without sufficient historical alignment remain availability-unverified even if they have a numerical value. Native foreign source sessions retain their actual source date separately from the research-session offset.

Tushare distinguishes statement period end, announced date, actual announced date, and update marker. Those fields alone do not prove that today's historical payload is the exact version available to an investor then. [Official income statement fields](https://tushare.pro/document/2?doc_id=33). FRED's real-time period and vintage-date interfaces illustrate the separate observation-time and information-vintage dimensions; they are a design reference, not proof that another provider supplies equivalent vintages. [Real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html), [vintage dates](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html).

For a training row:

`sampleReadyAt = max(responseEndDate, inputAvailableAt)`

and all required input availability bounds must be resolved. Training, preprocessing, imputation, feature selection and model search may use the row only when `sampleReadyAt < fitCutoff`. A target with horizon 5 can be mature while a factor at offset +10 is still unavailable. Purging only the target horizon would leak that input. Train-only transformation fitting follows the standard split-before-fit rule. [Scikit-learn leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html).

Offsets use the complete frozen research calendar, never a compressed list of nonmissing rows. A foreign feature may have its own native observation window before projection to this grid; foreign holidays must not manufacture zero returns. Same civil date does not imply availability across time zones. No current protocol evidence should be promoted from a conservative date policy to an exact timestamp or historically verified data vintage.

## Identity and evaluation boundaries

A current or historical own-asset input is not categorically forbidden. An input algebraically identical to the target, or an invertible transformation of that exact same interval and adjustment identity, must be identified as target-derived. Its near-perfect fit is a tautological conditional relationship, not validated predictive advantage. A broad index containing the asset introduces a different, partial mechanical relationship and is not automatically identical to the target.

Scenario values and separately predicted future factors must keep their origin separate from realized observations. Feeding a point forecast of X to nonlinear F is generally not the same as integrating F over X's predictive distribution: `E[F(X)] != F(E[X])`. A portable function evaluating numbers cannot independently certify when those numbers were knowable.

## Acceptance plan

1. The same base factor at negative, zero and positive offsets retains distinct instance identities and coefficients; offset and economic return window are independently tested.
2. Full-grid shifting preserves missing rows and explicit out-of-calendar tails. Future input gaps cannot be filled and relabelled observed.
3. A lead-10 input with a horizon-5 response is excluded from a fit until both have matured. Future perturbations cannot change earlier fit parameters.
4. Per-input evidence distinguishes origin-known-by-policy, conditional-realized, availability-unverified and unresolved inputs. Rehashed archives with false evidence are rejected.
5. Changing realized future values to assumptions or separate predictions changes evaluation evidence, not the frozen function parameters.
6. Financial revision and cross-market calendar counterexamples cannot acquire false point-in-time certainty. Foreign holidays do not create observations.
7. Exact target identity and log-equivalent identity are reported distinctly from useful historical inputs and partial index overlap.
8. Candidate comparisons disclose or enforce a common scoring population when offsets change available support.
9. Python, JavaScript, full panel, frozen function, archive and independent audit agree on instance identities, intervals, evidence and scalar inverse arithmetic.
10. Legacy study/1 and F4 fixtures retain their byte identities, acceptance rules and numerical outputs. Passing these checks proves the implementation contract, not trading profitability.
