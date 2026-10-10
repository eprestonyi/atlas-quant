# Per-asset return research: mathematical and implementation review

2026-10-11. Design review against commit `50a68e25b40816d081538f6f1b567ee3adc0892c`. This document does not assert implementation or deployment of the proposed new contract. The old price/basket artifacts remain immutable.

## Shared definitions, separate functions

Share a versioned factor library, source identities, economic transformations and availability rules. For asset `i`, build `R[i,t]` from own-asset, named reference, industry and global inputs. Fit and freeze a separate function `F[i,h]`, with its own parameters, selection record and training conditioning. A shared library does not require shared coefficients or even the same selected estimator. Global inputs are broadcast; they are not cross-sectionally standardized into zero.

Keep the stages explicit: `raw → economic feature R → fitted input X → scalar output`. Economic transformations belong to the factor definition; clipping, imputation, centering and scale belong to the particular fitted model. If two models use different training windows, their fitted X transforms can differ even for the same factor. Coefficients are interpretable only with their frozen transforms.

## Primary target and three different uses

Declare prices, adjustment, source clock, base session and endpoint before labels are constructed. For a positive asset price, a simple forward-return target is

`r[i,t,h] = P[i,t+h] / P[i,t] − 1`.

An optional volatility-scaled target is `z[i,t,h] = r[i,t,h] / s[i,t,h]`, where `s>0` is estimated using information available at origin `t` and stored with that origin. Then `r_hat = s × z_hat`. This is scaling, not necessarily a zero-mean/unit-variance z-score. If a mean is also removed, it must be stored and restored explicitly. A scalar research output must not be represented by a duplicated entry/future pair.

| Use | Equation and information set | What the result means |
| --- | --- | --- |
| Concurrent relationship | `r[i,t−h→t] = G[i,h](Z[t]; theta fitted before evaluation) + u[i,t]` | Conditional association between observations from the same interval; exposes loadings, conditional response and unexplained variation. It is not a forecast available at the start of that interval. |
| Known-input future forecast | `r[i,t,h] = F[i,h](X[t]) + epsilon[i,t,h]`, with `X[t]` measurable at the declared origin cutoff | Genuine ex-ante prediction evaluated only after the future label matures. Own historical momentum may be an input; future realized market or industry changes may not. |
| Future-factor scenario response | `r_hat[i,t,h](z*) = G[i,h](z*, current state; frozen theta)` | A response conditional on an explicitly assumed future factor value/path. It is not a claim that the scenario will occur and is not a causal intervention effect. |

Forecasting regression with unknown future predictors requires forecasts of those predictors or explicit scenarios; using subsequently observed predictors is ex-post evaluation. A direct horizon regression on already-known lagged predictors is a different operational choice. [Hyndman and Athanasopoulos, forecasting with regression](https://otexts.com/fpp3/forecasting-regression.html).

Scenario intervals conditional on one assumed factor path do not include uncertainty about that path. To obtain an unconditional forecast from a scenario-response model, use a separately specified joint factor forecast distribution and integrate the response: `E_t[r] = integral G(z, state_t) p_t(z) dz`, under the model's conditional-mean assumption. In general `E[G(Z)] != G(E[Z])`; nonlinear response requires more than plugging in factor means. [Dynamic regression forecasting](https://otexts.com/fpp3/forecasting.html).

For a volatility-scaled concurrent relationship, use the scale known at the beginning of the return interval (for example `s[i,t−h,h]`), not volatility realized over the same interval. For future forecasts/scenarios, use the origin-known scale. The agreed protocol defines `sqrt(h) × trailing daily volatility` as a reference scale. It does not claim this equals the conditional h-session standard deviation: that equality requires additional assumptions. Conditional variance recursion and its information cutoff must be explicit when a volatility forecast is actually claimed; residual variance and total forecast variance can differ when the mean is dynamic. [ARCH forecasting documentation](https://arch.readthedocs.io/en/latest/univariate/forecasting.html).

The Avellaneda–Lee construction separates systematic return exposures from residual-process dynamics. Regression residual extraction alone does not prove residual mean reversion or an executable trade. Their PCA/sector-ETF examples motivate return-based relationships, not a requirement that all models use PCA, one normalization window or a universal strategy. [Original paper, sections 2–3](https://math.nyu.edu/~avellane/AvellanedaLeeStatArb20090616.pdf).

## Leakage and interpretation traps

- A concurrent model must reject a predictor that directly contains its target asset's same-period return, including equivalent DSL expressions. Otherwise a high R² may be a tautology. An own-asset return lagged before the response interval is different. Broad indices containing the asset also create some mechanical dependence; report the membership and optionally compare a leave-one-out benchmark.
- Same-session observations become known only after their publication. A relationship estimated from end-of-day market/industry returns cannot claim a pre-close or same-open trade. US/A-share calendars need real availability joins, not equal date strings or forward-filled zero-return sessions.
- A financial ratio must be available by the origin; final revised fundamentals, current industry membership and historical constituents reconstructed from today's universe can leak information even if the date columns look valid.
- Every asset's preprocessing, candidate selection, factor pruning and volatility estimation must use its eligible training information. Purge labels overlapping the validation boundary. Changing only future observations must leave earlier X, scales, selected parameters and predictions unchanged.
- A scenario path is an input, not a fitted observation. Preserve units, originating model/factor identities, assumption timestamps and support/extrapolation flags. Do not score a hand-selected favorable scenario as an unconditional prediction, and do not optimize scenarios against terminal outcomes.
- Log returns are additive but need a different inverse link. `exp(E[log P])` is not generally `E[P]`; do not reconstruct an expected price by exponentiating a mean without declaring the approximation or distributional treatment.
- Standardized-return MSE changes sample weighting relative to raw-return MSE. Report metrics in the target unit and reconstructed return unit. No normalization guarantees normality, stationarity, lack of bias, or trading advantage.

## Existing code constraints and proposed separation

`schema.py` and `edge/statistical-quant/validation.mjs` currently admit only `asset_price` or `frozen_basket` targets. `parameterSharing=per_target` already exists for asset prices, capped at 50 assets, and `per_target.py` performs separate model searches. Thus per-asset dispatch is reusable, but target semantics are not a label change.

`targets.py` currently builds two labels: `(next_open−P_t)/gross` and `(open[t+1+h]−P_t)/gross`. The second is neither a close-to-close h-session return nor an open-to-open holding-period return. `model_function.py`, its strict validators and browser counterpart likewise require two outputs and reconstruct `P_t + gross × output`. These artifacts must retain their old meanings. A new function schema must specify one scalar return/scaled-return output, target alignment, volatility transform, inverse transform and per-asset scope without fabricating a second output.

`core.py` currently wraps research in a forecast/execution envelope. `execution.py` expects entry dates, frozen quantities, entry/future predictions, gross currency P&L and cost thresholds. Concurrent relationships and user scenarios are research-only; their output must fail admission to this execution path. Even a validated scalar future-return model needs a separately designed conversion to order timing and executable P&L. Return prediction is not itself a strategy or hedge.

Proposed frozen artifact information: schema/version; asset identity; research use (`concurrent`, `known_input_forecast`, `scenario_response`); output unit; return interval/price convention; factor library/input identities; complete transforms; training cutoff; selected estimator and scalar parameters; optional origin-scale specification; scenario assumptions; provenance; and evidence status. Exact serialized field names are to be agreed with the engine before implementation. Keep old schema validators and hashes unchanged.

## Acceptance checks

1. Independent raw-data arithmetic reproduces every selected asset's returns, origin-known volatility, economic features, final X and scalar F output; missing dates, zero scales and adjustments have explicit outcomes.
2. Same factor definitions can feed multiple independently fitted functions. Changing one asset's labels must not change another asset's independent parameters; global inputs remain identical before model-specific conditioning.
3. Freeze the exact timing contract: a one-session target hits the declared next endpoint; h-session tests do not silently add one extra session. Future perturbation and maturity-purge tests detect leakage.
4. For scaled returns, verify `r_hat = s_origin × z_hat` and any permitted price reconstruction. Reject missing/zero/nonfinite scales; changing realized future volatility must not change the origin's scale or output.
5. Concurrent target aliases/self-return expressions fail before fitting. Concurrent, ex-post and scenario artifacts cannot enter execution or claim out-of-sample trading edge.
6. Scenario evaluation changes only declared scenario inputs; frozen parameters remain identical. Nonlinear examples verify that response-at-mean and mean-response are kept distinct. Scenario intervals explicitly retain their conditional interpretation.
7. Python and browser evaluators agree on scalar linear, nonlinear-basis and tree artifacts, including missing inputs and threshold boundaries. Use identical constructed float inputs for exact tree comparisons; separately report sensitivity to mathematically equivalent but differently rounded preprocessing.
8. Separate per-asset training fit, concurrent held-out response, future out-of-sample prediction and scenario stress output in both data schemas and the report. An inspected historical test interval is not fresh prospective validation.
