"""Purged nested selection and sequential out-of-sample, immutable forecasts."""
from __future__ import annotations
from bisect import bisect_left
import numpy as np
import pandas as pd
from . import models
from .schema import digest, fail, MAX_FORECASTS


def mature_mask(samples, before, allowed_dates=None, window=None):
    m = samples.meta
    mask = m.inputValid & samples.y.notna().all(axis=1) & m.targetDate.notna() & (m.targetDate < before) & (m.date < before)
    if allowed_dates is not None:
        mask &= m.date.isin(allowed_dates)
    if window:
        dates = sorted(m.loc[mask, "date"].unique())[-window:]
        mask &= m.date.isin(dates)
    return mask


def folds(dates, count, minimum, gap):
    n = len(dates)
    first = max(minimum+gap+2, n//2)
    size = (n-first)//count
    if size < 5:
        fail("INSUFFICIENT_FORECAST_DATA", "嵌套时间验证需要更多已成熟观察日期；请延长区间或降低观察间隔")
    return [(dates[:first+i*size], dates[first+i*size:first+(i+1)*size] if i<count-1 else dates[first+i*size:]) for i in range(count)]


def _train(samples, spec, train_dates, cutoff, strategy, runtime=None):
    mask = mature_mask(samples, cutoff, train_dates, strategy["model"]["trainWindow"])
    actual_dates = sorted(samples.meta.loc[mask, "date"].unique())
    if len(actual_dates) < strategy["validation"]["minTrainDates"]:
        fail("INSUFFICIENT_FORECAST_DATA", "当前拟合截止前的已成熟训练观察日不足")
    if runtime is not None:
        runtime.before_fit(spec, cutoff, int(mask.sum()))
    model = None
    try:
        try:
            kwargs = {"automatic_metadata": samples.automatic_preprocessing,
                      "training_dates": samples.meta.loc[mask, "date"].tolist()} if "automatic" in strategy["preprocess"] else {}
            model = models.fit(spec, samples.X.loc[mask], samples.y.loc[mask], strategy["preprocess"], **kwargs)
        finally:
            if runtime is not None:
                runtime.after_fit()
    except Exception as exc:
        if model is not None:
            # after_fit can reject a completed fit on time/RSS limits. Keep its
            # audit, without admitting the model or invoking fitting again.
            exc.interrupted_fit_audit = {
                "trainStart": actual_dates[0], "trainEnd": actual_dates[-1],
                "informationCutoff": cutoff, "labelEndMax": samples.meta.loc[mask, "targetDate"].max(),
                "trainDates": len(actual_dates), **model.audit}
        raise
    audit = {"trainStart": actual_dates[0], "trainEnd": actual_dates[-1],
             "informationCutoff": cutoff, "labelEndMax": samples.meta.loc[mask, "targetDate"].max(),
             "trainDates": len(actual_dates), **model.audit}
    return model, audit


def _complexity(spec):
    rank = {"no_change": 0, "historical_drift": 1, "ridge": 2, "elastic_net": 3, "hist_gradient_boosting": 4}
    p = spec["params"]
    # A deterministic, predeclared preference, not an estimated degrees of freedom.
    if spec["estimator"] == "hist_gradient_boosting":
        within = (p["max_leaf_nodes"], -p["l2_regularization"])
    else:
        within = (-p.get("alpha", 0), -p.get("l1_ratio", 0))
    return (rank[spec["estimator"]], *within, spec["id"])


def select(samples, specs, dates, strategy, runtime=None):
    runtime_args = {} if runtime is None else {"runtime": runtime}
    schedule = folds(dates, strategy["validation"]["innerFolds"], strategy["validation"]["minTrainDates"], strategy["target"]["horizonSessions"]+1)
    trials = []
    for spec in specs:
        scores = []
        audits = []
        reason = None
        for training, testing in schedule:
            audit = None
            phase = "fit"
            try:
                model, audit = _train(samples, spec, training, testing[0], strategy, **runtime_args)
                phase = "score"
                valid = samples.meta.date.isin(testing) & samples.meta.inputValid & samples.y.notna().all(axis=1)
                if not valid.any():
                    fail("INSUFFICIENT_FORECAST_DATA", "验证折没有可评分标签")
                pred = model.predict(samples.X.loc[valid])
                with np.errstate(over="ignore", invalid="ignore"):
                    errors = np.mean((pred-samples.y.loc[valid].to_numpy())**2, axis=1)
                if not np.isfinite(errors).all():
                    fail("INVALID_FORECAST", "候选验证损失超出有限数值范围")
                # Equal dates, rather than treating correlated stock rows as IID.
                score = float(pd.Series(errors, index=samples.meta.loc[valid, "date"]).groupby(level=0).mean().mean())
                scores.append(score)
                audits.append({"testStart": testing[0], "testEnd": testing[-1], "score": score, **audit})
            except Exception as exc:
                if (str(getattr(exc, "code", "")).startswith("CAPACITY_")
                        or not isinstance(exc, (ValueError, FloatingPointError))):
                    # Only completed folds are in `folds`; neither their mean
                    # nor this unfinished candidate is a valid selection score.
                    interrupted = {**spec, "status": "interrupted", "score": None,
                                   "invalidReason": getattr(exc, "code", type(exc).__name__),
                                   "folds": audits, "completedFoldScores": scores,
                                   "interruptedFold": {"index": len(audits), "phase": phase,
                                       "testStart": testing[0], "testEnd": testing[-1],
                                       "fit": audit if audit is not None else getattr(exc, "interrupted_fit_audit", None)},
                                   "foldScoreStd": None, "foldScoreHeuristicSE": None,
                                   "complexityPreference": None,
                                   "heuristicIsConfidenceInterval": False}
                    exc.selection_trials = [*trials, interrupted]
                    raise
                reason = getattr(exc, "code", "MODEL_FIT_FAILED")
                break
        trials.append({**spec, "score": float(np.mean(scores)) if reason is None else None,
                       "status": "valid" if reason is None else "invalid", "invalidReason": reason, "folds": audits,
                       "foldScoreStd": float(np.std(scores, ddof=1)) if reason is None and len(scores)>1 else None,
                       "foldScoreHeuristicSE": float(np.std(scores, ddof=1)/np.sqrt(len(scores))) if reason is None and len(scores)>1 else None,
                       "complexityPreference": list(_complexity(spec)), "heuristicIsConfidenceInterval": False})
    valid = [x for x in trials if x["status"] == "valid" and np.isfinite(x["score"])]
    if not valid:
        try:
            fail("INSUFFICIENT_FORECAST_DATA", "没有候选完成全部时间验证折")
        except ValueError as exc:
            # Diagnostics only: preserve the original code/message and algorithm.
            # A local caller may retain this evidence without treating it as a
            # successful selection or retrying a different candidate universe.
            exc.selection_trials = trials
            raise
    best = min(valid, key=lambda x: (x["score"], _complexity(x)))
    tolerance = best["foldScoreHeuristicSE"] or 0.0
    admissible = [x for x in valid if x["score"] <= best["score"]+tolerance]
    winner = min(admissible, key=_complexity)
    for trial in trials:
        trial.update(selected=trial["id"] == winner["id"], selectionRule="one_standard_error_complexity_heuristic",
                     minimumMeanScore=best["score"], admissibleScoreCeiling=best["score"]+tolerance,
                     withinHeuristicTolerance=trial in admissible)
    return {k: winner[k] for k in ("id", "estimator", "params")}, trials


def _records(samples, idx, prediction, fit_id, strategy):
    records = []
    try:
        return _build_records(samples, idx, prediction, fit_id, strategy, records)
    except Exception as exc:
        exc.forecast_rows = records
        raise


def _build_records(samples, idx, prediction, fit_id, strategy, records):
    for row_i, values in zip(idx, prediction):
        row = samples.meta.loc[row_i]
        valid = bool(row.inputValid and np.isfinite(values).all() and row.entryDate and row.targetDate)
        reason = row.invalidReason
        if reason is None and (not row.entryDate or not row.targetDate):
            reason = "target_outside_available_calendar"
        if reason is None and not np.isfinite(values).all():
            reason = "model_unavailable"
        current, scale = float(row.currentState), float(row.scale)
        with np.errstate(over="ignore", invalid="ignore"):
            ve, vx = current+scale*values[0], current+scale*values[1]
        error = float(row.realizedFuture-vx) if np.isfinite(row.realizedFuture) and np.isfinite(vx) else None
        if np.isfinite(values).all():
            with np.errstate(over="ignore", invalid="ignore"):
                derived = [current, scale, ve, vx, current-vx, vx-current, vx-ve,
                           (values[1]-values[0])*10000]
            if error is not None:
                derived.append(error)
            if not np.isfinite(derived).all():
                fail("INVALID_FORECAST", "条件值还原或预测误差超出有限数值范围；不保存失真的有效预测")
        identity = {"date": row.date, "targetId": row.targetId, "modelFitId": fit_id, "entry": float(values[0]), "exit": float(values[1])}
        # Unknown forecasts still need stable identities without NaN JSON hashes.
        identity = {k: v if not isinstance(v, float) or np.isfinite(v) else None for k, v in identity.items()}
        records.append({"forecastId": "forecast_"+digest(identity)[:32], "date": row.date, "targetId": row.targetId,
                        "modelFitId": fit_id, "informationCutoff": row.date+"_AFTER_CLOSE",
                        "entryDate": row.entryDate, "targetDate": row.targetDate, "horizonSessions": strategy["target"]["horizonSessions"],
                        "currentState": current, "scale": scale, "expectedEntry": float(ve), "expectedFuture": float(vx),
                        "edgeGap": float(current-vx), "expectedChange": float(vx-current),
                        "expectedGrossPnl": float(vx-ve), "expectedGrossBps": float((values[1]-values[0])*10000),
                        "realizedEntry": float(row.realizedEntry), "realizedFuture": float(row.realizedFuture),
                        "forecastError": error, "labelMaturedAt": row.targetDate if np.isfinite(row.realizedFuture) and np.isfinite(row.realizedEntry) else None,
                        "status": "valid" if valid else "invalid", "invalidReason": reason, "uncertainty": None})
    return records


def forecast_origins(samples, strategy, *, max_forecasts=None):
    """Pre-fit terminal origin plan, independent of predictions or model success."""
    dates = samples.dates
    eligible_calendar = dates[samples.start_index:]
    validation = strategy["validation"]
    boundary = (bisect_left(eligible_calendar, validation["testStart"])
                if "testStart" in validation
                else int(len(eligible_calendar)*(1-validation["holdoutFraction"])))
    if boundary < 1 or len(eligible_calendar)-boundary < 10:
        fail("INSUFFICIENT_FORECAST_DATA", "终端报告窗口不足")
    holdout = eligible_calendar[boundary]
    indices = samples.meta.index[samples.meta.date >= holdout]
    max_forecasts = MAX_FORECASTS if max_forecasts is None else max_forecasts
    if len(indices) > max_forecasts:
        fail("FORECAST_BUDGET", f"完整预测超过{max_forecasts}条；请降低观察频率或减少标的")
    return holdout, indices


def forecast(samples, strategy, *, max_forecasts=None, runtime=None, export_functions=True):
    # References to already produced evidence, populated by the original
    # scheduler. Successful return values and exception code/message stay
    # unchanged. No refitting, re-prediction or new scoring during recovery.
    evidence = {"rows": [], "fits": [], "diagnostics": {
        "phase": "planning", "outerFolds": [], "finalTrials": [],
        "selectedModel": None, "currentOuter": None, "currentTerminal": None}}
    try:
        return _forecast(samples, strategy, max_forecasts=max_forecasts, runtime=runtime,
                         export_functions=export_functions, evidence=evidence)
    except Exception as exc:
        diagnostics = evidence["diagnostics"]
        trials = getattr(exc, "selection_trials", None)
        if trials is not None:
            if diagnostics["phase"] == "outer_selection":
                diagnostics["currentOuter"]["trials"] = trials
            elif diagnostics["phase"] == "final_selection":
                diagnostics["finalTrials"] = trials
        audit = getattr(exc, "interrupted_fit_audit", None)
        if audit is not None:
            current = diagnostics["currentTerminal"] or diagnostics["currentOuter"]
            if current is not None:
                current["interruptedFitAudit"] = audit
        evidence["rows"].extend(getattr(exc, "forecast_rows", []))
        diagnostics.update(status="interrupted", complete=False, publishable=False,
                           failureCode=getattr(exc, "code", type(exc).__name__),
                           factorIncrement={"status": "unavailable", "reason": "incomplete_research"})
        evidence.update(status="interrupted", complete=False)
        exc.forecast_partial = evidence
        raise


def _forecast(samples, strategy, *, max_forecasts, runtime, export_functions, evidence):
    from ..engine import ResearchError
    runtime_args = {} if runtime is None else {"runtime": runtime}
    dates = samples.dates
    holdout, indices = forecast_origins(samples, strategy, max_forecasts=max_forecasts)
    partial = evidence["diagnostics"]
    partial.update(holdoutStart=holdout, holdoutEnd=dates[-1], expectedForecastRows=len(indices))
    development = sorted(samples.meta.loc[mature_mask(samples, holdout), "date"].unique())
    specs = models.candidates(strategy["model"]["estimator"])
    gap = strategy["target"]["horizonSessions"]+1
    outer_schedule = folds(development, strategy["validation"]["outerFolds"],
                           strategy["validation"]["minTrainDates"]+2*(gap+10), gap)
    outer = partial["outerFolds"]
    for train_dates, test_dates in outer_schedule:
        partial.update(phase="outer_selection", currentOuter={
            "index": len(outer), "testStart": test_dates[0], "testEnd": test_dates[-1]})
        # Inner selection receives only labels already mature at this outer cutoff.
        inner_dates = sorted(samples.meta.loc[mature_mask(samples, test_dates[0], train_dates), "date"].unique())
        winner, trials = select(samples, specs, inner_dates, strategy, **runtime_args)
        partial["currentOuter"].update(selection=winner, trials=trials)
        partial["phase"] = "outer_fit"
        fitted, audit = _train(samples, winner, inner_dates, test_dates[0], strategy, **runtime_args)
        partial["currentOuter"]["fit"] = audit
        partial["phase"] = "outer_score"
        test = samples.meta.date.isin(test_dates) & samples.meta.inputValid & samples.y.notna().all(axis=1)
        scores = models.metrics(fitted.predict(samples.X.loc[test]), samples.y.loc[test].to_numpy(), samples.meta.loc[test, "date"].to_numpy())
        outer.append({"testStart": test_dates[0], "testEnd": test_dates[-1], "selection": winner,
                      "trials": trials, "fit": audit, "metrics": scores})
        partial["currentOuter"] = None
    partial["phase"] = "final_selection"
    winner, trials = select(samples, specs, development, strategy, **runtime_args)
    partial.update(finalTrials=trials, selectedModel=winner)
    records, fitted_models = evidence["rows"], evidence["fits"]
    current_model, fit_id, last_fit = None, None, -100000
    for date, group in samples.meta.loc[indices].groupby("date", sort=True):
        partial.update(phase="terminal_predict", currentTerminal={"date": date, "previousModelFitId": fit_id})
        t = dates.index(date)
        if current_model is None or t-last_fit >= strategy["model"]["refitDays"]:
            partial["phase"] = "terminal_fit"
            try:
                current_model, audit = _train(samples, winner, None, date, strategy, **runtime_args)
                audit["status"] = "valid"
            except ResearchError as exc:
                if exc.code not in {"MISSING_MODEL_DATA", "INSUFFICIENT_FORECAST_DATA", "MODEL_DID_NOT_CONVERGE", "INVALID_FORECAST"}:
                    raise
                # A predeclared rolling fit can lose usable observations. Keep
                # every origin, but never invent values or switch estimator.
                current_model = None
                audit = {"status": "invalid", "invalidReason": exc.code, "informationCutoff": date,
                         "labelEndMax": None, "trainStart": None, "trainEnd": None,
                         "estimator": winner["estimator"], "params": winner["params"], "featureNames": []}
            partial["currentTerminal"]["fitAudit"] = audit
            fit_id = "fit_"+digest({"date": date, "winner": winner, "audit": audit})[:24]
            fit_record = {"id": fit_id, "fitDate": date, "sequentialMaturedLabelsOnly": True, **audit}
            partial["currentTerminal"].update(modelFitId=fit_id, fit=fit_record)
            if current_model is not None and export_functions:
                partial["phase"] = "terminal_export"
                from .model_function import export_function
                fit_record["functionArtifact"] = export_function(current_model, audit, strategy)
            fitted_models.append(fit_record)
            last_fit = t
        partial["phase"] = "terminal_predict"
        pred = np.full((len(group), 2), np.nan)
        valid_idx = np.flatnonzero(group.inputValid.to_numpy())
        if len(valid_idx) and current_model is not None:
            pred[valid_idx] = current_model.predict(samples.X.loc[group.index[valid_idx]])
        partial["phase"] = "terminal_records"
        records.extend(_records(samples, group.index, pred, fit_id, strategy))
        partial["currentTerminal"] = None
    partial["phase"] = "terminal_metrics"
    truth, pred, score_dates = [], [], []
    target_groups = {}
    for row in records:
        if row["status"] != "valid" or row["labelMaturedAt"] is None:
            continue
        scale, current = row["scale"], row["currentState"]
        truth.append([(row["realizedEntry"]-current)/scale, (row["realizedFuture"]-current)/scale])
        pred.append([(row["expectedEntry"]-current)/scale, (row["expectedFuture"]-current)/scale])
        score_dates.append(row["date"])
        target_groups.setdefault(row["targetId"], []).append(row)
    terminal = models.metrics(np.asarray(pred).reshape(-1, 2), np.asarray(truth).reshape(-1, 2), score_dates)
    diagnostics = {"period": "terminal_sequential_out_of_sample", "holdoutStart": holdout, "holdoutEnd": dates[-1],
                   "metrics": terminal, "validForecasts": sum(x["status"] == "valid" for x in records),
                   "invalidForecasts": sum(x["status"] != "valid" for x in records),
                   "invalidModelFits": sum(x.get("status") == "invalid" for x in fitted_models),
                   "perTarget": [{"targetId": key, "observations": len(rows),
                                  "priceBias": float(np.mean([-r["forecastError"] for r in rows])),
                                  "priceRmse": float(np.sqrt(np.mean([r["forecastError"]**2 for r in rows])))} for key, rows in target_groups.items()],
                   "outerFolds": outer, "finalTrials": trials, "selectedModel": winner,
                   "selectionUsesHoldout": False, "rollingRefitsUseMaturedPastHoldoutLabels": True,
                   "purgeRule": "both label endpoints strictly before fit/validation cutoff",
                   "overlappingLabelsIndependent": False, "significanceTested": False,
                   "familyHypothesis": strategy["model"]["family"],
                   "meanReversionProven": False,
                   "selectionAudit": {"schemaVersion": 1, "familyFixedBeforeSelection": strategy["model"]["family"],
                       "candidateSet": specs, "candidateSetHash": digest(specs), "candidateCount": len(specs),
                       "candidateSetExpandedUsingOutcomes": False, "terminalSelectionCutoff": holdout,
                       "rule": "one_standard_error_complexity_heuristic", "score": "equal_date_joint_normalized_mse",
                       "fitBudgetPerSelection": len(specs)*strategy["validation"]["innerFolds"],
                       "outerEstimateUsedForSelection": False, "terminalMetricsUsedForSelection": False,
                       "dependentFoldHeuristicNotConfidenceInterval": True, "eliminatesBiasOrOverfitting": False,
                       "penalty": "predeclared_estimator_regularization_and_simpler_within_tolerance",
                       "reinforcementLearningIncluded": False}}
    from .inference import evaluate_forecast_uncertainty
    partial.update(diagnostics)
    partial["phase"] = "terminal_uncertainty"
    diagnostics["aggregateUncertainty"] = evaluate_forecast_uncertainty(
        records, strategy["target"]["horizonSessions"], strategy["research"]["observationDays"])
    return records, fitted_models, diagnostics
