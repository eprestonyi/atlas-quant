"""Independent asset fits, retaining the identical research-date protocol."""
from __future__ import annotations
import copy
from dataclasses import replace
import numpy as np
from .models import metrics
from .inference import evaluate_forecast_uncertainty


def forecast_separately(samples, strategy, run, *, max_forecasts, runtime, export_functions, evidence):
    from .validation import forecast_origins
    # Enforce the total output budget once, not once per stock.
    forecast_origins(samples, strategy, max_forecasts=max_forecasts)
    groups = []
    for target_id in sorted(samples.meta.targetId.unique()):
        idx = samples.meta.index[samples.meta.targetId == target_id]
        definition = samples.definitions[target_id]
        scoped = replace(samples, X=samples.X.loc[idx], y=samples.y.loc[idx], meta=samples.meta.loc[idx],
                         definitions={target_id: definition})
        config = copy.deepcopy(strategy)
        config["universe"]["symbols"] = definition["symbols"]
        part = {"rows": [], "fits": [], "diagnostics": {"outerFolds": [], "finalTrials": []}}
        try:
            rows, fits, diagnostics = run(scoped, config, max_forecasts=max_forecasts,
                runtime=runtime, export_functions=export_functions, evidence=part)
        except Exception as exc:
            evidence["rows"].extend(part["rows"])
            evidence["fits"].extend(part["fits"])
            evidence["diagnostics"]["failedTargetId"] = target_id
            evidence["diagnostics"]["perTargetResearch"] = groups
            evidence["diagnostics"]["failedTarget"] = {"targetId": target_id, "diagnostics": part["diagnostics"]}
            raise
        evidence["rows"].extend(rows)
        evidence["fits"].extend(fits)
        groups.append({"targetId": target_id, "symbols": definition["symbols"], "diagnostics": diagnostics})
    rows, fits = evidence["rows"], evidence["fits"]
    rows.sort(key=lambda row: (row["date"], row["targetId"]))
    fits.sort(key=lambda fit: (fit["fitDate"], fit["targetId"]))
    scored = [row for row in rows if row["status"] == "valid" and row["labelMaturedAt"] is not None]
    actual = [[(row["realizedEntry"]-row["currentState"])/row["scale"],
               (row["realizedFuture"]-row["currentState"])/row["scale"]] for row in scored]
    predicted = [[(row["expectedEntry"]-row["currentState"])/row["scale"],
                  (row["expectedFuture"]-row["currentState"])/row["scale"]] for row in scored]
    result = {key: copy.deepcopy(value) for key, value in groups[0]["diagnostics"].items()
              if key not in {"modelSearch", "aggregateUncertainty", "perTarget", "outerFolds", "finalTrials"}}
    result.update(metrics=metrics(np.asarray(predicted).reshape(-1, 2), np.asarray(actual).reshape(-1, 2), [row["date"] for row in scored]),
                  validForecasts=sum(row["status"] == "valid" for row in rows),
                  invalidForecasts=sum(row["status"] != "valid" for row in rows),
                  invalidModelFits=sum(fit.get("status") == "invalid" for fit in fits),
                  parameterSharing="per_target", modelGroups=len(groups),
                  perTarget=[item for group in groups for item in group["diagnostics"]["perTarget"]],
                  outerFolds=[dict(fold, targetId=group["targetId"]) for group in groups for fold in group["diagnostics"]["outerFolds"]],
                  finalTrials=[dict(trial, id=group["targetId"]+"::"+trial["id"], targetId=group["targetId"])
                               for group in groups for trial in group["diagnostics"]["finalTrials"]],
                  selectedModel={"id": "per_target", "estimator": "per_target", "params": {}},
                  targetSelections=[{"targetId": group["targetId"], "symbols": group["symbols"],
                                     "selectedModel": group["diagnostics"]["selectedModel"]} for group in groups])
    result["selectionAudit"].update(parameterSharing="per_target", modelGroups=len(groups),
                                    fitBudgetPerSelection=result["selectionAudit"]["fitBudgetPerSelection"]*len(groups))
    result["aggregateUncertainty"] = evaluate_forecast_uncertainty(rows, strategy["target"]["horizonSessions"], strategy["research"]["observationDays"])
    if "modelSearch" in groups[0]["diagnostics"]:
        combined = copy.deepcopy(groups[0]["diagnostics"]["modelSearch"])
        combined.update(candidates=[], selectedCandidateId="per_target", researchCandidateId=None,
                        selectedCandidateIds=[], researchCandidateIds=[], parameterSharing="per_target")
        for group in groups:
            search = group["diagnostics"]["modelSearch"]
            prefix = group["targetId"]+"::"
            combined["selectedCandidateIds"].append(prefix+search["selectedCandidateId"])
            if search["researchCandidateId"]:
                combined["researchCandidateIds"].append(prefix+search["researchCandidateId"])
            combined["candidates"].extend(dict(candidate, id=prefix+candidate["id"],
                                              targetId=group["targetId"], symbols=group["symbols"])
                                           for candidate in search["candidates"])
        combined["researchCandidateId"] = next(iter(combined["researchCandidateIds"]), None)
        result["modelSearch"] = combined
    evidence["diagnostics"].update(result)
    return rows, fits, result
