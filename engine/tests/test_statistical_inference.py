import copy
import json
import warnings
import numpy as np
import pandas as pd
import pytest

from atlas_quant.statistical_quant.inference import evaluate_forecast_uncertainty


def rows(n=200):
    data = []
    dates = pd.bdate_range("20230102", periods=n)
    rng = np.random.default_rng(123)
    state = 0.
    for date in dates:
        state = .85*state + rng.normal(0, .1)
        data.append({"date": date.strftime("%Y%m%d"), "status": "valid", "labelMaturedAt": "observed",
                     "currentState": 100., "scale": 200., "expectedEntry": 100., "expectedFuture": 101.,
                     "realizedEntry": 100.+state, "realizedFuture": 101.+state})
    return data


def test_date_clusters_prevent_copied_stocks_inflating_sample_size():
    original = rows()
    a = evaluate_forecast_uncertainty(original, 5, 1)
    b = evaluate_forecast_uncertainty([copy.deepcopy(row) for row in original for _ in range(10)], 5, 1)
    assert a["observedDates"] == b["observedDates"] == 200
    assert b["forecastRows"] == 10*a["forecastRows"]
    for name in a["intervals"]:
        for key in ("estimate", "lower", "upper"):
            assert a["intervals"][name][key] == pytest.approx(b["intervals"][name][key], abs=1e-16)
    assert a["independentSampleSize"] is None and not a["individualPricePredictionInterval"]


def test_small_effective_history_is_not_assigned_fake_confidence():
    result = evaluate_forecast_uncertainty(rows(100), 60, 1)
    assert result["blockLengthObservations"] >= 61
    assert result["status"] == "unavailable" and result["intervals"] is None


def test_interval_is_reproducible_and_does_not_mutate_issued_forecasts():
    data = rows()
    before = copy.deepcopy(data)
    a = evaluate_forecast_uncertainty(data, 5, 1)
    assert a == evaluate_forecast_uncertainty(data, 5, 1)
    assert data == before
    assert a["status"] == "computed_under_declared_assumptions"
    assert a["intervals"]["lossImprovement"]["estimate"] > 0
    assert not a["multipleExperimentsAdjusted"] and not a["profitabilityTested"]


def test_unmatured_or_invalid_predictions_never_enter_calibration():
    data = rows()
    a = evaluate_forecast_uncertainty(data, 5, 1)
    data.extend([{**data[0], "status": "invalid", "realizedFuture": 1e20},
                 {**data[0], "labelMaturedAt": None, "realizedFuture": 1e20}])
    b = evaluate_forecast_uncertainty(data, 5, 1)
    assert a["intervals"] == b["intervals"] and b["excludedRows"] == 2


def test_zero_change_baseline_has_exactly_zero_paired_loss_improvement():
    data = rows()
    for row in data:
        row["expectedEntry"] = row["expectedFuture"] = row["currentState"]
    result = evaluate_forecast_uncertainty(data, 5, 1)
    assert result["intervals"]["lossImprovement"] == {"estimate": 0., "lower": 0., "upper": 0.}


def test_bias_sign_and_units_match_realized_minus_predicted():
    data = rows()
    for row in data:
        row["realizedEntry"], row["realizedFuture"] = 102., 105.
    result = evaluate_forecast_uncertainty(data, 5, 1)
    assert result["intervals"]["entryBias"]["estimate"] == pytest.approx(.01)
    assert result["intervals"]["exitBias"]["estimate"] == pytest.approx(.02)
    assert result["intervals"]["remainingChangeBias"]["estimate"] == pytest.approx(.01)


@pytest.mark.parametrize("h,frequency", [(True, 1), (5, 0), (61, 1), (5, 1.5)])
def test_invalid_block_horizon_configuration_fails(h, frequency):
    with pytest.raises(ValueError):
        evaluate_forecast_uncertainty(rows(), h, frequency)


def test_finite_prices_with_overflowing_squared_error_fail_explicitly():
    data = rows(80)
    for row in data:
        row["realizedFuture"] = 1e200
        row["scale"] = 1
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = evaluate_forecast_uncertainty(data, 5, 1)
    assert result["status"] == "unavailable" and result["intervals"] is None
    assert result["numericFailureRows"] == 80
    assert result["unavailableReason"] == "nonfinite_derived_loss_or_error"
    json.dumps(result, allow_nan=False)


def test_one_numeric_failure_does_not_issue_interval_for_selected_subset():
    data = rows()
    data[30]["realizedFuture"] = 1e200
    result = evaluate_forecast_uncertainty(data, 5, 1)
    assert result["observedDates"] == 199 and result["numericFailureRows"] == 1
    assert result["status"] == "unavailable" and result["intervals"] is None
