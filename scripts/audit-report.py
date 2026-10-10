#!/usr/bin/env python3
"""Independently reconcile an exported v0.4 report using only Python's stdlib.

No engine imports, model fitting, provider access or future-data reconstruction.
This checks the report's identities and accounting, not its predictive validity.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def audit(envelope, source=None):
    result = envelope.get("result", envelope)
    assert result["schemaVersion"] == 2, "Requires a statistical_quant report"
    artifact = result["forecasts"]
    payload = {k: v for k, v in artifact.items() if k != "artifactId"}
    digest = hashlib.sha256(json.dumps(canonical(payload), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert digest == artifact["artifactId"], "Forecast artifact content/order hash mismatch"
    if source is not None:
        original = source.get("result", source)["forecasts"]
        assert artifact == original, "Execution changed the frozen forecast artifact"
        assert result["research"]["predictionRefitPerformed"] is False, "Replay refitted predictions"
    rows = artifact["rows"]
    assert artifact["totalRows"] == len(rows) and artifact["truncated"] is False
    forecasts = {row["forecastId"]: row for row in rows}
    assert len(forecasts) == len(rows), "Duplicate forecast identity"
    checks = 0
    max_error = 0.0

    def near(left, right, label):
        nonlocal checks, max_error
        assert isinstance(left, (int, float)) and isinstance(right, (int, float)), label
        assert math.isfinite(left) and math.isfinite(right), label
        difference = abs(left-right)
        assert difference <= 1e-7 + 2e-11*max(abs(left), abs(right)), f"{label}: {left} != {right}"
        max_error = max(max_error, difference)
        checks += 1

    for row in rows:
        if artifact.get('studyProtocol') == 'asset-return-study/1':
            from return_study_audit import check_return_row
            def require(condition, message):
                assert condition, message
            check_return_row(row, result['strategy'], near, require)
            continue
        if row["status"] != "valid":
            continue
        p, v = row["currentState"], row["expectedFuture"]
        near(row["edgeGap"], p-v, "e = P - V")
        near(row["expectedChange"], v-p, "predicted change = -e")
        near(row["expectedGrossPnl"], v-row["expectedEntry"], "tradable remaining change")
        near(row["expectedGrossBps"], row["expectedGrossPnl"]/row["scale"]*10000, "gross basis points")
        assert row["date"] < row["entryDate"] < row["targetDate"], "Prediction/entry/target chronology"
        if row["realizedFuture"] is not None:
            near(row["forecastError"], row["realizedFuture"]-v, "forecast error")
            near(row["realizedFuture"]-p, -row["edgeGap"]+row["forecastError"], "realized decomposition")
    execution = result["execution"]
    assert execution["forecastArtifactId"] == artifact["artifactId"]
    if not execution["enabled"]:
        assert result["metrics"] is None and not result["trades"] and not result["equity"]
        return {"status": "passed", "forecastRows": len(rows), "checks": checks,
                "trades": 0, "ledgerDates": 0, "maxAbsoluteError": max_error}

    cash = initial = result["strategy"]["portfolio"]["initialCapital"]
    costs = result["strategy"]["costs"]
    trades_by_date = defaultdict(list)
    for trade in result["trades"]:
        assert trade["forecastId"] in forecasts, "Trade without forecast"
        forecast = forecasts[trade["forecastId"]]
        assert trade["targetId"] == forecast["targetId"] and trade["signalDate"] == forecast["date"]
        assert trade["date"] > forecast["date"], "Trade before information cutoff"
        if trade["exitReason"] is None:
            assert trade["date"] == forecast["entryDate"], "Entry moved from predeclared date"
        trades_by_date[trade["date"]].append(trade)
    positions = defaultdict(float)
    lots = defaultdict(list)
    totals = dict.fromkeys(("commission", "slippage", "tax", "transfer", "borrow"), 0.0)
    ledger = execution["ledger"]
    dates = [point["date"] for point in ledger]
    assert dates == sorted(set(dates)), "Duplicate or unsorted ledger dates"
    assert set(trades_by_date) <= set(dates), "Trade outside ledger"
    equity = result["equity"]
    assert [point["date"] for point in equity] == dates, "Equity chart and ledger dates differ"
    calendar = result["provenance"].get("tradingDates")
    calendar_checked = isinstance(calendar, list) and bool(calendar)
    if calendar_checked:
        assert calendar == sorted(set(calendar)), "Source calendar is not unique and sorted"
        first_origin = min(row["date"] for row in rows)
        expected_dates = [d for d in calendar if first_origin <= d <= result["strategy"]["universe"]["end"]]
        if result["metrics"].get("bankrupt"):
            assert ledger[-1]["equity"] <= 0, "False bankruptcy termination"
            expected_dates = expected_dates[:len(dates)]
        assert dates == expected_dates, "Ledger does not cover every declared trading session"
    for displayed, recorded in zip(equity, ledger):
        for key in ("equity", "cash", "positionsValue", "drawdown", "benchmark", "dailyCosts", "borrowCost", "grossExposure", "netExposure"):
            if recorded[key] is None:
                assert displayed[key] is None, f"Chart {key} invents an unavailable value"
            else:
                near(displayed[key], recorded[key], f"chart/ledger {key}")
    peak = initial
    max_drawdown = 0.0
    for point in ledger:
        date = point["date"]
        day_fees = 0.0
        for trade in trades_by_date[date]:
            symbol, q, price = trade["symbol"], trade["signedQuantity"], trade["price"]
            assert (q > 0 and trade["side"] == "BUY") or (q < 0 and trade["side"] == "SELL")
            near(trade["quantity"], abs(q), "absolute quantity")
            notional = abs(q)*price
            near(trade["notional"], notional, "trade notional")
            expected_fees = {
                "commission": max(costs["minCommission"], notional*costs["commissionBps"]/10000),
                "slippage": notional*costs["slippageBps"]/10000,
                "tax": notional*costs["sellTaxBps"]/10000 if q < 0 else 0,
                "transfer": notional*costs["transferBps"]/10000,
            }
            for key, amount in expected_fees.items():
                near(trade[key], amount, f"trade {key}")
                totals[key] += amount
            fee = sum(expected_fees.values())
            near(trade["cost"], fee, "trade fee total")
            before = positions[symbol]
            reduction = min(max(before, 0), max(-q, 0))
            sellable = sum(quantity for entry, quantity in lots[symbol] if entry < date)
            assert reduction <= sellable+1e-7, "A-share long sold before T+1"
            for lot in lots[symbol]:
                if lot[0] < date and reduction > 0:
                    used = min(lot[1], reduction)
                    lot[1] -= used
                    reduction -= used
            added = max(before+q, 0)-max(before, 0)
            if added > 0:
                lots[symbol].append([date, added])
            positions[symbol] += q
            cash -= q*price+fee
            day_fees += fee
            near(trade["cashAfter"], cash, "cash after trade")
            near(trade["positionAfter"], positions[symbol], "position after trade")
        daily_positions = {p["symbol"]: p for p in point["positions"]}
        for symbol, q in positions.items():
            near(daily_positions.get(symbol, {}).get("quantity", 0), q, "end-of-day quantity")
        value = gross = short_value = 0.0
        for symbol, holding in daily_positions.items():
            near(holding["quantity"], positions[symbol], "ledger has no unexplained holding")
            marked = holding["quantity"]*holding["mark"]
            near(holding["value"], marked, "holding valuation")
            near(holding["sellableQuantity"], sum(q for d, q in lots[symbol] if d < date), "settled long inventory")
            value += marked
            gross += abs(marked)
            short_value += max(-marked, 0)
        borrowing = short_value*costs["borrowAnnualBps"]/10000/252
        near(point["borrowCost"], borrowing, "declared borrow accrual")
        cash -= borrowing
        totals["borrow"] += borrowing
        near(point["dailyCosts"], day_fees+borrowing, "daily fees")
        near(point["cash"], cash, "daily cash")
        near(point["positionsValue"], value, "signed holdings value")
        near(point["equity"], cash+value, "NAV from cash and holdings")
        if point["equity"] > 0:
            near(point["netExposure"], value/point["equity"], "net exposure")
            near(point["grossExposure"], gross/point["equity"], "gross exposure")
        peak = max(peak, point["equity"])
        near(point["drawdown"], point["equity"]/peak-1, "drawdown")
        max_drawdown = min(max_drawdown, point["drawdown"])
    metrics = result["metrics"]
    for key, amount in totals.items():
        near(metrics["costBreakdown"][key], amount, f"total {key}")
    near(metrics["totalCosts"], sum(totals.values()), "all fees")
    near(metrics["totalReturn"], ledger[-1]["equity"]/initial-1, "net return")
    near(metrics["maxDrawdown"], max_drawdown, "maximum drawdown")
    assert metrics["tradeCount"] == len(result["trades"])
    return {"status": "passed", "forecastRows": len(rows), "checks": checks,
            "trades": len(result["trades"]), "ledgerDates": len(ledger),
            "declaredCalendarCoverageChecked": calendar_checked,
            "maxAbsoluteError": max_error, "totalCosts": metrics["totalCosts"],
            "netReturn": metrics["totalReturn"], "forecastArtifactId": artifact["artifactId"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--source-report", type=Path, help="Assert replay uses the identical original artifact")
    args = parser.parse_args()
    try:
        result = audit(json.loads(args.report.read_text()),
                       json.loads(args.source_report.read_text()) if args.source_report else None)
    except (AssertionError, KeyError, TypeError, ValueError, OSError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
