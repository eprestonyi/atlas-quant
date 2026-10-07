"""Independent sizing adapter. It cannot construct or replace a forecast."""
from __future__ import annotations
import numpy as np
from ..factors import evaluate_expression


def bounded_units(positions, prices, direction_quantities, desired, nav, gross_limit, max_weight, *, snapshot=None, config=None, costs=None):
    """Scale the entire frozen basket, never clip individual hedge legs."""
    if nav <= 0 or desired <= 0:
        return 0.
    existing = np.asarray(positions)*np.asarray(prices)
    addition = np.asarray(direction_quantities)*np.asarray(prices)
    def allowed(units):
        values = existing + units*addition
        remaining_nav = nav
        if costs:
            from ..engine import _trade_costs
            remaining_nav -= sum(_trade_costs(abs(units*value), "BUY" if value>0 else "SELL", costs)["cost"]
                                 for value in addition if abs(units*value)>1e-10)
        basic = remaining_nav>0 and np.sum(np.abs(values)) <= remaining_nav*gross_limit+1e-8 and np.max(np.abs(values)) <= remaining_nav*max_weight+1e-8
        return basic and (not config or not breaches(values/remaining_nav, snapshot or {}, config))
    # Do not add risk while existing price drift already violates these limits.
    # Expiry/risk exits remain independent and are never suppressed by this gate.
    if not allowed(0):
        return 0.
    if allowed(desired):
        return float(desired)
    lo, hi = 0., float(desired)
    for _ in range(50):
        mid = (lo+hi)/2
        if allowed(mid):
            lo = mid
        else:
            hi = mid
    return lo


class RiskState:
    """Only past closes and same-or-earlier published factor observations."""
    def __init__(self, panel, dates, symbols, strategy):
        self.dates = dates
        self.symbols = symbols
        self.config = strategy["portfolio"]
        self.closes = panel.close.unstack("ts_code").reindex(index=dates, columns=symbols)
        self.returns = self.closes.pct_change(fill_method=None)
        ids = {x["factorId"] for x in self.config["factorExposureLimits"]}
        self.factors = {f["id"]: (evaluate_expression(f["expression"], panel)*f["direction"]).unstack("ts_code").reindex(index=dates, columns=symbols)
                        for f in strategy["factors"] if f["id"] in ids}

    def before(self, date):
        t = self.dates.index(date)
        result = {"informationCutoff": self.dates[t-1] if t else None,
                  "covariance": None, "invalidVolatilitySymbols": [], "factorExposures": {}, "invalidFactors": []}
        if self.config["sizingMode"] == "volatility_target":
            window = self.config["volatilityLookback"]
            values = self.returns.iloc[max(0, t-window):t].to_numpy()
            valid = np.isfinite(values).all(axis=0) if len(values)==window else np.zeros(len(self.symbols), dtype=bool)
            result["invalidVolatilitySymbols"] = [s for s, ok in zip(self.symbols, valid) if not ok]
            result["invalidVolatilityIndices"] = np.flatnonzero(~valid).tolist()
            covariance = np.zeros((len(self.symbols), len(self.symbols)))
            if valid.any():
                subset = np.atleast_2d(np.cov(values[:, valid], rowvar=False, ddof=1))*252
                subset = .9*subset + .1*np.diag(np.diag(subset))
                # Finite-sample PSD regularization is disclosed, not alpha tuning.
                eigenvalues, vectors = np.linalg.eigh(subset)
                subset = (vectors*np.maximum(eigenvalues, 0))@vectors.T
                covariance[np.ix_(valid, valid)] = subset
            result["covariance"] = covariance
            result["volatilityMethod"] = "past_complete_simple_return_covariance_252_10percent_diagonal_shrinkage_psd"
        for name, frame in self.factors.items():
            values = frame.iloc[t-1].to_numpy() if t else np.full(len(self.symbols), np.nan)
            scale = np.std(values, ddof=0)
            if not np.isfinite(values).all() or not np.isfinite(scale) or scale < 1e-12:
                result["invalidFactors"].append(name)
            else:
                result["factorExposures"][name] = (values-values.mean())/scale
        return result


def measure(weights, snapshot, config, symbols=None):
    result = {"gross": float(np.abs(weights).sum()), "net": float(np.sum(weights)), "annualVolatility": None, "factorExposures": {}}
    if snapshot.get("covariance") is not None and not any(abs(weights[k])>1e-10 for k in snapshot.get("invalidVolatilityIndices", [])):
        result["annualVolatility"] = float(np.sqrt(max(0., weights @ snapshot["covariance"] @ weights)))
    for name, values in snapshot.get("factorExposures", {}).items():
        result["factorExposures"][name] = float(weights @ values)
    return result


def breaches(weights, snapshot, config):
    active = np.abs(weights)>1e-10
    issues = []
    if abs(float(np.sum(weights))) > config["netExposureLimit"]+1e-10:
        issues.append("net_exposure_limit")
    if config["sizingMode"] == "volatility_target" and active.any():
        invalid = snapshot.get("invalidVolatilityIndices", [])
        if snapshot.get("covariance") is None or any(active[k] for k in invalid):
            issues.append("volatility_history_unavailable")
        elif float(weights @ snapshot["covariance"] @ weights) > config["targetAnnualVolatility"]**2+1e-12:
            issues.append("volatility_target_limit")
    if active.any():
        for limit in config["factorExposureLimits"]:
            name = limit["factorId"]
            values = snapshot.get("factorExposures", {}).get(name)
            if values is None:
                issues.append("factor_exposure_unavailable:"+name)
            elif abs(float(weights @ values)) > limit["maxAbsExposure"]+1e-10:
                issues.append("factor_exposure_limit:"+name)
    return issues


def estimated_round_trip_cost(delta, prices, costs, horizon):
    from ..engine import _trade_costs
    opening = closing = 0.
    for quantity, price in zip(delta, prices):
        if abs(quantity) < 1e-12:
            continue
        side = "BUY" if quantity > 0 else "SELL"
        notional = abs(quantity)*price
        opening += _trade_costs(notional, side, costs)["cost"]
        closing += _trade_costs(notional, "SELL" if side == "BUY" else "BUY", costs)["cost"]
    borrow = float(np.maximum(-np.asarray(delta), 0) @ np.asarray(prices))*costs["borrowAnnualBps"]/10000*horizon/252
    return {"opening": opening, "closing": closing, "borrow": borrow, "total": opening+closing+borrow,
            "method": "current_executable_reference_price_both_legs_plus_declared_holding_borrow"}
