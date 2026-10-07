"""Finite, two-output conditional level-change estimators with train-only transforms."""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.exceptions import ConvergenceWarning

from .schema import fail


GRIDS = {
    "no_change": [{}], "historical_drift": [{}],
    "ridge": [{"alpha": 1.}, {"alpha": 10.}],
    "elastic_net": [{"alpha": .0001, "l1_ratio": .2}, {"alpha": .001, "l1_ratio": .5}],
    "hist_gradient_boosting": [{"max_leaf_nodes": 7, "l2_regularization": 1.}, {"max_leaf_nodes": 15, "l2_regularization": 5.}],
}


def candidates(estimator):
    names = list(GRIDS) if estimator == "auto" else [estimator]
    return [{"id": f"{name}:{i}", "estimator": name, "params": params}
            for name in names for i, params in enumerate(GRIDS[name])]


class FittedModel:
    def __init__(self, spec, columns, predictor, audit):
        self.spec, self.columns, self.predictor, self.audit = spec, columns, predictor, audit

    def predict(self, X):
        if isinstance(self.predictor, np.ndarray):
            result = np.tile(self.predictor, (len(X), 1))
        else:
            result = np.asarray(self.predictor.predict(X[self.columns]), dtype=float)
        if result.shape != (len(X), 2) or not np.isfinite(result).all():
            fail("INVALID_FORECAST", "预测器未返回有限的入场/退出两个条件值")
        return result


def fit(spec, X, y, preprocess):
    from ..engine import _decorrelate, TrainWinsorizer
    if not len(X) or y.shape != (len(X), 2) or not np.isfinite(y.to_numpy()).all():
        fail("INSUFFICIENT_FORECAST_DATA", "拟合缺少已成熟的双目标样本")
    columns, decorrelation = _decorrelate(X, preprocess)
    if not columns:
        fail("MISSING_MODEL_DATA", "无有效预测特征")
    audit = {"trainRows": len(X), "featureNames": columns, "decorrelation": decorrelation,
             "estimator": spec["estimator"], "params": spec["params"], "outputs": ["entry_level_change_over_known_gross", "exit_level_change_over_known_gross"]}
    name = spec["estimator"]
    if name in ("no_change", "historical_drift"):
        value = np.zeros(2) if name == "no_change" else y.mean(axis=0).to_numpy()
        audit["constantPrediction"] = value.tolist()
        model = FittedModel(spec, columns, value, audit)
    else:
        if (X[columns].notna().sum() < 10).any():
            fail("MISSING_MODEL_DATA", "预测特征至少需要10个真实训练观测；不以全缺失值训练")
        steps = []
        if preprocess["winsorize"]:
            steps.append(("winsorize", TrainWinsorizer()))
        steps.append(("impute", SimpleImputer(strategy="median")))
        if preprocess["standardize"]:
            steps.append(("scale", StandardScaler()))
        if name == "ridge":
            estimator = Ridge(**spec["params"])
        elif name == "elastic_net":
            estimator = ElasticNet(**spec["params"], max_iter=5000, tol=1e-5, selection="cyclic")
        else:
            estimator = MultiOutputRegressor(HistGradientBoostingRegressor(**spec["params"], max_iter=60,
                min_samples_leaf=20, learning_rate=.06, max_bins=64, early_stopping=False, random_state=17), n_jobs=1)
        steps.append(("model", estimator))
        pipe = Pipeline(steps)
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ConvergenceWarning)
            pipe.fit(X[columns], y.to_numpy())
        if any(issubclass(w.category, ConvergenceWarning) for w in captured):
            fail("MODEL_DID_NOT_CONVERGE", "候选模型未收敛")
        audit["imputerMedian"] = pipe.named_steps["impute"].statistics_.tolist()
        if "winsorize" in pipe.named_steps:
            audit.update(winsorLower=pipe.named_steps["winsorize"].lower_.tolist(), winsorUpper=pipe.named_steps["winsorize"].upper_.tolist())
        if "scale" in pipe.named_steps:
            audit.update(scalerMean=pipe.named_steps["scale"].mean_.tolist(), scalerScale=pipe.named_steps["scale"].scale_.tolist())
        if hasattr(estimator, "coef_"):
            audit["coefficients"] = np.asarray(estimator.coef_).tolist()
            audit["intercepts"] = np.asarray(estimator.intercept_).tolist()
        model = FittedModel(spec, columns, pipe, audit)
    # A training-only conditional effect diagnostic, never a causal conclusion.
    effects = []
    for name in columns:
        if not name.startswith("state_deviation"):
            continue
        low, high = X[name].quantile([.25, .75]).to_numpy()
        if not np.isfinite([low, high]).all() or high-low < 1e-12:
            continue
        reference = X.iloc[np.linspace(0, len(X)-1, min(128, len(X)), dtype=int)].copy()
        bottom, top = reference.copy(), reference.copy()
        bottom[name], top[name] = low, high
        delta = model.predict(top)-model.predict(bottom)
        effect = float(np.mean(delta[:, 1]-delta[:, 0]))
        effects.append({"feature": name, "low": float(low), "high": float(high), "remainingChangeEffect": effect,
                        "negativeEffectObserved": effect < 0})
    audit["stateEffects"] = effects
    audit["stateEffectInterpretation"] = "Training conditional IQR perturbation, not causal attribution or proof of mean reversion."
    return model


def metrics(prediction, truth, dates=None):
    pred, actual = np.asarray(prediction), np.asarray(truth)
    finite = np.isfinite(pred).all(axis=1) & np.isfinite(actual).all(axis=1)
    if dates is not None and len(dates) != len(pred):
        raise ValueError("Metrics require one date per forecast row")
    date_values = np.asarray(dates)[finite] if dates is not None else np.arange(int(finite.sum()))
    weighting = "equal_weight_daily_average" if dates is not None else "equal_weight_forecast_rows"
    pred, actual = pred[finite], actual[finite]
    if not len(pred):
        return {"observations": 0, "observedDates": 0, "weighting": weighting,
                "mse": None, "rmse": None, "bias": None, "mae": None,
                "biasSign": "predicted_minus_realized"}
    def average(values):
        return pd.DataFrame(values, index=date_values).groupby(level=0).mean().mean(axis=0).to_numpy()
    with np.errstate(over="ignore", invalid="ignore"):
        error = pred-actual
        baseline = actual*actual
        loss = error*error
        remaining_error = (pred[:, 1]-pred[:, 0])-(actual[:, 1]-actual[:, 0])
        remaining_loss = remaining_error**2
    if not all(np.isfinite(value).all() for value in (error, baseline, loss, remaining_loss)):
        fail("INVALID_FORECAST", "预测误差超过可可靠计算的数值范围；不返回被置空的评分")
    squared = average(loss)
    mse = float(squared.mean()); baseline_mse = float(average(baseline).mean())
    result = {"observations": len(pred), "observedDates": len(set(date_values)), "weighting": weighting,
            "mse": mse, "rmse": float(np.sqrt(mse)), "mae": float(average(np.abs(error)).mean()),
            "bias": average(error).tolist(), "biasSign": "predicted_minus_realized", "entryRmse": float(np.sqrt(squared[0])),
            "exitRmse": float(np.sqrt(squared[1])), "remainingChangeRmse": float(np.sqrt(average(remaining_loss)[0])),
            "noChangeMse": baseline_mse, "mseImprovement": baseline_mse-mse,
            "relativeMseImprovement": 1-mse/baseline_mse if baseline_mse>1e-20 else None,
            "unit": "change_over_origin_known_gross", "significanceTested": False, "confidenceInterval": None}
    for value in result.values():
        if isinstance(value, (float, list)) and not np.isfinite(value).all():
            fail("INVALID_FORECAST", "聚合预测误差超过可可靠计算的数值范围")
    return result
