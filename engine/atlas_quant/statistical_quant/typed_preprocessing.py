"""Economic quantities for preprocessing v2; no observations or fitted values.

This is a conservative unit algebra over the already validated factor DSL.
It distinguishes a price level from a price ratio even when both are compound
expressions. Unknown external units require an explicit recorded Studio choice.
"""
from __future__ import annotations
import ast
import copy
import re
from dataclasses import dataclass

from ..context_sources import context_field, FOREIGN_APIS
from ..factors import _parse
from .schema import fail

SCHEMA = "auto-factor-preprocess/2"
STATE_FEATURES = "asset_returns_basket_gross/1"
TYPES = frozenset(("price", "log_price", "positive_size", "nonnegative_flow", "valuation_multiple",
                   "percent", "ratio", "currency_per_share", "currency_amount", "derived", "custom_numeric"))
CLOCKS = frozenset(("research_sessions", "observed_source_sessions_asof"))
SIMPLE_RETURN = {"kind": "simple_return", "lag": 1, "invalid": "missing"}
LOG_RETURN = {"kind": "log_return", "lag": 1, "invalid": "missing"}
SIGNED_LOG = {"kind": "signed_log1p", "referenceUnit": 1, "invalid": "missing"}
DIFFERENCE = {"kind": "first_difference", "lag": 1, "invalid": "missing"}
NEW_TRANSFORMS = [SIMPLE_RETURN, LOG_RETURN, SIGNED_LOG, DIFFERENCE]


@dataclass(frozen=True)
class Quantity:
    kind: str
    unit: str


RATIO = Quantity("ratio", "1")
DERIVED = Quantity("derived", "1")
UNKNOWN = Quantity("custom_numeric", "declared_numeric")
INVALID = Quantity("invalid", "incompatible_units")


def field_quantity(name):
    context = context_field(name)
    field = context["field"] if context else name
    unit = context["unit"] if context else None
    if field in {"open", "high", "low", "close", "raw_close"}:
        return Quantity("price", unit or "CNY_per_share")
    if field in {"total_mv", "circ_mv", "float_mv"}:
        return Quantity("positive_size", unit or "CNY_10000")
    if field in {"total_share", "float_share", "free_share"}:
        return Quantity("positive_size", "shares_10000")
    if field in {"vol", "amount"}:
        return Quantity("nonnegative_flow", unit or ("hands" if field == "vol" else "CNY_thousands"))
    if field in {"pe", "pe_ttm", "pb", "ps", "ps_ttm"}:
        return Quantity("valuation_multiple", "ratio")
    if field in {"turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"}:
        return Quantity("percent", "percent")
    if field in {"volume_ratio", "adj_factor"}:
        return RATIO
    from ..connectors import FINANCIAL_ALIASES, FINANCIAL_FIELDS
    if field in FINANCIAL_ALIASES:
        registered_unit = FINANCIAL_FIELDS[FINANCIAL_ALIASES[field]][1]
        return Quantity({"percent": "percent", "ratio": "ratio", "currency_per_share": "currency_per_share", "CNY": "currency_amount"}[registered_unit],
                        "CNY_per_share" if registered_unit == "currency_per_share" else registered_unit)
    from ..financial_statements.admission import is_registered_statement_state
    if is_registered_statement_state(field):
        return RATIO  # Every admitted statement recipe is an explicit ratio.
    return UNKNOWN


def _dimensionless(value):
    return value.kind in {"ratio", "derived"} and value.unit in {"1", "ratio"}


def _same_units(left, right):
    return left.unit == right.unit and left != UNKNOWN and right != UNKNOWN


def _literal(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        value = _literal(node.operand)
        if value is not None:
            return -value if isinstance(node.op, ast.USub) else value
    return None


def _scaled(quantity, scalar):
    if scalar is not None and scalar <= 0 and quantity.kind in {"price", "positive_size", "nonnegative_flow"}:
        return Quantity("derived", quantity.unit)
    return quantity


def _combine(node):
    if isinstance(node, ast.Name):
        return field_quantity(node.id)
    if isinstance(node, ast.Constant):
        return RATIO
    if isinstance(node, ast.UnaryOp):
        value = _combine(node.operand)
        return Quantity("derived", value.unit) if isinstance(node.op, ast.USub) and value.kind in {"price", "positive_size", "nonnegative_flow"} else value
    if isinstance(node, ast.BinOp):
        left, right = _combine(node.left), _combine(node.right)
        if INVALID in (left, right):
            return INVALID
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if isinstance(node.left, ast.Constant) and node.left.value == 0:
                if isinstance(node.op, ast.Sub) and right.kind in {"price", "positive_size", "nonnegative_flow"}:
                    return Quantity("derived", right.unit)
                return right
            if isinstance(node.right, ast.Constant) and node.right.value == 0:
                return left
            if _dimensionless(left) and _dimensionless(right):
                return RATIO
            if _same_units(left, right):
                if isinstance(node.op, ast.Sub) and left.kind == right.kind == "log_price":
                    return RATIO
                if isinstance(node.op, ast.Sub) and left.kind not in {"percent", "currency_per_share", "currency_amount"}:
                    return Quantity("derived", left.unit)
                return left if left.kind == right.kind else Quantity("derived", left.unit)
            return UNKNOWN if UNKNOWN in (left, right) else INVALID
        if isinstance(node.op, ast.Div):
            if _literal(node.right) == 0:
                return INVALID
            if _same_units(left, right) or _dimensionless(left) and _dimensionless(right):
                return RATIO
            if _dimensionless(left) and right.kind == "valuation_multiple":
                return RATIO
            if left.kind == "percent" and isinstance(node.right, ast.Constant) and node.right.value == 100:
                return RATIO
            if _dimensionless(right):
                return _scaled(left, _literal(node.right))
            return UNKNOWN if UNKNOWN in (left, right) else Quantity("derived", "1/" + right.unit) if _dimensionless(left) else INVALID
        if isinstance(node.op, ast.Mult):
            if left.kind == "percent" and isinstance(node.right, ast.Constant) and node.right.value == .01 or right.kind == "percent" and isinstance(node.left, ast.Constant) and node.left.value == .01:
                return RATIO
            if _dimensionless(left):
                return _scaled(right, _literal(node.left))
            if _dimensionless(right):
                return _scaled(left, _literal(node.right))
            return UNKNOWN if UNKNOWN in (left, right) else INVALID
    if isinstance(node, ast.Call):
        name = node.func.id
        value = _combine(node.args[0])
        if value == INVALID or any(_combine(child) == INVALID for child in node.args[1:]):
            return INVALID
        if name == "log" and value.kind == "price":
            return Quantity("log_price", "log_" + value.unit)
        if name == "log" and value.kind == "log_price":
            return INVALID
        if name in {"returns", "rank", "zscore", "ts_rank", "sign", "log"}:
            return DERIVED if name == "log" else RATIO
        if name == "sqrt":
            return DERIVED if _dimensionless(value) else UNKNOWN if value == UNKNOWN else INVALID
        if name in {"min", "max"}:
            other = _combine(node.args[1])
            if isinstance(node.args[1], ast.Constant):
                return value
            if isinstance(node.args[0], ast.Constant):
                return other
            return value if _same_units(value, other) else RATIO if _dimensionless(value) and _dimensionless(other) else UNKNOWN if UNKNOWN in (value, other) else INVALID
        # delta/std retain the physical unit, but are signed/change quantities:
        # do not reciprocal a change in a multiple or log a change in size.
        if name in {"delta", "ts_std"}:
            if value.kind == "log_price":
                return RATIO
            if value.kind in {"percent", "currency_per_share", "currency_amount"}:
                return value
            if _dimensionless(value):
                return RATIO
            return Quantity("derived", value.unit)
        if name in {"lag", "ts_mean", "ts_min", "ts_max", "ts_sum", "abs", "clip"}:
            return value
    return UNKNOWN


def default_transform(quantity):
    if quantity.kind == "derived" and not _dimensionless(quantity) and not quantity.unit.startswith("1/"):
        return None  # A dimensional change needs an explicit scale/definition.
    return copy.deepcopy({
        "price": SIMPLE_RETURN,
        "log_price": DIFFERENCE,
        "positive_size": {"kind": "log_positive", "invalid": "missing"},
        "nonnegative_flow": {"kind": "log1p_nonnegative", "invalid": "missing"},
        "valuation_multiple": {"kind": "reciprocal_nonzero", "invalid": "missing"},
        "percent": {"kind": "percent_to_fraction", "divisor": 100},
        "currency_per_share": SIGNED_LOG, "currency_amount": SIGNED_LOG,
        "ratio": {"kind": "identity"}, "derived": {"kind": "identity"},
    }.get(quantity.kind))


def validate_config(value, transforms):
    if not isinstance(value, dict) or set(value) - {"schema", "overrides"} or value.get("schema") != SCHEMA:
        fail("INVALID_AUTOMATIC_PREPROCESSING", "自动因子预处理协议无效")
    overrides = value.get("overrides", {})
    if not isinstance(overrides, dict) or len(overrides) > 32:
        fail("INVALID_AUTOMATIC_PREPROCESSING", "逐因子处理选择无效")
    for ident, override in overrides.items():
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident) or not isinstance(override, dict) or set(override) != {"transform"}:
            fail("INVALID_AUTOMATIC_PREPROCESSING", "逐因子处理选择无效")
        selected = override["transform"]
        if not isinstance(selected, dict) or selected not in transforms or any(isinstance(v, bool) for v in selected.values()):
            fail("INVALID_AUTOMATIC_PREPROCESSING", "逐因子经济变换无效")


def descriptor(factor, override=None):
    tree, info = _parse(factor["expression"])
    contexts = [context_field(field) for field in info["fields"]]
    global_ = bool(contexts) and all(contexts)
    sources = {(x["api"], x["ts_code"]) for x in contexts if x}
    native = global_ and len(sources) == 1 and next(iter(sources))[0] in FOREIGN_APIS
    if global_ and any(isinstance(node, ast.Call) and node.func.id in {"rank", "zscore"} for node in ast.walk(tree)):
        fail("GLOBAL_FACTOR_CROSS_SECTION", "全局因子不能在当日股票之间排名或标准化")
    quantity = _combine(tree.body)
    if quantity == INVALID:
        fail("FACTOR_UNIT_MISMATCH", "表达式混合了不相容的经济单位：" + factor["id"])
    transform = copy.deepcopy(override["transform"]) if override else default_transform(quantity)
    if transform is None:
        fail("FACTOR_TRANSFORM_REQUIRED", "因子经济单位未确定，请为该因子明确选择处理方式：" + factor["id"])
    if quantity.kind == "price" and transform["kind"] not in {"simple_return", "log_return", "return_over_trailing_volatility"}:
        fail("PRICE_RETURN_REQUIRED", "价格状态须明确转换为收益率")
    if quantity.kind == "log_price" and transform["kind"] != "first_difference":
        fail("PRICE_RETURN_REQUIRED", "对数价格须转换为对数收益率")
    if "raw_close" in info["fields"]:
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "raw_close":
                parent = parents[node]
                if not (isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div) and parent.right is node and _combine(parent.left).kind == "currency_per_share"):
                    fail("AUTO_FACTOR_REQUIRES_ADJUSTED_PRICE", "收益率须使用复权价格；raw_close 只用于显式每股财务估值分母")
    if factor.get("role") == "event" and transform["kind"] not in {"identity", "percent_to_fraction", "signed_log1p", "log1p_nonnegative"}:
        fail("EVENT_TRANSFORM_ZERO", "事件因子处理须保留零事件，不接受价格变化或倒数变换")
    return {"feature": "factor:" + factor["id"], "expression": factor["expression"],
            "direction": factor["direction"], "scope": "global" if global_ else "asset",
            "transform": transform, "aggregation": "global_once" if global_ else "signed_origin_dollar_over_gross",
            "economicType": quantity.kind, "sourceUnit": quantity.unit,
            "clock": "observed_source_sessions_asof" if native else "research_sessions"}
