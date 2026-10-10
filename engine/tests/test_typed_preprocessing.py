"""v2 protocol/domain regressions; independent numerical oracles live separately."""
import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

from atlas_quant.engine import ResearchError
from atlas_quant.statistical_quant.preprocessing import automatic_metadata, transform_values, validate_metadata
from atlas_quant.statistical_quant.typed_preprocessing import descriptor, SCHEMA, SIMPLE_RETURN, LOG_RETURN, SIGNED_LOG, DIFFERENCE
from atlas_quant.statistical_quant.model_function import export_function, predict_function, validate_function
from atlas_quant.statistical_quant.models import candidates, fit
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import build_samples
from test_automatic_preprocessing import automatic_strategy, panel_fixture, rows


def describe(expression, override=None, role="predictor"):
    return descriptor({"id": "f", "expression": expression, "direction": 1, "role": role}, override)


@pytest.mark.parametrize("expression,kind,transform", [
    ("close", "price", "simple_return"),
    ("ts_mean(close,20)", "price", "simple_return"),
    ("log(close)", "log_price", "first_difference"),
    ("ts_mean(log(close),20)", "log_price", "first_difference"),
    ("log(close)-log(lag(close,1))", "ratio", "identity"),
    ("delta(log(close),5)", "ratio", "identity"),
    ("close/lag(close,1)-1", "ratio", "identity"),
    ("(close-ts_mean(close,20))/close", "ratio", "identity"),
    ("ts_std(close,20)/ts_mean(close,20)", "ratio", "identity"),
    ("total_mv", "positive_size", "log_positive"),
    ("log(total_mv)", "derived", "identity"),
    ("1/pe_ttm", "ratio", "identity"),
    ("pe-pb", "derived", "identity"),
    ("ts_mean(pe_ttm,20)", "valuation_multiple", "reciprocal_nonzero"),
    ("fd_netprofit_margin", "percent", "percent_to_fraction"),
    ("delta(fd_netprofit_margin,20)", "percent", "percent_to_fraction"),
    ("fd_netprofit_margin/100", "ratio", "identity"),
    (".01*fd_netprofit_margin", "ratio", "identity"),
    ("fd_eps", "currency_per_share", "signed_log1p"),
    ("delta(fd_ebit,20)", "currency_amount", "signed_log1p"),
    ("fd_eps/raw_close", "ratio", "identity"),
    ("model_fin_operating_margin", "ratio", "identity"),
    ("abs(returns(close,1))/max(amount,1)", "derived", "identity"),
])
def test_quantity_algebra_distinguishes_levels_and_constructed_quantities(expression, kind, transform):
    item = describe(expression)
    assert item["economicType"] == kind
    assert item["transform"]["kind"] == transform


@pytest.mark.parametrize("expression", ["close+vol", "log(close+vol)", "returns(close+vol,1)",
                                         "rank(close+vol)", "sqrt(close)", "log(log(close))", "returns(close,1)/0", "(0/0)+close", "close/-0"])
def test_known_unit_mismatch_cannot_be_hidden_by_a_wrapper_or_override(expression):
    for override in (None, {"transform": {"kind": "identity"}}):
        with pytest.raises(ResearchError, match="经济单位"):
            describe(expression, override)


@pytest.mark.parametrize("expression", ["close-ts_mean(close,20)", "0-close", "-close", "delta(total_mv,20)", "ext_custom",
                                         "-1*total_mv", "total_mv/-2", "-1*close", "0*close", "-1*vol"])
def test_unscaled_quantities_need_explicit_choice(expression):
    with pytest.raises(ResearchError) as error:
        describe(expression)
    assert error.value.code == "FACTOR_TRANSFORM_REQUIRED"
    assert describe(expression, {"transform": SIGNED_LOG})["transform"] == SIGNED_LOG


@pytest.mark.parametrize("expression", ["raw_close", "log(raw_close)", "returns(raw_close,1)",
                                         "raw_close/lag(raw_close,1)", "close/raw_close"])
def test_raw_price_cannot_become_corporate_action_return(expression):
    with pytest.raises(ResearchError) as error:
        describe(expression)
    assert error.value.code == "AUTO_FACTOR_REQUIRES_ADJUSTED_PRICE"


def test_override_is_explicit_bounded_and_cannot_restore_price_level_or_change_event_admission():
    assert describe("close", {"transform": LOG_RETURN})["transform"] == LOG_RETURN
    for expression, choice in (("close", {"kind": "identity"}), ("log(close)", LOG_RETURN)):
        with pytest.raises(ResearchError) as error:
            describe(expression, {"transform": choice})
        assert error.value.code == "PRICE_RETURN_REQUIRED"
    assert describe("ext_event", {"transform": SIGNED_LOG}, role="event")["transform"] == SIGNED_LOG
    with pytest.raises(ResearchError) as error:
        describe("ext_event", {"transform": SIMPLE_RETURN}, role="event")
    assert error.value.code == "EVENT_TRANSFORM_ZERO"
    base = automatic_strategy()
    base["preprocess"]["automatic"] = {"schema": SCHEMA, "overrides": {"price": {"transform": LOG_RETURN}}}
    validate(base)
    for override in ({"missing_factor": {"transform": LOG_RETURN}},
                     {"price": {"transform": {**LOG_RETURN, "lag": True}}},
                     {"price": {"transform": {**LOG_RETURN, "lag": 2}}},
                     {"price": {"transform": LOG_RETURN, "futureFit": True}}):
        base["preprocess"]["automatic"]["overrides"] = override
        with pytest.raises(ResearchError):
            validate(base)


def test_zero_negative_missing_and_overflow_domains():
    prices = pd.DataFrame({"p": [10., 20., 0., 20., -2., 20., np.nan, 20., 30., np.inf]})
    simple = transform_values(prices, SIMPLE_RETURN).p.to_list()
    assert simple[1] == 1 and simple[8] == .5
    assert all(np.isnan(simple[i]) for i in [0, 2, 3, 4, 5, 6, 7, 9])
    log = transform_values(prices, LOG_RETURN).p.to_numpy()
    assert log[1] == pytest.approx(np.log(2)) and log[8] == pytest.approx(np.log(1.5))
    signed = transform_values(pd.DataFrame({"x": [-np.inf, -10., -0., 0., 10., np.nan]}), SIGNED_LOG).x
    assert np.isnan(signed.iloc[0]) and np.isnan(signed.iloc[-1])
    assert signed.iloc[1] == -signed.iloc[4] and signed.iloc[2] == signed.iloc[3] == 0
    # Taking logs separately avoids an unnecessary overflow of P[t]/P[t-1].
    huge = transform_values(pd.DataFrame({"x": [1e-200, 1e200]}), LOG_RETURN).x
    assert np.isfinite(huge.iloc[1])
    assert huge.iloc[1] == pytest.approx(400*np.log(10))
    np.testing.assert_allclose(transform_values(pd.DataFrame({"p": [np.log(10), np.log(20)]}), DIFFERENCE).p,
                               [np.nan, np.log(2)], equal_nan=True)


def test_global_scope_clock_and_cross_section_rejection():
    cn = describe("ext_ctx_000300_sh_close")
    assert cn["scope"] == "global" and cn["clock"] == "research_sessions"
    from atlas_quant.context_sources import FIELDS
    us = next(name for name, item in FIELDS.items() if item["api"] == "yfinance_history" and item["field"] == "close")
    assert describe(us)["clock"] == "observed_source_sessions_asof"
    assert describe(f"returns(close,1)-returns({us},1)")["clock"] == "research_sessions"
    for expression in ("rank(ext_ctx_000300_sh_close)", "zscore(returns(ext_ctx_000300_sh_close,1))"):
        with pytest.raises(ResearchError) as error:
            describe(expression)
        assert error.value.code == "GLOBAL_FACTOR_CROSS_SECTION"


def v2_fitted_fixture(spec=None):
    spec = spec or candidates("ridge")[0]
    strategy = automatic_strategy(estimator=spec["estimator"])
    strategy["preprocess"]["automatic"] = {"schema": SCHEMA}
    strategy = validate(strategy)
    panel, dates, _ = panel_fixture(190)
    samples = build_samples(panel, dates, strategy)
    count = 180
    with threadpool_limits(limits=1):
        model = fit(spec, samples.X.iloc[:count], samples.y.iloc[:count], strategy["preprocess"],
                    automatic_metadata=samples.automatic_preprocessing,
                    training_dates=samples.meta.date.iloc[:count].tolist())
    training = {**model.audit, "trainStart": dates[61], "trainEnd": dates[120],
                "informationCutoff": dates[130], "labelEndMax": dates[126], "trainDates": 60}
    artifact = export_function(model, training, strategy)
    return model, artifact, samples, strategy, training


@pytest.mark.parametrize("estimator", ["ridge", "transformed_ridge"])
def test_v2_frozen_function_has_exact_pipeline_metadata_and_portable_parity(estimator):
    model, artifact, samples, strategy, _ = v2_fitted_fixture(candidates(estimator)[0])
    frozen = artifact["featureConstruction"]["automatic"]
    assert frozen == samples.automatic_preprocessing
    assert frozen["stateFeatures"] == "asset_returns_basket_gross/1"
    validate_function(artifact)
    test = samples.X.iloc[200:204][model.columns].copy()
    test.iloc[1, :] = np.nan
    expected = model.predict(test)
    np.testing.assert_allclose(predict_function(artifact, rows(test))["normalizedChanges"], expected, atol=1e-11, rtol=1e-10)
    broken = copy.deepcopy(frozen)
    broken["factors"][0]["sourceUnit"] = "x"*81
    with pytest.raises(ValueError):
        validate_metadata(broken, strategy["factors"])


def test_exported_quantity_registry_and_catalog_descriptors_match_python():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("export_quantities", root / "scripts/export-factor-quantity-fields.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    expected = module.registry()
    assert json.loads((root / "engine/atlas_quant/factor_quantity_fields.json").read_text()) == expected
    catalog = json.loads((root / "engine/atlas_quant/catalog.json").read_text())
    assert len(catalog["factors"]) > 5000
    for factor in catalog["factors"]:
        assert factor["automaticProcessing"] == {"schema": SCHEMA, "status": "defined", "descriptor": descriptor(factor)}
