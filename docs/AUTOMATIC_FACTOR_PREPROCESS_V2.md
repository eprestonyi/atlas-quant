# Typed factor preprocessing v2

`auto-factor-preprocess/1` is unchanged. New studies may explicitly select `/2`.
This changes inputs and therefore creates a different research; it does not
rewrite saved forecasts or make their old evidence applicable to the new model.

## Saved configuration and frozen metadata

```json
{"automatic":{"schema":"auto-factor-preprocess/2","overrides":{
  "price":{"transform":{"kind":"log_return","lag":1,"invalid":"missing"}}
}}}
```

`overrides` is optional, at most 32 exact selected non-hedge factor IDs. Each value
has only `transform`. The automatic object accepts only `schema` and `overrides`.
Each transform is an exact dictionary from the list below, not a free parameter
search. Easy uses the common type defaults. Studio choices are saved before
training. Model/basis selection never retrospectively changes this policy using
the holdout result.

Metadata retains `/1`'s `schema`, `inputStage`, `scaling`, `fitPopulation`, `factors`
and adds `stateFeatures: "asset_returns_basket_gross/1"`. Each factor descriptor
retains `feature`, `expression`, `direction`, `scope`, `transform`, `aggregation`
and adds `economicType`, `sourceUnit`, `clock`. Unit is a nonempty ASCII string of
at most 80 characters. Registered units are generated from `field_quantity` by
`scripts/export-factor-quantity-fields.py` into
`engine/atlas_quant/factor_quantity_fields.json`; `--check` detects drift.

Types are `price`, `log_price`, `positive_size`, `nonnegative_flow`,
`valuation_multiple`, `percent`, `ratio`, `currency_per_share`, `currency_amount`,
`derived`, `custom_numeric`. Clocks are `research_sessions` and
`observed_source_sessions_asof`. Scope/aggregation semantics are unchanged.

## Economic stage D → R

| Quantity | Common transform | Domain and meaning |
| --- | --- | --- |
| Adjusted stock price or named index/ETF level | `simple_return` | `D[t]/D[t-1]-1`, positive endpoints |
| Explicit `log(price)` expression | `first_difference` | `log(P[t])-log(P[t-1])` |
| Positive capitalization or share count | `log_positive` | Natural log in the declared reported unit; zero/negative missing |
| Nonnegative volume or amount | `log1p_nonnegative` | `log(1+D)` in reported units; zero retained |
| PE, PB, PS multiples | `reciprocal_nonzero` | Corresponding yield; negative multiple gives negative yield, zero missing |
| Percent fields | `percent_to_fraction` | Divide by 100 exactly once |
| Registered financial per-share or monetary values | `signed_log1p` | `sign(D) log(1+abs(D)/1 reported unit)`; zero and negative retained |
| Ratios, returns, ranks, explicit dimensionless constructions | `identity` | Keep the declared quantity |
| Unknown raw external numeric unit | No automatic default | Requires an explicit Studio transform |

Signed log is compression of nominal magnitude, **not** a yield, currency
conversion, or size-neutral accounting ratio. Changing the reported currency
unit can change this nonlinear feature. The reference unit is frozen; no claim
of invariance across differing native monetary/volume units is made. Accounting
ratios with a suitable economic denominator are distinct available factors.

Exact additional transforms:

```json
{"kind":"simple_return","lag":1,"invalid":"missing"}
{"kind":"log_return","lag":1,"invalid":"missing"}
{"kind":"first_difference","lag":1,"invalid":"missing"}
{"kind":"signed_log1p","referenceUnit":1,"invalid":"missing"}
```

The six `/1` descriptors remain available: identity, log_positive,
log1p_nonnegative, reciprocal_nonzero, percent_to_fraction(divisor=100), and
return_over_trailing_volatility(returnLag=1,volatilityWindow=20,
volatilityLag=1,ddof=1,minVolatility=1e-8,invalid=missing). The four log/domain
descriptors include `invalid: "missing"`; identity has only `kind`.
Volatility normalization uses strictly previous 20 simple returns, excluding
the current shock, and cannot fill missing sessions. It is an explicit optional
alternative, not the common price default.

Price type only accepts simple_return, log_return or return_over_trailing_volatility.
Log-price type only accepts first_difference. Event-role automatic transforms
must preserve zero: identity, percent_to_fraction, signed_log1p or
log1p_nonnegative. Missing/non-finite values remain missing until training-only
imputation. No absolute-value workaround invents valid positive prices.

## Deterministic DSL quantity algebra

The bounded safe parser is unchanged. `typed_preprocessing._combine` recursively
interprets its AST; no code execution or display-name inference is involved.

- Name: registered field quantity; unknown alias is custom_numeric. Constant:
  ratio with unit `1`. `/1` AST-shape-only behavior remains frozen separately.
- Dimensionless means ratio/derived **and** unit `1` or `ratio`.
- Unary plus preserves quantity. Unary minus of price/positive size/nonnegative
  flow becomes dimensional derived; other quantities preserve type.
- Addition/subtraction: zero is neutral except `0-price/size/flow`, which follows
  unary minus. Two dimensionless inputs give ratio. Same-unit addition retains
  common type, otherwise derived(unit). Same-unit subtraction becomes derived
  except percent/signed currency retain type and log_price minus log_price gives
  ratio. Mismatched known units are invalid; unknown units remain unknown.
- Division: same known units cancel to ratio, as do two dimensionless values.
  Dimensionless divided by valuation_multiple gives ratio. Percent divided by
  literal 100 gives ratio. Division by a dimensionless value retains numerator
  type. Dimensionless divided by another known dimensional quantity gives
  derived with unit `1/<denominator unit>`. Other known mismatches are invalid;
  unresolved external units remain unknown.
- Multiplication: percent times literal .01 gives ratio. Dimensionless times
  another quantity retains that quantity. Other physical-unit products are
  invalid rather than silently assuming an undocumented unit conversion.
  A known nonpositive literal multiplier, or negative literal divisor, changes
  price/positive-size/nonnegative-flow into dimensional derived, matching unary
  minus; it cannot silently take the log of an entirely negative series. A
  literal zero divisor is invalid. Log-price retains the first-difference rule.
- `log(price)` gives log_price with unit `log_<unit>`; `log(log_price)` is invalid.
  Other explicit log constructions give derived(unit=1). `returns`, rank,
  zscore, ts_rank and sign give ratio. `sqrt` is supported for dimensionless or
  unresolved user numeric constructions, not a silently retyped price level.
- Lag, rolling mean/min/max/sum, abs and clip preserve input quantity. Delta and
  rolling standard deviation preserve percent/signed currency, map log_price or
  dimensionless inputs to ratio, and otherwise produce dimensional derived.
- min/max with a numeric constant retain the nonconstant quantity (the constant
  is a threshold in that quantity's unit); same units preserve quantity;
  dimensionless pairs give ratio. Other known mismatches are invalid.
- Invalid known-unit children stay invalid through every outer function. Thus
  `log(close+vol)` cannot hide a unit mismatch. Deliberate `log(ext_custom)` or
  `returns(ext_custom,1)` declares a mathematical construction without claiming
  that the external series has a known financial interpretation.
- Dimensional derived quantities need an explicit Studio transform, except
  inverse-unit quantities such as absolute return per trading amount, where
  identity preserves the explicitly constructed unit. Example: a standalone
  `close-ts_mean(close,20)` requires a scale; division by close gives an admitted
  dimensionless deviation. No automatic reciprocal of a difference in PE/PB.
- `raw_close` is admitted only as a direct denominator of an explicitly declared
  per-share financial quantity. It cannot enter returns or log-price defaults.
  Such a ratio still does not certify historical share-count alignment or TTM.
- Pure global expressions reject cross-sectional rank/zscore, which cannot
  manufacture within-date variation from one global observation.

Every existing catalog recipe has a generated `automaticProcessing` record:
defined descriptor or explicit-choice status. This describes construction, not
data coverage, observed data, predictive value or a ready trading strategy.

## Observation clocks and origin features

Pure global expressions using exactly one foreign `(api, symbol)` evaluate on
that source's observed sessions before economic transformation. The transformed
series is projected as-of to the Chinese observation grid using a strictly
earlier source date and a maximum seven-calendar-day mark age. Thus a repeated
known US mark does not become an invented zero US daily return. Chinese sources,
mixed stock/global expressions and multiple-source expressions retain the
complete Chinese research grid and its missingness semantics. The immutable
validated context source, not arbitrary serialized DataFrame attributes, is
required for the native-clock path.

For asset targets, built-in change/trend inputs are `P[t]/P[t-h]-1`; volatility20
is the sample standard deviation of the last 20 simple returns; state deviation
is `P[t]/mean(last n prices)-1` for n=20,60. Frozen signed baskets keep their
existing frozen-quantity change divided by positive origin gross exposure.
Asset and basket quantities are intentionally different; a zero signed basket
state does not create an infinite return. Entry/future **labels are unchanged**:
next-session open and open at origin+1+h, both measured over current known gross.

## Training stage R → X and evidence

Training dates and mature labels alone fit correlation filtering, 1%/99%
winsor bounds, missing-value median, median center and IQR scale. Global factors
contribute once per actual training date; asset factors contribute asset rows.
Frozen parameters are reused for validation/test rows. A constant IQR uses scale
1, not an invented cross-sectional variation. Nonlinear estimator basis expansion
is a separate, audited stage after X. Portable functions take R inputs and apply
the frozen training stage exactly once.

Contemporaneous exposure OLS and forward-label OLS remain separately labelled.
No transform is accepted because it increases holdout R². Tests cover declared
numeric identities, domains, clocks and compatibility, not profitability.
Primary references and independent acceptance requirements are recorded in
`FACTOR_TRANSFORM_ACCEPTANCE.md`.
