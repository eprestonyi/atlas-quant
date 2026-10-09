"""Preserve fitted research candidates independently from the admitted predictor."""
from __future__ import annotations
import numpy as np
import pandas as pd
from . import models
from .model_function import export_function
from .schema import digest

BASELINES = {"no_change", "historical_drift"}


def training_metrics(prediction, truth, dates):
    result = models.metrics(prediction, truth, dates)
    mean = pd.DataFrame(truth, index=dates).groupby(level=0).mean().mean().to_numpy()
    variance = pd.DataFrame((truth-mean)**2, index=dates).groupby(level=0).mean().mean().to_numpy()
    loss = pd.DataFrame((prediction-truth)**2, index=dates).groupby(level=0).mean().mean().to_numpy()
    r_squared = [float(1-mse/var) if var > 1e-20 else None for mse, var in zip(loss, variance)]
    result.update(rSquared=r_squared[1], entryRSquared=r_squared[0], rSquaredPerOutput=r_squared,
                  rSquaredWeighting="equal_weight_daily_average", rSquaredBaseline="training_weighted_output_mean",
                  sample="training_in_sample")
    return result


def freeze_candidates(samples, strategy, trials, winner, development, cutoff, train, mature_mask, runtime=None):
    """One final development fit per valid candidate; terminal data is never read."""
    candidates = []
    for trial in trials:
        entry = {"id": trial["id"], "estimator": trial["estimator"], "params": trial["params"],
                 "status": trial["status"], "invalidReason": trial["invalidReason"],
                 "validationScore": trial["score"], "selected": trial["id"] == winner["id"],
                 "baseline": trial["estimator"] in BASELINES,
                 "withinHeuristicTolerance": trial["withinHeuristicTolerance"],
                 "functionArtifact": None, "trainingMetrics": None}
        if trial["status"] != "valid":
            candidates.append(entry)
            continue
        spec = {key: trial[key] for key in ("id", "estimator", "params")}
        try:
            fitted, audit = train(samples, spec, development, cutoff, strategy, runtime=runtime)
            audit = {"id": "candidate_fit_"+digest({"spec": spec, "cutoff": cutoff, "audit": audit})[:24],
                     "fitDate": cutoff, "status": "valid", "sequentialMaturedLabelsOnly": True, **audit}
            mask = mature_mask(samples, cutoff, development, strategy["model"]["trainWindow"])
            predictions = fitted.predict(samples.X.loc[mask])
            truth = samples.y.loc[mask].to_numpy()
            entry.update(functionArtifact=export_function(fitted, audit, strategy), fit=audit,
                         trainingMetrics=training_metrics(predictions, truth, samples.meta.loc[mask, "date"].to_numpy()))
            # Fixed index sampling, never select the most flattering observations.
            selected = np.unique(np.linspace(0, len(predictions)-1, min(96, len(predictions)), dtype=int))
            meta = samples.meta.loc[mask].iloc[selected]
            entry["trainingPlot"] = {"sample": "training_in_sample", "selection": "uniform_row_index",
                "totalRows": len(predictions), "points": [{"date": row.date, "targetId": row.targetId,
                    "actualEntry": float(truth[i, 0]), "actualFuture": float(truth[i, 1]),
                    "fittedEntry": float(predictions[i, 0]), "fittedFuture": float(predictions[i, 1])}
                    for i, row in zip(selected, meta.itertuples())]}
        except (ValueError, FloatingPointError) as exc:
            if str(getattr(exc, "code", "")).startswith("CAPACITY_"):
                exc.candidate_functions = [*candidates, {**entry, "status": "interrupted",
                    "invalidReason": getattr(exc, "code", type(exc).__name__)}]
                raise
            entry.update(status="refit_invalid", invalidReason=getattr(exc, "code", "MODEL_FIT_FAILED"))
        except Exception as exc:
            exc.candidate_functions = [*candidates, {**entry, "status": "interrupted",
                "invalidReason": getattr(exc, "code", type(exc).__name__)}]
            raise
        candidates.append(entry)
    fitted = [entry for entry in candidates if not entry["baseline"] and entry["functionArtifact"] is not None]
    research = min(fitted, key=lambda entry: (entry["validationScore"], entry["id"])) if fitted else None
    return {"schema": "factor-model-search-report/1", "parameterSharing": strategy["model"].get("parameterSharing", "pooled"),
            "selectedCandidateId": winner["id"], "researchCandidateId": research["id"] if research else None,
            "candidates": candidates, "freezeCutoff": cutoff, "usesTerminalOutcomes": False,
            "trainingPlotIsOutOfSample": False, "researchCandidateIsDeploymentQualified": False,
            "target": "normalized_entry_and_future_changes", "assetReturnIdentity": "V_future = P_t * (1 + G_future(X_t))",
            "selectionMeaning": "researchCandidate_is_best_nonbaseline_inner_score; selectedCandidate_uses_complexity_rule"}
