"""A small envelope points to, rather than duplicates, the immutable artifact."""


def trial_summary(trials):
    return [{k: v for k, v in trial.items() if k != "folds"} for trial in trials]


def diagnostics_summary(diagnostics):
    result = {k: v for k, v in diagnostics.items() if k not in {"outerFolds", "finalTrials", "factorIncrement", "modelSearch", "perTargetResearch"}}
    if "modelSearch" in diagnostics:
        result["modelSearch"] = {k: v for k, v in diagnostics["modelSearch"].items() if k != "candidates"}
        result["modelSearch"]["completeArtifactPath"] = "forecasts.diagnostics.modelSearch"
    result["outerFolds"] = [{k: v for k, v in fold.items() if k != "trials"} for fold in diagnostics["outerFolds"]]
    result["finalTrials"] = trial_summary(diagnostics["finalTrials"])
    if "factorIncrement" in diagnostics:
        full = diagnostics["factorIncrement"]
        result["factorIncrement"] = {k: v for k, v in full.items() if k not in {"baselineRows", "baselineModelFits", "baselineValidation"}}
        if full.get("status") in {"available", "unavailable"}:
            result["factorIncrement"].update(baselineTotalRows=len(full["baselineRows"]),
                baselineModelFitCount=len(full["baselineModelFits"]),
                completeArtifactPath="forecasts.diagnostics.factorIncrement")
    result["completeArtifactPath"] = "forecasts.diagnostics"
    return result
