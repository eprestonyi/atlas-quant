"""Matched incremental forecast evidence; changing q is not a feature ablation."""
from __future__ import annotations
import numpy as np


def compare_factor_increment(rows, baseline_rows, factor_columns, fits, diagnostics):
    baseline = {(x["date"], x["targetId"]): x for x in baseline_rows}
    original = {(x["date"], x["targetId"]): x for x in rows}
    if len(original) != len(rows) or len(baseline) != len(baseline_rows) or set(original) != set(baseline):
        raise ValueError("Factor comparison requires the same unique forecast origins and frozen targets")
    date_losses = {}
    for row in rows:
        other = baseline[(row["date"], row["targetId"])]
        if row["status"] != "valid" or other["status"] != "valid" or row["labelMaturedAt"] is None or other["labelMaturedAt"] is None:
            continue
        if any(row[key] != other[key] for key in ("scale", "currentState", "realizedEntry", "realizedFuture", "entryDate", "targetDate")):
            raise ValueError("Factor comparison cannot compare different realized targets or normalization")
        truth = np.array([row["realizedEntry"], row["realizedFuture"]])
        full = np.array([row["expectedEntry"], row["expectedFuture"]])
        state = np.array([other["expectedEntry"], other["expectedFuture"]])
        scale = row["scale"]
        date_losses.setdefault(row["date"], []).append([
            float(np.mean(((full-truth)/scale)**2)),
            float(np.mean(((state-truth)/scale)**2))])
    daily = [{"date": date, "observations": len(losses),
              "withFactorsMse": float(np.mean(losses, axis=0)[0]),
              "stateOnlyMse": float(np.mean(losses, axis=0)[1])}
             for date, losses in sorted(date_losses.items())]
    full_loss = float(np.mean([x["withFactorsMse"] for x in daily])) if daily else None
    state_loss = float(np.mean([x["stateOnlyMse"] for x in daily])) if daily else None
    full_valid = sum(x["status"] == "valid" for x in rows)
    base_valid = sum(x["status"] == "valid" for x in baseline_rows)
    matched = sum(x["observations"] for x in daily)
    coverage = {"fullValidRows": full_valid, "baselineValidRows": base_valid,
        "fullMatureRows": sum(x["labelMaturedAt"] is not None for x in rows),
        "baselineMatureRows": sum(x["labelMaturedAt"] is not None for x in baseline_rows),
        "fullValidMatureRows": sum(x["status"] == "valid" and x["labelMaturedAt"] is not None for x in rows),
        "baselineValidMatureRows": sum(x["status"] == "valid" and x["labelMaturedAt"] is not None for x in baseline_rows),
        "matchedRows": matched, "matchedDates": len(daily),
        "fullValidUnmatchedRows": full_valid-matched, "baselineValidUnmatchedRows": base_valid-matched,
        "fullUnavailableModelRows": sum(x.get("invalidReason") == "model_unavailable" for x in rows),
        "baselineUnavailableModelRows": sum(x.get("invalidReason") == "model_unavailable" for x in baseline_rows),
        "fullInvalidRows": len(rows)-full_valid, "baselineInvalidRows": len(baseline_rows)-base_valid}
    # Keep all baseline origins, including untraded/invalid/tail, for inspection.
    return {"status": "available" if daily else "unavailable", "featuresRemoved": factor_columns,
            "method": "independently_selected_state_only_same_targets_masks_folds_and_candidate_budget",
            "pairedDates": len(daily), "pairedObservations": sum(x["observations"] for x in daily),
            "withFactorsMse": full_loss, "stateOnlyMse": state_loss,
            "dateBalancedMseImprovement": state_loss-full_loss if daily else None,
            "relativeMseImprovement": 1-full_loss/state_loss if daily and state_loss>1e-20 else None,
            "dailyLosses": daily, "baselineRows": baseline_rows, "baselineModelFits": fits,
            "baselineValidation": diagnostics, "hedgeFactorsAblated": False,
            "sameEventAndMissingInputMask": True, "significanceTested": False,
            "bothModelValidOnly": True, "coverage": coverage,
            "outputValidityMasksIdentical": all((original[k]["status"] == "valid") == (baseline[k]["status"] == "valid") for k in original),
            "coverageInterpretation": "Matched valid and mature predictions only; failures and unmatched origins remain in complete artifacts, not scored as zero error.",
            "causalAttribution": False, "profitabilityEstablished": False}
