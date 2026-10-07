"""Finite inputs must not silently become valid null-valued forecasts/scores."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from atlas_quant.engine import ResearchError
from atlas_quant.statistical_quant.models import FittedModel, metrics
from atlas_quant.statistical_quant.validation import _records


def test_constant_forecast_uses_same_finite_contract_as_fitted_estimators():
    model = FittedModel({}, [], np.array([0., np.inf]), {})
    with pytest.raises(ResearchError) as caught:
        model.predict(pd.DataFrame(index=[0]))
    assert caught.value.code == "INVALID_FORECAST"


def test_finite_normalized_prediction_cannot_overflow_when_restoring_price():
    samples = SimpleNamespace(meta=pd.DataFrame([{
        "inputValid": True, "entryDate": "20240103", "targetDate": "20240110",
        "invalidReason": None, "currentState": 1e308, "scale": 1e308,
        "realizedFuture": np.nan,
    }]))
    with pytest.raises(ResearchError) as caught:
        _records(samples, [0], np.array([[1., 2.]]), "fit", {})
    assert caught.value.code == "INVALID_FORECAST"


@pytest.mark.parametrize("prediction,truth", [
    ([[1e200, 1e200]], [[0., 0.]]),
    ([[1e200, 1e200]], [[1e200, 1e200]]),
])
def test_error_or_no_change_baseline_overflow_is_explicit(prediction, truth):
    with pytest.raises(ResearchError) as caught:
        metrics(np.asarray(prediction), np.asarray(truth), ["20240102"])
    assert caught.value.code == "INVALID_FORECAST"
