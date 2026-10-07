import copy
import json

import numpy as np
import pandas as pd
import pytest

from atlas_quant.engine import MODEL_NAMES, ResearchError, _build_samples, _fit_predict, _prepare_data, _trial_specs, run_research, validate_strategy
from atlas_quant.factors import FactorError, validate_expression
from atlas_quant.fixtures import make_demo_data


@pytest.fixture(scope="module")
def expanded_case():
    strategy = {"schemaVersion": 1, "name": "expanded", "universe": {"symbols": ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH"], "start": "20230101", "end": "20241231"}, "factors": [{"id": "mom", "expression": "returns(close,20)", "direction": 1}, {"id": "vol", "expression": "ts_std(returns(close,1),20)", "direction": -1}], "model": {"mode": "auto", "candidates": list(MODEL_NAMES), "horizon": 5}}
    frame, provenance = make_demo_data(strategy)
    return strategy, frame, provenance, run_research(strategy, frame, provenance)


def test_eight_model_nested_selection_and_forecast_units(expanded_case):
    strategy, frame, provenance, report = expanded_case
    assert len(report["selection"]["candidates"]) == 8
    assert report["selection"]["trialCount"] == 14
    assert all(t["status"] == "complete" for t in report["selection"]["trials"])
    prediction = report["predictions"]
    assert prediction["target"]["id"] == "forward_return"
    assert len(prediction["latest"]) == 5
    assert all(row["actualTarget"] is None and row["labelEnd"] is None for row in prediction["latest"])
    assert all(row["date"] >= report["selection"]["splits"]["holdout"]["start"] for row in prediction["rows"])
    lookup = frame.set_index(["trade_date", "ts_code"])
    mature = next(row for row in prediction["rows"] if row["actualTarget"] is not None)
    actual = lookup.loc[(mature["labelEnd"], mature["symbol"]), "open"] / lookup.loc[(mature["earliestExecutionDate"], mature["symbol"]), "open"] - 1
    assert mature["actualTarget"] == pytest.approx(actual)
    assert report["trainingDiagnostics"]["period"] == "final_purged_training_only"
    json.dumps(report, allow_nan=False)


def test_new_model_holdout_and_future_absence_do_not_change_selection(expanded_case):
    strategy, frame, provenance, report = expanded_case
    boundary = report["selection"]["splits"]["holdout"]["start"]
    damaged = frame.copy()
    mask = (damaged.trade_date >= boundary) & (damaged.ts_code == "000001.SZ")
    damaged.loc[mask, ["open", "high", "low", "close", "raw_close"]] *= 4
    removed_days = provenance["tradingDates"][-12:-10]
    damaged = damaged[~damaged.trade_date.isin(removed_days)]
    other = run_research(strategy, damaged, provenance)
    assert other["selection"]["trials"] == report["selection"]["trials"]
    assert other["selection"]["params"] == report["selection"]["params"]
    assert other["validation"]["finalFit"] == report["validation"]["finalFit"]
    assert other["trainingDiagnostics"] == report["trainingDiagnostics"]


def test_factor_baseline_does_not_present_percentile_as_return(expanded_case):
    strategy, frame, provenance, _ = expanded_case
    strategy = copy.deepcopy(strategy)
    strategy["model"].update(mode="manual", candidates=["factor_score"])
    report = run_research(strategy, frame, provenance)
    pred = report["predictions"]
    assert pred["predictedTargetUnit"] is None
    assert pred["errorMetrics"] is None
    assert all(row["predictedTarget"] is None for row in pred["rows"])
    assert all(0 <= row["score"] <= 1 for row in pred["rows"])


def test_excess_return_label_is_cross_sectional_without_feature_change(expanded_case):
    strategy, frame, provenance, _ = expanded_case
    s = validate_strategy(strategy)
    panel, dates, _ = _prepare_data(frame, s, provenance)
    X, y, ends, _ = _build_samples(panel, dates, s["factors"], 5)
    X2, excess, ends2, _ = _build_samples(panel, dates, s["factors"], 5, "forward_excess_return")
    pd.testing.assert_frame_equal(X, X2)
    pd.testing.assert_series_equal(ends, ends2)
    expected = y - y.groupby(level="trade_date").transform("mean")
    pd.testing.assert_series_equal(excess, expected)
    np.testing.assert_allclose(excess.groupby(level="trade_date").mean().dropna(), 0, atol=1e-16)


@pytest.mark.parametrize("model", ["bayesian_ridge", "huber", "random_forest", "extra_trees"])
def test_new_models_fixed_seed_and_train_only_preprocessing(model):
    rng = np.random.default_rng(127)
    idx = pd.MultiIndex.from_product([pd.bdate_range("2024-01-01", periods=160).strftime("%Y%m%d"), ["A", "B", "C"]], names=["trade_date", "ts_code"])
    X = pd.DataFrame(rng.normal(size=(len(idx), 3)), index=idx, columns=["a", "b", "c"])
    y = X.a * 0.01 + rng.normal(0, 0.004, len(idx))
    train, test = X.iloc[:360], X.iloc[360:]
    spec = _trial_specs([model])[0]
    first, audit = _fit_predict(spec, train, y.iloc[:360], test, {"winsorize": True, "standardize": True})
    second, audit2 = _fit_predict(spec, train, y.iloc[:360], test, {"winsorize": True, "standardize": True})
    pd.testing.assert_series_equal(first, second)
    assert audit == audit2
    _, shock_audit = _fit_predict(spec, train, y.iloc[:360], test * 10000, {"winsorize": True, "standardize": True})
    assert audit == shock_audit


def _external_case(expanded_case):
    strategy, frame, provenance, _ = expanded_case
    strategy, frame, provenance = copy.deepcopy(strategy), frame.copy(), copy.deepcopy(provenance)
    alias = "pcd_test_1234567890abcdef"
    strategy["factors"] = [{"id": "external", "expression": f"rank({alias})", "direction": 1}]
    frame[alias] = np.arange(len(frame), dtype=float)
    frame[alias + "__available_date"] = frame.trade_date
    provenance["externalFields"] = {alias: {"dataType": "number", "source": "PCD_TEST_ONLY", "path": "test.value", "availabilityPolicy": "point_in_time_asof", "availableDateColumn": alias + "__available_date"}}
    return validate_strategy(strategy), frame, provenance, alias


def test_external_numeric_fields_require_point_in_time_dates(expanded_case):
    strategy, frame, provenance, alias = _external_case(expanded_case)
    panel, _, audit = _prepare_data(frame, strategy, provenance)
    assert alias in panel
    assert audit["externalFieldAudit"][0]["observedValues"] == len(frame)
    with pytest.raises(ResearchError, match="可知日期"):
        _prepare_data(frame, strategy, {})
    frame.loc[0, alias + "__available_date"] = "20990101"
    with pytest.raises(ResearchError) as exc:
        _prepare_data(frame, strategy, provenance)
    assert exc.value.code == "FUTURE_EXTERNAL_FIELD"


def test_external_availability_affects_fingerprint_and_text_is_not_numeric(expanded_case):
    strategy, frame, provenance, alias = _external_case(expanded_case)
    _, _, first = _prepare_data(frame, strategy, provenance)
    frame[alias + "__available_date"] = "20220101"
    _, _, second = _prepare_data(frame, strategy, provenance)
    assert first["dataSha256"] != second["dataSha256"]
    frame[alias] = frame[alias].astype(object)
    frame.loc[0, alias] = "a narrative is not a numerical factor"
    with pytest.raises(ResearchError) as exc:
        _prepare_data(frame, strategy, provenance)
    assert exc.value.code == "EXTERNAL_FIELD_NONNUMERIC"


@pytest.mark.parametrize("name", ["pcd_", "fd_", "pcd___class__.x", "arbitrary_field", "pcd_" + "a" * 61, "pcd_CAPITAL"])
def test_external_namespace_is_narrow(name):
    with pytest.raises(FactorError):
        validate_expression(name)


def test_factor_and_model_budget_reject_overflow(expanded_case):
    strategy, _, _, _ = expanded_case
    s = copy.deepcopy(strategy)
    s["factors"] = [{"id": f"f{i}", "expression": f"returns(close,{i+1})", "direction": 1} for i in range(32)]
    assert len(validate_strategy(s)["factors"]) == 32
    s["factors"].append({"id": "too_many", "expression": "close", "direction": 1})
    with pytest.raises(ResearchError):
        validate_strategy(s)
    s = copy.deepcopy(strategy)
    s["model"]["target"] = "future_profit_guaranteed"
    with pytest.raises(ResearchError):
        validate_strategy(s)
