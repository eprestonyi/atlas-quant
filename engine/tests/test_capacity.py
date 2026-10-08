"""Full-pool capacity is a new explicit admission, with a reference oracle."""

import copy
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from atlas_quant.engine import _prepare_data, ResearchError, run_research
from atlas_quant.fixtures import make_demo_data
from atlas_quant.factors import evaluate_expression
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import build_samples
from atlas_quant.capacity import (
    PROFILE_ID,
    PanelStore,
    FeatureGraph,
    build_asset_samples,
)


def strategy():
    return {
        "schemaVersion": 2,
        "name": "capacity oracle",
        "universe": {
            "symbols": [
                "000001.SZ",
                "000002.SZ",
                "600000.SH",
                "600036.SH",
                "600519.SH",
            ],
            "start": "20230101",
            "end": "20250930",
        },
        "research": {"mode": "statistical_quant", "observationDays": 1},
        "target": {"kind": "asset_price", "horizonSessions": 5},
        "model": {"family": "mean_reversion", "estimator": "ridge"},
        "execution": {"enabled": False},
        "factors": [
            {
                "id": "rank5",
                "expression": "rank(returns(close,5))",
                "direction": -1,
                "role": "predictor",
            },
            {
                "id": "nested",
                "expression": "ts_mean(zscore(delta(close,2)),5)",
                "direction": 1,
                "role": "predictor",
            },
        ],
    }


@pytest.fixture
def source():
    s = validate(strategy(), capacity_profile=PROFILE_ID)
    data, provenance = make_demo_data(s)
    # Missing complete session, ties, and one optional missing factor value.
    data = data.drop(data.index[101]).copy()
    return s, data, provenance


def setup(source, tmp_path):
    s, data, p = source
    panel, dates, audit = _prepare_data(data, s, p)
    store = PanelStore.from_prepared(
        panel,
        dates,
        sorted(s["universe"]["symbols"]),
        audit,
        p,
        tmp_path / "panels",
        max_bytes=100 * 1024 * 1024,
    )
    return panel, dates, store


def test_profile_is_explicit_and_default_limits_are_unchanged():
    s = strategy()
    s["universe"]["symbols"] = [f"{100000+i:06d}.SZ" for i in range(300)]
    with pytest.raises(ResearchError):
        validate(s)
    assert len(validate(s, capacity_profile=PROFILE_ID)["universe"]["symbols"]) == 300
    for change in (
        {"execution": {"enabled": True}},
        {"model": {"family": "trend", "estimator": "auto"}},
        {"validation": {"innerFolds": 3}},
    ):
        invalid = copy.deepcopy(s)
        invalid.update(change)
        with pytest.raises(ResearchError) as exc:
            validate(invalid, capacity_profile=PROFILE_ID)
        assert exc.value.code == "CAPACITY_PROFILE"
    with pytest.raises(ResearchError):
        validate(s, capacity_profile="unlimited")


@pytest.mark.parametrize(
    "expression",
    [
        "rank(close)",
        "zscore(close)",
        "ts_mean(rank(returns(close,5)),20)",
        "rank(ts_mean(returns(close,5),20))",
        "ts_rank(close,5)",
        "ts_std(close,10)",
        "lag(close,2)",
        "delta(close,5)",
        "ts_min(close,4)+ts_max(close,3)",
        "clip(sign(log(close))+sqrt(abs(delta(close,1))),-2,3)",
        "min(close,lag(close,1))/max(close,lag(close,2))",
        "sign(close*1000000*1000000*1000000)",
    ],
)
def test_graph_matches_original_dsl_exactly_across_uneven_blocks(
    source, tmp_path, expression
):
    s, _, _ = source
    panel, dates, store = setup(source, tmp_path)
    factor = {
        "id": "test",
        "expression": expression,
        "direction": -1,
        "role": "predictor",
    }
    graph = FeatureGraph.compile([factor])
    result = graph.evaluate(store, tmp_path / "features", symbol_block=2, date_block=17)
    expected = (
        (evaluate_expression(expression, panel) * -1)
        .to_numpy()
        .reshape(len(dates), len(store.symbols))
    )
    np.testing.assert_array_equal(result["test"], expected)
    again = graph.evaluate(store, tmp_path / "features", symbol_block=3, date_block=113)
    assert result.identity == again.identity
    np.testing.assert_array_equal(again["test"], expected)


def test_full_pool_rank_is_not_concatenated_local_ranks(source, tmp_path):
    _, _, store = setup(source, tmp_path)
    f = {"id": "rank", "expression": "rank(close)", "direction": 1, "role": "predictor"}
    result = FeatureGraph.compile([f]).evaluate(store, tmp_path / "f")
    date = 200
    prices = store.field("close")[date]
    expected = pd.Series(prices).rank(pct=True).to_numpy()
    split = np.r_[
        pd.Series(prices[:2]).rank(pct=True), pd.Series(prices[2:]).rank(pct=True)
    ]
    np.testing.assert_array_equal(result["rank"][date], expected)
    assert not np.array_equal(expected, split)


def test_asset_builder_matches_every_reference_sample_and_order(source, tmp_path):
    s, _, _ = source
    panel, dates, store = setup(source, tmp_path)
    features = FeatureGraph.compile(s["factors"]).evaluate(store, tmp_path / "features")
    expected = build_samples(panel, dates, s)
    actual = build_asset_samples(
        store, features, s, tmp_path / "samples", max_samples=250000
    )
    pd.testing.assert_frame_equal(actual.X, expected.X, check_exact=True)
    pd.testing.assert_frame_equal(actual.y, expected.y, check_exact=True)
    pd.testing.assert_frame_equal(actual.meta, expected.meta, check_exact=True)
    assert (
        actual.definitions == expected.definitions
        and actual.hedge_fits == expected.hedge_fits
    )
    assert actual.start_index == expected.start_index and actual.dates == expected.dates
    assert not actual.X.to_numpy(copy=False).flags.writeable


def test_cache_content_and_manifest_tampering_are_rejected(source, tmp_path):
    s, _, _ = source
    _, _, store = setup(source, tmp_path)
    assert (
        isinstance(store.field("close"), np.memmap)
        and not store.field("close").flags.writeable
    )
    result = FeatureGraph.compile(s["factors"]).evaluate(store, tmp_path / "features")
    manifest_path = result.root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["outputs"]["rank5"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ResearchError):
        FeatureGraph.compile(s["factors"]).evaluate(store, tmp_path / "features")
    field = store.root / store.manifest["fields"]["close"]["file"]
    with field.open("r+b") as f:
        f.seek(-1, 2)
        f.write(b"!")
    with pytest.raises(ResearchError):
        PanelStore.open(store.root)


def test_content_missingness_pool_and_provenance_all_change_cache_identity(
    source, tmp_path
):
    s, data, p = source
    identities = []
    for case in range(4):
        d = data.copy()
        u = copy.deepcopy(s)
        v = copy.deepcopy(p)
        if case == 1:
            d = d.iloc[1:].copy()
        if case == 2:
            u["universe"]["symbols"] = u["universe"]["symbols"][:-1]
        if case == 3:
            v["source"] = "different-source-evidence"
        store, _ = PanelStore.prepare(d, u, v, tmp_path / "p", profile_id=PROFILE_ID)
        identities.append(store.identity)
    assert len(set(identities)) == 4


def test_pit_future_availability_is_rejected_before_cache_creation(source, tmp_path):
    s, data, p = copy.deepcopy(source)
    s["factors"] = [
        {
            "id": "external",
            "expression": "ext_example",
            "role": "predictor",
            "direction": 1,
        }
    ]
    data["ext_example"] = 1.0
    data["ext_example__available_date"] = "20990101"
    p["externalFields"] = {
        "ext_example": {
            "availabilityPolicy": "point_in_time_asof",
            "dataType": "number",
            "source": "fixture",
            "path": "test/value",
            "availableDateColumn": "ext_example__available_date",
        }
    }
    with pytest.raises(ResearchError) as exc:
        PanelStore.prepare(data, s, p, tmp_path / "p", profile_id=PROFILE_ID)
    assert exc.value.code == "FUTURE_EXTERNAL_FIELD"
    assert not (tmp_path / "p").exists()


def test_same_pooled_two_output_forecast_and_factor_baseline_are_exact(
    source, tmp_path
):
    from atlas_quant.capacity import run_capacity_research

    s, data, p = source
    reference = run_research(s, data, p)
    plans = []
    actual = run_capacity_research(
        s,
        data,
        p,
        profile_id=PROFILE_ID,
        cache_dir=tmp_path / "capacity",
        plan_sink=plans.append,
    )
    assert actual["forecasts"] == reference["forecasts"]
    assert actual["validation"] == reference["validation"]
    assert actual["selection"] == reference["selection"]
    assert actual["capacity"]["modelScope"] == "pooled_all_symbols"
    assert (
        len(plans) == 1 and len(plans[0]["origins"]) == actual["forecasts"]["totalRows"]
    )


def test_future_data_does_not_change_earlier_feature_values(source, tmp_path):
    s, data, p = source
    a, _ = PanelStore.prepare(data, s, p, tmp_path / "panels", profile_id=PROFILE_ID)
    graph = FeatureGraph.compile(s["factors"])
    before = graph.evaluate(a, tmp_path / "features")
    changed = data.copy()
    cutoff = p["tradingDates"][400]
    for field in ["open", "high", "low", "close", "raw_close"]:
        changed.loc[changed.trade_date > cutoff, field] *= 1.7
    b, _ = PanelStore.prepare(changed, s, p, tmp_path / "panels", profile_id=PROFILE_ID)
    after = graph.evaluate(b, tmp_path / "features")
    assert before.identity != after.identity
    for factor in s["factors"]:
        np.testing.assert_array_equal(
            before[factor["id"]][:401], after[factor["id"]][:401]
        )


def test_cross_section_ties_missing_values_and_negative_direction_are_exact(
    source, tmp_path
):
    s, data, p = source
    date = p["tradingDates"][200]
    members = sorted(s["universe"]["symbols"])
    data = data.copy()
    same = data.trade_date.eq(date) & data.ts_code.isin(members[:3])
    for field, value in [
        ("open", 50.0),
        ("close", 50.0),
        ("raw_close", 50.0),
        ("low", 49.0),
        ("high", 51.0),
    ]:
        data.loc[same, field] = value
    data = data[~(data.trade_date.eq(date) & data.ts_code.eq(members[-1]))]
    s["factors"] = [
        {
            "id": "rank",
            "expression": "rank(close)",
            "direction": -1,
            "role": "predictor",
        }
    ]
    panel, dates, store = setup((s, data, p), tmp_path)
    f = FeatureGraph.compile(s["factors"]).evaluate(
        store, tmp_path / "features", date_block=1, symbol_block=2
    )
    expected = (
        -evaluate_expression("rank(close)", panel)
        .to_numpy()
        .reshape(len(dates), len(members))
    )
    np.testing.assert_array_equal(f["rank"], expected)
    assert f["rank"][200, 0] == f["rank"][200, 1] == f["rank"][200, 2]
    assert np.isnan(f["rank"][200, -1])


@pytest.mark.parametrize("family", ["trend", "event", "fundamental"])
def test_family_state_and_external_missing_masks_match_reference(
    source, tmp_path, family
):
    s, data, p = copy.deepcopy(source)
    s["model"]["family"] = family
    data["fd_example"] = np.where(np.arange(len(data)) % 7 == 0, 2.0, 0.0)
    data.loc[data.index[::13], "fd_example"] = np.nan
    data["fd_example__available_date"] = data.trade_date
    p["externalFields"] = {
        "fd_example": {
            "availabilityPolicy": "point_in_time_asof",
            "dataType": "number",
            "source": "synthetic",
            "path": "source/fixture",
            "availableDateColumn": "fd_example__available_date",
        }
    }
    s["factors"] = [
        {
            "id": "external",
            "expression": "fd_example",
            "direction": 1,
            "role": "event" if family == "event" else "predictor",
        }
    ]
    s = validate(s, capacity_profile=PROFILE_ID)
    panel, dates, store = setup((s, data, p), tmp_path)
    f = FeatureGraph.compile(s["factors"]).evaluate(store, tmp_path / "features")
    reference = build_samples(panel, dates, s)
    actual = build_asset_samples(store, f, s, tmp_path / "samples", max_samples=250000)
    for key in ("X", "y", "meta"):
        pd.testing.assert_frame_equal(
            getattr(actual, key), getattr(reference, key), check_exact=True
        )


def test_future_perturbation_preserves_earlier_conditional_predictions(
    source, tmp_path
):
    from atlas_quant.capacity import run_capacity_research

    s, data, p = source
    original = run_capacity_research(
        s, data, p, profile_id=PROFILE_ID, cache_dir=tmp_path / "a"
    )
    rows = original["forecasts"]["rows"]
    cutoff = rows[len(rows) // 2]["date"]
    changed = data.copy()
    for field in ("open", "high", "low", "close", "raw_close"):
        changed.loc[changed.trade_date > cutoff, field] *= 1.9
    revised = run_capacity_research(
        s, changed, p, profile_id=PROFILE_ID, cache_dir=tmp_path / "b"
    )
    keys = ("forecastId", "modelFitId", "date", "expectedEntry", "expectedFuture")
    before = [{k: r[k] for k in keys} for r in rows if r["date"] <= cutoff]
    after = [
        {k: r[k] for k in keys}
        for r in revised["forecasts"]["rows"]
        if r["date"] <= cutoff
    ]
    assert before == after


def test_row_input_order_is_normalized_but_expression_and_direction_change_key(
    source, tmp_path
):
    s, data, p = source
    first, _ = PanelStore.prepare(
        data, s, p, tmp_path / "panels", profile_id=PROFILE_ID
    )
    reordered, _ = PanelStore.prepare(
        data.iloc[::-1], s, p, tmp_path / "panels", profile_id=PROFILE_ID
    )
    assert first.identity == reordered.identity
    expression = FeatureGraph.compile(
        [{"id": "a", "expression": "rank(close)", "direction": 1}]
    )
    opposite = FeatureGraph.compile(
        [{"id": "a", "expression": "rank(close)", "direction": -1}]
    )
    altered = FeatureGraph.compile(
        [{"id": "a", "expression": "rank(lag(close,1))", "direction": 1}]
    )
    assert len({expression.identity, opposite.identity, altered.identity}) == 3


@pytest.mark.parametrize("boundary", ["before_fit", "after_fit"])
def test_resource_rejection_is_not_swallowed_as_invalid_model_trial(
    source, tmp_path, monkeypatch, boundary
):
    from atlas_quant.capacity import run_capacity_research
    from atlas_quant.capacity.core import FitRuntime

    s, data, p = source

    def reject(*args):
        raise ResearchError("CAPACITY_MEMORY", "Synthetic resource boundary rejection")

    monkeypatch.setattr(FitRuntime, boundary, reject)
    with pytest.raises(ResearchError) as exc:
        run_capacity_research(
            s, data, p, profile_id=PROFILE_ID, cache_dir=tmp_path / "cache"
        )
    assert exc.value.code == "CAPACITY_MEMORY"
