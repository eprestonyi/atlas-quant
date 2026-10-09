"""Closed local Stage 2B controls and a complete no-fit temporal/resource plan."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math

from ..engine import _finite_json
from ..statistical_quant.core import declared_fit_budget
from ..statistical_quant.models import candidates
from ..statistical_quant.validation import folds, forecast_origins, mature_mask
from .contract import digest, keys, require
from .samples import build_samples

FORMAT = "atlas.quant.pair_fit.local"
MAX_FITS = 512
MAX_FORECAST_ROWS = 25000
MAX_RESULT_BYTES = 24 * 1024 * 1024
MAX_RSS_BYTES = 1024 * 1024 * 1024
PORTABLE_STATUS = "NOT_IMPLEMENTED_FOR_THIS_TARGET_PROTOCOL"


@dataclass
class PreparedFit:
    preparation: object
    contract: dict
    plan: dict
    controls: dict


def _number(value, name, lo, hi, *, integer=False):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    require(finite and lo <= value <= hi
            and (not integer or type(value) is int),
            "PAIR_FIT_CONTROL", f"{name} must be an explicit finite {'integer' if integer else 'number'} in {lo}..{hi}")


def _controls(model, preprocess, validation, resources):
    keys(model, {"family", "estimator", "trainWindow", "refitDays"}, "local model")
    require(model["family"] == "pair_reversion" and model["estimator"] == "auto",
            "PAIR_FIT_CONTROL", "Only the fixed pair_reversion finite auto candidate set is supported")
    _number(model["trainWindow"], "trainWindow", 120, 366, integer=True)
    _number(model["refitDays"], "refitDays", 1, 126, integer=True)
    keys(preprocess, {"winsorize", "standardize", "decorrelation", "correlationThreshold"}, "preprocess")
    require(type(preprocess["winsorize"]) is bool and type(preprocess["standardize"]) is bool
            and preprocess["decorrelation"] in ("none", "drop_correlated"),
            "PAIR_FIT_CONTROL", "Explicit supported training-only transforms required")
    _number(preprocess["correlationThreshold"], "correlationThreshold", .5, 1)
    # Stage 2B's independently versioned contract remains fraction-only. General
    # statistical research's optional testStart must not silently extend it.
    keys(validation, {"holdoutFraction", "minTrainDates", "innerFolds", "outerFolds"}, "validation")
    for key, lo, hi, integer in (("holdoutFraction", .1, .4, False), ("minTrainDates", 40, 252, True),
                                 ("innerFolds", 2, 3, True), ("outerFolds", 2, 3, True)):
        _number(validation[key], key, lo, hi, integer=integer)
    require(validation["minTrainDates"] <= model["trainWindow"], "PAIR_FIT_CONTROL",
            "minTrainDates exceeds trainWindow")
    keys(resources, {"maxFitAttempts", "maxForecastRows", "maxWallSeconds", "maxRssBytes", "maxResultBytes"},
         "local resources")
    for key, lo, hi in (("maxFitAttempts", 1, MAX_FITS), ("maxForecastRows", 1, MAX_FORECAST_ROWS),
                         ("maxWallSeconds", 1, 300), ("maxRssBytes", 128 * 1024 * 1024, MAX_RSS_BYTES),
                         ("maxResultBytes", 1, MAX_RESULT_BYTES)):
        _number(resources[key], key, lo, hi, integer=True)


def _training_plan(samples, dates, cutoff, controls):
    mask = mature_mask(samples, cutoff, dates, controls["model"]["trainWindow"])
    actual = sorted(samples.meta.loc[mask, "date"].unique())
    require(len(actual) >= controls["validation"]["minTrainDates"], "PAIR_FIT_INSUFFICIENT_DATA",
            "A prescribed training fold has insufficient mature dates; no fit started")
    return {"cutoff": cutoff, "trainDates": len(actual), "trainRows": int(mask.sum()),
            "trainStart": actual[0], "trainEnd": actual[-1],
            "entryLabelEndMax": samples.meta.loc[mask, "entryDate"].max(),
            "exitLabelEndMax": samples.meta.loc[mask, "targetDate"].max()}


def _selection_plan(samples, dates, controls):
    v = controls["validation"]
    schedule = folds(dates, v["innerFolds"], v["minTrainDates"], controls["target"]["horizonSessions"] + 1)
    result = []
    for training, testing in schedule:
        fit = _training_plan(samples, training, testing[0], controls)
        score = samples.meta.date.isin(testing) & samples.meta.inputValid & samples.y.notna().all(axis=1)
        require(bool(score.any()), "PAIR_FIT_INSUFFICIENT_DATA", "A prescribed validation fold has no mature labels")
        result.append({**fit, "testStart": testing[0], "testEnd": testing[-1], "scoreRows": int(score.sum())})
    return result


def _plan(prepared, controls, resources, control_root):
    samples = prepared.samples
    require(bool(samples.definitions), "PAIR_FIT_EMPTY_TARGET_SCOPE", "Empty T is a no-fit preparation, not a forecast experiment")
    holdout, origins = forecast_origins(samples, controls, max_forecasts=resources["maxForecastRows"])
    development = sorted(samples.meta.loc[mature_mask(samples, holdout), "date"].unique())
    gap, v = controls["target"]["horizonSessions"] + 1, controls["validation"]
    outer = folds(development, v["outerFolds"], v["minTrainDates"] + 2 * (gap + 10), gap)
    outer_plan = []
    for training, testing in outer:
        inner_dates = sorted(samples.meta.loc[mature_mask(samples, testing[0], training), "date"].unique())
        outer_plan.append({"testStart": testing[0], "testEnd": testing[-1],
                           "innerFolds": _selection_plan(samples, inner_dates, controls),
                           "fit": _training_plan(samples, inner_dates, testing[0], controls)})
    selection = _selection_plan(samples, development, controls)
    first_terminal = _training_plan(samples, None, samples.meta.loc[origins, "date"].min(), controls)
    budget = declared_fit_budget(controls, samples, max_forecasts=resources["maxForecastRows"])
    require(budget["maximumFitAttempts"] <= resources["maxFitAttempts"], "PAIR_FIT_BUDGET",
            "Complete main/baseline fit budget exceeds the declared bound; no fit started")
    # Actual input/status byte size is known before fitting. Reserve conservatively
    # for bounded rows, trial audits and diagnostics; verify actual output too.
    evidence = _finite_json({"targets": prepared.targets, "sampleStatusRows": samples.meta.to_dict("records")})
    input_bytes = len(json.dumps(evidence, allow_nan=False, separators=(",", ":")).encode())
    estimate = input_bytes + len(origins) * budget["branches"] * 3072 + budget["maximumFitAttempts"] * 32768 + 512 * 1024
    require(estimate <= resources["maxResultBytes"], "PAIR_FIT_BUDGET",
            "Complete result reservation exceeds the declared byte bound; no fit started")
    body = {"format": "atlas.quant.pair_fit.plan", "version": 1, "controlRoot": control_root,
            "researchRoot": prepared.research["researchRoot"],
            "targetDeclarationRoot": prepared.targets["declarationRoot"],
            "targetScopeRoot": prepared.targets["targetScopeRoot"],
            "featureInputRoot": prepared.evidence["featureInputRoot"],
            "sourceDomainRoot": prepared.targets["sourceDomainRoot"],
            "priceProjectionRoot": prepared.targets["priceProjectionRoot"],
            "sampleRows": len(samples.meta), "forecastRowsPerBranch": len(origins),
            "holdoutStart": holdout, "outerFolds": outer_plan,
            "terminalSelectionFolds": selection, "firstTerminalFit": first_terminal,
            "originPlan": [{"date": row.date, "targetId": row.targetId,
                            "entryDate": row.entryDate, "targetDate": row.targetDate,
                            "inputValid": bool(row.inputValid), "invalidReason": row.invalidReason}
                           for row in samples.meta.itertuples()],
            "candidateSet": deepcopy(candidates("auto")), "fitBudget": budget,
            "resources": deepcopy(resources), "estimatedResultBytes": estimate,
            "allocationEnvelopeNotMeasuredCapacity": True,
            "inFlightHardLimitsRequireIsolatedSupervisor": True,
            "trainingWeighting": "equal_weight_sample_rows", "scoringWeighting": "equal_weight_daily_average",
            "terminalRefitsMayUseMaturedPastTerminalLabels": True,
            "failurePolicy": "retain_fixed_domain_and_failed_branch_no_retry_or_candidate_fallback",
            "portableFunctionStatus": PORTABLE_STATUS}
    return {**body, "planRoot": digest(body)}


def _assemble(source, declaration, research, *, model, preprocess, validation, resources):
    _controls(model, preprocess, validation, resources)
    prepared = build_samples(source, declaration, research)
    controls = {"model": deepcopy(model), "preprocess": deepcopy(preprocess), "validation": deepcopy(validation),
                "target": {"horizonSessions": declaration["horizonSessions"]},
                "research": {"observationDays": research["observationDays"]}, "factors": deepcopy(research["factors"])}
    body = {"format": FORMAT, "version": 1, "stage": "2B_local_forecast",
            "researchRoot": research["researchRoot"], "targetDeclarationRoot": declaration["declarationRoot"],
            "targetScopeRoot": declaration["targetScopeRoot"], "sourceDomainRoot": declaration["sourceDomainRoot"],
            "priceProjectionRoot": declaration["priceProjectionRoot"],
            "featureInputRoot": prepared.evidence["featureInputRoot"],
            "targetTiming": declaration["targetTiming"], "horizonSessions": declaration["horizonSessions"],
            "model": controls["model"], "preprocess": controls["preprocess"], "validation": controls["validation"],
            "resources": deepcopy(resources), "candidateSetHash": digest(candidates("auto")),
            "execution": {"enabled": False}, "portableFunctionStatus": PORTABLE_STATUS}
    plan = _plan(prepared, controls, resources, digest(body))
    with_plan = {**body, "fitPlanRoot": plan["planRoot"]}
    contract = {**with_plan, "fitContractRoot": digest(with_plan)}
    return PreparedFit(prepared, contract, plan, controls)


def declare_fit_contract(source, declaration, research, *, model, preprocess, validation, resources):
    """No fitting: all bindings, chronological sufficiency and budgets freeze now."""
    return _assemble(source, declaration, research, model=model, preprocess=preprocess,
                     validation=validation, resources=resources).contract


def validate_fit_contract(value, source, declaration, research):
    keys(value, {"format", "version", "stage", "researchRoot", "targetDeclarationRoot", "targetScopeRoot",
                 "sourceDomainRoot", "priceProjectionRoot", "featureInputRoot", "targetTiming", "horizonSessions",
                 "model", "preprocess", "validation", "resources", "candidateSetHash", "execution",
                 "portableFunctionStatus", "fitPlanRoot", "fitContractRoot"}, "local fit contract")
    keys(value["execution"], {"enabled"}, "execution")
    require(type(value["version"]) is int and value["version"] == 1 and value["execution"]["enabled"] is False
            and type(value["horizonSessions"]) is int, "PAIR_FIT_CONTROL", "Only versioned local forecast-only fitting is supported")
    expected = _assemble(source, declaration, research, model=value["model"], preprocess=value["preprocess"],
                         validation=value["validation"], resources=value["resources"])
    require(value == expected.contract, "PAIR_FIT_BINDING", "Fit/source/target/feature/plan identity differs")
    return expected
