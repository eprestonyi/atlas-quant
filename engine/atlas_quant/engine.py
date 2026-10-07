"""Bounded, reproducible panel research with causal factors and time splits.

This is an adjusted-unit research simulator, not an exchange simulator. Model
selection is confined to the development period. All headline portfolio results
come from the untouched terminal holdout, after the selected model is frozen.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import warnings as pywarnings

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import BayesianRidge, ElasticNet, HuberRegressor, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .factors import FactorError, evaluate_expression, validate_expression
from .research_registry import MODEL_REGISTRY, TARGETS, is_external_field

ENGINE_VERSION = "0.3.0"
MAX_SYMBOLS = 50
MAX_FACTORS = 32
MAX_DATA_ROWS = 110_000
MODEL_NAMES = {key: spec["name"] for key, spec in MODEL_REGISTRY.items()}


class ResearchError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _finite_json(value):
    if isinstance(value, dict):
        return {str(k): _finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_finite_json(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _number(value, name, lo, hi, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ResearchError("INVALID_STRATEGY", f"{name} 须在 {lo}–{hi} 范围内")
    if integer and value != int(value):
        raise ResearchError("INVALID_STRATEGY", f"{name} 须为整数")
    return int(value) if integer else float(value)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ResearchError("INVALID_DATE", "日期须为 YYYYMMDD")
    try:
        pd.to_datetime(value, format="%Y%m%d", errors="raise")
    except ValueError as exc:
        raise ResearchError("INVALID_DATE", "无效日历日期") from exc
    return value


def validate_strategy(strategy):
    if not isinstance(strategy, dict) or strategy.get("schemaVersion", 1) != 1:
        raise ResearchError("INVALID_STRATEGY", "需要 schemaVersion=1 的策略")
    s = copy.deepcopy(strategy)
    s["schemaVersion"] = 1
    for section in ("universe", "preprocess", "model", "portfolio", "costs", "research"):
        if section in s and not isinstance(s[section], dict):
            raise ResearchError("INVALID_STRATEGY", f"{section} 须为对象")
    u = s.get("universe", {})
    symbols = u.get("symbols", [])
    if not isinstance(symbols, list) or not 3 <= len(symbols) <= MAX_SYMBOLS or any(not isinstance(x, str) for x in symbols) or len(set(symbols)) != len(symbols):
        raise ResearchError("INVALID_UNIVERSE", f"股票池须包含 3–{MAX_SYMBOLS} 个不重复 A 股代码")
    if any(not isinstance(x, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", x) for x in symbols):
        raise ResearchError("INVALID_UNIVERSE", "股票代码须为 000001.SZ 等 A 股格式")
    start, end = _date(u.get("start")), _date(u.get("end"))
    if start >= end or (pd.Timestamp(end) - pd.Timestamp(start)).days > 366 * 8:
        raise ResearchError("INVALID_RANGE", "起止日期须递增且不超过 8 年")
    factors = s.get("factors", [])
    if not isinstance(factors, list) or not 1 <= len(factors) <= MAX_FACTORS:
        raise ResearchError("INVALID_FACTORS", f"须选择 1–{MAX_FACTORS} 个因子")
    ids = set()
    for factor in factors:
        if not isinstance(factor, dict) or not isinstance(factor.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", factor["id"]):
            raise ResearchError("INVALID_FACTORS", "因子需要唯一且有效的 id")
        if factor["id"] in ids:
            raise ResearchError("INVALID_FACTORS", "因子 id 不可重复")
        ids.add(factor["id"])
        if isinstance(factor.get("direction", 1), bool) or factor.get("direction", 1) not in (-1, 1):
            raise ResearchError("INVALID_FACTORS", "因子方向须为 1 或 -1")
        factor["direction"] = factor.get("direction", 1)
        validate_expression(factor.get("expression"))
    research = s.setdefault("research", {})
    research.setdefault("mode", "legacy_long_only")
    if research["mode"] not in ("factor", "legacy_long_only"):
        raise ResearchError("INVALID_RESEARCH", "本引擎支持 factor/legacy_long_only；统计套利使用独立研究核心")
    research["observationDays"] = _number(research.get("observationDays", 1), "因子观察采样间隔", 1, 60, True)
    pre = s.setdefault("preprocess", {})
    for key in ("winsorize", "standardize"):
        pre.setdefault(key, True)
        if not isinstance(pre[key], bool):
            raise ResearchError("INVALID_STRATEGY", f"{key} 须为布尔值")
    pre.setdefault("decorrelation", "none")
    if pre["decorrelation"] not in ("none", "drop_correlated"):
        raise ResearchError("INVALID_STRATEGY", "去相关模式须为 none/drop_correlated")
    pre["correlationThreshold"] = _number(pre.get("correlationThreshold", 0.9), "相关性阈值", 0.5, 1)
    model = s.setdefault("model", {})
    model.setdefault("mode", "auto")
    model.setdefault("candidates", list(MODEL_NAMES))
    if model["mode"] not in ("auto", "manual"):
        raise ResearchError("INVALID_MODEL", "模型模式须为 auto/manual")
    names = model["candidates"]
    if not isinstance(names, list) or not names or any(not isinstance(x, str) or x not in MODEL_NAMES for x in names) or len(set(names)) != len(names):
        raise ResearchError("INVALID_MODEL", "模型候选列表无效")
    if model["mode"] == "manual" and len(names) != 1:
        raise ResearchError("INVALID_MODEL", "手动模式仅允许一个模型系列")
    model["horizon"] = _number(model.get("horizon", 5), "预测期", 1, 20, True)
    model.setdefault("target", "forward_return")
    if not isinstance(model["target"], str) or model["target"] not in TARGETS:
        raise ResearchError("INVALID_TARGET", "预测目标须为 forward_return 或 forward_excess_return")
    if model.get("metric", "rank_ic") != "rank_ic":
        raise ResearchError("INVALID_MODEL", "v1 仅支持预先固定的 rank_ic 选择指标")
    model["metric"] = "rank_ic"
    p = s.setdefault("portfolio", {})
    p["topN"] = _number(p.get("topN", 3), "持仓数量", 1, len(symbols), True)
    p["maxWeight"] = _number(p.get("maxWeight", 0.4), "单股目标上限", 0.01, 1)
    p["rebalanceDays"] = _number(p.get("rebalanceDays", 5), "调仓间隔", 1, 60, True)
    p["rebalanceThresholdBps"] = _number(p.get("rebalanceThresholdBps", 0), "目标权重偏离阈值", 0, 10000)
    p["rankBuffer"] = _number(p.get("rankBuffer", 0), "持仓名次缓冲", 0, len(symbols) - p["topN"], True)
    p["initialCapital"] = _number(p.get("initialCapital", 1_000_000), "初始资金", 1_000, 1_000_000_000)
    costs = s.setdefault("costs", {})
    for key, default in (("commissionBps", 3), ("slippageBps", 10), ("sellTaxBps", 5), ("transferBps", 0)):
        costs[key] = _number(costs.get(key, default), key, 0, 200)
    costs["minCommission"] = _number(costs.get("minCommission", 0), "单笔最低佣金", 0, 1000)
    costs.setdefault("taxMode", "fixed")
    if costs["taxMode"] != "fixed":
        raise ResearchError("INVALID_STRATEGY", "当前仅支持显式 fixed 税率场景，不复刻历史税率")
    return s


class TrainWinsorizer(TransformerMixin, BaseEstimator):
    """Quantile thresholds are learned exclusively on the current train split."""
    def fit(self, X, y=None):
        a = np.asarray(X, dtype=float)
        self.lower_ = np.nanquantile(a, 0.01, axis=0)
        self.upper_ = np.nanquantile(a, 0.99, axis=0)
        return self

    def transform(self, X):
        return np.clip(np.asarray(X, dtype=float), self.lower_, self.upper_)


def _prepare_data(data, strategy, provenance):
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ResearchError("NO_DATA", "没有可用行情")
    required = {"ts_code", "trade_date", "open", "high", "low", "close", "raw_close", "vol", "amount", "adj_factor"}
    missing = required - set(data.columns)
    if missing:
        raise ResearchError("MISSING_FIELDS", "行情缺少字段：" + ", ".join(sorted(missing)))
    if len(data) > MAX_DATA_ROWS:
        raise ResearchError("DATA_LIMIT", f"单次最多 {MAX_DATA_ROWS} 行行情")
    df = data.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    for dt in df["trade_date"].unique():
        _date(dt)
    if df.duplicated(["trade_date", "ts_code"]).any():
        raise ResearchError("DUPLICATE_DATA", "行情存在重复的日期/股票组合")
    u = strategy["universe"]
    df = df[df.ts_code.isin(u["symbols"]) & df.trade_date.between(u["start"], u["end"])].copy()
    if df.empty:
        raise ResearchError("NO_DATA", "所选区间与股票池没有行情")
    factor_fields = set().union(*(set(validate_expression(f["expression"])["fields"]) for f in strategy["factors"]))
    if factor_fields - set(df.columns):
        raise ResearchError("MISSING_FACTOR_DATA", "缺少所选因子数据：" + ", ".join(sorted(factor_fields - set(df.columns))))
    external_audit, availability_columns = [], []
    for field in sorted(f for f in factor_fields if is_external_field(f)):
        registry = provenance.get("externalFields")
        meta = registry.get(field) if isinstance(registry, dict) else None
        companion = field + "__available_date"
        if (not isinstance(meta, dict) or meta.get("availabilityPolicy") != "point_in_time_asof"
                or not isinstance(meta.get("dataType"), str) or meta["dataType"] not in {"number", "decimal", "integer"}
                or meta.get("availableDateColumn") != companion
                or not isinstance(meta.get("source"), str) or not meta["source"].strip()
                or not isinstance(meta.get("path"), str) or not meta["path"].strip()
                or companion not in df):
            raise ResearchError("EXTERNAL_FIELD_PIT_REQUIRED", f"外部字段 {field} 需要数值类型、来源路径与逐行可知日期证明")
        observed = df[field].notna()
        if df.loc[observed, field].map(lambda value: isinstance(value, (bool, str)) or not isinstance(value, (int, float, np.number))).any():
            raise ResearchError("EXTERNAL_FIELD_NONNUMERIC", f"外部字段 {field} 不允许把文本或布尔值自动转为数值")
        converted = pd.to_numeric(df[field], errors="coerce")
        if not np.isfinite(converted[observed]).all():
            raise ResearchError("EXTERNAL_FIELD_NONNUMERIC", f"外部字段 {field} 含非数值或非有限观测")
        availability = df.loc[observed, companion]
        if availability.isna().any():
            raise ResearchError("EXTERNAL_FIELD_PIT_REQUIRED", f"外部字段 {field} 缺少可知日期")
        availability = availability.astype(str)
        for date in availability.unique():
            _date(date)
        if (availability > df.loc[observed, "trade_date"]).any():
            raise ResearchError("FUTURE_EXTERNAL_FIELD", f"外部字段 {field} 包含信号日尚不可知的数据")
        df[field] = converted
        df[companion] = df[companion].where(df[companion].notna(), "").astype(str)
        availability_columns.append(companion)
        external_audit.append({"field": field, "source": meta["source"], "path": meta["path"], "observedValues": int(observed.sum()), "availableDateColumn": companion, "availabilityPolicy": "point_in_time_asof", "latestAvailableDate": availability.max() if len(availability) else None, "independentSourcePublicationVerified": False})
        if field.startswith("model_"):
            external_audit[-1]["independentTrainingHistoryVerified"] = False
    numeric = sorted((required | factor_fields) - {"ts_code", "trade_date"})
    for field in numeric:
        df[field] = pd.to_numeric(df[field], errors="coerce")
    core = ["open", "high", "low", "close", "raw_close", "adj_factor"]
    if not np.isfinite(df[core].to_numpy()).all() or (df[core] <= 0).any().any():
        raise ResearchError("INVALID_PRICES", "已有行情行的 OHLC、原价和复权因子须为有限正数；缺失交易日应整行省略")
    if not np.isfinite(df[["vol", "amount"]].to_numpy()).all() or (df[["vol", "amount"]] < 0).any().any():
        raise ResearchError("INVALID_VOLUME", "成交量和成交额须为有限非负数")
    tolerance = df["high"] * 1e-8
    if ((df["low"] > df[["open", "close"]].min(axis=1) + tolerance) | (df["high"] < df[["open", "close"]].max(axis=1) - tolerance) | (df["low"] > df["high"] + tolerance)).any():
        raise ResearchError("INVALID_OHLC", "OHLC 高低价不一致")
    dates = provenance.get("tradingDates")
    calendar_verified = isinstance(dates, list) and bool(dates)
    if calendar_verified:
        dates = sorted(set(_date(x) for x in dates if u["start"] <= str(x) <= u["end"]))
        if set(df.trade_date) - set(dates):
            raise ResearchError("CALENDAR_MISMATCH", "行情日期不在提供的交易日历中")
    else:
        dates = sorted(df.trade_date.unique().tolist())
    if len(dates) > 2200:
        raise ResearchError("DATA_LIMIT", "单次最多 2200 个交易日")
    if len(dates) < 180:
        raise ResearchError("INSUFFICIENT_DATA", "至少需要 180 个交易日，另需因子预热与预测期")
    df = df.sort_values(["trade_date", "ts_code"])
    row_bytes = df[["trade_date", "ts_code"] + numeric + availability_columns].to_csv(index=False, float_format="%.17g").encode()
    calendar_bytes = json.dumps(dates, separators=(",", ":")).encode()
    digest = hashlib.sha256(row_bytes + b"\ncalendar:" + calendar_bytes).hexdigest()
    idx = pd.MultiIndex.from_product([dates, sorted(u["symbols"])], names=["trade_date", "ts_code"])
    panel = df.set_index(["trade_date", "ts_code"])[numeric].reindex(idx)
    return panel, dates, {"dataSha256": digest, "calendarSha256": hashlib.sha256(calendar_bytes).hexdigest(), "observedRows": len(df), "expectedRows": len(panel), "calendarProvided": calendar_verified, "externalFieldAudit": external_audit}


def _build_samples(panel, dates, factors, horizon, target="forward_return"):
    X = pd.DataFrame({f["id"]: evaluate_expression(f["expression"], panel) * f["direction"] for f in factors}, index=panel.index)
    opens = panel["open"].groupby(level="ts_code", sort=False)
    entry, exit_ = opens.shift(-1), opens.shift(-(horizon + 1))
    y = (exit_ / entry - 1).replace([np.inf, -np.inf], np.nan)
    if target == "forward_excess_return":
        y = y - y.groupby(level="trade_date").transform("mean")
    date_ends = {date: dates[i + horizon + 1] if i + horizon + 1 < len(dates) else None for i, date in enumerate(dates)}
    ends = pd.Series(panel.index.get_level_values("trade_date").map(date_ends), index=panel.index, dtype="object")
    valid = X.notna().any(axis=1) & y.notna() & panel["close"].notna()
    return X, y, ends, valid


def _time_folds(dates, count=3):
    dates = list(dates)
    size = max(10, len(dates) // (count + 3))
    start = len(dates) - size * count
    if start < 30:
        raise ResearchError("INSUFFICIENT_DATA", "时间验证训练窗口不足 30 个交易日")
    return [(dates[:start + i * size], dates[start + i * size:start + (i + 1) * size]) for i in range(count)]


def _trial_specs(names):
    specs = []
    for name in names:
        grids = MODEL_REGISTRY[name]["grid"]
        for i, params in enumerate(grids):
            specs.append({"id": name, "trialId": f"{name}:{i}", "name": MODEL_NAMES[name], "params": params})
    return specs


def _decorrelate(X_train, preprocess):
    """Greedy stable-order feature selection, using training values only."""
    threshold = preprocess.get("correlationThreshold", 0.9)
    corr = X_train.corr(min_periods=10)
    kept, dropped = [], []
    for column in X_train.columns:
        against = next((old for old in kept if np.isfinite(corr.loc[old, column])
                        and abs(corr.loc[old, column]) >= threshold), None)
        if preprocess.get("decorrelation", "none") == "drop_correlated" and against is not None:
            dropped.append({"id": column, "retainedAgainst": against, "correlation": float(corr.loc[against, column])})
        else:
            kept.append(column)
    return kept, {"mode": preprocess.get("decorrelation", "none"), "threshold": threshold,
                  "method": "absolute_pearson_pairwise_training_min10_stable_input_order",
                  "fitOnTrainOnly": True, "retained": kept, "dropped": dropped,
                  "trainRows": len(X_train)}


def _fit_predict(spec, X_train, y_train, X_test, preprocess):
    columns, decorrelation = _decorrelate(X_train, preprocess)
    X_train, X_test = X_train[columns], X_test[columns]
    if spec["id"] == "factor_score":
        # Daily ranks use only contemporaneously available cross-sectional data.
        scores = X_test.groupby(level="trade_date").rank(pct=True).mean(axis=1)
        return scores, {"method": "mean_contemporaneous_factor_percentile", "trained": False, "decorrelation": decorrelation}
    if X_train.notna().sum().min() < 10:
        raise ValueError("训练段有因子不足 10 个有效样本")
    steps = []
    if preprocess["winsorize"]:
        steps.append(("winsorize", TrainWinsorizer()))
    steps.append(("impute", SimpleImputer(strategy="median")))
    if preprocess["standardize"]:
        steps.append(("scale", StandardScaler()))
    if spec["id"] == "ridge":
        estimator = Ridge(**spec["params"])
    elif spec["id"] == "elastic_net":
        estimator = ElasticNet(**spec["params"], max_iter=3000, tol=1e-5, selection="cyclic")
    elif spec["id"] == "bayesian_ridge":
        estimator = BayesianRidge(max_iter=500, tol=1e-5)
    elif spec["id"] == "huber":
        estimator = HuberRegressor(**spec["params"], max_iter=500, tol=1e-5)
    elif spec["id"] in {"random_forest", "extra_trees"}:
        cls = RandomForestRegressor if spec["id"] == "random_forest" else ExtraTreesRegressor
        estimator = cls(**spec["params"], n_estimators=64, max_features=0.7, random_state=17, n_jobs=1)
    elif spec["id"] == "hist_gradient_boosting":
        estimator = HistGradientBoostingRegressor(**spec["params"], max_iter=80, min_samples_leaf=20, learning_rate=0.06, max_bins=64, early_stopping=False, random_state=17)
    else:
        raise ValueError("未知模型系列")
    steps.append(("model", estimator))
    pipe = Pipeline(steps)
    with pywarnings.catch_warnings(record=True) as captured:
        pywarnings.simplefilter("always", ConvergenceWarning)
        pipe.fit(X_train, y_train)
    if any(issubclass(w.category, ConvergenceWarning) for w in captured):
        raise ValueError("模型未收敛，候选无效")
    pred = pd.Series(pipe.predict(X_test), index=X_test.index)
    if not np.isfinite(pred.to_numpy()).all():
        raise ValueError("模型产生非有限预测")
    audit = {"trained": True, "decorrelation": decorrelation, "featureNames": columns, "imputerMedian": pipe.named_steps["impute"].statistics_.tolist()}
    if "winsorize" in pipe.named_steps:
        audit["winsorLower"] = pipe.named_steps["winsorize"].lower_.tolist()
        audit["winsorUpper"] = pipe.named_steps["winsorize"].upper_.tolist()
    if "scale" in pipe.named_steps:
        audit["scalerMean"] = pipe.named_steps["scale"].mean_.tolist()
        audit["scalerScale"] = pipe.named_steps["scale"].scale_.tolist()
    if hasattr(estimator, "coef_"):
        audit["coefficients"] = estimator.coef_.tolist()
        audit["intercept"] = float(estimator.intercept_)
    if hasattr(estimator, "feature_importances_"):
        audit["impurityFeatureImportances"] = estimator.feature_importances_.tolist()
        audit["importanceCaution"] = "Training impurity importance is biased by correlated features and is not causal attribution or holdout validation."
    return pred, audit


def _rank_ics(pred, truth):
    combined = pd.DataFrame({"prediction": pred, "truth": truth}).dropna()
    records = []
    for date, group in combined.groupby(level="trade_date", sort=True):
        if len(group) < 3 or group.prediction.nunique() < 2 or group.truth.nunique() < 2:
            continue
        score = group.prediction.rank().corr(group.truth.rank())
        if np.isfinite(score):
            records.append({"date": date, "ic": float(score), "count": len(group)})
    return records


def _fold_masks(X, ends, valid, train_dates, test_dates):
    sample_dates = X.index.get_level_values("trade_date")
    unpurged = valid & sample_dates.isin(train_dates)
    train = unpurged & ends.lt(test_dates[0])
    test = valid & sample_dates.isin(test_dates)
    if train.sum() < 60 or test.sum() < 15:
        raise ResearchError("INSUFFICIENT_DATA", "清除跨窗标签后训练或验证样本不足")
    return train, test, int(unpurged.sum() - train.sum())


def _evaluate(spec, X, y, ends, valid, folds, pre):
    scores, records = [], []
    try:
        for train_dates, test_dates in folds:
            train, test, purged = _fold_masks(X, ends, valid, train_dates, test_dates)
            # Compute contemporaneous ranks before filtering on future label
            # availability; otherwise future suspensions can alter baseline ranks.
            predict = X.index.get_level_values("trade_date").isin(test_dates) & X.notna().any(axis=1) & _observation_mask(X)
            pred, fitted = _fit_predict(spec, X[train], y[train], X[predict], pre)
            ics = _rank_ics(pred, y[test])
            if len(ics) < 5:
                raise ValueError("可计算 RankIC 的验证日期少于 5 天")
            fold_scores = [x["ic"] for x in ics]
            scores.extend(fold_scores)
            records.append({"trainStart": train_dates[0], "trainEnd": train_dates[-1], "trainLabelEndMax": ends[train].max(), "testStart": test_dates[0], "testEnd": test_dates[-1], "trainRows": int(train.sum()), "testRows": int(test.sum()), "purgedRows": purged, "score": float(np.mean(fold_scores)), "scoredDates": len(ics), "decorrelation": fitted["decorrelation"]})
        return {**spec, "score": float(np.mean(scores)), "scoreStd": float(np.std(scores)), "folds": records, "status": "complete", "scoredDates": len(scores)}
    except (ValueError, FloatingPointError) as exc:
        return {**spec, "score": None, "folds": records, "status": "failed", "error": str(exc)}


def _choose(trials):
    available = [trial for trial in trials if trial["score"] is not None and trial["status"] == "complete"]
    if not available:
        raise ResearchError("NO_VALID_MODEL", "所有候选均缺少有效的时间验证结果")
    # Stable ordering breaks ties in favour of the earlier, simpler candidate.
    return max(available, key=lambda trial: trial["score"])


def _trade_costs(notional, side, costs):
    if notional <= 1e-10:
        return {"commission": 0.0, "slippage": 0.0, "tax": 0.0, "transfer": 0.0, "cost": 0.0}
    parts = {"commission": max(notional * costs["commissionBps"] / 10000, costs.get("minCommission", 0)),
             "slippage": notional * costs["slippageBps"] / 10000,
             "tax": notional * costs["sellTaxBps"] / 10000 if side == "SELL" else 0.0,
             "transfer": notional * costs.get("transferBps", 0) / 10000}
    return {**parts, "cost": sum(parts.values())}


def _sellable_quantity(lots, date):
    """A-share T+1: lots acquired this trading date cannot be sold yet."""
    return sum(lot["quantity"] for lot in lots if lot["date"] < date)


def _consume_sellable(lots, quantity, date):
    if quantity > _sellable_quantity(lots, date) + 1e-7:
        raise ResearchError("T1_VIOLATION", "卖出数量超过 T+1 可卖持仓")
    remaining = quantity
    for lot in lots:
        if lot["date"] < date:
            sold = min(lot["quantity"], remaining)
            lot["quantity"] -= sold
            remaining -= sold
    if remaining > 1e-7:
        raise ResearchError("T1_VIOLATION", "卖出数量超过 T+1 可卖持仓")
    lots[:] = [lot for lot in lots if lot["quantity"] > 1e-10]


def _simulate(panel, dates, predictions, strategy):
    p, c = strategy["portfolio"], strategy["costs"]
    capital, cash = p["initialCapital"], p["initialCapital"]
    symbols = sorted(strategy["universe"]["symbols"])
    positions = {symbol: 0.0 for symbol in symbols}
    lots = {symbol: [] for symbol in symbols}
    marks, trades, equity, ledger, skipped = {}, [], [], [], []
    costs_total, turnover_notional = 0.0, 0.0
    cost_breakdown = {key: 0.0 for key in ("commission", "slippage", "tax", "transfer")}
    benchmark_qty, benchmark_cash = {}, capital
    observation_dates = sorted(set(predictions.index.get_level_values("trade_date")))
    pending = {}
    for symbol in symbols:
        price = panel.loc[(dates[0], symbol), "close"]
        if np.isfinite(price) and panel.loc[(dates[0], symbol), "vol"] > 0:
            benchmark_qty[symbol] = capital / len(symbols) / price
            benchmark_cash -= capital / len(symbols)
            marks[symbol] = float(price)
    peak, previous_nav = capital, capital
    for i, date in enumerate(dates):
        day = panel.xs(date, level="trade_date")
        day_cost, day_notional = 0.0, 0.0
        bought_today = {symbol: 0.0 for symbol in symbols}
        opening_prices = {symbol: float(day.loc[symbol, "open"]) if np.isfinite(day.loc[symbol, "open"]) else marks.get(symbol, 0.0) for symbol in symbols}
        opening_nav = cash + sum(qty * opening_prices[symbol] for symbol, qty in positions.items())
        # Sampling and rebalancing clocks are independent. Only observations
        # available by yesterday's close may create/replace target instructions.
        if i > 0 and (i - 1) % p["rebalanceDays"] == 0:
            available_dates = [d for d in observation_dates if d <= dates[i - 1]]
            signal_date = available_dates[-1] if available_dates else None
            scores = predictions.xs(signal_date, level="trade_date").dropna() if signal_date else pd.Series(dtype=float)
            if len(scores) >= p["topN"]:
                ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
                rank = {symbol: j + 1 for j, (symbol, _) in enumerate(ranked)}
                retained = [symbol for symbol, _ in ranked if positions[symbol] > 1e-10 and rank[symbol] <= p["topN"] + p.get("rankBuffer", 0)]
                desired = retained[:p["topN"]]
                desired += [symbol for symbol, _ in ranked if symbol not in desired][:p["topN"] - len(desired)]
                weight = min(1.0 / p["topN"], p["maxWeight"])
                # Unfilled instructions survive suspensions until the next
                # scheduled rebalance replaces them. No unseen future fill.
                pending = {symbol: {"weight": weight if symbol in desired else 0.0,
                                    "signalDate": signal_date, "orderDate": date}
                           for symbol in symbols if symbol in desired or positions[symbol] > 1e-10}
            else:
                skipped.append({"date": date, "signalDate": signal_date, "reason": "insufficient_signals_keep_positions"})
        targets = {}
        for symbol, instruction in list(pending.items()):
            price = day.loc[symbol, "open"]
            if not np.isfinite(price) or day.loc[symbol, "vol"] <= 0:
                skipped.append({"date": date, "signalDate": instruction["signalDate"], "symbol": symbol, "reason": "missing_session_or_zero_volume", "pending": True})
                continue
            current_weight = positions[symbol] * float(price) / opening_nav
            delta_weight = instruction["weight"] - current_weight
            if abs(delta_weight) * 10000 < p.get("rebalanceThresholdBps", 0) or abs(delta_weight) < 1e-12:
                skipped.append({"date": date, "signalDate": instruction["signalDate"], "symbol": symbol, "reason": "within_rebalance_threshold", "weightDeviationBps": abs(delta_weight) * 10000})
                del pending[symbol]
                continue
            targets[symbol] = opening_nav * instruction["weight"] / float(price)
        for side in ("SELL", "BUY"):
            differences = {symbol: target - positions[symbol] for symbol, target in targets.items()}
            buy_scale = 1.0
            if side == "BUY":
                wants = {symbol: qty * float(day.loc[symbol, "open"]) for symbol, qty in differences.items() if qty > 1e-10}
                def required(scale):
                    return sum(notional * scale + _trade_costs(notional * scale, "BUY", c)["cost"] for notional in wants.values())
                if required(1.0) > cash:
                    lo, hi = 0.0, 1.0
                    for _ in range(55):
                        mid = (lo + hi) / 2
                        if required(mid) <= max(cash, 0):
                            lo = mid
                        else:
                            hi = mid
                    buy_scale = lo
            for symbol in symbols:
                if symbol not in targets or symbol not in pending:
                    continue
                difference = targets[symbol] - positions[symbol]
                if (side == "SELL" and difference >= -1e-10) or (side == "BUY" and difference <= 1e-10):
                    continue
                instruction = pending[symbol]
                price = float(day.loc[symbol, "open"])
                quantity = abs(difference)
                if side == "SELL":
                    available = _sellable_quantity(lots[symbol], date)
                    if quantity > available + 1e-10:
                        skipped.append({"date": date, "signalDate": instruction["signalDate"], "symbol": symbol, "reason": "t_plus_one_locked", "blockedQuantity": quantity - available, "pending": True})
                    quantity = min(quantity, available)
                else:
                    quantity *= buy_scale
                if quantity <= 1e-10:
                    continue
                notional = quantity * price
                fees = _trade_costs(notional, side, c)
                if side == "SELL" and cash + notional < fees["cost"]:
                    skipped.append({"date": date, "signalDate": instruction["signalDate"], "symbol": symbol, "reason": "insufficient_cash_for_minimum_fee", "pending": True})
                    continue
                if side == "SELL":
                    _consume_sellable(lots[symbol], quantity, date)
                    positions[symbol] -= quantity
                    cash += notional - fees["cost"]
                else:
                    positions[symbol] += quantity
                    lots[symbol].append({"date": date, "quantity": quantity})
                    bought_today[symbol] += quantity
                    cash -= notional + fees["cost"]
                if abs(cash) < 1e-8:
                    cash = 0.0
                if cash < -1e-6 or positions[symbol] < -1e-8:
                    raise ResearchError("LEDGER_ERROR", "资金或持仓记账出现负值")
                marks[symbol] = price
                day_cost += fees["cost"]
                day_notional += notional
                costs_total += fees["cost"]
                turnover_notional += notional
                for key in cost_breakdown:
                    cost_breakdown[key] += fees[key]
                trades.append({"date": date, "signalDate": instruction["signalDate"], "orderDate": instruction["orderDate"], "delayedSessions": dates.index(date) - dates.index(instruction["orderDate"]), "symbol": symbol, "side": side, "quantity": quantity, "price": price, "notional": notional, **fees, "cashAfter": cash, "settlementRule": "A_SHARE_T_PLUS_ONE"})
                # Cash-scaled buys are final. T+1 blocked remainders stay pending.
                if side == "BUY" or quantity >= abs(difference) - 1e-8:
                    del pending[symbol]
        stale = []
        for symbol in symbols:
            closing = day.loc[symbol, "close"]
            if np.isfinite(closing):
                marks[symbol] = float(closing)
            elif positions[symbol] > 0:
                stale.append(symbol)
        market_value = sum(qty * marks.get(symbol, 0) for symbol, qty in positions.items())
        nav = cash + market_value
        benchmark = benchmark_cash + sum(qty * marks[symbol] for symbol, qty in benchmark_qty.items())
        peak = max(peak, nav)
        equity.append({"date": date, "equity": nav, "benchmark": benchmark, "drawdown": nav / peak - 1, "cash": cash, "positionsValue": market_value, "dailyCosts": day_cost})
        ledger.append({"date": date, "cash": cash, "positionsValue": market_value, "equity": nav, "costs": day_cost, "tradedNotional": day_notional, "turnover": day_notional / previous_nav, "holdingCount": sum(qty > 1e-10 for qty in positions.values()), "positions": [{"symbol": sym, "quantity": qty, "mark": marks.get(sym), "value": qty * marks.get(sym, 0), "weight": qty * marks.get(sym, 0) / nav, "boughtToday": bought_today[sym], "sellableQuantity": _sellable_quantity(lots[sym], date)} for sym, qty in positions.items() if qty > 1e-10], "staleMarks": stale, "pendingOrders": [{"symbol": sym, **order} for sym, order in pending.items()]})
        previous_nav = nav
    navs = np.array([point["equity"] for point in equity])
    rets = navs[1:] / navs[:-1] - 1
    std = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    total_return = navs[-1] / capital - 1
    metrics = {"totalReturn": total_return, "annualReturn": (navs[-1] / capital) ** (252 / max(1, len(rets))) - 1, "volatility": std * np.sqrt(252), "sharpe": float(np.mean(rets)) / std * np.sqrt(252) if std > 1e-12 else None, "maxDrawdown": min(point["drawdown"] for point in equity), "turnover": turnover_notional / float(np.mean(navs)), "totalCosts": costs_total, "costBreakdown": cost_breakdown, "costDragOnInitialCapital": costs_total / capital, "tradeCount": len(trades), "benchmarkReturn": equity[-1]["benchmark"] / capital - 1}
    return metrics, equity, trades, ledger, skipped


def _forecast_report(predictions, truth, label_end, panel, dates, strategy, model_id):
    """Expose only frozen-model holdout forecasts with honest output units."""
    is_estimate = MODEL_REGISTRY[model_id]["predictionKind"] == "target_estimate"
    target = TARGETS[strategy["model"]["target"]]
    rank = predictions.groupby(level="trade_date").rank(method="average", ascending=False)
    percentiles = predictions.groupby(level="trade_date").rank(pct=True)
    vol = evaluate_expression("ts_std(returns(close,1),20)", panel) * np.sqrt(252)
    downside = evaluate_expression("sqrt(ts_mean(min(returns(close,1),0)*min(returns(close,1),0),20))", panel) * np.sqrt(252)
    next_dates = {d: dates[i + 1] if i + 1 < len(dates) else None for i, d in enumerate(dates)}
    rows = []
    for (date, symbol), value in predictions.items():
        idx = (date, symbol)
        rows.append({"date": date, "symbol": symbol, "score": float(value), "rank": float(rank.loc[idx]), "percentile": float(percentiles.loc[idx]), "predictedTarget": float(value) if is_estimate else None, "actualTarget": truth.loc[idx], "labelEnd": label_end.loc[idx], "earliestExecutionDate": next_dates[date], "trailingVolatility20Annualized": vol.loc[idx], "trailingDownside20Annualized": downside.loc[idx]})
    mature = pd.DataFrame({"prediction": predictions, "actual": truth}).dropna()
    metrics = None
    if is_estimate and len(mature):
        errors = mature.prediction - mature.actual
        metrics = {"observations": len(mature), "mae": float(errors.abs().mean()), "rmse": float(np.sqrt((errors * errors).mean())), "directionAccuracy": float((np.sign(mature.prediction) == np.sign(mature.actual)).mean()), "calibratedProbability": False, "period": "terminal_holdout_only"}
    latest_date = max(row["date"] for row in rows) if rows else None
    return {"target": target, "horizonSessions": strategy["model"]["horizon"], "period": "terminal_holdout_only", "frozenModel": True, "scoreInterpretation": "predicted_target_higher_is_better" if is_estimate else "mean_factor_percentile_higher_is_better_not_return", "predictedTargetUnit": target["unit"] if is_estimate else None, "riskInterpretation": "Trailing 20-session annualized observed risk, not predicted future risk or confidence interval.", "latestDate": latest_date, "rows": rows, "latest": sorted((row for row in rows if row["date"] == latest_date), key=lambda row: (row["rank"], row["symbol"])), "errorMetrics": metrics, "confidenceInterval": None}


def _training_diagnostics(X, mask, fitted=None):
    train = X.loc[mask]
    correlation = train.corr(min_periods=30)
    pairs = []
    for i, first in enumerate(train.columns):
        for second in train.columns[i + 1:]:
            value = correlation.loc[first, second]
            if np.isfinite(value) and abs(value) >= 0.9:
                pairs.append({"first": first, "second": second, "correlation": float(value)})
    pairs.sort(key=lambda row: -abs(row["correlation"]))
    return {"period": "final_purged_training_only", "featureCoverage": {col: float(train[col].notna().mean()) for col in train}, "highCorrelationPairs": pairs[:50], "highCorrelationThreshold": 0.9, "totalHighCorrelationPairs": len(pairs), "automaticallyDroppedFeatures": [item["id"] for item in (fitted or {}).get("decorrelation", {}).get("dropped", [])], "decorrelation": (fitted or {}).get("decorrelation"), "features": list(train.columns), "matrix": correlation.to_numpy().tolist(), "note": "Correlated window recipes are not independent evidence. Diagnostics do not select features using holdout."}


def _observation_mask(X):
    dates = X.index.get_level_values("trade_date")
    return dates.isin(X.attrs.get("observationDates", dates))


def _factor_research(X, y, valid, holdout_start, strategy, diagnostics, metrics, ledger):
    """Descriptive untouched-holdout diagnostics, never a short-sale backtest."""
    rows = (X.index.get_level_values("trade_date") >= holdout_start) & _observation_mask(X)
    groups = min(5, len(strategy["universe"]["symbols"]))
    factors = []
    for definition in strategy["factors"]:
        name = definition["id"]
        ic = _rank_ics(X.loc[rows, name], y.loc[valid & rows])
        values = np.asarray([point["ic"] for point in ic])
        std = float(np.std(values, ddof=1)) if len(values) > 1 else None
        mean = float(values.mean()) if len(values) else None
        observed = pd.DataFrame({"factor": X.loc[rows, name], "target": y.loc[valid & rows]}).dropna()
        daily = []
        for date, frame in observed.groupby(level="trade_date", sort=True):
            if len(frame) < groups or frame.factor.nunique() < 2:
                continue
            # Stable tie handling: symbol order inside equal factor values.
            ordered = frame.sort_values("factor", kind="stable")
            bucket = np.floor(np.arange(len(ordered)) * groups / len(ordered)).astype(int)
            returns = [float(ordered.iloc[np.flatnonzero(bucket == g)].target.mean()) for g in range(groups)]
            daily.append({"date": date, "groupReturns": returns, "counts": [int((bucket == g).sum()) for g in range(groups)], "longShort": returns[-1] - returns[0]})
        factors.append({"id": name, "direction": definition["direction"], "rankIC": {"mean": mean, "std": std, "icir": mean / std if std is not None and std > 1e-12 else None, "count": len(ic), "daily": ic, "annualized": False},
                        "quantiles": {"groups": groups, "order": "direction_adjusted_factor_low_to_high", "daily": daily, "meanReturns": np.mean([row["groupReturns"] for row in daily], axis=0).tolist() if daily else [None] * groups,
                                      "longShortMean": float(np.mean([row["longShort"] for row in daily])) if daily else None,
                                      "horizonSessions": strategy["model"]["horizon"], "overlappingLabels": strategy["research"]["observationDays"] < strategy["model"]["horizon"], "costsIncluded": False, "executablePortfolio": False}})
    capital = strategy["portfolio"]["initialCapital"]
    return {"period": "terminal_holdout", "usedForSelection": False, "observationDates": sorted(set(X.index.get_level_values("trade_date")[rows])), "factors": factors,
            "correlation": diagnostics,
            "portfolio": {"kind": "legacy_long_only_research_diagnostic", "turnover": metrics["turnover"], "costBreakdown": metrics["costBreakdown"], "costDragOnInitialCapital": metrics["costDragOnInitialCapital"],
                          "netReturn": metrics["totalReturn"], "grossReturnSameExecutedPositions": metrics["totalReturn"] + metrics["totalCosts"] / capital,
                          "costDragDefinition": "Paid costs divided by initial capital; gross adds these costs back without reinvestment, not a rerun with zero fees.", "averageHoldingCount": float(np.mean([row["holdingCount"] for row in ledger])), "daily": [{key: row[key] for key in ("date", "holdingCount", "turnover", "costs", "positionsValue", "cash")} for row in ledger]},
            "limitations": ["Long-short is high-minus-low forward-label arithmetic, not a financed, borrowable or T+1-executable short portfolio.", "Forward labels can overlap. ICIR is unannualized mean/sample-standard-deviation, not a significance test.", "Factor directions and universe are user choices. Reusing holdout for revisions introduces selection bias."]}


def run_research(strategy: dict, data: pd.DataFrame, provenance: dict) -> dict:
    """Run deterministic research. No provider call, network, file or trade side effect."""
    if isinstance(strategy, dict) and isinstance(strategy.get('research'), dict) and strategy['research'].get('mode') == 'stat_arb':
        from .stat_arb import run_stat_arb
        return run_stat_arb(strategy, data, provenance)
    s = validate_strategy(strategy)
    if provenance is not None and not isinstance(provenance, dict):
        raise ResearchError("INVALID_PROVENANCE", "provenance 须为对象")
    provenance = copy.deepcopy(provenance or {})
    panel, dates, data_audit = _prepare_data(data, s, provenance)
    X, y, label_end, valid = _build_samples(panel, dates, s["factors"], s["model"]["horizon"], s["model"]["target"])
    counts = valid.groupby(level="trade_date").sum()
    eligible_dates = counts[counts >= 3].index.tolist()
    if len(eligible_dates) < 180:
        raise ResearchError("INSUFFICIENT_DATA", "因子预热、缺失数据和预测期后，至少需要 180 个每日有 3 只有效股票的交易日")
    # Freeze boundaries from the calendar and declared lookback, never from
    # whether a future return happens to be observable (e.g. suspensions).
    warmup = max(validate_expression(f["expression"])["lookback"] for f in s["factors"])
    horizon = s["model"]["horizon"]
    research_dates = dates[warmup:len(dates) - horizon - 1]
    if len(research_dates) < 180:
        raise ResearchError("INSUFFICIENT_DATA", "完整因子预热与预测期后至少需要 180 个研究交易日")
    observation_dates = dates[warmup::s["research"]["observationDays"]]
    X.attrs["observationDates"] = observation_dates
    X.attrs["observationDays"] = s["research"]["observationDays"]
    valid = valid & _observation_mask(X)
    date_ends = {date: dates[i + horizon + 1] if i + horizon + 1 < len(dates) else None for i, date in enumerate(dates)}
    cutoff = int(len(research_dates) * 0.8)
    development_dates, holdout_dates = research_dates[:cutoff], research_dates[cutoff:]
    holdout_start = holdout_dates[0]
    # No training label may consume a price in the terminal holdout period.
    dev_valid = valid & (X.index.get_level_values("trade_date") >= development_dates[0]) & (X.index.get_level_values("trade_date") < holdout_start) & label_end.lt(holdout_start)
    dev_dates = [date for date in development_dates if date_ends[date] < holdout_start]
    names = list(s["model"]["candidates"])
    specs = _trial_specs(names)
    warn = [
        "收益为终端留出期的假设研究结果；不代表可实现或未来收益。",
        "使用用户选定的股票池，可能存在事后选股/存活偏差；未声称历史指数成分回测。",
        "成交使用次日开盘的复权归一化研究单位，可为小数；不是券商股数，未模拟涨跌停、100股整手、成交排队、容量冲击或完整A股规则。",
        "缺失交易日不成交；持仓以最近价格估值，复牌价格变化才计入。固定费率为用户情景参数，不是历史费率复刻。",
        "基准为所选股票在留出首日收盘等权买入并持有，不计费用；缺少首日价格的份额保持现金。",
        "重看留出结果再修改因子或策略会污染留出期；新版本应另设未查看数据或积累前向记录。",
    ]
    source_warnings = provenance.get("warnings", [])
    if isinstance(source_warnings, list):
        warn = [str(w) for w in source_warnings[:10]] + warn
    if provenance.get("synthetic") or str(provenance.get("source", "")).upper() == "SYNTHETIC":
        warn.insert(0, "SYNTHETIC 教学模式：全部市场数据为合成数据，不是真实行情验证。")
    if not data_audit["calendarProvided"]:
        warn.append("未提供完整交易日历，只能使用观测日期；全股票池共同缺失的交易日无法识别。")
    if len(s["universe"]["symbols"]) < 10:
        warn.append("股票池少于10只，截面RankIC离散且不稳定，模型比较证据较弱。")
    if data_audit["observedRows"] < data_audit["expectedRows"]:
        warn.append("输入行情有缺失交易日；因子保留缺失，跨缺失窗口可能不可用。")
    if data_audit["externalFieldAudit"]:
        warn.append("外部数值字段已检查声明的逐行可知日期不晚于信号日；源文件时间戳和发布真实性仍依赖上传者/连接器，字段目录本身不是历史数据。")
    if any(item["field"].startswith("model_") for item in data_audit["externalFieldAudit"]):
        warn.append("MODEL 输入保留所声明的来源记录与可用日期；原模型训练截止、历史生成记录及独立预测性质尚未独立核实。")
    if s["model"]["target"] == "forward_excess_return":
        warn.append("超额收益目标相对同日可观测标签的用户股票池均值，不是官方指数；缺失标签会改变基准组成。")
    outer_records = []
    with threadpool_limits(limits=1):
        outer_folds = _time_folds(dev_dates, 3)
        for train_dates, test_dates in outer_folds:
            train_mask, test_mask, purged = _fold_masks(X, label_end, dev_valid, train_dates, test_dates)
            inner_dates = [date for date in train_dates if date_ends[date] < test_dates[0]]
            inner_folds = _time_folds(inner_dates, 2)
            inner_trials = [_evaluate(spec, X, y, label_end, train_mask, inner_folds, s["preprocess"]) for spec in specs]
            winner = _choose(inner_trials)
            predict = X.index.get_level_values("trade_date").isin(test_dates) & X.notna().any(axis=1) & _observation_mask(X)
            pred, outer_fit = _fit_predict(winner, X[train_mask], y[train_mask], X[predict], s["preprocess"])
            ics = _rank_ics(pred, y[test_mask])
            if len(ics) < 5:
                raise ResearchError("INSUFFICIENT_VALIDATION", "外层验证可评分日期不足")
            outer_records.append({"trainStart": train_dates[0], "trainEnd": train_dates[-1], "trainLabelEndMax": label_end[train_mask].max(), "testStart": test_dates[0], "testEnd": test_dates[-1], "trainRows": int(train_mask.sum()), "testRows": int(test_mask.sum()), "purgedRows": purged, "selectedModel": winner["id"], "selectedParams": winner["params"], "innerScore": winner["score"], "score": float(np.mean([ic["ic"] for ic in ics])), "scoredDates": len(ics), "innerSelection": [{"trialId": trial["trialId"], "score": trial["score"], "status": trial["status"], "folds": trial["folds"]} for trial in inner_trials], "dailyIC": ics, "decorrelation": outer_fit["decorrelation"]})
        final_folds = _time_folds(dev_dates, 3)
        trials = [_evaluate(spec, X, y, label_end, dev_valid, final_folds, s["preprocess"]) for spec in specs]
        winner = _choose(trials)
        predict_mask = (X.index.get_level_values("trade_date") >= holdout_start) & X.notna().any(axis=1) & panel["close"].notna() & _observation_mask(X)
        holdout_predictions, fitted_audit = _fit_predict(winner, X[dev_valid], y[dev_valid], X[predict_mask], s["preprocess"])
    holdout_ics = _rank_ics(holdout_predictions, y[valid & (X.index.get_level_values("trade_date") >= holdout_start)])
    if len(holdout_ics) < 10:
        raise ResearchError("INSUFFICIENT_HOLDOUT", "独立留出期有效 RankIC 日期少于 10 天")
    holdout_score = float(np.mean([row["ic"] for row in holdout_ics]))
    outer_score = float(np.mean([row["ic"] for fold in outer_records for row in fold["dailyIC"]]))
    # Qualification is a descriptive post-selection flag. It never changes winner.
    qualified = winner["score"] > 0 and outer_score > 0 and holdout_score > 0
    if not qualified:
        warn.append("没有验证出一致的正向预测优势。仍展示候选内优选模型的研究结果，不能视为已验证策略。")
    else:
        warn.append("多个时间窗口的平均RankIC为正，但尚未检验统计显著性或证明可盈利。")
    simulation_dates = [date for date in dates if date >= holdout_start]
    metrics, equity, trades, ledger, skipped = _simulate(panel, simulation_dates, holdout_predictions, s)
    if skipped:
        warn.append(f"存在 {len(skipped)} 条未成交/跳过调仓记录，详见 execution.skipped。")
    factor_results = []
    holdout_rows = X.index.get_level_values("trade_date") >= holdout_start
    observed_holdout = panel["close"].notna() & holdout_rows
    for factor in s["factors"]:
        ics = _rank_ics(X.loc[holdout_rows, factor["id"]], y[valid & holdout_rows])
        factor_results.append({"id": factor["id"], "ic": float(np.mean([row["ic"] for row in ics])) if ics else None, "coverage": float(X.loc[observed_holdout, factor["id"]].notna().mean()), "period": "terminal_holdout", "scoredDates": len(ics), "direction": factor["direction"], "definition": factor["expression"], "requiredFields": validate_expression(factor["expression"])["fields"]})
    candidates = []
    for name in names:
        family = [trial for trial in trials if trial["id"] == name]
        ok = [trial for trial in family if trial["status"] == "complete"]
        candidates.append(_choose(ok) if ok else family[0])
    candidates.sort(key=lambda row: row["score"] if row["score"] is not None else -np.inf, reverse=True)
    strategy_digest = hashlib.sha256(json.dumps(s, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    prov = {**provenance, **data_audit, "strategySha256": strategy_digest, "engineVersion": ENGINE_VERSION}
    training_diagnostics = _training_diagnostics(X, dev_valid, fitted_audit)
    factor_research = _factor_research(X, y, valid, holdout_start, s, training_diagnostics, metrics, ledger)
    result = {
        "schemaVersion": 1, "status": "completed", "engineVersion": ENGINE_VERSION,
        "strategy": s, "provenance": prov,
        "research": {"mode": s["research"]["mode"], "primaryAnalysis": "factorResearch", "standaloneMarketForecast": False, "observationDays": s["research"]["observationDays"], "labelHorizonSessions": horizon, "rebalanceDays": s["portfolio"]["rebalanceDays"], "samplingAnchor": dates[warmup], "observationDates": observation_dates, "portfolioInterpretation": "legacy_long_only_diagnostic_not_statistical_arbitrage"},
        "factorResearch": factor_research,
        "selection": {"winner": winner["id"], "winnerTrialId": winner["trialId"], "params": winner["params"], "metric": "rank_ic", "reason": "以开发期内扩展窗口验证的平均每日截面RankIC选择有限候选；终端留出结果从未参与排序。", "score": winner["score"], "candidates": candidates, "trials": trials, "trialCount": len(specs), "holdoutUsedForSelection": False, "qualified": qualified, "deploymentQualified": False, "evidenceStatus": "POSITIVE_WINDOW_MEANS_NOT_SIGNIFICANCE_TESTED" if qualified else "NO_VALIDATED_EDGE", "splits": {"unit": "unique_trade_date", "boundaryRule": "fixed_calendar_after_declared_factor_warmup", "development": {"start": development_dates[0], "end": development_dates[-1]}, "finalTraining": {"start": dev_dates[0], "end": dev_dates[-1], "labelEndMax": label_end[dev_valid].max(), "rows": int(dev_valid.sum())}, "holdout": {"start": holdout_start, "end": simulation_dates[-1], "fractionOfResearchDates": len(holdout_dates) / len(research_dates)}, "labelHorizonSessions": s["model"]["horizon"], "purgeRule": "training label end strictly before validation start", "outerFolds": outer_records}},
        "metrics": metrics, "equity": equity, "trades": trades, "factors": factor_results,
        "predictions": _forecast_report(holdout_predictions, y, label_end, panel, dates, s, winner["id"]),
        "trainingDiagnostics": training_diagnostics,
        "warnings": warn,
        "validation": {"metricPeriod": "terminal_holdout_only", "holdoutRankIC": holdout_score, "holdoutDailyIC": holdout_ics, "nestedWalkForwardRankIC": outer_score, "nestedWalkForwardFolds": len(outer_records), "randomSplitUsed": False, "preprocessingFitOnTrainOnly": True, "holdoutUsedForSelection": False, "finalFit": fitted_audit, "features": list(X.columns), "eligibleDates": len(eligible_dates), "eligibleObservationDates": int((valid.groupby(level="trade_date").sum() >= 3).sum()), "labelDefinition": TARGETS[s["model"]["target"]]["definition"] + "; T signal generated after close", "calendar": "provided" if data_audit["calendarProvided"] else "observed_dates_only", "seed": 17, "candidateBudget": len(specs), "totalFitsUpperBound": len(specs) * 9 + 4, "predictiveSignificanceTested": False},
        "execution": {"unit": "fractional_adjusted_research_unit", "fillRule": "T-close signal, T+1 open reference price plus explicit cash slippage cost", "positionCap": "target weight at rebalance, not a continuous hard exposure limit", "costUnit": "basis_points_of_reference_notional", "turnoverDefinition": "gross_traded_notional / mean_daily_equity", "benchmarkDefinition": "equal_weight_buy_and_hold_at_first_holdout_close_cost_free", "terminalLiquidation": False, "settlementRule": "A_SHARE_T_PLUS_ONE", "pendingOrderRule": "retry target weights at first tradable open until replaced by next scheduled signal; cash-scaled buys are final", "costBreakdown": metrics["costBreakdown"], "taxMode": "fixed_scenario_not_historical_schedule", "ledger": ledger, "skipped": skipped},
    }
    clean = _finite_json(result)
    json.dumps(clean, allow_nan=False)
    return clean
