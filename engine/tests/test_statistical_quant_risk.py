import copy
import numpy as np
import pandas as pd
import pytest
from atlas_quant.statistical_quant.risk import RiskState, bounded_units, breaches
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.execution import execute
from test_statistical_quant_execution import artifact, audit


def setup_risk():
    dates = pd.bdate_range("2024-01-02", periods=85).strftime("%Y%m%d").tolist()
    symbols = ["000001.SZ", "600000.SH"]
    rng = np.random.default_rng(17)
    prices = 100*np.exp(np.cumsum(rng.normal(0, .025, (85, 2)), axis=0))
    rows = [{"trade_date": d, "ts_code": s, "open": prices[t, k], "close": prices[t, k], "vol": 1000.}
            for t, d in enumerate(dates) for k, s in enumerate(symbols)]
    panel = pd.DataFrame(rows).set_index(["trade_date", "ts_code"])
    strategy = validate({"schemaVersion": 2, "name": "Risk", "universe": {"symbols": symbols, "start": dates[0], "end": dates[-1]},
                         "research": {"mode": "statistical_quant"}, "target": {"kind": "asset_price"}, "model": {"family": "mean_reversion"},
                         "portfolio": {"initialCapital": 10000, "maxWeight": 1, "rebalanceThresholdBps": 0, "sizingMode": "volatility_target", "targetAnnualVolatility": .05, "volatilityLookback": 20},
                         "execution": {"maxPositions": 1, "minEdgeBps": 0},
                         "costs": {k: 0 for k in ("commissionBps", "slippageBps", "sellTaxBps", "transferBps", "minCommission", "borrowAnnualBps")}})
    a = artifact([dates[30], dates[31], dates[50]], expected_exit=120)
    return panel, dates, symbols, strategy, a


def test_volatility_uses_complete_prior_return_window_psd_shrinkage_and_no_future():
    panel, dates, symbols, s, _ = setup_risk()
    state = RiskState(panel, dates, symbols, s)
    before = state.before(dates[31])
    prices = panel.close.unstack().reindex(columns=symbols).to_numpy()
    returns = prices[1:]/prices[:-1]-1
    covariance = np.cov(returns[10:30], rowvar=False, ddof=1)*252
    expected = .9*covariance+.1*np.diag(np.diag(covariance))
    np.testing.assert_allclose(before["covariance"], expected, atol=1e-14)
    assert np.linalg.eigvalsh(before["covariance"]).min() >= 0
    changed = panel.copy()
    changed.loc[changed.index.get_level_values("trade_date") >= dates[31], "close"] *= 3
    after = RiskState(changed, dates, symbols, s).before(dates[31])
    np.testing.assert_array_equal(before["covariance"], after["covariance"])
    assert before["informationCutoff"] == dates[30]


def test_volatility_target_really_scales_position_and_caps_predicted_risk():
    panel, dates, symbols, s, a = setup_risk()
    result = execute(panel, dates, s, a)
    opening = [t for t in result[2] if t["exitReason"] is None]
    assert opening and opening[0]["notional"] < 5000
    decision = next(d for d in result[3]["decisions"] if d["action"] == "entered")
    assert decision["postTradeRisk"]["annualVolatility"] == pytest.approx(.05, abs=1e-8)
    fixed = copy.deepcopy(s); fixed["portfolio"]["sizingMode"] = "fixed"
    fixed_result = execute(panel, dates, fixed, a)
    assert fixed_result[2][0]["notional"] > opening[0]["notional"]*2
    audit(*result)


def test_net_constraint_scales_entire_frozen_basket():
    panel, dates, symbols, s, a = setup_risk()
    s["portfolio"].update(sizingMode="fixed", netExposureLimit=.1)
    result = execute(panel, dates, s, a)
    entry = result[2][0]
    assert entry["notional"] == pytest.approx(1000, abs=1e-5)
    assert next(d for d in result[3]["decisions"] if d["action"] == "entered")["postTradeRisk"]["net"] <= .1+1e-9


def test_known_opening_costs_reduce_risk_constraint_denominator():
    panel, dates, symbols, s, a = setup_risk()
    s["portfolio"].update(sizingMode="fixed", netExposureLimit=.1)
    s["costs"].update(minCommission=100.)
    result = execute(panel, dates, s, a)
    opening = result[2][0]
    # Fixed opening fee leaves NAV=9900, hence exposure <=990, not1000.
    assert opening["notional"] == pytest.approx(990., abs=1e-5)
    entered = next(d for d in result[3]["decisions"] if d["action"] == "entered")
    assert entered["postTradeRisk"]["net"] == pytest.approx(.1, abs=1e-9)
    audit(*result)


def test_factor_constraint_uses_prior_cross_section_not_same_day_prices():
    panel, dates, symbols, s, a = setup_risk()
    s["factors"] = [{"id": "price_exposure", "expression": "close", "direction": 1, "role": "predictor"}]
    s["portfolio"].update(sizingMode="fixed", factorExposureLimits=[{"factorId": "price_exposure", "maxAbsExposure": .05}])
    result = execute(panel, dates, s, a)
    opening = [t for t in result[2] if t["exitReason"] is None]
    assert opening[0]["notional"] == pytest.approx(500, abs=1e-5)
    decision = next(d for d in result[3]["decisions"] if d["action"] == "entered")
    assert abs(decision["postTradeRisk"]["factorExposures"]["price_exposure"]) == pytest.approx(.05, abs=1e-8)


def test_constant_missing_factor_is_not_zero_risk():
    panel, dates, symbols, s, a = setup_risk()
    panel.loc[:, "vol"] = 1000.
    s["factors"] = [{"id": "constant", "expression": "vol", "direction": 1, "role": "predictor"}]
    s["portfolio"].update(sizingMode="fixed", factorExposureLimits=[{"factorId": "constant", "maxAbsExposure": .1}])
    result = execute(panel, dates, s, a)
    assert result[2] == []
    assert any(d["reason"] == "risk_inputs_unavailable" for d in result[3]["decisions"])
    assert result[3]["ledger"][0]["unavailableRiskInputs"]["factors"] == ["constant"]


def test_incomplete_covariance_blocks_entry_and_does_not_invent_zero_volatility():
    panel, dates, symbols, s, a = setup_risk()
    panel.loc[(dates[25], symbols[0]), "close"] = np.nan
    result = execute(panel, dates, s, a)
    assert result[2] == []
    assert any(d["reason"] == "risk_inputs_unavailable" for d in result[3]["decisions"])


def test_missing_risk_history_after_entry_triggers_explicit_risk_exit():
    panel, dates, symbols, s, a = setup_risk()
    panel.loc[(dates[32], symbols[0]), ["close", "open", "vol"]] = [np.nan, np.nan, 0.]
    result = execute(panel, dates, s, a)
    assert result[2][0]["date"] == dates[31]
    assert any(t["exitReason"] == "risk_limit_exit" and t["date"] == dates[33] for t in result[2])
    audit(*result)


def test_one_price_adverse_bar_is_matching_rejection_not_replacement_signal():
    panel, dates, symbols, s, a = setup_risk()
    s["portfolio"]["sizingMode"] = "fixed"
    panel["high"] = panel.close*1.01; panel["low"] = panel.close*.99
    prior = panel.loc[(dates[30], symbols[0]), "close"]
    panel.loc[(dates[31], symbols[0]), ["open", "close", "high", "low"]] = prior*1.1
    a["targetDefinitions"].append({"id": "other", "symbols": [symbols[1]], "quantities": [1.]})
    other = copy.deepcopy(a["rows"][0]); other.update(forecastId="forecast_B", targetId="other", expectedGrossPnl=10., expectedGrossBps=1000.)
    a["rows"].append(other)
    result = execute(panel, dates, s, a)
    assert result[2] == []
    assert any(d["reason"] == "one_price_bar_adverse_fill_unavailable" for d in result[3]["decisions"])
    assert any(d["reason"] == "forecast_order_budget" for d in result[3]["decisions"])
