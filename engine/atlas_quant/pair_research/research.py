"""Private/local Stage 2B entry point. No hosted profile, execution or F export."""
from __future__ import annotations

from copy import deepcopy
import json
import time

from ..engine import _finite_json, ResearchError
from ..statistical_quant.core import forecast_branches
from ..statistical_quant.schema import fail
from .contract import PairContractError, digest
from .fit_contract import PORTABLE_STATUS, validate_fit_contract


def _pair_rows(rows):
    # Preserve the common numerical computation, naming the result as a state
    # change here. No inventory, execution, costs, strategy or tradability claim.
    result = []
    for row in rows:
        value = dict(row)
        value["expectedRemainingChange"] = value.pop("expectedGrossPnl")
        value["expectedRemainingChangeOverGrossBps"] = value.pop("expectedGrossBps")
        result.append(value)
    return result


def _branch(value):
    if value is None:
        return None
    result = deepcopy(value)
    result["rows"] = _pair_rows(result["rows"])
    increment = result["diagnostics"].get("factorIncrement")
    if increment and "baselineRows" in increment:
        increment["baselineRows"] = _pair_rows(increment["baselineRows"])
    return result


def _bounded_result(value, maximum_bytes):
    """Seal complete local evidence, including runtime events, or retain it on error.

    An oversized result is never returned as a bounded/publication-ready value.
    The exception keeps the untruncated in-memory partial for an explicit local
    evidence writer; callers must not rerun fitting to recover those values.
    """
    value = _finite_json(value)
    value["resultRoot"] = digest(value)
    if len(json.dumps(value, allow_nan=False, separators=(",", ":")).encode()) > maximum_bytes:
        value.pop("resultRoot")
        value.update(status="failed_result_size", complete=False, publishable=False, deploymentQualified=False,
                     factorIncrement={"status": "unavailable", "reason": "incomplete_research"})
        if value.get("main") is not None:
            value["main"]["diagnostics"]["factorIncrement"] = value["factorIncrement"]
        value["resultRoot"] = digest(value)
        error = PairContractError("CAPACITY_PAIR_RESULT", "Complete result exceeds output bytes; untruncated partial_result retained, do not refit")
        error.partial_result = value
        raise error
    return value


def run_pair_research(source, declaration, research, fit_contract, *, plan_sink=None, progress=None):
    """Rebuild/revalidate exact Samples before calling the unchanged scheduler.

    Sinks are optional process-local diagnostics. This API is not exposed by a
    service, CLI, strategy registry, or source bridge. A failed branch never
    yields a completed/publishable research result or an automatic rerun.
    """
    from ..capacity.core import FitRuntime, peak_rss_bytes

    start = time.monotonic()
    fixed = validate_fit_contract(fit_contract, source, declaration, research)
    prepared, contract, plan = fixed.preparation, fixed.contract, fixed.plan
    resources = contract["resources"]
    stage, completed, event_branches = "pre_fit", {}, {}

    def check():
        if time.monotonic() - start > resources["maxWallSeconds"]:
            fail("CAPACITY_PAIR_TIMEOUT", "Local pair elapsed-time envelope exceeded")
        if peak_rss_bytes() > resources["maxRssBytes"]:
            fail("CAPACITY_PAIR_MEMORY", "Local pair process peak-RSS envelope exceeded")

    def on_progress(event):
        if event["phase"] == "fit_started":
            event_branches[event["ordinal"]] = stage
        if progress is not None:
            progress({"branch": stage, **deepcopy(event)})

    runtime = FitRuntime(check, plan["fitBudget"]["maximumFitAttempts"], on_progress)

    def on_branch(event, value):
        nonlocal stage
        if event.endswith("_started"):
            stage = event.removesuffix("_started")
        elif event.endswith("_completed"):
            completed[event.removesuffix("_completed")] = value

    # Full input domain survives even when selection or a later baseline fails.
    base = {"format": "atlas.quant.pair_forecast.local", "version": 1,
            "fitContract": contract, "plan": plan, "preparationEvidence": prepared.evidence,
            "targets": prepared.targets, "sampleStatusRows": prepared.samples.meta.to_dict("records"),
            "sampleStatusRowsStage": "before_model_fitting",
            "executionEnabled": False, "portableFunctionStatus": PORTABLE_STATUS,
            "publishable": False, "deploymentQualified": False,
            "scope": "conditional_on_declared_pair_map_and_quantities",
            "quantityProvenanceVerified": False, "pairSelectionLeakageVerified": False,
            "sourceAuthorityVerified": False, "historicalMembershipVerified": False,
            "historicalRevisionVintageVerified": False,
            "warnings": ["LOCAL_EXPLICIT_PAIR_FORECAST_NOT_A_STRATEGY", "PAIR_FORMATION_PROVENANCE_NOT_VERIFIED",
                         "SHARED_LEGS_AND_OVERLAPPING_LABELS_ARE_NOT_INDEPENDENT",
                         "PREDICTIVE_IMPROVEMENT_IS_NOT_PROFITABILITY",
                         "TERMINAL_REFITS_USE_MATURED_PAST_TERMINAL_LABELS"]}
    if plan_sink is not None:
        plan_sink(deepcopy(plan))
    result = None
    try:
        check()
        branches = forecast_branches(fixed.controls, prepared.samples, runtime=runtime,
                                     max_forecasts=resources["maxForecastRows"],
                                     export_functions=False, branch_sink=on_branch)
        result = {**base, "status": "completed_local_forecast", "complete": True,
                  "main": _branch({k: branches[k] for k in ("rows", "fits", "diagnostics")}),
                  "baseline": _branch(branches["baseline"]),
                  "baselineStatus": "completed" if branches["baseline"] is not None else "not_applicable_no_predictors",
                  "factorResearch": branches["factorResearch"]}
        check()
        encoded = json.dumps(_finite_json(result), allow_nan=False, separators=(",", ":")).encode()
        if len(encoded) > resources["maxResultBytes"]:
            fail("CAPACITY_PAIR_RESULT", "Complete local result exceeds declared output bytes; never truncate")
    except (ResearchError, ValueError, FloatingPointError, MemoryError) as exc:
        partial = getattr(exc, "forecast_partial", None)
        if stage in {"main", "baseline"} and partial is not None:
            completed[stage] = partial
        failure = {"stage": stage, "code": getattr(exc, "code", "MemoryError" if isinstance(exc, MemoryError) else "MODEL_FIT_FAILED"),
                   "message": str(exc), "selectionTrials": getattr(exc, "selection_trials", None),
                   "forecastPhase": partial["diagnostics"]["phase"] if partial is not None else None}
        main = _branch(completed.get("main"))
        if main is not None:
            main["diagnostics"]["factorIncrement"] = {"status": "unavailable", "reason": "incomplete_research"}
        result = {**base, "status": "failed_" + stage, "complete": False,
                  "main": main, "baseline": _branch(completed.get("baseline")),
                  "baselineStatus": "failed" if stage == "baseline" else "not_completed",
                  "factorIncrement": {"status": "unavailable", "reason": "incomplete_research"},
                  "failure": failure}
    result["fitEvidence"] = {"declaredMaximum": plan["fitBudget"]["maximumFitAttempts"],
                             "actualFitAttempts": len(runtime.events),
                             "events": [{**event, "branch": event_branches[event["ordinal"]]} for event in runtime.events],
                             "elapsedSeconds": time.monotonic() - start,
                             "peakRssBytes": peak_rss_bytes(), "modelFitCountsIncludeFailedCalls": True,
                             "quantityFits": 0, "providerCalls": 0, "cloudMutations": 0}
    return _bounded_result(result, resources["maxResultBytes"])
