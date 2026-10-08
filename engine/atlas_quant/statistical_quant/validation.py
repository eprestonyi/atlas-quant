"""Purged nested selection and sequential out-of-sample, immutable forecasts."""
from __future__ import annotations
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
    try:
        model = models.fit(spec, samples.X.loc[mask], samples.y.loc[mask], strategy["preprocess"])
    finally:
        if runtime is not None:
            runtime.after_fit()
    audit = {"trainStart": actual_dates[0], "trainEnd": actual_dates[-1],
             "informationCutoff": cutoff, "labelEndMax": samples.meta.loc[mask, "targetDate"].max(),
             "trainDates": len(actual_dates), **model.audit}
    return model, audit


def select(samples, specs, dates, strategy, runtime=None):
    runtime_args = {} if runtime is None else {"runtime": runtime}
    schedule = folds(dates, strategy["validation"]["innerFolds"], strategy["validation"]["minTrainDates"], strategy["target"]["horizonSessions"]+1)
    trials = []
    for spec in specs:
        scores = []
        audits = []
        reason = None
        for training, testing in schedule:
            try:
                model, audit = _train(samples, spec, training, testing[0], strategy, **runtime_args)
                valid = samples.meta.date.isin(testing) & samples.meta.inputValid & samples.y.notna().all(axis=1)
                if not valid.any():
                    fail("INSUFFICIENT_FORECAST_DATA", "验证折没有可评分标签")
                pred = model.predict(samples.X.loc[valid])
                errors = np.mean((pred-samples.y.loc[valid].to_numpy())**2, axis=1)
                # Equal dates, rather than treating correlated stock rows as IID.
                score = float(pd.Series(errors, index=samples.meta.loc[valid, "date"]).groupby(level=0).mean().mean())
                scores.append(score)
                audits.append({"testStart": testing[0], "testEnd": testing[-1], "score": score, **audit})
            except (ValueError, FloatingPointError) as exc:
                if str(getattr(exc, "code", "")).startswith("CAPACITY_"):
                    raise
                reason = getattr(exc, "code", "MODEL_FIT_FAILED")
                break
        trials.append({**spec, "score": float(np.mean(scores)) if reason is None else None,
                       "status": "valid" if reason is None else "invalid", "invalidReason": reason, "folds": audits})
    valid = [x for x in trials if x["status"] == "valid" and np.isfinite(x["score"])]
    if not valid:
        fail("INSUFFICIENT_FORECAST_DATA", "没有候选完成全部时间验证折")
    winner = min(valid, key=lambda x: (x["score"], 0 if x["estimator"] == "no_change" else 1, x["id"]))
    return {k: winner[k] for k in ("id", "estimator", "params")}, trials


def _records(samples, idx, prediction, fit_id, strategy):
    records = []
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
    boundary = int(len(eligible_calendar)*(1-strategy["validation"]["holdoutFraction"]))
    if boundary < 1 or len(eligible_calendar)-boundary < 10:
        fail("INSUFFICIENT_FORECAST_DATA", "终端报告窗口不足")
    holdout = eligible_calendar[boundary]
    indices = samples.meta.index[samples.meta.date >= holdout]
    max_forecasts = MAX_FORECASTS if max_forecasts is None else max_forecasts
    if len(indices) > max_forecasts:
        fail("FORECAST_BUDGET", f"完整预测超过{max_forecasts}条；请降低观察频率或减少标的")
    return holdout, indices


def forecast(samples, strategy, *, max_forecasts=None, runtime=None):
    from ..engine import ResearchError
    runtime_args = {} if runtime is None else {"runtime": runtime}
    dates = samples.dates
    holdout, indices = forecast_origins(samples, strategy, max_forecasts=max_forecasts)
    development = sorted(samples.meta.loc[mature_mask(samples, holdout), "date"].unique())
    specs = models.candidates(strategy["model"]["estimator"])
    gap = strategy["target"]["horizonSessions"]+1
    outer_schedule = folds(development, strategy["validation"]["outerFolds"],
                           strategy["validation"]["minTrainDates"]+2*(gap+10), gap)
    outer = []
    for train_dates, test_dates in outer_schedule:
        # Inner selection receives only labels already mature at this outer cutoff.
        inner_dates = sorted(samples.meta.loc[mature_mask(samples, test_dates[0], train_dates), "date"].unique())
        winner, trials = select(samples, specs, inner_dates, strategy, **runtime_args)
        fitted, audit = _train(samples, winner, inner_dates, test_dates[0], strategy, **runtime_args)
        test = samples.meta.date.isin(test_dates) & samples.meta.inputValid & samples.y.notna().all(axis=1)
        scores = models.metrics(fitted.predict(samples.X.loc[test]), samples.y.loc[test].to_numpy(), samples.meta.loc[test, "date"].to_numpy())
        outer.append({"testStart": test_dates[0], "testEnd": test_dates[-1], "selection": winner,
                      "trials": trials, "fit": audit, "metrics": scores})
    winner, trials = select(samples, specs, development, strategy, **runtime_args)
    records, fitted_models = [], []
    current_model, fit_id, last_fit = None, None, -100000
    for date, group in samples.meta.loc[indices].groupby("date", sort=True):
        t = dates.index(date)
        if current_model is None or t-last_fit >= strategy["model"]["refitDays"]:
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
            fit_id = "fit_"+digest({"date": date, "winner": winner, "audit": audit})[:24]
            fitted_models.append({"id": fit_id, "fitDate": date, "sequentialMaturedLabelsOnly": True, **audit})
            last_fit = t
        pred = np.full((len(group), 2), np.nan)
        valid_idx = np.flatnonzero(group.inputValid.to_numpy())
        if len(valid_idx) and current_model is not None:
            pred[valid_idx] = current_model.predict(samples.X.loc[group.index[valid_idx]])
        records.extend(_records(samples, group.index, pred, fit_id, strategy))
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
                   "meanReversionProven": False}
    from .inference import evaluate_forecast_uncertainty
    diagnostics["aggregateUncertainty"] = evaluate_forecast_uncertainty(
        records, strategy["target"]["horizonSessions"], strategy["research"]["observationDays"])
    return records, fitted_models, diagnostics
