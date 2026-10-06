import copy
import json

import numpy as np
import pandas as pd
import pytest

from atlas_quant.engine import ResearchError, TrainWinsorizer, _build_samples, _prepare_data, _simulate, run_research, validate_strategy
from atlas_quant.fixtures import make_demo_data


@pytest.fixture
def strategy():
    return {"schemaVersion": 1, "name": "验算", "universe": {"symbols": ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH"], "start": "20230101", "end": "20251231"}, "factors": [{"id": "momentum_20", "expression": "returns(close,20)", "direction": 1}, {"id": "volatility_20", "expression": "ts_std(returns(close,1),20)", "direction": -1}], "model": {"mode": "auto", "candidates": ["factor_score", "ridge", "elastic_net", "hist_gradient_boosting"], "horizon": 5, "metric": "rank_ic"}}


@pytest.fixture
def data(strategy):
    return make_demo_data(strategy)


@pytest.fixture
def report(strategy, data):
    frame, provenance = data
    return run_research(strategy, frame, provenance)


def test_report_complete_finite_and_folds_purged(report):
    assert report["status"] == "completed"
    json.dumps(report, allow_nan=False)
    assert len(report["selection"]["candidates"]) == 4
    assert report["selection"]["holdoutUsedForSelection"] is False
    splits = report["selection"]["splits"]
    assert splits["finalTraining"]["labelEndMax"] < splits["holdout"]["start"]
    for trial in report["selection"]["trials"]:
        assert trial["status"] == "complete"
        for fold in trial["folds"]:
            assert fold["trainLabelEndMax"] < fold["testStart"] <= fold["testEnd"] < splits["holdout"]["start"]
    assert len(splits["outerFolds"]) == 3
    for fold in splits["outerFolds"]:
        assert fold["trainLabelEndMax"] < fold["testStart"]
    assert report["equity"][0]["date"] == splits["holdout"]["start"]


def test_untouched_holdout_cannot_change_model_selection(strategy, data, report):
    frame, provenance = data
    modified = frame.copy()
    holdout_start = report["selection"]["splits"]["holdout"]["start"]
    mask = modified.trade_date >= holdout_start
    # Alter the future radically, preserving validity and the calendar. All
    # selection scores, fitted preprocessing and winner must be unchanged.
    multipliers = modified.ts_code.map({code: (i + 1) * 2.0 for i, code in enumerate(strategy["universe"]["symbols"])})
    modified.loc[mask, ["open", "high", "low", "close", "raw_close"]] = modified.loc[mask, ["open", "high", "low", "close", "raw_close"]].mul(multipliers[mask], axis=0)
    other = run_research(strategy, modified, provenance)
    assert other["selection"]["winner"] == report["selection"]["winner"]
    assert other["selection"]["params"] == report["selection"]["params"]
    assert other["selection"]["trials"] == report["selection"]["trials"]
    assert other["validation"]["finalFit"] == report["validation"]["finalFit"]
    assert other["metrics"] != report["metrics"]


def test_future_missing_sessions_cannot_move_split_or_change_selection(strategy, data, report):
    frame, provenance = data
    start = report["selection"]["splits"]["holdout"]["start"]
    days = sorted(frame.loc[frame.trade_date >= start, "trade_date"].unique())[:8]
    damaged = frame[~frame.trade_date.isin(days)].copy()
    other = run_research(strategy, damaged, provenance)
    assert other["selection"]["splits"]["holdout"] == report["selection"]["splits"]["holdout"]
    assert other["selection"]["trials"] == report["selection"]["trials"]
    assert other["validation"]["finalFit"] == report["validation"]["finalFit"]
    assert all(t["date"] not in days for t in other["trades"])


def test_cash_cost_position_ledger_reconciles(report):
    cash = report["strategy"]["portfolio"]["initialCapital"]
    positions = {}
    cumulative_cost = 0.0
    grouped = {}
    for trade in report["trades"]:
        assert trade["signalDate"] < trade["date"]
        assert trade["notional"] == pytest.approx(trade["quantity"] * trade["price"])
        assert trade["cost"] == pytest.approx(trade["commission"] + trade["slippage"] + trade["tax"])
        cash += trade["notional"] * (1 if trade["side"] == "SELL" else -1) - trade["cost"]
        assert cash == pytest.approx(trade["cashAfter"], abs=1e-7)
        assert cash >= -1e-7
        symbol = trade["symbol"]
        positions[symbol] = positions.get(symbol, 0) + trade["quantity"] * (1 if trade["side"] == "BUY" else -1)
        assert positions[symbol] >= -1e-7
        cumulative_cost += trade["cost"]
        grouped.setdefault(trade["date"], []).append(trade)
    assert cumulative_cost == pytest.approx(report["metrics"]["totalCosts"])
    ledger = report["execution"]["ledger"]
    for day in ledger:
        mv = sum(p["quantity"] * p["mark"] for p in day["positions"])
        assert mv == pytest.approx(day["positionsValue"])
        assert day["equity"] == pytest.approx(day["cash"] + mv)
        assert day["costs"] == pytest.approx(sum(t["cost"] for t in grouped.get(day["date"], [])))
    assert ledger[-1]["cash"] == pytest.approx(cash, abs=1e-7)
    assert report["metrics"]["totalReturn"] == pytest.approx(ledger[-1]["equity"] / report["strategy"]["portfolio"]["initialCapital"] - 1)


def test_labels_use_next_session_open_and_do_not_bridge_missing_entry(strategy, data):
    frame, provenance = data
    strategy = validate_strategy(strategy)
    panel, dates, _ = _prepare_data(frame, strategy, provenance)
    symbol = strategy["universe"]["symbols"][0]
    panel.loc[(dates[31], symbol), :] = np.nan
    _, labels, ends, _ = _build_samples(panel, dates, strategy["factors"], 5)
    assert pd.isna(labels.loc[(dates[30], symbol)])
    other = strategy["universe"]["symbols"][1]
    expected = panel.loc[(dates[36], other), "open"] / panel.loc[(dates[31], other), "open"] - 1
    assert labels.loc[(dates[30], other)] == pytest.approx(expected)
    assert ends.loc[(dates[30], other)] == dates[36]


def test_missing_session_cannot_fill_and_residual_cash_allowed(strategy, data):
    frame, provenance = data
    s = validate_strategy(strategy)
    s["portfolio"].update(topN=1, maxWeight=0.2, rebalanceDays=1)
    panel, all_dates, _ = _prepare_data(frame, s, provenance)
    dates = all_dates[-5:]
    sym = sorted(s["universe"]["symbols"])[0]
    index = panel.index[panel.index.get_level_values("trade_date").isin(dates)]
    pred = pd.Series([10.0 if symbol == sym else 0.0 for _, symbol in index], index=index)
    panel.loc[(dates[1], sym), :] = np.nan
    metrics, equity, trades, ledger, skipped = _simulate(panel, dates, pred, s)
    assert not any(t["symbol"] == sym and t["date"] == dates[1] for t in trades)
    assert any(x.get("symbol") == sym and x["reason"] == "missing_session_or_zero_volume" for x in skipped)
    assert ledger[1]["cash"] == s["portfolio"]["initialCapital"]
    assert ledger[2]["cash"] > s["portfolio"]["initialCapital"] * 0.79
    assert metrics["totalCosts"] >= 0


def test_equal_targets_with_costs_receive_pro_rata_cash(strategy, data):
    frame, provenance = data
    s = validate_strategy(strategy)
    panel, dates, _ = _prepare_data(frame, s, provenance)
    dates = dates[-3:]
    index = panel.index[panel.index.get_level_values("trade_date").isin(dates)]
    preds = pd.Series([float(i % 5) for i in range(len(index))], index=index)
    _, _, trades, ledger, _ = _simulate(panel, dates, preds, s)
    first_buys = [t for t in trades if t["date"] == dates[1] and t["side"] == "BUY"]
    assert len(first_buys) == 3
    notionals = [t["notional"] for t in first_buys]
    assert max(notionals) == pytest.approx(min(notionals))
    assert ledger[1]["cash"] == pytest.approx(0, abs=1e-7)


def test_winsorizer_does_not_refit_on_future():
    train = np.arange(100, dtype=float).reshape(-1, 1)
    transform = TrainWinsorizer().fit(train)
    before = transform.upper_.copy()
    out = transform.transform(np.array([[1e12], [-1e12]]))
    np.testing.assert_array_equal(before, transform.upper_)
    assert out[0, 0] == pytest.approx(98.01)
    assert out[1, 0] == pytest.approx(0.99)


@pytest.mark.parametrize("path,value", [("maxWeight", -1), ("maxWeight", 1.1), ("topN", 6), ("topN", 2.5), ("initialCapital", float("nan")), ("rebalanceDays", 0)])
def test_invalid_portfolio_is_rejected(strategy, path, value):
    strategy["portfolio"] = {path: value}
    with pytest.raises(ResearchError):
        validate_strategy(strategy)


def test_duplicates_short_data_and_invalid_prices_fail(strategy, data):
    frame, provenance = data
    with pytest.raises(ResearchError, match="重复"):
        run_research(strategy, pd.concat([frame, frame.iloc[:1]]), provenance)
    short = frame[frame.trade_date < "20230601"]
    with pytest.raises(ResearchError, match="180"):
        run_research(strategy, short, {})
    frame = frame.copy()
    frame.loc[0, "open"] = -1
    with pytest.raises(ResearchError, match="有限正数"):
        run_research(strategy, frame, provenance)


def test_manual_model_is_respected(strategy, data):
    strategy["model"].update(mode="manual", candidates=["ridge"])
    frame, provenance = data
    report = run_research(strategy, frame, provenance)
    assert report["selection"]["winner"] == "ridge"
    assert len(report["selection"]["candidates"]) == 1
    assert report["selection"]["trialCount"] == 2
