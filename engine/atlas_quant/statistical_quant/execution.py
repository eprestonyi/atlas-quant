"""Replay immutable predictions into a signed, self-financing daily cash ledger."""
from __future__ import annotations
import numpy as np
from .risk import bounded_units, estimated_round_trip_cost, RiskState, breaches, measure


def execute(panel, dates, strategy, artifact):
    from ..engine import _trade_costs, _sellable_quantity, _consume_sellable
    ex, portfolio, costs = (strategy[k] for k in ("execution", "portfolio", "costs"))
    if not ex["enabled"]:
        return None, [], [], {"enabled": False, "forecastArtifactId": artifact["artifactId"], "ledger": [], "decisions": []}
    symbols = sorted(strategy["universe"]["symbols"]); n = len(symbols)
    index = {s: i for i, s in enumerate(symbols)}
    definitions = {x["id"]: x for x in artifact["targetDefinitions"]}
    forecasts = artifact["rows"]
    first = min((x["date"] for x in forecasts), default=dates[-1])
    run_dates = [d for d in dates if d >= first]
    scheduled = {}
    for row in forecasts:
        if row["entryDate"]:
            scheduled.setdefault(row["entryDate"], []).append(row)
    capital = cash = float(portfolio["initialCapital"])
    risk_state = RiskState(panel, dates, symbols, strategy)
    positions, marks = np.zeros(n), np.zeros(n)
    lots = {s: [] for s in symbols}
    tickets, trades, equity, ledger, decisions = [], [], [], [], []
    cost_totals = {key: 0. for key in ("commission", "slippage", "tax", "transfer", "borrow")}
    peak, turnover, bankrupt = capital, 0., False

    def can_fill(delta, prices, tradable, date):
        needed = np.abs(delta)>1e-10
        if (needed & ~tradable).any():
            return "basket_leg_unavailable"
        # This is an ex-post daily matching filter, never a forecast feature.
        # Without historical exchange/ST limit metadata we conservatively refuse
        # adverse-direction fills on a moved one-price bar, not assert limit rules.
        if "high" in day and "low" in day:
            high, low = day.high.to_numpy(), day.low.to_numpy()
            one_price = np.isfinite(high) & np.isfinite(low) & (np.abs(high-low) <= np.maximum(abs(prices), 1)*1e-10)
            moved_up = (marks>0) & (prices>marks*(1+1e-10))
            moved_down = (marks>0) & (prices<marks*(1-1e-10))
            if (needed & one_price & (((delta>0)&moved_up) | ((delta<0)&moved_down))).any():
                return "one_price_bar_adverse_fill_unavailable"
        for k in np.flatnonzero(needed):
            reduced_long = min(max(positions[k], 0.), max(-delta[k], 0.))
            if reduced_long > _sellable_quantity(lots[symbols[k]], date)+1e-8:
                return "t_plus_one_locked"
        debit = float(delta @ prices)+sum(_trade_costs(abs(delta[k])*prices[k], "BUY" if delta[k]>0 else "SELL", costs)["cost"] for k in np.flatnonzero(needed))
        if cash-debit < -1e-7:
            return "insufficient_cash_no_margin_loan"
        return None

    def fill(delta, prices, date, forecast, reason):
        nonlocal cash, turnover
        day_fees = 0.
        # Complete basket feasibility was checked before any leg is mutated.
        order = list(np.flatnonzero(delta < -1e-10))+list(np.flatnonzero(delta > 1e-10))
        for k in order:
            q = float(delta[k]); before = float(positions[k]); symbol = symbols[k]
            side = "BUY" if q>0 else "SELL"; notional = abs(q)*float(prices[k])
            fee = _trade_costs(notional, side, costs)
            if q<0 and before>0:
                _consume_sellable(lots[symbol], min(before, -q), date)
            new_long = max(before+q, 0.)-max(before, 0.)
            if new_long>1e-10:
                lots[symbol].append({"date": date, "quantity": new_long})
            cash -= q*prices[k]+fee["cost"]; positions[k] += q
            for key in ("commission", "slippage", "tax", "transfer"):
                cost_totals[key] += fee[key]
            day_fees += fee["cost"]; turnover += notional
            trades.append({"date": date, "forecastId": forecast["forecastId"], "targetId": forecast["targetId"],
                           "signalDate": forecast["date"], "targetDate": forecast["targetDate"], "exitReason": reason,
                           "symbol": symbol, "side": side, "signedQuantity": q, "quantity": abs(q), "price": float(prices[k]),
                           "notional": notional, **fee, "cashAfter": cash, "positionAfter": float(positions[k]),
                           "shortInventory": "theoretical_unverified"})
        return day_fees

    for calendar_i, date in enumerate(run_dates):
        day = panel.xs(date, level="trade_date").reindex(symbols)
        op, close, volume = (day[x].to_numpy() for x in ("open", "close", "vol"))
        tradable = np.isfinite(op) & (op>0) & (volume>0)
        prices = np.where(np.isfinite(op), op, marks)
        risk_snapshot = risk_state.before(date)
        previous_nav = cash+float(positions@marks)
        risk_reasons = breaches(positions*marks/previous_nav, risk_snapshot, portfolio) if previous_nav>0 else ["nonpositive_equity"]
        fees = 0.
        # Expiry of a held frozen basket is an independent risk exit; no new
        # prediction is required, and the target date never slides after entry.
        remaining = []
        for ticket in tickets:
            if date < ticket["forecast"]["targetDate"] and not risk_reasons:
                remaining.append(ticket); continue
            delta = -ticket["quantities"]
            reason = can_fill(delta, prices, tradable, date)
            if reason:
                decisions.append({"date": date, "forecastId": ticket["forecast"]["forecastId"], "action": "exit_pending", "reason": reason, "riskReasons": risk_reasons})
                remaining.append(ticket)
            else:
                exit_reason = "risk_limit_exit" if risk_reasons else "target_expiry" if date == ticket["forecast"]["targetDate"] else "delayed_target_expiry"
                fees += fill(delta, prices, date, ticket["forecast"], exit_reason)
        tickets = remaining
        candidates = sorted(scheduled.get(date, []), key=lambda x: (-abs(x["expectedGrossBps"] or 0), x["forecastId"]))
        attempted_entries = 0
        available_slots = max(0, ex["maxPositions"]-len(tickets))
        for row in candidates:
            reason = None
            if row["status"] != "valid" or row["date"] >= date or not row["targetDate"] or row["targetDate"] <= date:
                reason = row.get("invalidReason") or "invalid_or_expired_forecast"
            elif calendar_i % portfolio["rebalanceDays"] != 1 % portfolio["rebalanceDays"]:
                reason = "entry_clock_not_due"
            elif len(tickets) >= ex["maxPositions"]:
                reason = "position_count_limit"
            elif abs(row["expectedGrossBps"]) <= ex["minEdgeBps"]:
                reason = "insufficient_predicted_gross_edge"
            if reason:
                decisions.append({"date": date, "forecastId": row["forecastId"], "action": "no_entry", "reason": reason}); continue
            # Select an order budget from immutable forecasts before consulting
            # this session's ex-post volume/flat-bar fill availability. A rejected
            # fill must not fund a hindsight replacement from lower-ranked names.
            if attempted_entries >= available_slots:
                decisions.append({"date": date, "forecastId": row["forecastId"], "action": "no_entry", "reason": "forecast_order_budget"}); continue
            attempted_entries += 1
            definition = definitions[row["targetId"]]
            q = np.zeros(n)
            direction = 1. if row["expectedGrossPnl"]>0 else -1.
            for symbol, quantity in zip(definition["symbols"], definition["quantities"]):
                q[index[symbol]] = direction*quantity
            if ex["side"] == "long_only" and (q < -1e-12).any():
                reason = "short_leg_forbidden"
            elif ((np.abs(q)>1e-12) & ~tradable).any():
                reason = "entry_unavailable_forecast_not_delayed"
            elif (portfolio["sizingMode"] == "volatility_target" and any(abs(q[k])>1e-12 for k in risk_snapshot.get("invalidVolatilityIndices", []))) or risk_snapshot.get("invalidFactors"):
                reason = "risk_inputs_unavailable"
            nav = cash+float(positions@prices)
            gross_unit = float(np.abs(q*prices).sum())
            if not reason and gross_unit>1e-12:
                desired = nav*portfolio["grossExposure"]/ex["maxPositions"]/gross_unit
                units = bounded_units(positions, prices, q, desired, nav, portfolio["grossExposure"], portfolio["maxWeight"], snapshot=risk_snapshot, config=portfolio, costs=costs)
                delta = q*units
                if units <= 1e-10:
                    reason = "exposure_limit"
                elif float(np.abs(delta*prices).sum())/nav*10000 <= portfolio["rebalanceThresholdBps"]:
                    reason = "basket_rebalance_band"
                estimate = estimated_round_trip_cost(delta, prices, costs, row["horizonSessions"])
                expected = units*abs(row["expectedGrossPnl"])
                known_scale = units*row["scale"]
                if not reason and expected-estimate["total"] <= known_scale*ex["minEdgeBps"]/10000:
                    reason = "insufficient_predicted_edge_after_estimated_cost"
                if not reason:
                    reason = can_fill(delta, prices, tradable, date)
                if not reason:
                    fees += fill(delta, prices, date, row, None)
                    tickets.append({"forecast": row, "quantities": delta, "entryDate": date})
                    decisions.append({"date": date, "forecastId": row["forecastId"], "action": "entered", "reason": "forecast_edge_after_cost",
                                      "expectedGrossPnl": expected, "estimatedCosts": estimate, "frozenUnits": units,
                                      "riskInformationCutoff": risk_snapshot["informationCutoff"], "postTradeRisk": measure(positions*prices/(cash+float(positions@prices)), risk_snapshot, portfolio)})
            elif not reason:
                reason = "invalid_entry_scale"
            if reason:
                decisions.append({"date": date, "forecastId": row["forecastId"], "action": "no_entry", "reason": reason})
        marks = np.where(np.isfinite(close), close, marks)
        borrowing = float(np.maximum(-positions, 0)@marks)*costs["borrowAnnualBps"]/10000/252
        cash -= borrowing; cost_totals["borrow"] += borrowing; fees += borrowing
        nav = cash+float(positions@marks); peak = max(peak, nav)
        gross = float(np.abs(positions*marks).sum()); net = float(positions@marks)
        point = {"date": date, "equity": nav, "cash": cash, "positionsValue": net, "drawdown": nav/peak-1,
                 "benchmark": capital, "dailyCosts": fees, "borrowCost": borrowing,
                 "grossExposure": gross/nav if nav>0 else None, "netExposure": net/nav if nav>0 else None}
        equity.append(point)
        ledger.append({**point, "openForecastIds": [x["forecast"]["forecastId"] for x in tickets],
                       "riskInformationCutoff": risk_snapshot["informationCutoff"], "risk": measure(positions*marks/nav if nav>0 else np.zeros(n), risk_snapshot, portfolio),
                       "riskBreaches": breaches(positions*marks/nav, risk_snapshot, portfolio) if nav>0 else ["nonpositive_equity"],
                       "unavailableRiskInputs": {"volatilitySymbols": risk_snapshot.get("invalidVolatilitySymbols", []), "factors": risk_snapshot.get("invalidFactors", [])},
                       "staleMarks": [symbols[k] for k in range(n) if abs(positions[k])>1e-10 and not np.isfinite(close[k])],
                       "positions": [{"symbol": symbols[k], "quantity": float(positions[k]), "mark": float(marks[k]),
                                      "value": float(positions[k]*marks[k]), "sellableQuantity": _sellable_quantity(lots[symbols[k]], date)}
                                     for k in range(n) if abs(positions[k])>1e-10]})
        if nav <= 0:
            bankrupt = True; break
    values = np.array([capital]+[x["equity"] for x in equity]); returns = values[1:]/values[:-1]-1
    std = float(np.std(returns, ddof=1)) if len(returns)>1 else 0.
    metrics = {"totalReturn": values[-1]/capital-1, "annualReturn": (values[-1]/capital)**(252/max(1, len(returns)))-1 if values[-1]>0 else -1.,
               "volatility": std*np.sqrt(252), "sharpe": float(np.mean(returns)/std*np.sqrt(252)) if std>1e-12 else None,
               "maxDrawdown": min((x["drawdown"] for x in equity), default=0), "totalCosts": sum(cost_totals.values()),
               "costBreakdown": cost_totals, "tradeCount": len(trades), "turnover": turnover/float(np.mean(values)),
               "bankrupt": bankrupt, "benchmarkReturn": 0.}
    execution = {"enabled": True, "forecastArtifactId": artifact["artifactId"], "ledger": ledger, "decisions": decisions,
                 "unit": "fractional_adjusted_research_units", "fillRule": "declared_next_open_atomic_frozen_basket",
                 "entryDelayPolicy": "cancel_not_shift_target", "settlement": "A_SHARE_LONG_T_PLUS_ONE",
                 "terminalLiquidation": False, "shortInventoryVerified": False, "marginCashLoansModeled": False,
                 "shortSaleProceedsTreatment": "unsegregated_research_cash",
                 "shortCollateralAndMarginCallsModeled": False, "cashInterestModeled": False,
                 "borrowAccrualRule": "net_short_close_notional * annual_bps / 10000 / 252",
                 "forecastPriceErrorIsCashPnl": False,
                 "dailyMatchingInformation": "volume_and_one_price_bar_used_only_for_ex_post_fill_availability_no_same_session_replacement",
                 "limitPriceRule": "conservative_adverse_one_price_bar_filter_not_full_historical_exchange_limit_model",
                 "riskAdapter": {"grossExposure": portfolio["grossExposure"], "maxWeight": portfolio["maxWeight"],
                                 "netExposureLimit": portfolio["netExposureLimit"], "sizingMode": portfolio["sizingMode"],
                                 "targetAnnualVolatility": portfolio["targetAnnualVolatility"], "volatilityLookback": portfolio["volatilityLookback"],
                                 "factorExposureLimits": portfolio["factorExposureLimits"], "informationPolicy": "prior_close_only",
                                 "covarianceMethod": "252_simple_return_covariance_10percent_diagonal_shrinkage_psd",
                                 "factorScope": "prior_close_cross_section_zscore_within_selected_universe_not_beta_neutrality",
                                 "missingInputs": "block_new_risk_and_request_existing_risk_exit"}}
    return metrics, equity, trades, execution
