import copy
import numpy as np
import pandas as pd
import pytest
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.execution import execute


def inputs(symbols=("000001.SZ", "600000.SH"), prices=None):
    dates = pd.bdate_range("2025-01-06", periods=6).strftime("%Y%m%d").tolist()
    prices = np.asarray(prices if prices is not None else [[100., 100.]]*6)
    rows = [{"trade_date": date, "ts_code": symbol, "open": prices[t, k], "close": prices[t, k], "vol": 1000.}
            for t, date in enumerate(dates) for k, symbol in enumerate(symbols)]
    panel = pd.DataFrame(rows).set_index(["trade_date", "ts_code"])
    strategy = validate({"schemaVersion": 2, "name": "Execution", "model": {"family": "mean_reversion"}, "universe": {"symbols": list(symbols), "start": dates[0], "end": dates[-1]},
                         "research": {"mode": "statistical_quant"}, "target": {"kind": "asset_price"},
                         "portfolio": {"initialCapital": 10000, "maxWeight": 1., "rebalanceThresholdBps": 0},
                         "execution": {"maxPositions": 1, "minEdgeBps": 0},
                         "costs": {k: 0. for k in ("commissionBps", "slippageBps", "sellTaxBps", "transferBps", "minCommission", "borrowAnnualBps")}})
    return panel, dates, strategy


def artifact(dates, quantities=(1.,), expected_entry=100., expected_exit=110., current=100., symbols=("000001.SZ",), maturity=2):
    return {"artifactId": "test_artifact", "targetDefinitions": [{"id": "basket", "symbols": list(symbols), "quantities": list(quantities)}],
            "rows": [{"forecastId": "forecast_A", "targetId": "basket", "date": dates[0], "entryDate": dates[1], "targetDate": dates[maturity],
                      "horizonSessions": maturity-1, "currentState": current, "scale": 100*sum(abs(q) for q in quantities),
                      "expectedEntry": expected_entry, "expectedFuture": expected_exit,
                      "expectedGrossPnl": expected_exit-expected_entry, "expectedGrossBps": (expected_exit-expected_entry)/(100*sum(abs(q) for q in quantities))*10000,
                      "status": "valid", "invalidReason": None, "realizedFuture": -999999.}]}


def audit(metrics, equity, trades, execution, initial=10000.):
    cash = initial; positions = {}; total = 0.
    for day in execution["ledger"]:
        for trade in [x for x in trades if x["date"] == day["date"]]:
            cash -= trade["signedQuantity"]*trade["price"]+trade["cost"]
            total += trade["cost"]
            positions[trade["symbol"]] = positions.get(trade["symbol"], 0.)+trade["signedQuantity"]
            assert cash == pytest.approx(trade["cashAfter"])
            assert positions[trade["symbol"]] == pytest.approx(trade["positionAfter"])
        cash -= day["borrowCost"]; total += day["borrowCost"]
        assert cash == pytest.approx(day["cash"])
        assert cash+sum(p["quantity"]*p["mark"] for p in day["positions"]) == pytest.approx(day["equity"])
    assert total == pytest.approx(metrics["totalCosts"])


def test_overnight_gap_is_not_a_remaining_tradeable_edge():
    panel, dates, s = inputs(prices=[[100, 100]]+[[110, 100]]*5)
    a = artifact(dates, expected_entry=110, expected_exit=110)
    metrics, equity, trades, execution = execute(panel, dates, s, a)
    assert trades == [] and metrics["totalReturn"] == 0
    assert execution["decisions"][0]["reason"] == "insufficient_predicted_gross_edge"


def test_exact_fixed_quantity_self_financing_and_forecast_reference():
    panel, dates, s = inputs(prices=[[100, 100], [100, 100]]+[[105, 95]]*4)
    a = artifact(dates, quantities=(1., -1.), symbols=("000001.SZ", "600000.SH"), current=0, expected_entry=0, expected_exit=10)
    result = execute(panel, dates, s, a)
    metrics, equity, trades, execution = result
    assert len(trades) == 4 and metrics["totalReturn"] == pytest.approx(.05)
    assert all(t["forecastId"] == "forecast_A" for t in trades)
    assert sorted(t["signedQuantity"] for t in trades[:2]) == [-50., 50.]
    assert equity[-1]["cash"] == pytest.approx(10500.)
    audit(*result)


def test_unavailable_entry_is_canceled_not_retried_or_shifted():
    panel, dates, s = inputs()
    panel.loc[(dates[1], "600000.SH"), ["open", "close", "vol"]] = [np.nan, np.nan, 0]
    a = artifact(dates, quantities=(1., -1.), symbols=("000001.SZ", "600000.SH"), current=0, expected_entry=0, expected_exit=10)
    metrics, equity, trades, execution = execute(panel, dates, s, a)
    assert trades == []
    assert execution["decisions"][0]["reason"] == "entry_unavailable_forecast_not_delayed"
    assert len(equity) == len(dates)


def test_missing_expiry_waits_all_legs_and_keeps_daily_cash_ledger():
    panel, dates, s = inputs(prices=[[100, 100], [100, 100], [105, 95]]+[[110, 90]]*3)
    panel.loc[(dates[2], "600000.SH"), ["open", "close", "vol"]] = [np.nan, np.nan, 0]
    a = artifact(dates, quantities=(1., -1.), symbols=("000001.SZ", "600000.SH"), current=0, expected_entry=0, expected_exit=10)
    result = execute(panel, dates, s, a)
    metrics, equity, trades, execution = result
    assert len(trades) == 4 and not any(t["date"] == dates[2] for t in trades)
    assert [t["exitReason"] for t in trades[-2:]] == ["delayed_target_expiry"]*2
    assert execution["ledger"][2]["staleMarks"] == ["600000.SH"]
    assert metrics["totalReturn"] == pytest.approx(.1)
    audit(*result)


def test_t_plus_one_blocks_opposing_entry_that_would_sell_today_acquired_long():
    panel, dates, s = inputs()
    s["execution"]["maxPositions"] = 2
    a = artifact(dates, maturity=3)
    other = copy.deepcopy(a["rows"][0]); other.update(forecastId="forecast_B", expectedGrossPnl=-10, expectedGrossBps=-1000)
    a["rows"].append(other)
    result = execute(panel, dates, s, a)
    assert len([t for t in result[2] if t["date"] == dates[1]]) == 1
    assert any(d["reason"] == "t_plus_one_locked" for d in result[3]["decisions"])
    audit(*result)


def test_costs_borrow_cash_positions_independently_reconcile():
    panel, dates, s = inputs(prices=[[100, 100], [100, 100]]+[[108, 92]]*4)
    s["costs"].update(commissionBps=2.5, minCommission=5., slippageBps=3., sellTaxBps=5., transferBps=.1, borrowAnnualBps=300.)
    a = artifact(dates, quantities=(1., -1.), symbols=("000001.SZ", "600000.SH"), current=0, expected_entry=0, expected_exit=16)
    result = execute(panel, dates, s, a)
    audit(*result)
    # Opening fees are 2*5 minimum commission + (2*3+5+2*.1) bps
    # of one leg. Gross2N may not exceed post-fee NAV10000-10-.00112N.
    expected_leg_notional = 9990/2.00112
    assert result[0]["costBreakdown"]["borrow"] == pytest.approx(expected_leg_notional*.03/252)
    assert result[0]["totalCosts"] > 20.


def test_risk_adapter_preserves_leg_ratio_and_net_frozen_quantities():
    panel, dates, s = inputs()
    s["portfolio"]["maxWeight"] = .1
    a = artifact(dates, quantities=(1., -2.), symbols=("000001.SZ", "600000.SH"), current=-100, expected_entry=-100, expected_exit=-70)
    result = execute(panel, dates, s, a)
    opening = [x for x in result[2] if x["date"] == dates[1]]
    by_symbol = {x["symbol"]: x["signedQuantity"] for x in opening}
    assert by_symbol["600000.SH"] == pytest.approx(-2*by_symbol["000001.SZ"])
    assert max(x["notional"] for x in opening) == pytest.approx(1000, abs=1e-6)


def test_same_day_prediction_target_cannot_create_t_plus_zero_round_trip():
    panel, dates, s = inputs()
    a = artifact(dates, maturity=1)
    assert execute(panel, dates, s, a)[2] == []
