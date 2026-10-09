# Factor model search protocol

This is a research protocol, not a list of validated strategies. Old configurations omit the new fields and retain their original eight-candidate `auto` search.

## Configuration

```json
{"model":{"family":"mean_reversion","estimator":"auto","trainWindow":504,"refitDays":20,
  "search":{"schema":"factor-model-search/1"},"parameterSharing":"pooled"}}
```

`parameterSharing` is optional: `pooled` fits one shared parameter vector; `per_target` independently fits and selects each asset's model using the same declared train/test calendar, horizon and maturity rules. It requires `target.kind=asset_price` and at most 50 assets, including when a larger pooled capacity profile is supplied. Both the aggregate output budget and every target's fit budget are counted. This does not infer heterogeneous stock exposures from a single global coefficient.

New `auto` contains 22 predeclared candidates. Its initial 16 are the original no-change, historical drift, two Ridge, two ElasticNet and two histogram-tree specifications, plus two each of:

- `polynomial_ridge`: all linear terms, squares and pairwise interactions; alpha 10 or 100.
- `polynomial_elastic_net`: the same dictionary; alpha 0.001 or 0.01 and l1 ratio 0.5.
- `transformed_ridge`: linear, square, cubic, signed log1p and bounded signed expm1 for every processed input; alpha 10 or 100.
- `factorwise_basis`: fit a penalized univariate basis dictionary separately for each factor, retain nonzero terms, remove absolute training-correlation >=0.995 duplicates in declared order, then jointly fit ElasticNet. Alpha 0.001 or 0.01 and l1 ratio 0.5 apply at both stages. Every stage is refit inside each training fold. The complete dictionary and retained/removed terms are audited; no terminal-data-based term selection occurs.

Six reserve specifications are fixed before any data are scored: Ridge alpha 100/1000, ElasticNet alpha 0.01 with l1 ratio 0.5, polynomial Ridge alpha 1000, transformed Ridge alpha 1000, and factorwise alpha 0.1 with l1 ratio 0.5. They carry `searchStage=predeclared_refinement`. Every selection ultimately evaluates the same complete 22-candidate set. Existing non-search `auto` still has eight candidates, including its original parameter ranges.

The polynomial dictionary has a 1,024-term hard limit, with explicit failure instead of silent truncation. Factorwise selection may internally fit one regression per input plus one joint regression; this is declared separately from a candidate-level fit attempt. Regularization is not proof that selection bias is absent.

## Portable function /3

Original input semantic transforms and frozen training imputation/clipping/scaling remain unchanged. Basis expansion acts on these processed inputs `X`, never on unadjusted raw price by default.

```json
{"schema":"atlas-model-function/3","estimator":{
  "kind":"basis_linear",
  "terms":[{"kind":"power","feature":0,"degree":1},
           {"kind":"power","feature":1,"degree":2},
           {"kind":"interaction","features":[0,1]},
           {"kind":"signed_log1p","feature":0},
           {"kind":"signed_expm1","feature":1}],
  "termCenter":[0,0,0,0,0],"termScale":[1,1,1,1,1],
  "signedExpm1AbsoluteInputCap":3,
  "coefficients":[[0,0,0,0,0],[0.1,0.02,0,0,0]],"intercepts":[0,0]}}
```

This snippet only illustrates the estimator. Complete artifacts retain the existing input/schema, construction, training, scope, provenance, digest and numeric-edit fields. Construction schema may be `/1` (legacy explicit transforms) or `/2` (automatic factor transforms).

For output `o`, `G_o(X) = intercept[o] + sum_j coefficients[o][j] * (phi_j(X) - termCenter[j]) / termScale[j]`. The means and scales are fitted on the training fold. `signed_log1p(x)=sign(x) ln(1+|x|)`; `signed_expm1(x)=sign(x)(exp(min(|x|,3))-1)`; the cap is part of the function, not an adjustable coefficient. A UI can algebraically expand the term normalization into displayed coefficients/intercept without approximating the function. Powers are integers 1–3; interactions contain two strictly increasing feature indices. No executable source/eval is accepted.

For an individual stock, the primary research function is the predicted return `G_future(X)`, with `V_future=P_t*(1+G_future(X))`. For a frozen quantity state it is a change over known gross, with `V_future=P_t+scale*G_future(X)`. These identities are distinct from contemporary exposure estimation.

## Candidate functions and reports

After final inner-fold selection, each valid candidate is fitted once on mature development labels strictly before the terminal cutoff. This extra fit is included in the declared resource budget. No forecast is recomputed just to produce the report.

`forecasts.diagnostics.modelSearch` contains:

```text
schema: factor-model-search-report/1
parameterSharing: pooled | per_target
selectedCandidateId: ID selected by the frozen selection policy, possibly no_change:0
researchCandidateId: best inner-score nonbaseline candidate with a valid frozen function, or null
freezeCutoff, usesTerminalOutcomes: false
candidates[]:
  id, estimator, params, status, invalidReason, validationScore
  selected, baseline, withinHeuristicTolerance
  fit: id, fitDate, status, sequentialMaturedLabelsOnly, trainStart, trainEnd,
       informationCutoff, labelEndMax, trainDates, trainRows, featureNames,
       coefficients, intercepts, ordinary frozen transformations, optional basisFit
  functionArtifact: complete portable F, or null when candidate could not refit
  trainingMetrics: dual-output training MSE/RMSE/bias etc; not OOS;
                   rSquared=normalized-future R2, entryRSquared, rSquaredPerOutput;
                   equal-date weights, fitted-training-mean variance baseline
  trainingPlot: sample=training_in_sample, selection=uniform_row_index,
                totalRows, points[{date,targetId,actualEntry,actualFuture,fittedEntry,fittedFuture}]
```

`basisFit` stores `schema=factor-basis-fit/1`, route, terms, termCenter, termScale, dictionarySize, retainedTerms, factorSelections (per-factor initial coefficients), redundantTerms and the fixed expm1 cap. Function terms exactly match this audit. Coefficients/intercepts remain at `fit` top level.

In per-target mode candidate IDs are prefixed `targetId::`, candidates additionally carry `targetId` and `symbols`. `selectedCandidateId=per_target`, `selectedCandidateIds` and `researchCandidateIds` retain all target choices; default `researchCandidateId` is the first declared target's research candidate, not the best-performing stock. The selected prediction stream remains separate from these explanatory candidate artifacts.

A baseline winning the conservative selection rule does not erase estimated F candidates. The report should lead with the actual nonbaseline research function and show its validation status and selected-baseline comparison. It must not represent an in-sample training plot as prediction performance or imply that the displayed research candidate was deployed.

## Optional AI review boundary

Only the final primary development selection may call `runtime.review_candidates(payload)`; outer folds and the factor-free baseline never call AI. With an enabled reviewer, the primary final selection evaluates the initial 16, sends development-only inner-fold evidence to the high/max wrapper, then actually trains and scores all six reserve candidates. The payload `factor-model-review-input/1` contains development date bounds, the complete fixed candidate-set hash, completed inner-fold audits/scores, admissible IDs, `reserveCandidates` and `reserveEvaluationPending`. It excludes all outer/terminal observations and scores.

The return is `{candidateId, refinementCandidateIds?, receipt}`. The initial ID must be within the completed candidates' predeclared one-standard-error tolerance, and every refinement ID must be in the fixed reserve. Suggested IDs record the review focus; all six reserve candidates still run, so the AI cannot silently reduce the search budget or improve the factor increment by comparing unequal candidate sets. After their fresh scores are available the complete-dictionary deterministic rule chooses the winner; a stale recommendation made before reserve outcomes does not override it. The review input hash, suggested IDs and actually evaluated reserve IDs are retained. If initial candidates all fail, reserve candidates may still run before a review is attempted.

Without a configured reviewer, and for every outer/factor-free selection, all 22 candidates are evaluated directly. No arbitrary new model, executable expression, or outcome-driven retry enters this hook. An outer-fold estimate evaluates the deterministic complete-dictionary selector, not the final AI reviewer; the report explicitly records this distinction. The wrapper caps high/max calls at two per research, including per-target mode. Further research suggestions are recorded but do not authorize an unbounded retry loop or reuse of terminal outcomes.

## Statistical diagnostic targets

Existing descriptive R² and IC use `(realizedFuture-currentState)/scale`, the horizon's future change. They do not measure how well an industry's same-day return explains a member stock's same-day return. Global inputs are constant across stocks on one date, so their cross-sectional IC is undefined.

Each feature now carries its actual `inputConstruction` when automatic transforms apply. A separate `contemporaneousFit` supplies pooled and per-target univariate OLS statistics against the known current-session return (individual assets) or current-session change/gross (basket states). It is explicitly descriptive and never selection evidence. High contemporaneous R² does not establish predictive edge, and low future R² does not disprove common industry exposure.
