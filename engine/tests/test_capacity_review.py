"""Independent small capacity counterexamples; no provider, queue, or large fit."""

import numpy as np
import pandas as pd
import pytest

from atlas_quant.capacity import (
    PROFILE_ID,
    FeatureGraph,
    PanelStore,
    build_asset_samples,
)
from atlas_quant.engine import ResearchError, _prepare_data
from atlas_quant.factors import evaluate_expression
from atlas_quant.fixtures import make_demo_data
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import build_samples
from test_capacity import strategy


def uneven(family="trend"):
    s = strategy()
    s["universe"]["end"] = "20231231"
    s["model"]["family"] = family
    s["factors"] = [
        {
            "id": "valuation",
            "expression": "fd_review" if family == "event" else "zscore(pb)",
            "direction": -1,
            "role": "event" if family == "event" else "predictor",
        },
        {
            "id": "mixed",
            "expression": "rank(ts_mean(pb,3))+zscore(close)",
            "direction": 1,
            "role": "predictor",
        },
    ]
    s = validate(s, capacity_profile=PROFILE_ID)
    data, p = make_demo_data(s)
    symbols = sorted(s["universe"]["symbols"])
    # One member has no observations; another arrives late; missing sessions
    # must remain on the complete pool/calendar axes rather than compress time.
    data = data[data.ts_code.ne(symbols[-1])].copy()
    data = data[
        ~(data.ts_code.eq(symbols[0]) & data.trade_date.lt(p["tradingDates"][120]))
    ]
    missing = data.ts_code.eq(symbols[1]) & data.trade_date.isin(
        p["tradingDates"][80:90]
    )
    data = data[~missing].copy()
    data["pb"] = np.resize(
        np.array([0.0, np.nan, np.inf, -np.inf, 1.0, 1.0, 2.0]), len(data)
    )
    if family == "event":
        data["fd_review"] = np.resize(np.array([0.0, np.nan, 1.0, -1.0]), len(data))
        data["fd_review__available_date"] = data.trade_date
        p["externalFields"] = {
            "fd_review": {
                "availabilityPolicy": "point_in_time_asof",
                "dataType": "number",
                "source": "SYNTHETIC_REVIEW_FIXTURE",
                "path": "test/event",
                "availableDateColumn": "fd_review__available_date",
            }
        }
    return s, data, p


def cached(source, root):
    s, data, p = source
    panel, dates, audit = _prepare_data(data, s, p, capacity_profile=PROFILE_ID)
    store = PanelStore.from_prepared(
        panel,
        dates,
        sorted(s["universe"]["symbols"]),
        audit,
        p,
        root / "panels",
        max_bytes=20 * 1024 * 1024,
    )
    graph = FeatureGraph.compile(s["factors"])
    features = graph.evaluate(store, root / "features", symbol_block=2, date_block=7)
    return s, panel, dates, store, graph, features


@pytest.mark.parametrize("family", ["mean_reversion", "trend", "fundamental", "event"])
def test_uneven_pool_nan_infinity_family_masks_match_reference_exactly(
    tmp_path, family
):
    s, panel, dates, store, _, features = cached(uneven(family), tmp_path)
    for factor in s["factors"]:
        reference = (
            (evaluate_expression(factor["expression"], panel) * factor["direction"])
            .to_numpy()
            .reshape(len(dates), len(store.symbols))
        )
        np.testing.assert_array_equal(features[factor["id"]], reference)
    reference = build_samples(panel, dates, s)
    actual = build_asset_samples(
        store, features, s, tmp_path / "samples", max_samples=10000
    )
    for key in ("X", "y", "meta"):
        pd.testing.assert_frame_equal(
            getattr(actual, key), getattr(reference, key), check_exact=True
        )
    assert actual.X.to_numpy(copy=False).flags.f_contiguous
    assert not actual.X.to_numpy(copy=False).flags.writeable
    assert actual.definitions == reference.definitions
    assert actual.hedge_fits == reference.hedge_fits
    assert len(actual.meta) == (len(dates) - actual.start_index) * len(store.symbols)
    assert actual.meta.targetDate.isna().sum() == 6 * len(store.symbols)


def test_rank_and_zscore_use_all_51_symbols_not_fifty_stock_partitions(tmp_path):
    s, data, p = uneven()
    template = data[data.ts_code.eq(sorted(s["universe"]["symbols"])[1])].copy()
    symbols = [f"{100000 + i:06d}.SZ" for i in range(51)]
    blocks = []
    for i, symbol in enumerate(symbols):
        block = template.copy()
        block["ts_code"] = symbol
        for field in ("open", "high", "low", "close", "raw_close"):
            block[field] *= 1 + i / 10
        blocks.append(block)
    s["universe"]["symbols"] = symbols
    s["factors"] = [
        {"id": "r", "expression": "rank(close)", "direction": 1, "role": "predictor"},
        {"id": "z", "expression": "zscore(close)", "direction": 1, "role": "predictor"},
    ]
    s = validate(s, capacity_profile=PROFILE_ID)
    s, panel, dates, store, _, features = cached((s, pd.concat(blocks), p), tmp_path)
    for factor in s["factors"]:
        expected = (
            evaluate_expression(factor["expression"], panel)
            .to_numpy()
            .reshape(len(dates), 51)
        )
        np.testing.assert_array_equal(features[factor["id"]], expected)
    day = next(i for i in range(len(dates)) if np.isfinite(features["r"][i]).all())
    assert features["r"][day, 0] == 1 / 51
    assert features["r"][day, 49] == 50 / 51
    assert features["r"][day, 50] == 1


def test_reused_feature_cache_still_obeys_callers_explicit_byte_budget(tmp_path):
    _, _, _, store, graph, features = cached(uneven(), tmp_path)
    assert features.manifest["totalBytes"] > 1
    # A cache hit must not silently disable the same max_bytes admission that
    # a cold cache enforces. This is a public exported component boundary.
    with pytest.raises(ResearchError) as error:
        graph.evaluate(store, tmp_path / "features", max_bytes=1)
    assert error.value.code == "CAPACITY_DISK"


def test_sample_staging_failure_removes_only_its_unpublished_files(
    tmp_path, monkeypatch
):
    import atlas_quant.capacity.asset_samples as module

    s, _, _, store, _, features = cached(uneven(), tmp_path)
    protected = tmp_path / "existing-proof.txt"
    protected.write_text("do not remove")

    def reject(*args, **kwargs):
        raise OSError("synthetic fsync/storage failure after arrays were written")

    monkeypatch.setattr(module, "file_hash", reject)
    with pytest.raises(OSError, match="synthetic"):
        build_asset_samples(store, features, s, tmp_path / "samples", max_samples=10000)
    assert not (tmp_path / "samples").exists()
    assert protected.read_text() == "do not remove"
    assert not list(
        tmp_path.glob("stage_samples_*")
    ), "failed sample construction leaves unbounded retry debris"


@pytest.mark.parametrize("filename", ["../outside.npy", "/absolute.npy"])
def test_array_descriptor_paths_cannot_escape_cache(tmp_path, filename):
    from atlas_quant.capacity.panel_store import open_array

    with pytest.raises(ResearchError) as error:
        open_array(tmp_path, {"file": filename, "dtype": "<f8"})
    assert error.value.code == "CAPACITY_CACHE"


def test_baseline_changes_only_factor_columns_preserving_every_origin_and_label(
    tmp_path, monkeypatch
):
    import atlas_quant.statistical_quant.core as core
    import atlas_quant.statistical_quant.comparison as comparison

    s, _, dates, store, _, features = cached(uneven(), tmp_path)
    samples = build_asset_samples(
        store, features, s, tmp_path / "samples", max_samples=10000
    )
    captured = []
    runtime = object()

    def fake_forecast(actual, strategy, **limits):
        captured.append((actual, strategy, limits))
        return [], [], {}

    monkeypatch.setattr(core, "forecast", fake_forecast)
    monkeypatch.setattr(
        comparison, "compare_factor_increment", lambda *args: {"status": "TEST_ONLY"}
    )
    monkeypatch.setattr(
        core, "_envelope", lambda strategy, p, audit, artifact, panel, dates: artifact
    )
    core._research_from_samples(
        s,
        None,
        dates,
        {"dataSha256": "a" * 64},
        {},
        samples,
        max_forecasts=60000,
        runtime=runtime,
    )
    assert len(captured) == 2
    full, baseline = captured[0][0], captured[1][0]
    for key in ("y", "meta", "definitions", "dates", "hedge_fits"):
        assert getattr(baseline, key) is getattr(full, key)
    assert baseline.start_index == full.start_index
    pd.testing.assert_frame_equal(
        baseline.X,
        full.X.drop(columns=[c for c in full.X if c.startswith("factor:")]),
        check_exact=True,
    )
    assert captured[0][1] is captured[1][1]
    assert (
        captured[0][2] == captured[1][2] == {"max_forecasts": 60000, "runtime": runtime}
    )


@pytest.mark.parametrize(
    "failure",
    [
        MemoryError("test memory"),
        OSError("test disk"),
        ResearchError("CAPACITY_DISK", "test cap"),
    ],
)
def test_selection_does_not_turn_resource_exceptions_into_invalid_trials(
    monkeypatch, failure
):
    import atlas_quant.statistical_quant.validation as module

    monkeypatch.setattr(module, "folds", lambda *args: [(["20230101"], ["20230102"])])

    def reject(*args, **kwargs):
        raise failure

    monkeypatch.setattr(module, "_train", reject)
    with pytest.raises(type(failure)) as raised:
        module.select(
            None,
            [{"id": "test"}],
            [],
            {
                "validation": {"innerFolds": 2, "minTrainDates": 80},
                "target": {"horizonSessions": 5},
            },
        )
    assert raised.value is failure
