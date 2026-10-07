"""Actual point-in-time conditional inputs and matched factor-increment evidence."""
import copy
import numpy as np
import pytest

from atlas_quant.engine import ResearchError, run_research
from atlas_quant.fixtures import make_demo_data
from atlas_quant.statistical_quant import validate_statistical_quant
from test_statistical_quant import strategy


def conditional_source(family):
    s = strategy(); s["model"]["family"] = family
    s["validation"] = {"minTrainDates": 40}
    field = "fd_roe" if family == "fundamental" else "ext_surprise"
    s["factors"] = [{"id": "condition", "expression": field, "role": "event" if family == "event" else "predictor"}]
    data, provenance = make_demo_data(s)
    dates = sorted(data.trade_date.unique()); lookup = {date: i for i, date in enumerate(dates)}
    t = data.trade_date.map(lookup).to_numpy()
    data[field] = .05+np.sin(t/13)/10
    if family == "event":
        data.loc[t%3 != 0, field] = 0.
    data[field+"__available_date"] = data.trade_date
    provenance["externalFields"] = {field: {"dataType": "number", "source": "SYNTHETIC_CONDITIONAL_TEST",
        "path": "test.fixture."+field, "availabilityPolicy": "point_in_time_asof", "availableDateColumn": field+"__available_date"}}
    return s, data, provenance, field


def disappearing_predictor_source():
    s = strategy(); s["name"] = "Synthetic disappearing predictor coverage"
    s["model"]["trainWindow"] = 120
    s["factors"] = [{"id": "sparse_condition", "expression": "ext_sparse"}]
    data, provenance = make_demo_data(s)
    t = data.trade_date.map({d: i for i, d in enumerate(sorted(data.trade_date.unique()))}).to_numpy()
    data["ext_sparse"] = np.where(t < 600, np.sin(t/13), np.nan)
    data["ext_sparse__available_date"] = data.trade_date.where(data.ext_sparse.notna(), None)
    provenance["externalFields"] = {"ext_sparse": {"dataType": "number", "source": "SYNTHETIC_MISSING_INPUT_TEST",
        "path": "fixture.ext_sparse", "availabilityPolicy": "point_in_time_asof", "availableDateColumn": "ext_sparse__available_date"}}
    return s, data, provenance


def test_real_rolling_model_failure_cannot_claim_increment_on_unmatched_baseline_predictions():
    s, data, provenance = disappearing_predictor_source()
    result = run_research(s, data, provenance)
    inc = result["forecasts"]["diagnostics"]["factorIncrement"]
    coverage = inc["coverage"]
    assert inc["status"] == "unavailable" and inc["dateBalancedMseImprovement"] is None
    assert coverage["fullValidRows"] == coverage["matchedRows"] == 0
    assert coverage["baselineValidRows"] == coverage["baselineValidUnmatchedRows"] > 100
    assert coverage["fullMatureRows"] == coverage["baselineMatureRows"] > 100
    assert coverage["fullValidMatureRows"] == 0 and coverage["baselineValidMatureRows"] > 100
    assert coverage["fullUnavailableModelRows"] > 100 and not inc["outputValidityMasksIdentical"]
    assert len(result["forecasts"]["rows"]) == len(inc["baselineRows"])


@pytest.mark.parametrize("family", ["fundamental", "event"])
def test_conditional_models_use_real_supplied_pit_values_and_matched_increment(family):
    s, data, provenance, _ = conditional_source(family)
    result = run_research(s, data, provenance)
    assert result["provenance"]["synthetic"]
    increment = result["forecasts"]["diagnostics"]["factorIncrement"]
    assert increment["status"] == "available" and increment["pairedDates"] > 20
    assert not increment["causalAttribution"] and not increment["profitabilityEstablished"]
    assert increment["sameEventAndMissingInputMask"] and not increment["hedgeFactorsAblated"]
    original = result["forecasts"]["rows"]; baseline = increment["baselineRows"]
    assert len(original) == len(baseline)
    assert [(r["date"], r["targetId"], r["status"], r["labelMaturedAt"]) for r in original] == [
        (r["date"], r["targetId"], r["status"], r["labelMaturedAt"]) for r in baseline]
    assert len(result["validation"]["finalTrials"]) == len(increment["baselineValidation"]["finalTrials"])
    for full, control in zip(result["validation"]["outerFolds"], increment["baselineValidation"]["outerFolds"]):
        assert (full["testStart"], full["testEnd"]) == (control["testStart"], control["testEnd"])
        assert full["fit"]["labelEndMax"] < full["testStart"]
        assert control["fit"]["labelEndMax"] < control["testStart"]
    assert all(not any(x.startswith("factor:") for x in fit["featureNames"]) for fit in increment["baselineModelFits"])
    independent = np.mean([x["stateOnlyMse"]-x["withFactorsMse"] for x in increment["dailyLosses"]])
    assert increment["dateBalancedMseImprovement"] == pytest.approx(independent)
    if family == "event":
        assert any(row["invalidReason"] == "no_observed_event" for row in original)
    else:
        # A later conditional observation may affect later fits, never an already
        # generated prediction or its state-only comparison before that date.
        cutoff = sorted({row["date"] for row in original})[20]
        changed = data.copy()
        changed.loc[changed.trade_date >= cutoff, "fd_roe"] *= -30
        other = run_research(s, changed, provenance)
        fields = ("forecastId", "date", "targetId", "modelFitId", "expectedEntry", "expectedFuture")
        keep = lambda values: [{k: row[k] for k in fields} for row in values if row["date"] < cutoff]
        assert keep(original) == keep(other["forecasts"]["rows"])
        assert keep(baseline) == keep(other["forecasts"]["diagnostics"]["factorIncrement"]["baselineRows"])
    summary = result["validation"]["factorIncrement"]
    assert summary["baselineTotalRows"] == len(baseline)
    assert summary["completeArtifactPath"] == "forecasts.diagnostics.factorIncrement"
    assert not {"baselineRows", "baselineModelFits", "baselineValidation"}.intersection(summary)


@pytest.mark.parametrize("family", ["fundamental", "event"])
def test_all_missing_conditional_data_cannot_claim_a_conditional_study_even_with_baseline(family):
    s, data, provenance, field = conditional_source(family)
    s["model"]["estimator"] = "no_change"
    data[field] = np.nan; data[field+"__available_date"] = None
    with pytest.raises(ResearchError) as caught:
        run_research(s, data, provenance)
    assert caught.value.code == "MISSING_MODEL_DATA"


def test_future_conditional_value_is_rejected_before_any_fit():
    s, data, provenance, field = conditional_source("fundamental")
    data.loc[data.index[-1], field+"__available_date"] = "20261001"
    with pytest.raises(ResearchError) as caught:
        run_research(s, data, provenance)
    assert caught.value.code == "FUTURE_EXTERNAL_FIELD"


def test_prediction_constant_baseline_has_zero_factor_increment():
    s, data, provenance, _ = conditional_source("fundamental")
    s["model"]["estimator"] = "no_change"
    result = run_research(s, data, provenance)
    assert result["validation"]["factorIncrement"]["dateBalancedMseImprovement"] == 0


@pytest.mark.parametrize("bad", [{}, [], 17, True])
def test_risk_factor_identifier_malformed_type_has_safe_validation_error(bad):
    s = strategy(); s["portfolio"] = {"factorExposureLimits": [{"factorId": bad, "maxAbsExposure": .1}]}
    with pytest.raises(ResearchError):
        validate_statistical_quant(s)
