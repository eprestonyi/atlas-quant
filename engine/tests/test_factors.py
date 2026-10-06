import numpy as np
import pandas as pd
import pytest

from atlas_quant.factors import FactorError, evaluate_expression, load_catalog, validate_expression


@pytest.fixture
def panel():
    dates = pd.bdate_range("2023-01-01", periods=120).strftime("%Y%m%d")
    index = pd.MultiIndex.from_product([dates, ["000001.SZ", "000002.SZ", "600000.SH"]], names=["trade_date", "ts_code"])
    rng = np.random.default_rng(19)
    close = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, (120, 3)), axis=0)).ravel()
    frame = pd.DataFrame({"close": close, "open": close * 0.997, "high": close * 1.01, "low": close * 0.99, "vol": rng.uniform(10, 100, len(index)), "amount": rng.uniform(100, 1000, len(index))}, index=index)
    return frame


@pytest.mark.parametrize("expression", [
    "__import__('os')", "close.__class__", "close[0]", "lag(close,-1)",
    "lag(close,0)", "lag(close,True)", "lag(close,253)", "lag(close,1.1)",
    "ts_mean(close,window=3)", "close ** 2", "close > open", "[close]",
    "float('nan')", "999999999 * close", "clip(close,3,2)", "1",
    "lambda: close", "returns(close,1) if close else open", "lag(lag(lag(close,252),252),252)",
])
def test_restricted_ast_rejects_unsafe_or_future_expressions(expression):
    with pytest.raises(FactorError):
        validate_expression(expression)


def test_catalog_metadata_and_all_factors_causal(panel):
    catalog = load_catalog()
    assert 15 <= len(catalog["factors"]) <= 25
    assert len({f["id"] for f in catalog["factors"]}) == len(catalog["factors"])
    future = panel.copy()
    boundary = panel.index.get_level_values("trade_date").unique()[90]
    later = future.index.get_level_values("trade_date") >= boundary
    future.loc[later, :] *= 17
    for factor in catalog["factors"]:
        metadata = validate_expression(factor["expression"])
        assert metadata["lookback"] == factor["lookback"]
        before = evaluate_expression(factor["expression"], panel)
        after = evaluate_expression(factor["expression"], future)
        pd.testing.assert_series_equal(before.loc[~later], after.loc[~later])
        assert np.isfinite(before.dropna()).all()


def test_lag_is_per_symbol_and_calendar_missing_rows_not_skipped(panel):
    days = panel.index.get_level_values("trade_date").unique()
    damaged = panel.copy()
    damaged.loc[(days[7], "000001.SZ"), :] = np.nan
    lag = evaluate_expression("lag(close,1)", damaged)
    assert pd.isna(lag.loc[(days[8], "000001.SZ")])
    assert lag.loc[(days[8], "000002.SZ")] == damaged.loc[(days[7], "000002.SZ"), "close"]
    rolling = evaluate_expression("ts_mean(close,3)", damaged)
    assert pd.isna(rolling.loc[(days[9], "000001.SZ")])
    assert np.isfinite(rolling.loc[(days[10], "000001.SZ")])
    assert pd.isna(lag.loc[(days[7], "000001.SZ")])


def test_cross_sectional_rank_is_contemporaneous(panel):
    actual = evaluate_expression("rank(close)", panel)
    expected = panel.close.groupby(level="trade_date").rank(pct=True)
    pd.testing.assert_series_equal(actual, expected)


def test_invalid_domains_become_missing_not_infinite(panel):
    for expr in ("close/(close-close)", "log(-close)", "sqrt(-close)"):
        assert evaluate_expression(expr, panel).isna().all()


def test_field_requirement_and_frame_order(panel):
    assert validate_expression("ts_mean(close,5)/lag(open,2)")["fields"] == ["close", "open"]
    with pytest.raises(FactorError):
        evaluate_expression("pe_ttm", panel)
    with pytest.raises(FactorError):
        evaluate_expression("close", panel.iloc[::-1])
