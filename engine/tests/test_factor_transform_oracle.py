"""Independent normalization policy and numeric family oracle; no provider/F calls."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from atlas_quant.engine import ResearchError
from atlas_quant.factors import evaluate_expression
from atlas_quant.statistical_quant.preprocessing import transform_values, PRICE_TRANSFORM
from atlas_quant.statistical_quant.targets import _features
from atlas_quant.statistical_quant.typed_preprocessing import descriptor
from factor_transform_reference import economic_values, expected_transform, expression_values, state_features


CATALOG = json.loads((Path(__file__).parents[1]/"atlas_quant/catalog.json").read_text())
UNITS = {x["id"]: x["unit"] for x in CATALOG["fieldRegistry"]}
FAMILIES = {x["family"] for x in CATALOG["factors"]}


def test_entire_catalog_against_independently_reviewed_family_policy():
    assert len(CATALOG["factors"]) >= 5967
    for factor in CATALOG["factors"]:
        result = descriptor(factor)
        assert result["transform"]["kind"] == expected_transform(factor, UNITS), factor["id"]
        global_ = all(field.startswith("ext_ctx_") for field in factor["requiredFields"])
        assert result["scope"] == ("global" if global_ else "asset"), factor["id"]
        assert result["aggregation"] == ("global_once" if global_ else "signed_origin_dollar_over_gross")


def representatives():
    # One numeric case for every family and every distinct underlying unit set
    # and semantic transform. This deliberately includes signed monetary,
    # percent, ratio and per-share financial inputs rather than one generic FD.
    selected = {}
    for factor in CATALOG["factors"]:
        key = (factor["family"], tuple(sorted({UNITS[x] for x in factor["requiredFields"]})),
               expected_transform(factor, UNITS))
        selected.setdefault(key, factor)
    assert {factor["family"] for factor in selected.values()} == FAMILIES
    return list(selected.values())


def fixture_fields(required, count=142):
    t = np.arange(count, dtype=float)[:, None]
    s = np.arange(3, dtype=float)[None, :]
    price = (20+5*s)*np.exp(.001*t+.035*np.sin(t*.27+s))
    values = {"close": price.copy()}
    for index, field in enumerate(required):
        unit = UNITS[field]
        if field == "close": continue
        if field == "open": value = price*(1+.006*np.sin(t*.3))
        elif field == "high": value = price*1.017
        elif field == "low": value = price*.981
        elif field == "raw_close": value = price/1.2
        elif unit in {"adjusted_research_price", "index_points", "adjusted_USD_per_share", "USD_per_share"}:
            value = np.broadcast_to(900*np.exp(.0013*t+.02*np.cos(t*.31)), price.shape).copy()
        elif field.endswith(("_mv", "_share")): value = 1e5*(2+s+.1*np.sin(t*.11))
        elif unit in {"currency_per_share", "CNY"}: value = (2 if unit == "currency_per_share" else 1e6)*(np.sin(t*.09+s)-.3)
        elif unit == "percent": value = 12*np.sin(t*.1+s)+5
        elif field.endswith(("amount", "vol")): value = 2000*(2+s+np.sin(t*.08))
        else: value = 15+2*s+np.sin(t*.13)
        values[field] = np.asarray(value).copy()
        if field.startswith("ext_ctx_"):
            values[field][:] = values[field][:, :1]
    # Missing calendar sessions must remain gaps through lag/rolling/returns.
    for field in values: values[field][38, 1] = np.nan
    return values


def frame_for(fields):
    length, assets = fields["close"].shape
    index = pd.MultiIndex.from_product([pd.date_range("2022-01-03", periods=length, freq="B").strftime("%Y%m%d"),
                                        [f"S{i}" for i in range(assets)]], names=["trade_date", "ts_code"])
    return pd.DataFrame({key: value.ravel() for key, value in fields.items()}, index=index)


@pytest.mark.parametrize("factor", representatives(), ids=lambda f: f["id"])
def test_each_family_recipe_and_semantic_transform_against_independent_arrays(factor):
    fields = fixture_fields(factor["requiredFields"])
    raw_expected = expression_values(factor["expression"], fields)
    expected = economic_values(raw_expected, expected_transform(factor, UNITS))*factor["direction"]
    raw_actual = evaluate_expression(factor["expression"], frame_for(fields)).unstack("ts_code")
    actual = transform_values(raw_actual, descriptor(factor)["transform"])*factor["direction"]
    np.testing.assert_allclose(raw_actual.to_numpy(), raw_expected, rtol=2e-9, atol=2e-11, equal_nan=True)
    np.testing.assert_allclose(actual.to_numpy(), expected, rtol=2e-9, atol=2e-11, equal_nan=True)
    assert np.isfinite(expected).sum() > 100


@pytest.mark.parametrize("expression,transform", [
    ("close", "simple_return"), ("+close", "simple_return"), ("close*1", "simple_return"),
    ("lag(close,1)", "simple_return"), ("ts_mean(close,20)", "simple_return"),
    ("log(close)", "first_difference"), ("ts_mean(log(close),20)", "first_difference"),
    ("delta(log(close),5)", "identity"), ("log(close)-log(lag(close,1))", "identity"),
    ("close/lag(close,1)-1", "identity"), ("(close-ts_mean(close,20))/close", "identity"),
    ("fd_netprofit_margin/100", "identity"), (".01*fd_netprofit_margin", "identity"),
    ("delta(fd_netprofit_margin,20)", "percent_to_fraction"), ("fd_eps/raw_close", "identity"),
])
def test_compound_numeric_quantity_is_not_transformed_twice(expression, transform):
    fields = fixture_fields(["close", "fd_netprofit_margin", "fd_eps", "raw_close"])
    factor = {"id": "audit", "expression": expression, "direction": 1}
    actual_descriptor = descriptor(factor)
    assert actual_descriptor["transform"]["kind"] == transform
    raw = expression_values(expression, fields)
    expected = economic_values(raw, transform)
    actual = transform_values(evaluate_expression(expression, frame_for(fields)).unstack("ts_code"), actual_descriptor["transform"])
    np.testing.assert_allclose(actual, expected, atol=1e-10, rtol=1e-10, equal_nan=True)


@pytest.mark.parametrize("family", ["mean_reversion", "pair_reversion", "trend", "fundamental", "event"])
@pytest.mark.parametrize("asset", [True, False])
def test_all_mechanism_states_and_signed_basket_invariance(family, asset):
    t = np.arange(61)
    prices = (20*np.exp(.003*t+.05*np.sin(.3*t)))[:, None] if asset else np.column_stack((20+np.sin(t*.4), 20+np.cos(t*.3)))
    q = np.array([1.]) if asset else np.array([1., -1.])
    strategy = {"model": {"family": family}, "target": {"kind": "asset_price" if asset else "frozen_basket"}, "factors": []}
    metadata = {"stateFeatures": "asset_returns_basket_gross/1", "factors": []}
    def actual(p, quantities, meta):
        return _features(p, quantities, np.abs(p[-1]*quantities).sum(), {}, strategy, meta)[0]
    expected = state_features(prices, q, family, asset)
    result = actual(prices, q, metadata)
    assert result == pytest.approx(expected, rel=1e-11, abs=1e-13)
    assert actual(prices*17, q, metadata) == pytest.approx(expected, rel=1e-11, abs=1e-13)
    if not asset:
        assert min(prices@q) < 0 < max(prices@q)
        assert result == actual(prices, q, None), "basket v2 must preserve positive-gross denominator"
    assert actual(prices, q, None) == pytest.approx(state_features(prices, q, family, asset, version=1), rel=1e-11, abs=1e-13)


@pytest.mark.parametrize("expression", ["close-ts_mean(close,20)", "delta(close,1)", "ts_std(close,20)"])
def test_price_difference_cannot_masquerade_as_dimensionless_return(expression):
    with pytest.raises(ResearchError) as error:
        descriptor({"id": "audit", "expression": expression, "direction": 1})
    assert error.value.code == "FACTOR_TRANSFORM_REQUIRED"


def test_return_and_log_return_are_invariant_to_positive_price_rebasing():
    price = fixture_fields(["close"])["close"]
    for expression in ("close", "log(close)", "(close-ts_mean(close,20))/close"):
        factor = {"id": "audit", "expression": expression, "direction": 1}
        transform = descriptor(factor)["transform"]
        results = []
        for scale in (1., .001, 1e6):
            raw = evaluate_expression(expression, frame_for({"close": price*scale})).unstack("ts_code")
            results.append(transform_values(raw, transform).to_numpy())
        for result in results[1:]:
            np.testing.assert_allclose(result, results[0], atol=1e-12, rtol=1e-10, equal_nan=True)


@pytest.mark.parametrize("transform", [
    {"kind": "identity"}, {"kind": "log_positive", "invalid": "missing"},
    {"kind": "log1p_nonnegative", "invalid": "missing"},
    {"kind": "reciprocal_nonzero", "invalid": "missing"},
    {"kind": "percent_to_fraction", "divisor": 100},
    {"kind": "signed_log1p", "referenceUnit": 1, "invalid": "missing"},
    {"kind": "first_difference", "lag": 1, "invalid": "missing"},
    {"kind": "simple_return", "lag": 1, "invalid": "missing"},
    {"kind": "log_return", "lag": 1, "invalid": "missing"}, PRICE_TRANSFORM,
], ids=lambda x: x["kind"])
def test_every_economic_transform_domain_against_scalar_reference(transform):
    t = np.arange(80, dtype=float)
    values = np.column_stack((20*np.exp(.001*t+.03*np.sin(t)), np.full(80, 15.)))
    values[26:32, 0] = [np.nan, 0., -2., np.inf, -np.inf, -0.]
    expected = economic_values(values, transform["kind"])
    actual = transform_values(pd.DataFrame(values), transform).to_numpy()
    np.testing.assert_array_equal(np.isfinite(actual), np.isfinite(expected))
    np.testing.assert_allclose(actual, expected, rtol=2e-10, atol=2e-11, equal_nan=True)


def test_lagged_volatility_cannot_include_current_or_future_shock():
    t = np.arange(80, dtype=float)
    values = (20*np.exp(.001*t+.03*np.sin(t)))[:, None]
    expected = economic_values(values, "return_over_trailing_volatility")
    changed = values.copy()
    changed[50:] *= 4
    actual = transform_values(pd.DataFrame(changed), PRICE_TRANSFORM).to_numpy()
    np.testing.assert_allclose(actual[:50], expected[:50], rtol=1e-10, atol=1e-12, equal_nan=True)
    previous_returns = [values[i, 0]/values[i-1, 0]-1 for i in range(30, 50)]
    independent_denominator = np.std(previous_returns, ddof=1)
    assert actual[50, 0] == pytest.approx((changed[50, 0]/changed[49, 0]-1)/independent_denominator)
