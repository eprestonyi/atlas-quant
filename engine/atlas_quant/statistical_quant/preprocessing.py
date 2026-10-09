"""Versioned economic transforms, followed by separate train-fold transforms.

The semantic stage is causal and has no fitted population parameters. It runs
before direction and origin aggregation. Portable F accepts its constructed
outputs, not raw prices or market caps, and must never repeat these transforms.
"""
from __future__ import annotations
import ast
import copy
import numpy as np
import pandas as pd

from ..context_sources import context_field
from ..factors import _parse, evaluate_expression, validate_expression
from .schema import fail

AUTOMATIC_SCHEMA = "auto-factor-preprocess/1"
INPUT_STAGE = "after_per_leg_semantic_transform_and_origin_aggregation"
SCALING = "train_fold_median_iqr"
FIT_POPULATION = "asset_rows_global_dates"
PRICE_TRANSFORM = {"kind": "return_over_trailing_volatility", "returnLag": 1,
                   "volatilityWindow": 20, "volatilityLag": 1, "ddof": 1,
                   "minVolatility": 1e-8, "invalid": "missing"}
TRANSFORMS = [
    {"kind": "identity"}, {"kind": "log_positive", "invalid": "missing"},
    {"kind": "log1p_nonnegative", "invalid": "missing"},
    {"kind": "reciprocal_nonzero", "invalid": "missing"},
    {"kind": "percent_to_fraction", "divisor": 100}, PRICE_TRANSFORM,
]


def automatic_enabled(preprocess):
    return "automatic" in preprocess


def validate_automatic(value):
    if not isinstance(value, dict) or value != {"schema": AUTOMATIC_SCHEMA}:
        fail("INVALID_AUTOMATIC_PREPROCESSING", "自动因子预处理协议须为 auto-factor-preprocess/1")


def factor_descriptor(factor):
    tree, info = _parse(factor["expression"])
    contexts = [context_field(field) for field in info["fields"]]
    scope = "global" if contexts and all(contexts) else "asset"
    transform = {"kind": "identity"}
    # Compound DSL already defines its economic quantity. Never infer units
    # from a display name or apply log a second time to log(total_mv).
    if isinstance(tree.body, ast.Name):
        raw = tree.body.id
        context = context_field(raw)
        field = context["field"] if context else raw
        if field == "raw_close":
            fail("AUTO_FACTOR_REQUIRES_ADJUSTED_PRICE", "自动预处理请使用复权 close，原始 raw_close 的除权变化不能视作收益")
        if field in {"open", "high", "low", "close"}:
            transform = PRICE_TRANSFORM
        elif field in {"total_mv", "circ_mv", "float_mv", "total_share", "float_share", "free_share"}:
            transform = {"kind": "log_positive", "invalid": "missing"}
        elif field in {"vol", "amount"}:
            transform = {"kind": "log1p_nonnegative", "invalid": "missing"}
        elif field in {"pe", "pe_ttm", "pb", "ps", "ps_ttm"}:
            transform = {"kind": "reciprocal_nonzero", "invalid": "missing"}
        elif field in {"turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"}:
            transform = {"kind": "percent_to_fraction", "divisor": 100}
        elif field.startswith("fd_"):
            from ..connectors import FINANCIAL_ALIASES, FINANCIAL_FIELDS
            registered = FINANCIAL_ALIASES.get(field)
            if registered and FINANCIAL_FIELDS[registered][1] == "percent":
                transform = {"kind": "percent_to_fraction", "divisor": 100}
    return {"feature": "factor:" + factor["id"], "expression": factor["expression"],
            "direction": factor["direction"], "scope": scope, "transform": copy.deepcopy(transform),
            "aggregation": "global_once" if scope == "global" else "signed_origin_dollar_over_gross"}


def automatic_metadata(strategy):
    validate_automatic(strategy["preprocess"]["automatic"])
    return {"schema": AUTOMATIC_SCHEMA, "inputStage": INPUT_STAGE, "scaling": SCALING, "fitPopulation": FIT_POPULATION,
            "factors": [factor_descriptor(f) for f in strategy["factors"] if f["role"] != "hedge"]}


def transform_values(values, transform):
    """Columns are independent time series on the unfilled session grid."""
    values = values.astype(float).replace([np.inf, -np.inf], np.nan)
    kind = transform["kind"]
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if kind == "identity":
            result = values
        elif kind == "log_positive":
            result = np.log(values.where(values > 0))
        elif kind == "log1p_nonnegative":
            result = np.log1p(values.where(values >= 0))
        elif kind == "reciprocal_nonzero":
            result = 1. / values.where(values != 0)
        elif kind == "percent_to_fraction":
            result = values / 100.
        elif kind == "return_over_trailing_volatility":
            positive = values.where(values > 0)
            returns = positive / positive.shift(1) - 1.
            # t's shock cannot enlarge its own normalizer. No fill across gaps.
            volatility = returns.shift(1).rolling(20, min_periods=20).std(ddof=1)
            result = returns / volatility.where(volatility > 1e-8)
        else:
            fail("INVALID_AUTOMATIC_PREPROCESSING", "未知因子经济变换")
    return result.replace([np.inf, -np.inf], np.nan)


def _context_panel(panel, dates, symbols, factors):
    """Merge same-date broadcasts without filling a missing source date."""
    fields = sorted({field for factor in factors for field in validate_expression(factor["expression"])["fields"]
                     if context_field(field) is not None})
    if not fields:
        return panel, None
    daily = pd.DataFrame(index=dates)
    for field in fields:
        if field not in panel:
            fail("MISSING_MODEL_DATA", "缺少上下文因子字段：" + field)
        wide = panel[field].unstack("ts_code").reindex(index=dates, columns=symbols)
        if (wide.nunique(axis=1, dropna=True) > 1).any():
            fail("CONFLICTING_GLOBAL_FACTOR", "同日全局因子观测存在冲突：" + field)
        daily[field] = wide.bfill(axis=1).iloc[:, 0]
    evaluated = panel.copy()
    origin_dates = evaluated.index.get_level_values("trade_date")
    for field in fields:
        evaluated[field] = daily[field].reindex(origin_dates).to_numpy()
    single = daily.copy()
    single.index = pd.MultiIndex.from_arrays([single.index, ["GLOBAL"] * len(single)], names=["trade_date", "ts_code"])
    return evaluated, single


def evaluate_factors(panel, dates, symbols, strategy):
    metadata = automatic_metadata(strategy)
    descriptions = {item["feature"][7:]: item for item in metadata["factors"]}
    evaluated, global_panel = _context_panel(panel, dates, symbols, strategy["factors"])
    result = {}
    for factor in strategy["factors"]:
        description = descriptions.get(factor["id"])
        if description is None:  # Hedge exposures retain their declared units.
            raw = evaluate_expression(factor["expression"], panel).unstack("ts_code").reindex(index=dates, columns=symbols)
            result[factor["id"]] = raw * factor["direction"]
            continue
        if description["scope"] == "global":
            raw = evaluate_expression(factor["expression"], global_panel, mask_asset_availability=False).unstack("ts_code").reindex(index=dates)
            transformed = transform_values(raw, description["transform"]).iloc[:, 0] * factor["direction"]
            result[factor["id"]] = pd.DataFrame({symbol: transformed for symbol in symbols}, index=dates)
        else:
            raw = evaluate_expression(factor["expression"], evaluated).unstack("ts_code").reindex(index=dates, columns=symbols)
            result[factor["id"]] = transform_values(raw, description["transform"]) * factor["direction"]
    return result, metadata


def validate_metadata(metadata, factors):
    """Validate frozen declarations without reinterpreting a mutable catalogue."""
    expected = {"schema", "inputStage", "scaling", "fitPopulation", "factors"}
    if not isinstance(metadata, dict) or set(metadata) != expected or metadata["schema"] != AUTOMATIC_SCHEMA or metadata["inputStage"] != INPUT_STAGE or metadata["scaling"] != SCALING or metadata["fitPopulation"] != FIT_POPULATION:
        raise ValueError("invalid automatic preprocessing metadata")
    items = metadata["factors"]
    predictors = [f for f in factors if f["role"] != "hedge"]
    if not isinstance(items, list) or len(items) != len(predictors):
        raise ValueError("automatic factor declarations must match non-hedge factors")
    for item, factor in zip(items, predictors):
        if not isinstance(item, dict) or set(item) != {"feature", "expression", "direction", "scope", "transform", "aggregation"}:
            raise ValueError("invalid automatic factor declaration")
        if item["feature"] != "factor:" + factor["id"] or item["expression"] != factor["expression"] or isinstance(item["direction"], bool) or item["direction"] != factor["direction"]:
            raise ValueError("automatic factor source mismatch")
        if item["scope"] not in ("asset", "global") or item["aggregation"] != ("global_once" if item["scope"] == "global" else "signed_origin_dollar_over_gross"):
            raise ValueError("automatic factor scope mismatch")
        transform = item["transform"]
        # bool equals 1 in Python but is never a numeric protocol parameter.
        if not isinstance(transform, dict) or transform not in TRANSFORMS or any(isinstance(value, bool) for value in transform.values()):
            raise ValueError("invalid semantic transformation")


def feature_names(family, metadata):
    names = {"volatility20"}
    if family in ("mean_reversion", "pair_reversion"):
        names.update(("state_deviation20", "state_deviation60", "change1"))
    elif family == "trend":
        names.update(("trend1", "trend5", "trend20", "trend60"))
    else:
        names.update(("change1", "change5"))
    return names | {item["feature"] for item in metadata["factors"]}
