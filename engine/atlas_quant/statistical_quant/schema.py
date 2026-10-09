"""Versioned forecast-first configuration; no implicit signal strategy adapters."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import re
from datetime import datetime

from .. import __version__ as VERSION
FAMILIES = ("mean_reversion", "pair_reversion", "trend", "fundamental", "event")
ESTIMATORS = ("auto", "no_change", "historical_drift", "ridge", "elastic_net", "hist_gradient_boosting",
              "polynomial_ridge", "polynomial_elastic_net", "transformed_ridge", "factorwise_basis")
MAX_FORECASTS = 25000
MAX_SAMPLES = 110000


def fail(code, message):
    from ..engine import ResearchError
    raise ResearchError(code, message)


def digest(value):
    # JSON has one numeric type. Worker parse/stringify removes 1.0 and -0.0;
    # normalize exactly integral floats so immutable hashes survive that transport.
    def canonical(item):
        if isinstance(item, dict):
            return {key: canonical(v) for key, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [canonical(v) for v in item]
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        return item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def number(value, name, lo, hi, integer=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lo <= value <= hi or (integer and int(value) != value):
        fail("INVALID_STATISTICAL_QUANT", f"{name} 须在 {lo}–{hi} 范围内" + ("且为整数" if integer else ""))
    return int(value) if integer else float(value)


def section(parent, name, allowed, defaults=None):
    raw = parent.setdefault(name, {})
    if not isinstance(raw, dict) or set(raw) - set(allowed) or any(v is None for v in raw.values()):
        fail("INVALID_STATISTICAL_QUANT", f"{name} 对象含无效字段")
    for key, value in (defaults or {}).items():
        raw.setdefault(key, value)
    return raw


def validate(strategy, *, capacity_profile=None):
    from ..factors import validate_expression
    from ..financial_statements.admission import (
        is_fundamental_field, is_registered_statement_state,
    )
    from .metadata import text, normalize_universe, normalize_bindings
    profile = None
    if capacity_profile is not None:
        from ..capacity.profiles import get_profile
        profile = get_profile(capacity_profile)
    symbol_limit = profile.max_symbols if profile is not None else 50
    if not isinstance(strategy, dict) or strategy.get("schemaVersion") != 2:
        fail("INVALID_STATISTICAL_QUANT", "预测研究需要 schemaVersion=2")
    allowed = {"schemaVersion", "name", "universe", "research", "factors", "preprocess", "target", "model", "validation", "execution", "portfolio", "costs", "dataBindings"}
    if set(strategy) - allowed or any(v is None for v in strategy.values()):
        fail("INVALID_STATISTICAL_QUANT", "预测研究含不支持的配置字段")
    s = copy.deepcopy(strategy)
    s["name"] = text(s.get("name"), "研究名称", 80)
    if not isinstance(s.get("research"), dict) or s["research"].get("mode") != "statistical_quant" or not isinstance(s.get("target"), dict) or "kind" not in s["target"] or not isinstance(s.get("model"), dict) or "family" not in s["model"]:
        fail("INVALID_STATISTICAL_QUANT", "需要明确research.mode、target.kind、model.family")
    u = s.get("universe")
    if not isinstance(u, dict):
        fail("INVALID_STATISTICAL_QUANT", "需要股票池")
    symbols = u.get("symbols")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= symbol_limit or any(not isinstance(x, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", x) for x in symbols) or len(set(symbols)) != len(symbols):
        fail("INVALID_STATISTICAL_QUANT", f"需要1–{symbol_limit}只唯一沪深A股")
    try:
        start, end = (datetime.strptime(u[k], "%Y%m%d") for k in ("start", "end"))
        if not all(isinstance(u[k], str) and re.fullmatch(r"\d{8}", u[k]) for k in ("start", "end")) or not 0 < (end-start).days <= 366*8:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        fail("INVALID_STATISTICAL_QUANT", "日期须递增且不超过8年")
    s["universe"] = u = normalize_universe(u)
    s["dataBindings"] = normalize_bindings(s.get("dataBindings", {}), symbols)
    r = section(s, "research", {"mode", "observationDays"}, {"mode": "statistical_quant", "observationDays": 1})
    if r["mode"] != "statistical_quant":
        fail("INVALID_STATISTICAL_QUANT", "研究模式须为 statistical_quant")
    r["observationDays"] = number(r["observationDays"], "observationDays", 1, 60, True)
    factors = s.setdefault("factors", [])
    if not isinstance(factors, list) or len(factors) > 32:
        fail("INVALID_STATISTICAL_QUANT", "最多32个因子")
    seen, roles, fields = set(), {}, {}
    for f in factors:
        if not isinstance(f, dict) or set(f)-{"id", "expression", "direction", "role", "version"} or any(v is None for v in f.values()):
            fail("INVALID_STATISTICAL_QUANT", "因子定义无效或重复")
        f["id"] = text(f.get("id"), "因子ID", 100)
        f["expression"] = text(f.get("expression"), "表达式", 500)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", f["id"]) or f["id"] in seen:
            fail("INVALID_STATISTICAL_QUANT", "因子定义无效或重复")
        if "version" in f:
            f["version"] = number(f["version"], "version", 1, 1e6, True)
        seen.add(f["id"])
        f.setdefault("direction", 1); f.setdefault("role", "predictor")
        if isinstance(f["direction"], bool) or f["direction"] not in (-1, 1) or f["role"] not in ("predictor", "hedge", "event"):
            fail("INVALID_STATISTICAL_QUANT", "因子方向或角色无效")
        fields[f["id"]] = validate_expression(f.get("expression"))["fields"]
        from ..context_sources import context_field
        if any(field.startswith("ext_ctx_") and context_field(field) is None for field in fields[f["id"]]):
            fail("CONTEXT_FIELD_UNKNOWN", "指数与行业因子须使用已登记的明确来源字段")
        if any(
            x.startswith("model_fin_") and not is_registered_statement_state(x)
            for x in fields[f["id"]]
        ):
            fail("UNREGISTERED_FINANCIAL_STATE", "财务状态须使用已注册的公式 ID")
        roles[f["id"]] = f["role"]
    from ..context_sources import context_field
    sources = {(spec["api"], spec["ts_code"]) for selected in fields.values() for field in selected
               if (spec := context_field(field)) is not None}
    if len(sources) > 16:
        fail("CONTEXT_SOURCE_LIMIT", "一次研究最多使用16个独立指数来源")
    pre = section(s, "preprocess", {"winsorize", "standardize", "decorrelation", "correlationThreshold", "automatic"}, {"winsorize": True, "standardize": True, "decorrelation": "drop_correlated", "correlationThreshold": .9})
    if any(not isinstance(pre[k], bool) for k in ("winsorize", "standardize")) or pre["decorrelation"] not in ("none", "drop_correlated"):
        fail("INVALID_STATISTICAL_QUANT", "预处理配置无效")
    pre["correlationThreshold"] = number(pre["correlationThreshold"], "correlationThreshold", .5, 1)
    if "automatic" in pre:
        from .preprocessing import automatic_metadata
        automatic_metadata(s)  # Reject invalid protocol/raw unadjusted prices before data acquisition.
    else:
        from ..context_sources import context_field
        if any(context_field(field) is not None for items in fields.values() for field in items):
            fail("CONTEXT_REQUIRES_AUTOMATIC_PREPROCESSING", "指数与行业上下文因子需要启用自动预处理，以保留全局输入")
    target = section(s, "target", {"kind", "horizonSessions", "basket"}, {"kind": "asset_price", "horizonSessions": 5})
    if target["kind"] not in ("asset_price", "frozen_basket"):
        fail("INCOMPATIBLE_TARGET", "预测对象无效")
    target["horizonSessions"] = number(target["horizonSessions"], "horizonSessions", 1, 60, True)
    if target["kind"] == "asset_price":
        if "basket" in target or "hedge" in roles.values():
            fail("INCOMPATIBLE_TARGET", "单资产目标不接受篮子或对冲暴露")
    else:
        b = section(target, "basket", {"method", "symbols", "formationDays", "components", "quantities"}, {"formationDays": 126})
        members = b.get("symbols")
        if not isinstance(members, list) or not 1 <= len(members) <= 20 or any(not isinstance(x, str) for x in members) or len(set(members)) != len(members) or set(members)-set(symbols):
            fail("INCOMPATIBLE_TARGET", "篮子需要股票池内1–20只唯一证券")
        method = b.get("method")
        if method not in ("pair_ols", "pca_residual", "fixed"):
            fail("INCOMPATIBLE_TARGET", "篮子构建方法无效")
        b["formationDays"] = number(b["formationDays"], "formationDays", 60, 504, True)
        if method == "pair_ols" and len(members) != 2:
            fail("INCOMPATIBLE_TARGET", "配对目标恰好需要两只证券")
        if method == "pca_residual":
            if len(members) < 3:
                fail("INCOMPATIBLE_TARGET", "PCA篮子至少需要三只证券")
            b["components"] = number(b.get("components", min(2, len(members)-2)), "components", 1, min(10, len(members)-2), True)
        elif "components" in b or "hedge" in roles.values():
            fail("INCOMPATIBLE_TARGET", "仅PCA篮子接受components和hedge因子")
        if method == "fixed":
            q = b.get("quantities")
            if not isinstance(q, dict) or set(q) != set(members):
                fail("INCOMPATIBLE_TARGET", "冻结数量必须逐一对应篮子成员")
            b["quantities"] = {k: number(v, "quantity", -1e6, 1e6) for k, v in q.items()}
            if not any(b["quantities"].values()):
                fail("INCOMPATIBLE_TARGET", "冻结数量不可全零")
        elif "quantities" in b:
            fail("INCOMPATIBLE_TARGET", "估计篮子不接受手工数量覆盖")
    model = section(s, "model", {"family", "estimator", "trainWindow", "refitDays", "search", "parameterSharing"}, {"family": "mean_reversion", "estimator": "auto", "trainWindow": 504, "refitDays": 20})
    if model["family"] not in FAMILIES or model["estimator"] not in ESTIMATORS:
        fail("INVALID_STATISTICAL_QUANT", "模型族或估计器无效")
    if "search" in model and model["search"] != {"schema": "factor-model-search/1"}:
        fail("INVALID_STATISTICAL_QUANT", "模型搜索协议无效")
    if "parameterSharing" in model and model["parameterSharing"] not in ("pooled", "per_target"):
        fail("INVALID_STATISTICAL_QUANT", "模型参数作用域无效")
    if model.get("parameterSharing") == "per_target" and target["kind"] != "asset_price":
        fail("INVALID_STATISTICAL_QUANT", "逐标的模型需要逐只股票目标")
    if model.get("parameterSharing") == "per_target" and len(symbols) > 50:
        fail("MODEL_SCOPE_CAPACITY", "逐标的独立搜索当前支持最多50只；请继续筛选或使用共享模型")
    for key, lo, hi in (("trainWindow", 120, 1260), ("refitDays", 1, 126)):
        model[key] = number(model[key], key, lo, hi, True)
    if model["family"] == "pair_reversion" and target.get("basket", {}).get("method") != "pair_ols":
        fail("INCOMPATIBLE_TARGET", "pair_reversion需要pair_ols冻结篮子")
    if model["family"] == "fundamental" and not any(
        roles[key] == "predictor" and any(is_fundamental_field(field) for field in selected)
        for key, selected in fields.items()
    ):
        fail("MISSING_MODEL_DATA", "财务条件模型需要已选实际基本面字段")
    if model["family"] == "event" and not any(roles[k] == "event" and any(x.startswith(("ext_", "pcd_", "fd_")) for x in v) for k, v in fields.items()):
        fail("MISSING_MODEL_DATA", "事件模型需要role:event的点时外部数值")
    val = section(s, "validation", {"holdoutFraction", "testStart", "minTrainDates", "innerFolds", "outerFolds"}, {"holdoutFraction": .2, "minTrainDates": 80, "innerFolds": 2, "outerFolds": 2})
    for key, lo, hi, integer in (("holdoutFraction", .1, .4, False), ("minTrainDates", 40, 252, True), ("innerFolds", 2, 3, True), ("outerFolds", 2, 3, True)):
        val[key] = number(val[key], key, lo, hi, integer)
    if "testStart" in val:
        value = val["testStart"]
        try:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
                raise ValueError()
            datetime.strptime(value, "%Y%m%d")
            if not u["start"] <= value <= u["end"]:
                raise ValueError()
        except (ValueError, TypeError):
            fail("INVALID_STATISTICAL_QUANT", "testStart须为研究区间内的YYYYMMDD日期")
    if val["minTrainDates"] > model["trainWindow"]:
        fail("INVALID_STATISTICAL_QUANT", "minTrainDates不可超过trainWindow")
    ex = section(s, "execution", {"enabled", "side", "shorting", "minEdgeBps", "maxPositions"}, {"enabled": True, "side": "long_short", "shorting": "theoretical", "minEdgeBps": 10, "maxPositions": 5})
    if not isinstance(ex["enabled"], bool) or ex["side"] not in ("long_only", "long_short") or ex["shorting"] != "theoretical":
        fail("INVALID_STATISTICAL_QUANT", "执行模式无效；空头仅限明确理论情景")
    if ex["enabled"] and any(
        field.startswith("model_fin_")
        for selected in fields.values() for field in selected
    ):
        fail("FINANCIAL_FORECAST_ONLY_REQUIRED", "财务状态当前仅支持明确 execution.enabled=false 的预测研究。")
    ex["minEdgeBps"] = number(ex["minEdgeBps"], "minEdgeBps", 0, 10000)
    ex["maxPositions"] = number(ex["maxPositions"], "maxPositions", 1, 50, True)
    p = section(s, "portfolio", {"initialCapital", "grossExposure", "maxWeight", "rebalanceDays", "rebalanceThresholdBps", "netExposureLimit", "sizingMode", "targetAnnualVolatility", "volatilityLookback", "factorExposureLimits"}, {"initialCapital": 1e6, "grossExposure": 1, "maxWeight": .3, "rebalanceDays": 1, "rebalanceThresholdBps": 25, "netExposureLimit": 2., "sizingMode": "fixed", "targetAnnualVolatility": .1, "volatilityLookback": 60, "factorExposureLimits": []})
    for key, lo, hi, integer in (("initialCapital", 10000, 1e9, False), ("grossExposure", .1, 2, False), ("maxWeight", .01, 1, False), ("rebalanceDays", 1, 60, True), ("rebalanceThresholdBps", 0, 10000, False)):
        p[key] = number(p[key], key, lo, hi, integer)
    p["netExposureLimit"] = number(p["netExposureLimit"], "netExposureLimit", 0, 2)
    p["targetAnnualVolatility"] = number(p["targetAnnualVolatility"], "targetAnnualVolatility", .01, 1)
    p["volatilityLookback"] = number(p["volatilityLookback"], "volatilityLookback", 20, 252, True)
    if p["sizingMode"] not in ("fixed", "volatility_target") or not isinstance(p["factorExposureLimits"], list) or len(p["factorExposureLimits"])>32:
        fail("INVALID_STATISTICAL_QUANT", "风险规模或暴露约束无效")
    limit_ids = set()
    for limit in p["factorExposureLimits"]:
        if not isinstance(limit, dict) or set(limit) != {"factorId", "maxAbsExposure"} or not isinstance(limit.get("factorId"), str) or limit["factorId"] not in seen or limit["factorId"] in limit_ids:
            fail("INVALID_STATISTICAL_QUANT", "风险暴露须引用唯一的已选因子")
        limit_ids.add(limit["factorId"])
        limit["maxAbsExposure"] = number(limit["maxAbsExposure"], "maxAbsExposure", 0, 5)
    c = section(s, "costs", {"commissionBps", "slippageBps", "sellTaxBps", "transferBps", "minCommission", "borrowAnnualBps"}, {"commissionBps": 2.5, "slippageBps": 3, "sellTaxBps": 5, "transferBps": .1, "minCommission": 5, "borrowAnnualBps": 300})
    for key, hi in (("commissionBps", 100), ("slippageBps", 200), ("sellTaxBps", 100), ("transferBps", 100), ("minCommission", 1000), ("borrowAnnualBps", 10000)):
        c[key] = number(c[key], key, 0, hi)
    if profile is not None:
        profile.validate_strategy(s)
    return s


def prediction_config(s):
    return {k: v for k, v in s.items() if k not in {"execution", "portfolio", "costs", "name", "graph"}}
