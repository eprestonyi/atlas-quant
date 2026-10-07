"""A small, causal factor language. Expressions are interpreted, never eval'd.

Input frames must have a (trade_date, ts_code) MultiIndex containing the entire
session grid. Missing sessions stay missing; rolling windows require every
observation. This is deliberately stricter than skipping suspended sessions.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .research_registry import is_external_field

FIELDS = frozenset("open high low close raw_close vol amount adj_factor turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv".split())
WINDOW_FUNCTIONS = frozenset("lag returns delta ts_mean ts_std ts_min ts_max ts_sum ts_rank".split())
UNARY_FUNCTIONS = frozenset("rank zscore log abs sqrt sign".split())
BINARY_FUNCTIONS = frozenset(("min", "max"))
ALLOWED_FUNCTIONS = WINDOW_FUNCTIONS | UNARY_FUNCTIONS | BINARY_FUNCTIONS | {"clip"}


class FactorError(ValueError):
    code = "INVALID_FACTOR"


def _number(node):
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise FactorError("窗口和边界必须为数字常量")
    value = sign * node.value
    if not np.isfinite(value) or abs(value) > 1_000_000:
        raise FactorError("数字常量必须有限且绝对值不超过 1000000")
    return value


def _parse(expression):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 500:
        raise FactorError("因子表达式须为 1–500 字符")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise FactorError("因子表达式语法错误") from exc
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise FactorError("因子表达式过于复杂")
    fields = set()

    def visit(node, depth=0):
        if depth > 16:
            raise FactorError("因子表达式嵌套超过 16 层")
        if isinstance(node, ast.Constant):
            _number(node)
            return 0
        if isinstance(node, ast.Name):
            if node.id not in FIELDS and not is_external_field(node.id):
                raise FactorError(f"不支持的字段：{node.id}")
            fields.add(node.id)
            return 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand, depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            return max(visit(node.left, depth + 1), visit(node.right, depth + 1))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name in WINDOW_FUNCTIONS and len(node.args) == 2:
                window = _number(node.args[1])
                if int(window) != window or not 1 <= window <= 252:
                    raise FactorError("窗口须为 1–252 的正整数")
                base = visit(node.args[0], depth + 1)
                return base + int(window) - (0 if name in {"lag", "returns", "delta"} else 1)
            if name in UNARY_FUNCTIONS and len(node.args) == 1:
                return visit(node.args[0], depth + 1)
            if name in BINARY_FUNCTIONS and len(node.args) == 2:
                return max(visit(arg, depth + 1) for arg in node.args)
            if name == "clip" and len(node.args) == 3:
                lo, hi = _number(node.args[1]), _number(node.args[2])
                if lo >= hi:
                    raise FactorError("clip 下界必须小于上界")
                return visit(node.args[0], depth + 1)
        raise FactorError("只允许白名单字段、因果函数及 + - * / 运算")

    lookback = visit(tree.body)
    if lookback > 504:
        raise FactorError("组合因子的回看期不得超过 504 个交易日")
    if not fields:
        raise FactorError("因子至少须依赖一个行情字段")
    return tree, {"fields": sorted(fields), "lookback": lookback, "causal": True}


def validate_expression(expression: str) -> dict:
    return _parse(expression)[1]


def required_fields(expressions) -> set[str]:
    return set().union(*(set(validate_expression(x)["fields"]) for x in expressions))


def evaluate_expression(expression: str, frame: pd.DataFrame) -> pd.Series:
    tree, meta = _parse(expression)
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["trade_date", "ts_code"]:
        raise FactorError("因子数据索引须为 trade_date / ts_code")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise FactorError("因子数据必须按日期和股票排序且不能重复")
    missing = set(meta["fields"]) - set(frame.columns)
    if missing:
        raise FactorError("缺少因子字段：" + ", ".join(sorted(missing)))

    def series(value):
        if isinstance(value, pd.Series):
            return value.astype(float)
        return pd.Series(float(value), index=frame.index)

    def divide(a, b):
        aa, bb = series(a), series(b)
        return aa.div(bb.where(bb.abs() > 1e-12))

    def calc(node):
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return pd.to_numeric(frame[node.id], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if isinstance(node, ast.UnaryOp):
            val = calc(node.operand)
            return -val if isinstance(node.op, ast.USub) else val
        if isinstance(node, ast.BinOp):
            a, b = calc(node.left), calc(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            return divide(a, b)
        name = node.func.id
        x = series(calc(node.args[0]))
        if name in WINDOW_FUNCTIONS:
            n = int(_number(node.args[1]))
            grouped = x.groupby(level="ts_code", sort=False)
            if name == "lag":
                return grouped.shift(n)
            if name == "returns":
                return divide(x, grouped.shift(n)) - 1
            if name == "delta":
                return x - grouped.shift(n)
            def rolling(s):
                roll = s.rolling(n, min_periods=n)
                if name == "ts_std":
                    return roll.std(ddof=0)
                if name == "ts_rank":
                    return roll.rank(pct=True)
                return getattr(roll, name.removeprefix("ts_"))()
            return grouped.transform(rolling)
        if name == "rank":
            return x.groupby(level="trade_date").rank(pct=True, method="average")
        if name == "zscore":
            group = x.groupby(level="trade_date")
            return divide(x - group.transform("mean"), group.transform(lambda s: s.std(ddof=0)))
        if name == "log":
            return np.log(x.where(x > 0))
        if name == "sqrt":
            return np.sqrt(x.where(x >= 0))
        if name == "abs":
            return x.abs()
        if name == "sign":
            return np.sign(x)
        if name == "clip":
            return x.clip(_number(node.args[1]), _number(node.args[2]))
        y = series(calc(node.args[1]))
        return pd.Series(np.minimum(x, y) if name == "min" else np.maximum(x, y), index=frame.index)

    with np.errstate(all="ignore"):
        result = series(calc(tree.body)).replace([np.inf, -np.inf], np.nan)
    # A missing market session must not generate a trade signal through a lag.
    return result.where(frame["close"].notna())


def load_catalog() -> dict:
    return json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
