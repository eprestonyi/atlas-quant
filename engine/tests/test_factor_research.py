"""Behavioral regressions for causal sampling, settlement and factor statistics."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from atlas_quant.engine import (
    ResearchError, _consume_sellable, _decorrelate, _fit_predict,
    _sellable_quantity, _simulate, _trade_costs, run_research, validate_strategy,
)
from atlas_quant.fixtures import make_demo_data


@pytest.fixture
def strategy():
    return {"schemaVersion": 1, "name": "factor semantics", "universe": {
        "symbols": ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH"],
        "start": "20230101", "end": "20251231"},
        "factors": [{"id": "momentum", "expression": "returns(close,20)", "direction": 1},
                    {"id": "duplicate", "expression": "2*returns(close,20)", "direction": 1}],
        "research": {"mode": "factor", "observationDays": 1},
        "preprocess": {"decorrelation": "drop_correlated", "correlationThreshold": .9},
        "model": {"mode": "manual", "candidates": ["factor_score"], "horizon": 5},
        "portfolio": {"topN": 1, "maxWeight": 1, "rebalanceDays": 5},
        "costs": {"commissionBps": 2.5, "slippageBps": 3, "sellTaxBps": 5,
                  "transferBps": .1, "minCommission": 5}}


def market(strategy, sessions=8):
    s = validate_strategy(strategy)
    dates = pd.bdate_range("20250101", periods=sessions).strftime("%Y%m%d").tolist()
    idx = pd.MultiIndex.from_product([dates, sorted(s["universe"]["symbols"])], names=["trade_date", "ts_code"])
    panel = pd.DataFrame(10., index=idx, columns=["open", "high", "low", "close", "vol"])
    scores = pd.Series([5 - j % 5 for j in range(len(idx))], index=idx, dtype=float)
    return s, panel, dates, scores


def test_legacy_missing_research_preserves_old_fee_defaults(strategy):
    strategy.pop("research"); strategy.pop("costs")
    s = validate_strategy(strategy)
    assert s["research"]["mode"] == "legacy_long_only"
    assert s["costs"] == {"commissionBps": 3, "slippageBps": 10, "sellTaxBps": 5,
                           "transferBps": 0, "minCommission": 0, "taxMode": "fixed"}


def test_sampling_label_horizon_and_rebalance_are_independent(strategy):
    strategy["portfolio"]["maxWeight"] = .4
    data, provenance = make_demo_data(strategy)
    daily = run_research(strategy, data, provenance)
    strategy["research"]["observationDays"] = 5
    sparse = run_research(strategy, data, provenance)
    assert sparse["selection"]["splits"]["holdout"] == daily["selection"]["splits"]["holdout"]
    assert sparse["research"]["labelHorizonSessions"] == sparse["research"]["rebalanceDays"] == 5
    assert len(sparse["predictions"]["rows"]) < len(daily["predictions"]["rows"]) / 4
    calendar = provenance["tradingDates"]
    sampled = sparse["research"]["observationDates"]
    assert all(calendar.index(b) - calendar.index(a) == 5 for a, b in zip(sampled, sampled[1:]))
    for trade in sparse["trades"]:
        assert trade["signalDate"] in sampled and trade["signalDate"] < trade["date"]
    strategy["portfolio"]["rebalanceDays"] = 1
    faster = run_research(strategy, data, provenance)
    assert faster["selection"] == sparse["selection"]
    assert faster["predictions"] == sparse["predictions"]
    assert faster["trades"] != sparse["trades"]
    assert all(t["date"] > t["signalDate"] for t in faster["trades"])


def test_decorrelation_uses_training_only_and_reports_every_fit(strategy):
    data, provenance = make_demo_data(strategy)
    report = run_research(strategy, data, provenance)
    final = report["validation"]["finalFit"]["decorrelation"]
    assert final["retained"] == ["momentum"]
    assert final["dropped"][0]["id"] == "duplicate"
    for outer in report["selection"]["splits"]["outerFolds"]:
        assert outer["decorrelation"]["retained"] == ["momentum"]
        for trial in outer["innerSelection"]:
            for fold in trial["folds"]:
                assert fold["decorrelation"]["retained"] == ["momentum"]
    for trial in report["selection"]["trials"]:
        assert all(f["decorrelation"]["dropped"][0]["id"] == "duplicate" for f in trial["folds"])
    mutated = data.copy()
    start = report["selection"]["splits"]["holdout"]["start"]
    multiplier = mutated.ts_code.map({code: 1 + i for i, code in enumerate(strategy["universe"]["symbols"])})
    mask = mutated.trade_date >= start
    for col in ["open", "high", "low", "close", "raw_close"]:
        mutated.loc[mask, col] *= multiplier[mask]
    future = run_research(strategy, mutated, provenance)
    assert future["selection"] == report["selection"]
    assert future["validation"]["finalFit"] == report["validation"]["finalFit"]
    assert future["factorResearch"] != report["factorResearch"]


def test_train_correlation_switch_changes_real_feature_computation():
    dates = pd.bdate_range("20240101", periods=30).strftime("%Y%m%d")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C"]], names=["trade_date", "ts_code"])
    train = pd.DataFrame({"first": np.arange(90.), "negative_copy": -np.arange(90.)}, index=idx)
    testing = pd.DataFrame({"first": [1., 2., 3.], "negative_copy": [10., 5., 1.]}, index=idx[:3])
    selected, audit = _decorrelate(train, {"decorrelation": "drop_correlated", "correlationThreshold": .9})
    assert selected == ["first"] and audit["dropped"][0]["correlation"] == pytest.approx(-1)
    spec = {"id": "factor_score"}
    no_drop, _ = _fit_predict(spec, train, train["first"], testing, {"decorrelation": "none"})
    drop, _ = _fit_predict(spec, train, train["first"], testing, {"decorrelation": "drop_correlated"})
    assert no_drop.nunique() == 1 and drop.nunique() == 3


def test_daily_marks_change_equity_between_rebalances(strategy):
    s, panel, dates, scores = market(strategy)
    symbol = sorted(s["universe"]["symbols"])[0]
    s["portfolio"]["rebalanceDays"] = 60
    panel.loc[(dates[2], symbol), "close"] = 11
    panel.loc[(dates[3], symbol), "close"] = 12
    _, equity, trades, ledger, _ = _simulate(panel, dates, scores, s)
    assert {t["date"] for t in trades} == {dates[1]}
    assert equity[3]["equity"] > equity[2]["equity"] > equity[1]["equity"]
    for day in ledger:
        assert day["equity"] == pytest.approx(day["cash"] + sum(p["quantity"] * p["mark"] for p in day["positions"]))


def test_suspended_target_retries_next_open_without_waiting_for_rebalance(strategy):
    s, panel, dates, scores = market(strategy)
    symbol = sorted(s["universe"]["symbols"])[0]
    s["portfolio"]["rebalanceDays"] = 60
    panel.loc[(dates[1], symbol), :] = np.nan
    _, _, trades, ledger, _ = _simulate(panel, dates, scores, s)
    assert not trades or trades[0]["date"] != dates[1]
    assert ledger[1]["pendingOrders"][0]["symbol"] == symbol
    assert trades[0]["date"] == dates[2]
    assert trades[0]["signalDate"] == dates[0] and trades[0]["delayedSessions"] == 1


def test_stale_marks_are_preserved_until_observed_price_returns(strategy):
    s, panel, dates, scores = market(strategy)
    symbol = sorted(s["universe"]["symbols"])[0]
    s["portfolio"]["rebalanceDays"] = 60
    panel.loc[(dates[2], symbol), :] = np.nan
    panel.loc[(dates[3], symbol), "close"] = 8
    _, _, _, ledger, _ = _simulate(panel, dates, scores, s)
    assert ledger[2]["staleMarks"] == [symbol]
    assert ledger[2]["equity"] == pytest.approx(ledger[1]["equity"])
    assert ledger[3]["equity"] < ledger[2]["equity"]


def test_weight_threshold_and_rank_buffer_change_actual_turnover(strategy):
    s, panel, dates, scores = market(strategy)
    s["portfolio"]["rebalanceDays"] = 1
    first, second = sorted(s["universe"]["symbols"])[:2]
    for date in dates[1:]:
        scores.loc[(date, first)], scores.loc[(date, second)] = 4., 5.
    plain = _simulate(panel, dates, scores, s)
    buffered = copy.deepcopy(s); buffered["portfolio"]["rankBuffer"] = 1
    kept = _simulate(panel, dates, scores, buffered)
    assert any(t["side"] == "SELL" and t["symbol"] == first for t in plain[2])
    assert not any(t["side"] == "SELL" and t["symbol"] == first for t in kept[2])
    assert kept[0]["turnover"] < plain[0]["turnover"]
    threshold = copy.deepcopy(s); threshold["portfolio"]["maxWeight"] = .4; threshold["portfolio"]["rebalanceThresholdBps"] = 5000
    blocked = _simulate(panel, dates, scores, threshold)
    assert blocked[2] == []
    assert any(x["reason"] == "within_rebalance_threshold" for x in blocked[4])


def test_t1_lots_cannot_sell_today_but_unlock_next_session(strategy):
    lots = [{"date": "20250102", "quantity": 20.}, {"date": "20250103", "quantity": 30.}]
    assert _sellable_quantity(lots, "20250103") == 20
    with pytest.raises(ResearchError, match="T\\+1"):
        _consume_sellable(lots, 21, "20250103")
    assert lots == [{"date": "20250102", "quantity": 20.}, {"date": "20250103", "quantity": 30.}]
    _consume_sellable(lots, 20, "20250103")
    assert _sellable_quantity(lots, "20250103") == 0
    assert _sellable_quantity(lots, "20250106") == 30
    s, panel, dates, scores = market(strategy)
    _, _, _, ledger, _ = _simulate(panel, dates, scores, s)
    assert all(p["sellableQuantity"] == 0 for p in ledger[1]["positions"])
    assert all(p["sellableQuantity"] == p["quantity"] for p in ledger[2]["positions"])


def test_fee_floor_transfer_and_cash_reconcile(strategy):
    costs = validate_strategy(strategy)["costs"]
    fees = _trade_costs(1000, "SELL", costs)
    assert fees == pytest.approx({"commission": 5, "slippage": .3, "tax": .5, "transfer": .01, "cost": 5.81})
    assert _trade_costs(0, "BUY", costs)["cost"] == 0
    s, panel, dates, scores = market(strategy)
    s["portfolio"].update(initialCapital=1000, topN=3, maxWeight=.4)
    metrics, _, trades, ledger, _ = _simulate(panel, dates, scores, s)
    cash = 1000
    for trade in trades:
        assert trade["cost"] == pytest.approx(sum(trade[k] for k in ["commission", "slippage", "tax", "transfer"]))
        cash += trade["notional"] * (1 if trade["side"] == "SELL" else -1) - trade["cost"]
        assert cash == pytest.approx(trade["cashAfter"], abs=1e-7)
        assert cash >= -1e-7
    assert ledger[-1]["cash"] == pytest.approx(cash, abs=1e-7)
    assert sum(metrics["costBreakdown"].values()) == pytest.approx(metrics["totalCosts"])
    assert len([t for t in trades if t["date"] == dates[1]]) == 3


def test_factor_statistics_are_holdout_observations_not_executable_short_returns(strategy):
    data, provenance = make_demo_data(strategy)
    report = run_research(strategy, data, provenance)
    factor = report["factorResearch"]["factors"][0]
    values = [d["ic"] for d in factor["rankIC"]["daily"]]
    assert factor["rankIC"]["mean"] == pytest.approx(np.mean(values))
    assert factor["rankIC"]["icir"] == pytest.approx(np.mean(values) / np.std(values, ddof=1))
    assert all(d["date"] >= report["selection"]["splits"]["holdout"]["start"] for d in factor["rankIC"]["daily"])
    quantiles = factor["quantiles"]
    assert quantiles["executablePortfolio"] is False
    assert quantiles["costsIncluded"] is False
    assert quantiles["overlappingLabels"] is True
    assert all(r["longShort"] == pytest.approx(r["groupReturns"][-1] - r["groupReturns"][0]) for r in quantiles["daily"])
    summary = report["factorResearch"]["portfolio"]
    assert summary["grossReturnSameExecutedPositions"] - summary["netReturn"] == pytest.approx(summary["costDragOnInitialCapital"])
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("section,key,value", [("research", "observationDays", 0), ("research", "observationDays", True),
    ("preprocess", "decorrelation", "pca"), ("preprocess", "correlationThreshold", .1),
    ("portfolio", "rebalanceThresholdBps", 10001), ("portfolio", "rankBuffer", 99),
    ("costs", "minCommission", -1), ("costs", "transferBps", -1), ("costs", "taxMode", "historical")])
def test_new_parameters_reject_unsupported_semantics(strategy, section, key, value):
    strategy.setdefault(section, {})[key] = value
    with pytest.raises(ResearchError):
        validate_strategy(strategy)


def test_correlation_threshold_changes_retained_features_without_using_test_values():
    rng = np.random.default_rng(17)
    first = rng.normal(size=1000)
    related = first + .5 * rng.normal(size=1000)
    train = pd.DataFrame({"a": first, "b": related})
    correlation = train.corr().loc["a", "b"]
    assert .8 < correlation < .99
    low, _ = _decorrelate(train, {"decorrelation": "drop_correlated", "correlationThreshold": .8})
    high, _ = _decorrelate(train, {"decorrelation": "drop_correlated", "correlationThreshold": .99})
    assert low == ["a"] and high == ["a", "b"]


def test_new_signal_replaces_suspended_unfilled_instruction(strategy):
    s, panel, dates, scores = market(strategy)
    s["portfolio"]["rebalanceDays"] = 1
    first, second = sorted(s["universe"]["symbols"])[:2]
    panel.loc[(dates[1], first), :] = np.nan
    for date in dates[1:]:
        scores.loc[(date, first)], scores.loc[(date, second)] = 1., 10.
    _, _, trades, ledger, _ = _simulate(panel, dates, scores, s)
    assert ledger[1]["pendingOrders"][0]["symbol"] == first
    assert trades[0]["date"] == dates[2] and trades[0]["symbol"] == second
    assert not any(t["side"] == "BUY" and t["symbol"] == first for t in trades)
