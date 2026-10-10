# Factor transformation acceptance

This work starts from v0.11 / main `b65c83e`. The original baijiu research,
its input snapshot, and its previous diagnostics remain immutable. A corrected
transformation is a new protocol and a new research, never a rewrite of an old
model. This document records requirements; it is not a completion certificate.

## Economic quantity before numerical conditioning

Every admitted input needs a declared economic quantity, units, asset/global
scope, observation clock, invalid domain and transformation. A display name or
the fact that an expression is compound is insufficient to establish meaning.
Returning an already meaningful ratio unchanged is a deliberate decision.

Price inputs must identify the return convention and horizon. Simple returns,
log returns and returns divided by lagged volatility are different quantities;
the last must not silently replace the first in a stock/market exposure
regression. Raw traded prices remain available for valuation denominators,
while research returns respect the source's corporate-action convention.

Size may use log positive market capitalization. Valuation multiples may become
the corresponding yields, with zero, negative and unavailable values treated
explicitly. Percent versus decimal ratios must be converted exactly once.
Nominal accounting amounts and per-share amounts retain an explicit native-unit
magnitude definition when signed-log compressed; this is not a size-neutral
ratio or yield. Accounting-to-size ratios remain separate factor constructions.
An unknown external unit requires an explicit Studio choice. Event zero must
remain zero at event admission; missing
events are not zero. Global series cannot be cross-sectionally standardized
away or counted once for each stock in training population statistics.

The Easy flow chooses defaults by economic type; any learned selection occurs
inside training/validation only. Studio overrides become part of the saved
contract. Model artifacts must expose the economic transform separately from
training clipping, imputation, centering, scaling and estimator basis expansion.

## Independent numerical acceptance

1. Independently reconstruct the original seven-stock baijiu snapshot without
   calling engine cleaning, transformation or regression helpers. Pin all input
   hashes. Check identities, dates, price/adjustment conventions and units.
2. Compute aligned stock/market/industry simple and log returns, individual and
   pooled OLS, and both contemporaneous exposure and declared forward-target
   association. Report counts, date ranges, beta and R-squared. No minimum
   R-squared is manufactured as a passing threshold.
3. For every supported economic transform, compare independent calculations
   against the engine, including invalid domains, missing sessions, training
   cutoff, shocks, broadcast factors and heterogeneous targets. Cover the
   catalog's actual admitted expressions; a large catalog count is not proof.
4. Independently reconstruct the training-only conditioning and estimator
   prediction from saved parameters. Compare Python, portable function and
   browser evaluation with declared numerical tolerances. Preserve failures.
5. Run new bounded frozen-input acceptance only after the protocol is fixed.
   Do not request the old provider data again or overwrite earlier results.
6. Require current-commit CI, source/package pins, production readback and
   browser acceptance before describing the release as deployed. Preserve the
   existing disabled strategy/execution boundaries.

## Primary methodological references

- Fama and French (1992), *The Cross-Section of Expected Stock Returns*,
  https://doi.org/10.1111/j.1540-6261.1992.tb04398.x — size as ln(ME), explicit
  accounting-to-market measures and separate treatment of negative earnings.
- Gu, Kelly and Xiu (2020), *Empirical Asset Pricing via Machine Learning*,
  https://dachxiu.chicagobooth.edu/download/ML.pdf — characteristic transforms,
  macro/stock interactions and separated training, validation and test periods.
  Its cross-sectional rank convention is an example for stock characteristics,
  not a rule for global time series or every economic quantity.
- Kenneth French Data Library, operating profitability construction,
  https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library/det_port_form_op.html
  — the denominator and accounting period are part of a factor's definition.
- MSCI, *Value-Performance Anxiety*,
  https://www.msci.com/research-and-insights/blog-post/value-performance-anxiety
  — book-to-price and earnings-yield definitions distinguish the economic input
  from its numerical conditioning.

References motivate individual choices; they do not validate this software,
these data, a particular model, or a profitable strategy.
