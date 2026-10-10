"""Causal origin-specific, frozen-quantity targets and two executable labels."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from ..factors import evaluate_expression, validate_expression
from ..financial_statements.admission import is_fundamental_field
from .schema import digest, fail, MAX_SAMPLES


@dataclass
class Samples:
    X: pd.DataFrame
    y: pd.DataFrame
    meta: pd.DataFrame
    definitions: dict
    start_index: int
    dates: list
    hedge_fits: list
    automatic_preprocessing: dict | None = None


def _definition(kind, symbols, q, construction, start=None, end=None, audit=None):
    content = {"kind": kind, "symbols": list(symbols), "quantities": [float(x) for x in q],
               "unit": "CNY_adjusted_research_basket" if kind == "frozen_basket" else "CNY_adjusted_research_price",
               "construction": construction, "formationStart": start, "formationEnd": end,
               "hedgeAudit": audit or {}}
    return {"id": "target_" + digest(content)[:24], **content}


def _construct(strategy, closes, factors, dates, t):
    target = strategy["target"]
    if target["kind"] == "asset_price":
        return [_definition("asset_price", [symbol], [1.], "single_asset") for symbol in closes.columns]
    b = target["basket"]; symbols = b["symbols"]
    if b["method"] == "fixed":
        return [_definition("frozen_basket", symbols, [b["quantities"][x] for x in symbols], "fixed")]
    prices = closes.loc[:, symbols].iloc[t-b["formationDays"]:t].to_numpy()
    if len(prices) != b["formationDays"] or not np.isfinite(prices).all():
        return []
    if b["method"] == "pair_ols":
        x, y = prices[:, 1], prices[:, 0]
        if np.std(x) < 1e-10:
            return []
        beta = float(np.sum((x-x.mean())*(y-y.mean()))/np.sum((x-x.mean())**2))
        intercept = float(y.mean()-beta*x.mean())
        audit = {"beta": beta, "intercept": intercept, "interceptIsTradableLeg": False,
                 "cointegrationTestPerformed": False, "fitRows": len(prices)}
        return [_definition("frozen_basket", symbols, [1., -beta], "pair_ols", dates[t-b["formationDays"]], dates[t-1], audit)]
    from ..stat_arb import hedge_projection
    # Returns are measured strictly inside the completed formation window.
    returns = np.diff(np.log(prices), axis=0)
    exposures = {f["id"]: factors[f["id"]].loc[dates[t-1], symbols].to_numpy()
                 for f in strategy["factors"] if f["role"] == "hedge"}
    projection, B, audit = hedge_projection(returns, exposures,
                                           {"method": "pca_residual", "components": b["components"]},
                                           strategy["preprocess"])
    audit = {**audit, "loadings": B.tolist(), "fitRows": len(returns), "meanReversionAssumed": False}
    definitions = []
    for column in range(len(symbols)):
        dollar = projection[:, column]
        if np.abs(dollar).sum() < 1e-10:
            continue
        # Dollar exposures become SHARES at this historical formation cutoff.
        # Never reinterpret these frozen quantities using future prices.
        q = dollar / prices[-1]
        definitions.append(_definition("frozen_basket", symbols, q, "pca_residual",
                                       dates[t-b["formationDays"]], dates[t-1],
                                       {**audit, "projectionColumn": column}))
    return definitions


def _features(prices, q, scale, factor_values, strategy, automatic=None):
    state = prices @ q
    changes = np.diff(state)
    feature = {"volatility20": float(np.std(changes[-20:], ddof=1)/scale)}
    family = strategy["model"]["family"]
    if family in ("mean_reversion", "pair_reversion"):
        feature.update(state_deviation20=float((state[-1]-state[-20:].mean())/scale),
                       state_deviation60=float((state[-1]-state[-60:].mean())/scale),
                       change1=float(changes[-1]/scale))
    elif family == "trend":
        feature.update({f"trend{h}": float((state[-1]-state[-1-h])/scale) for h in (1, 5, 20, 60)})
    else:
        feature.update(change1=float(changes[-1]/scale), change5=float((state[-1]-state[-6])/scale))
    # v1 origin-gross features are immutable. v2 asset predictors use familiar
    # simple-return quantities; signed frozen baskets still need a positive gross
    # denominator because their state may cross zero without any economic jump.
    if (automatic or {}).get("stateFeatures") == "asset_returns_basket_gross/1" and strategy["target"]["kind"] == "asset_price":
        returns = np.diff(state) / state[:-1]
        feature = {"volatility20": float(np.std(returns[-20:], ddof=1))}
        if family in ("mean_reversion", "pair_reversion"):
            feature.update(state_deviation20=float(state[-1]/state[-20:].mean()-1),
                           state_deviation60=float(state[-1]/state[-60:].mean()-1),
                           change1=float(returns[-1]))
        elif family == "trend":
            feature.update({f"trend{h}": float(state[-1]/state[-1-h]-1) for h in (1, 5, 20, 60)})
        else:
            feature.update(change1=float(returns[-1]), change5=float(state[-1]/state[-6]-1))
    dollar = q * prices[-1] / scale
    event = False
    for f in strategy["factors"]:
        if f["role"] == "hedge":
            continue
        values = factor_values[f["id"]]
        finite = np.isfinite(values)
        # Partial legs must not silently change the factor's target definition.
        global_factor = automatic is not None and any(item["feature"] == "factor:" + f["id"] and item["scope"] == "global" for item in automatic["factors"])
        if global_factor and finite.all() and not np.equal(values, values[0]).all():
            fail("CONFLICTING_GLOBAL_FACTOR", "全局因子聚合需要相同的逐腿输入")
        value = float(values[0] if global_factor else dollar @ values) if finite.all() else np.nan
        feature["factor:"+f["id"]] = value
        if f["role"] == "event" and finite.all() and np.any(np.abs(values) > 1e-12):
            event = True
    return feature, event


def build_samples(panel, dates, strategy):
    symbols = sorted(strategy["universe"]["symbols"])
    closes = panel.close.unstack("ts_code").reindex(index=dates, columns=symbols)
    opens = panel.open.unstack("ts_code").reindex(index=dates, columns=symbols)
    automatic = None
    if "automatic" in strategy["preprocess"]:
        from .preprocessing import evaluate_factors
        factor_values, automatic = evaluate_factors(panel, dates, symbols, strategy)
    else:
        factor_values = {f["id"]: (evaluate_expression(f["expression"], panel)*f["direction"]).unstack("ts_code").reindex(index=dates, columns=symbols)
                         for f in strategy["factors"]}
    financial_predictors = [f["id"] for f in strategy["factors"] if f["role"] == "predictor" and
                            any(is_fundamental_field(x)
                                for x in validate_expression(f["expression"])["fields"])]
    warmup = max((validate_expression(f["expression"])["lookback"] for f in strategy["factors"]), default=0)
    formation = strategy["target"].get("basket", {}).get("formationDays", 0)
    start = max(61, warmup+1, formation+1)
    horizon = strategy["target"]["horizonSessions"]
    rows, features, labels, definitions, fits = [], [], [], {}, []
    active, last_fit = [], -100000
    for t in range(start, len(dates), strategy["research"]["observationDays"]):
        if not active or t-last_fit >= strategy["model"]["refitDays"]:
            active = _construct(strategy, closes, factor_values, dates, t)
            last_fit = t
            fits.append({"date": dates[t], "informationCutoff": dates[t-1], "targetIds": [x["id"] for x in active],
                         "status": "valid" if active else "incomplete_formation"})
            definitions.update({x["id"]: x for x in active})
        # Keep an invalid origin when construction fails, not only successful bets.
        targets = active or [{"id": "unavailable", "symbols": symbols, "quantities": [0.]*len(symbols)}]
        for definition in targets:
            members = definition["symbols"]; q = np.asarray(definition["quantities"])
            history = closes.loc[:, members].iloc[t-60:t+1].to_numpy()
            usable = np.isfinite(history).all() and np.any(np.abs(q)>1e-12)
            current = float(history[-1] @ q) if usable else np.nan
            scale = float(np.abs(history[-1]*q).sum()) if usable else np.nan
            usable = usable and scale > 1e-12
            factor_at_t = {key: table.loc[dates[t], members].to_numpy() for key, table in factor_values.items()}
            feat, event = _features(history, q, scale, factor_at_t, strategy, automatic) if usable else ({}, False)
            entry_i, exit_i = t+1, t+1+horizon
            entry_date = dates[entry_i] if entry_i < len(dates) else None
            exit_date = dates[exit_i] if exit_i < len(dates) else None
            entry = opens.loc[entry_date, members].to_numpy() if entry_date else np.array([np.nan])
            exit_ = opens.loc[exit_date, members].to_numpy() if exit_date else np.array([np.nan])
            realized_entry = float(entry @ q) if entry_date and np.isfinite(entry).all() and usable else np.nan
            realized_exit = float(exit_ @ q) if exit_date and np.isfinite(exit_).all() and usable else np.nan
            labels.append([(realized_entry-current)/scale if usable else np.nan,
                           (realized_exit-current)/scale if usable else np.nan])
            features.append(feat)
            reason = None if usable else "incomplete_state_or_formation"
            if strategy["model"]["family"] == "event" and not event:
                reason = reason or "no_observed_event"
            if strategy["model"]["family"] == "fundamental" and not any(np.isfinite(feat.get("factor:"+key, np.nan)) for key in financial_predictors):
                reason = reason or "no_observed_fundamental_predictor"
            rows.append({"date": dates[t], "dateIndex": t, "targetId": definition["id"], "currentState": current,
                         "scale": scale, "entryDate": entry_date, "targetDate": exit_date,
                         "realizedEntry": realized_entry, "realizedFuture": realized_exit,
                         "inputValid": usable and reason is None, "invalidReason": reason,
                         "eventObserved": event})
            if len(rows) > MAX_SAMPLES:
                fail("FORECAST_BUDGET", "预测样本超过资源上限；请减少标的或提高观察间隔")
    if not rows:
        fail("INSUFFICIENT_FORECAST_DATA", "预热后没有预测观察日期")
    return Samples(pd.DataFrame(features).astype(float), pd.DataFrame(labels, columns=["entry", "exit"]),
                   pd.DataFrame(rows), definitions, start, list(dates), fits, automatic)
