"""Independent audit mathematics. No atlas_quant or pandas imports.

Family policy is an explicit reviewed list, not the engine's quantity algebra.
The small array interpreter exists only to compare recipe arithmetic; source
clocks, PIT admission and provider identity require their separate audits.
"""
import ast
import math
import numpy as np


IDENTITY_FAMILIES = frozenset("""
named_index_momentum20 named_index_momentum60 named_index_volatility20
named_index_drawdown60 named_index_amount20 named_index_volume20
named_index_beta60 named_index_exposure_shock named_index_relative_momentum20
momentum reversal volatility range volume_surge volume_variation liquidity
illiquidity trend_strength range_position rsi_ratio downside_risk
overnight_momentum intraday_momentum breakout drawdown rebound trend_efficiency
trend_persistence mean_return historical_sharpe bollinger_position
parkinson_volatility max_return min_return return_dispersion volume_rank
amount_surge money_flow volume_weighted_return moving_average_change
intraday_strength gap_reversal amplitude earnings_yield book_yield sales_yield
small_size small_float_size volume_ratio float_fraction free_float_fraction
valuation_spread daily_basic_change financial_price_ratio
""".split())
PERCENT_FAMILIES = frozenset(("dividend_yield", "turnover", "free_turnover"))


def expected_transform(factor, units):
    """Reviewed economic meaning, independent of automaticProcessing metadata."""
    family = factor["family"]
    if family in IDENTITY_FAMILIES:
        return "identity"
    if family in PERCENT_FAMILIES:
        return "percent_to_fraction"
    if family == "named_index_price":
        return "simple_return"
    if family in {"financial_level", "financial_disclosed_change"}:
        unit = units[factor["requiredFields"][0]]
        return {"percent": "percent_to_fraction", "ratio": "identity",
                "currency_per_share": "signed_log1p", "CNY": "signed_log1p"}[unit]
    if family in {"raw_input", "named_industry_level"}:
        field = factor["requiredFields"][0]
        suffix = field.rsplit("_", 1)[-1]
        if field in {"pe_ttm", "ps_ttm"} or suffix in {"pe", "pb"}:
            return "reciprocal_nonzero"
        if field in {"total_mv", "circ_mv"} or field.endswith("_total_mv"):
            return "log_positive"
        if field == "close":
            return "simple_return"
        if field in {"amount", "vol"}:
            return "log1p_nonnegative"
        raise AssertionError(("new raw field requires independent review", field))
    raise AssertionError(("new family requires independent review", family))


def shift(a, n):
    out = np.full_like(a, np.nan)
    out[n:] = a[:-n]
    return out


def safe_divide(a, b):
    a, b = np.broadcast_arrays(a, b)
    out = np.full(a.shape, np.nan, dtype=float)
    np.divide(a, b, out=out, where=np.abs(b) > 1e-12)
    return out


def rank(values):
    valid = np.isfinite(values)
    out = np.full(len(values), np.nan)
    for i, value in enumerate(values):
        if valid[i]:
            out[i] = (np.sum(values[valid] < value) + (np.sum(values[valid] == value)+1)/2) / valid.sum()
    return out


def expression_values(expression, fields):
    """Arrays have shape [session, asset]; all rolling windows are complete."""
    shape = next(iter(fields.values())).shape
    def array(value):
        return np.broadcast_to(value, shape).copy()
    def calc(node):
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return fields[node.id].copy()
        if isinstance(node, ast.UnaryOp):
            x = calc(node.operand)
            return -x if isinstance(node.op, ast.USub) else x
        if isinstance(node, ast.BinOp):
            a, b = calc(node.left), calc(node.right)
            if isinstance(node.op, ast.Add): return a+b
            if isinstance(node.op, ast.Sub): return a-b
            if isinstance(node.op, ast.Mult): return a*b
            return safe_divide(a, b)
        name = node.func.id
        x = array(calc(node.args[0]))
        if name in {"lag", "delta", "returns"}:
            lagged = shift(x, int(node.args[1].value))
            return lagged if name == "lag" else x-lagged if name == "delta" else safe_divide(x, lagged)-1
        if name.startswith("ts_"):
            n = int(node.args[1].value)
            out = np.full(shape, np.nan)
            for t in range(n-1, shape[0]):
                for s in range(shape[1]):
                    window = x[t-n+1:t+1, s]
                    if np.isfinite(window).all():
                        out[t, s] = rank(window)[-1] if name == "ts_rank" else {
                            "ts_mean": np.mean, "ts_sum": np.sum, "ts_std": np.std,
                            "ts_min": np.min, "ts_max": np.max,
                        }[name](window)
            return out
        if name == "rank": return np.asarray([rank(row) for row in x])
        if name == "zscore":
            return np.asarray([safe_divide(row-np.nanmean(row), np.nanstd(row)) for row in x])
        if name == "log": return np.log(np.where(x > 0, x, np.nan))
        if name == "sqrt": return np.sqrt(np.where(x >= 0, x, np.nan))
        if name == "abs": return np.abs(x)
        if name == "sign": return np.sign(x)
        if name == "clip": return np.clip(x, ast.literal_eval(node.args[1]), ast.literal_eval(node.args[2]))
        other = calc(node.args[1])
        if name == "min": return np.minimum(x, other)
        if name == "max": return np.maximum(x, other)
        raise AssertionError(name)
    with np.errstate(all="ignore"):
        result = array(calc(ast.parse(expression, mode="eval").body))
    result[~np.isfinite(result)] = np.nan
    result[~np.isfinite(fields["close"])] = np.nan
    return result


def economic_values(values, kind):
    """Scalar-loop reference intentionally separate from vectorized production."""
    assert kind in {"identity", "percent_to_fraction", "log_positive", "log1p_nonnegative",
                    "reciprocal_nonzero", "signed_log1p", "first_difference", "simple_return",
                    "log_return", "return_over_trailing_volatility"}
    output = np.full_like(values, np.nan)
    for t, s in np.ndindex(values.shape):
        value = values[t, s]
        if not math.isfinite(value): continue
        previous = values[t-1, s] if t else float("nan")
        if kind == "identity": output[t, s] = value
        elif kind == "percent_to_fraction": output[t, s] = value / 100
        elif kind == "log_positive" and value > 0: output[t, s] = math.log(value)
        elif kind == "log1p_nonnegative" and value >= 0: output[t, s] = math.log1p(value)
        elif kind == "reciprocal_nonzero" and value != 0: output[t, s] = 1/value
        elif kind == "signed_log1p": output[t, s] = math.copysign(math.log1p(abs(value)), value)
        elif kind == "first_difference" and math.isfinite(previous): output[t, s] = value-previous
        elif kind in {"simple_return", "log_return"} and value > 0 and previous > 0 and math.isfinite(previous):
            output[t, s] = value/previous-1 if kind == "simple_return" else math.log(value)-math.log(previous)
        elif kind == "return_over_trailing_volatility" and t >= 21:
            history = values[t-21:t, s]
            if value > 0 and np.isfinite(history).all() and (history > 0).all():
                returns = [b/a-1 for a, b in zip(history, history[1:])]
                mean = math.fsum(returns)/20
                volatility = math.sqrt(math.fsum((r-mean)**2 for r in returns)/19)
                if volatility > 1e-8:
                    output[t, s] = (value/previous-1)/volatility
    output[~np.isfinite(output)] = np.nan
    return output


def state_features(prices, quantities, family, asset, version=2):
    state = [sum(p*q for p, q in zip(row, quantities)) for row in prices]
    gross = sum(abs(p*q) for p, q in zip(prices[-1], quantities))
    differences = [b-a for a, b in zip(state, state[1:])]
    returns = [b/a-1 for a, b in zip(state, state[1:])]
    ordinary = asset and version == 2
    volatility = np.std(returns[-20:] if ordinary else differences[-20:], ddof=1)
    output = {"volatility20": float(volatility if ordinary else volatility/gross)}
    def change(h): return state[-1]/state[-1-h]-1 if ordinary else (state[-1]-state[-1-h])/gross
    if family in {"mean_reversion", "pair_reversion"}:
        for n in (20, 60):
            mean = sum(state[-n:])/n
            output["state_deviation"+str(n)] = state[-1]/mean-1 if ordinary else (state[-1]-mean)/gross
        output["change1"] = change(1)
    elif family == "trend":
        output.update({"trend"+str(n): change(n) for n in (1, 5, 20, 60)})
    else:
        output.update(change1=change(1), change5=change(5))
    return output
