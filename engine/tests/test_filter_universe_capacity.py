"""Whole-pool profile admission and cross-sectional domain; no provider/fit."""

import copy
import numpy as np
import pandas as pd
import pytest
from atlas_quant.capacity.profiles import (
    get_profile,
    FULL_FILTER_PROFILE_ID,
    PROFILE_ID,
)
from atlas_quant.capacity import PanelStore, FeatureGraph
from atlas_quant.capacity.benchmark import benchmark_strategy
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.engine import ResearchError


def strategy():
    s = benchmark_strategy()
    s["universe"] = {
        "symbols": [f"{100000+i:06d}.SZ" for i in range(1000)],
        "start": "20240101",
        "end": "20241231",
    }
    s["model"].update(trainWindow=120, refitDays=20)
    s["validation"]["minTrainDates"] = 40
    return s


def test_1000_profile_is_explicit_and_does_not_enlarge_old_routes():
    s = strategy()
    normalized = validate(s, capacity_profile=FULL_FILTER_PROFILE_ID)
    assert normalized["universe"]["symbols"] == s["universe"]["symbols"]
    for profile in [None, PROFILE_ID]:
        with pytest.raises(ResearchError):
            validate(s, capacity_profile=profile)
    assert get_profile(PROFILE_ID).max_symbols == 300
    assert get_profile(FULL_FILTER_PROFILE_ID).max_wall_seconds == 900


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["universe"]["symbols"].append("999999.SZ"),
        lambda s: s["universe"].update(start="20230101"),
        lambda s: s["model"].update(estimator="auto"),
        lambda s: s["model"].update(estimator="hist_gradient_boosting"),
        lambda s: s["model"].update(family="fundamental"),
        lambda s: s["execution"].update(enabled=True),
        lambda s: s["validation"].update(innerFolds=3),
        lambda s: s["model"].update(refitDays=1),
    ],
)
def test_unproved_combination_fails_without_changing_requested_scope(change):
    s = strategy()
    change(s)
    before = copy.deepcopy(s)
    with pytest.raises(ResearchError):
        validate(s, capacity_profile=FULL_FILTER_PROFILE_ID)
    assert s == before


def test_thousand_member_rank_zscore_use_full_pool_even_with_small_storage_blocks(
    tmp_path,
):
    s = strategy()
    s["factors"] = [
        {"id": "rank", "expression": "rank(close)"},
        {"id": "z", "expression": "zscore(close)"},
    ]
    dates = ["20240102", "20240103", "20240104"]
    symbols = s["universe"]["symbols"]
    rows = []
    for day_index, day in enumerate(dates):
        for i, symbol in enumerate(symbols):
            if day_index == 1 and i in {0, 50, 999}:
                continue
            close = float(i // 2 + 10 + day_index)
            rows.append(
                {
                    "ts_code": symbol,
                    "trade_date": day,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "raw_close": close,
                    "vol": 100.0,
                    "amount": close * 10,
                    "adj_factor": 1.0,
                }
            )
    data = pd.DataFrame(rows)
    calendar = pd.bdate_range("2024-01-02", periods=180).strftime("%Y%m%d").tolist()
    p = {"source": "SYNTHETIC_DOMAIN_TEST", "synthetic": True, "tradingDates": calendar}
    s = validate(s, capacity_profile=FULL_FILTER_PROFILE_ID)
    store, audit = PanelStore.prepare(
        data, s, p, tmp_path / "panel", profile_id=FULL_FILTER_PROFILE_ID
    )
    features = FeatureGraph.compile(s["factors"]).evaluate(
        store, tmp_path / "features", symbol_block=17, date_block=1
    )
    assert (
        store.symbols == symbols
        and audit["expectedRows"] == 180000
        and audit["observedRows"] == 2997
    )
    for row_index, day in enumerate(dates):
        observed = (
            data[data.trade_date.eq(day)].set_index("ts_code").close.reindex(symbols)
        )
        expected_rank = observed.rank(pct=True, method="average").to_numpy()
        expected_z = ((observed - observed.mean()) / observed.std(ddof=0)).to_numpy()
        np.testing.assert_array_equal(features["rank"][row_index], expected_rank)
        np.testing.assert_allclose(
            features["z"][row_index], expected_z, rtol=1e-13, atol=1e-13, equal_nan=True
        )
    assert features["rank"][0, 49] < 0.06, "rank must not restart in fifty-stock chunks"
    assert np.isnan(features["rank"][1, 50]) and np.isnan(features["z"][1, 999])
