"""Separate trend auto admission and a three-symbol synthetic vertical check."""
from copy import deepcopy
import json
import math
import time

import pandas as pd
import pytest

from atlas_quant import runner
from atlas_quant.capacity import core
from atlas_quant.capacity.benchmark import benchmark_strategy, make_benchmark_data
from atlas_quant.capacity.profiles import (
    get_profile, TREND_AUTO_PROFILE_ID, AUTO_FILTER_CANDIDATE_ID, FULL_FILTER_PROFILE_ID,
)
from atlas_quant.engine import _prepare_data, ResearchError
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import build_samples
from atlas_quant.statistical_quant.model_function import predict_function
from atlas_quant.runner_claims import claim_request
from atlas_quant.market_acquisition.protocol import encode
from atlas_quant.market_research_runner.client import MarketResearchClient
from atlas_quant.market_research_runner.spool import MarketResearchSpool
from dataset_runner_support import config, JOB
from test_market_research_runner_review import market_source
from test_dataset_client import Response, Session


def strategy(count=3):
    s = benchmark_strategy()
    s["universe"] = {"symbols": [f"{100000+i:06d}.SZ" for i in range(count)], "start": "20240101", "end": "20241231"}
    s["model"].update(family="trend", estimator="auto", trainWindow=120, refitDays=20)
    s["validation"]["minTrainDates"] = 40
    return s


def test_trend_is_separate_from_old_mean_auto_and_ridge_profiles():
    s = strategy(1000)
    assert len(validate(s, capacity_profile=TREND_AUTO_PROFILE_ID)["universe"]["symbols"]) == 1000
    for profile in (AUTO_FILTER_CANDIDATE_ID, FULL_FILTER_PROFILE_ID):
        with pytest.raises(ResearchError):
            validate(s, capacity_profile=profile)
    trend, mean = get_profile(TREND_AUTO_PROFILE_ID).to_dict(), get_profile(AUTO_FILTER_CANDIDATE_ID).to_dict()
    assert {k:v for k,v in trend.items() if k != "id"} == {k:v for k,v in mean.items() if k != "id"}


@pytest.mark.parametrize("change", ["mean", "event", "fundamental", "pair", "ridge", "execution", "1001", "367days", "17factors", "refit", "inner", "outer"])
def test_profile_rejects_every_unsupported_combination_without_slicing(change):
    s = strategy(1000)
    if change in {"mean", "event", "fundamental", "pair"}:
        s["model"]["family"] = {"mean":"mean_reversion", "pair":"pair_reversion"}.get(change, change)
    elif change == "ridge": s["model"]["estimator"] = "ridge"
    elif change == "execution": s["execution"]["enabled"] = True
    elif change == "1001": s["universe"]["symbols"].append("101000.SZ")
    elif change == "367days": s["universe"]["end"] = "20250101"
    elif change == "17factors": s["factors"].append({**s["factors"][0], "id":"extra"})
    elif change == "refit": s["model"]["refitDays"] = 19
    else: s["validation"][change+"Folds"] = 3
    original = deepcopy(s)
    with pytest.raises(ResearchError): validate(s, capacity_profile=TREND_AUTO_PROFILE_ID)
    assert s == original


def test_capability_is_literal_dual_opt_in_and_config_defaults_closed(tmp_path):
    old = claim_request(JOB, market_datasets=True)["marketResearchProfiles"]
    assert old == [FULL_FILTER_PROFILE_ID, AUTO_FILTER_CANDIDATE_ID]
    for value in (None, False, "true", 1):
        assert claim_request(JOB, market_datasets=True, market_trend_auto=value)["marketResearchProfiles"] == old
        assert "marketResearchProfiles" not in claim_request(JOB, market_datasets=value, market_trend_auto=True)
    assert claim_request(JOB, market_datasets=True, market_trend_auto=True)["marketResearchProfiles"] == old+[TREND_AUTO_PROFILE_ID]
    path = tmp_path/"runner.json"
    for bad in ({"market_trend_auto_research_enabled":"true"}, {"market_trend_auto_research_enabled":True}):
        path.write_text(json.dumps({**config(tmp_path), **bad})); path.chmod(0o600)
        with pytest.raises(runner.RunnerError) as error: runner.load_config(path)
        assert error.value.code == "CONFIG_MARKET"
    path.write_text(json.dumps(config(tmp_path))); path.chmod(0o600)
    assert runner.load_config(path).get("market_trend_auto_research_enabled", False) is False


def test_new_research_profile_reuses_old_immutable_source_only_after_opt_in(tmp_path, market_source):
    job, meta, responses = deepcopy(market_source[:3])
    old_source_root = job["marketDatasetRef"]["datasetRoot"]
    job["strategy"]["model"].update(family="trend", estimator="auto")
    for value in (job, meta):
        value["admissionProfile"] = TREND_AUTO_PROFILE_ID
        value["sourceEvidence"]["admissionProfile"] = TREND_AUTO_PROFILE_ID
    responses[job["marketInputUrl"]] = encode(meta)
    session = Session({key: Response(raw) for key, raw in responses.items()})
    cfg = config(tmp_path)
    spool = runner.CompletionSpool(cfg)
    # Neither the general source opt-in nor the new flag alone grants this job.
    for flags in ({}, {"market_dataset_research_enabled":True}, {"market_trend_auto_research_enabled":True}):
        with pytest.raises(runner.RunnerError) as error:
            MarketResearchClient({**cfg, **flags}, session).prepare(job, spool, deadline=time.monotonic()+30, check=lambda:None)
        assert error.value.code == "MARKET_TREND_AUTO_DISABLED"
        assert session.calls == []
    prepared = MarketResearchClient({**cfg, "market_dataset_research_enabled":True, "market_trend_auto_research_enabled":True}, session).prepare(
        job, spool, deadline=time.monotonic()+30, check=lambda:None)
    frame, provenance = MarketResearchSpool.from_spool(spool, prepared).inputs(prepared).research_input()
    assert prepared["marketDatasetRef"]["datasetRoot"] == old_source_root
    assert len(frame) == market_source[3]["rowCount"]
    assert prepared["sourceEvidence"]["admissionProfile"] == TREND_AUTO_PROFILE_ID
    assert provenance["synthetic"] is True
    assert len(session.calls) == 8 and all(method == "GET" for method, _, _ in session.calls)


def test_array_trend_features_match_reference_and_budget_is_declared_before_fit(tmp_path, monkeypatch):
    s = validate(strategy(), capacity_profile=TREND_AUTO_PROFILE_ID)
    data, provenance = make_benchmark_data(s)
    # Uneven missingness is retained, never replaced by a smaller pool.
    data = data.drop(data.index[200]).copy()
    panel, dates, _ = _prepare_data(data, s, provenance)
    expected = build_samples(panel, dates, s)
    observed = []
    def no_fit(*args, **kwargs):
        samples = args[5]
        for name in ("X", "y", "meta"):
            pd.testing.assert_frame_equal(getattr(samples,name), getattr(expected,name), check_exact=True)
        assert samples.definitions == expected.definitions and samples.hedge_fits == expected.hedge_fits
        observed.append(kwargs["runtime"].maximum)
        return {}
    monkeypatch.setattr(core, "_research_from_samples", no_fit)
    result = core.run_capacity_research(s, data, provenance, profile_id=TREND_AUTO_PROFILE_ID, cache_dir=tmp_path/"cache")
    plan = result["capacity"]
    assert len(expected.X.columns) == 21
    assert set(expected.X.columns[:5]) == {"volatility20", "trend1", "trend5", "trend20", "trend60"}
    assert plan["candidateConfigurations"] == 8 and plan["baselineRequired"] is True
    assert plan["scheduledFits"] == 106 and plan["fitAttemptsUpperBound"] == 182 == observed[0]
    assert plan["actualFitAttempts"] == 0


def test_three_symbol_auto_exports_complete_f_and_all_210_joint_tables(tmp_path):
    s = strategy()
    data, provenance = make_benchmark_data(s)
    result = core.run_capacity_research(s, data, provenance, profile_id=TREND_AUTO_PROFILE_ID, cache_dir=tmp_path/"cache")
    f = result["forecasts"]; d = f["diagnostics"]; stats = f["factorResearch"]["diagnostics"]
    assert f["sourceStrategy"]["model"]["family"] == "trend"
    assert len(f["rows"]) == 123
    assert len(d["factorIncrement"]["baselineRows"]) == 123
    assert sum(r["labelMaturedAt"] is not None for r in f["rows"]) == 105
    assert len(d["finalTrials"]) == 8
    assert d["selectionAudit"]["candidateCount"] == 8
    assert d["selectionAudit"]["eliminatesBiasOrOverfitting"] is False
    assert len(stats["features"]) == 21
    joints = stats["dependence"]["jointDistributions"]
    assert len(joints) == len({(j["x"],j["y"]) for j in joints}) == 210
    for joint in joints:
        if joint["status"] == "available":
            assert sum(map(sum, joint["counts"])) == joint["sampleCount"]
            assert math.isclose(sum(map(sum, joint["probabilities"])), 1.0)
    functions = [fit["functionArtifact"] for fit in f["modelFits"] if fit["status"] == "valid"]
    assert functions
    for artifact in functions:
        assert artifact["scope"]["family"] == "trend"
        assert artifact["scope"]["symbols"] == sorted(s["universe"]["symbols"])
        out = predict_function(artifact, [{x["name"]:0.0 for x in artifact["inputSchema"]}], current_state=[50.0], scale=[50.0])
        level = out["levels"][0]
        assert math.isfinite(level["expectedFuture"])
        assert level["e"] == 50.0-level["expectedFuture"] == -level["expectedChange"]
    assert result["trades"] == [] and result["metrics"] is None
    assert result["capacity"]["actualFitAttempts"] <= 182
