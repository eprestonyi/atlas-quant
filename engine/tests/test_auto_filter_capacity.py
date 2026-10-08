"""Experimental auto profile never broadens the existing Ridge admission."""
import copy
import pytest
from atlas_quant.engine import ResearchError
from atlas_quant.capacity.benchmark import benchmark_strategy, make_benchmark_data
from atlas_quant.capacity.profiles import get_profile, FULL_FILTER_PROFILE_ID, AUTO_FILTER_CANDIDATE_ID
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.models import candidates


def strategy():
    s = benchmark_strategy()
    s["universe"] = {"symbols":[f"{100000+i:06d}.SZ" for i in range(1000)], "start":"20240101", "end":"20241231"}
    s["model"].update(family="mean_reversion", estimator="auto", trainWindow=120, refitDays=20)
    s["validation"]["minTrainDates"] = 40
    return s


def test_auto_candidate_has_explicit_guarded_scope_and_all_eight_candidates():
    s = validate(strategy(), capacity_profile=AUTO_FILTER_CANDIDATE_ID)
    assert len(candidates(s["model"]["estimator"])) == 8
    profile = get_profile(AUTO_FILTER_CANDIDATE_ID)
    assert profile.max_symbols == 1000 and profile.max_wall_seconds == 900
    assert profile.max_rss_bytes == 3*1024**3
    with pytest.raises(ResearchError): validate(s, capacity_profile=FULL_FILTER_PROFILE_ID)
    for change in ("family", "window", "execution", "folds"):
        bad = copy.deepcopy(s)
        if change == "family": bad["model"]["family"] = "trend"
        if change == "window": bad["universe"]["start"] = "20230101"
        if change == "execution": bad["execution"]["enabled"] = True
        if change == "folds": bad["validation"]["innerFolds"] = 3
        with pytest.raises(ResearchError): validate(bad, capacity_profile=AUTO_FILTER_CANDIDATE_ID)


def test_capacity_plan_counts_all_candidates_and_separate_baseline_before_fit(tmp_path, monkeypatch):
    from atlas_quant.capacity import core
    s = strategy(); s["universe"]["symbols"] = s["universe"]["symbols"][:3]
    data, provenance = make_benchmark_data(s)
    seen = []
    def no_fit(*args, **kwargs):
        seen.append(kwargs["runtime"].maximum)
        return {}
    monkeypatch.setattr(core, "_research_from_samples", no_fit)
    result = core.run_capacity_research(s, data, provenance, profile_id=AUTO_FILTER_CANDIDATE_ID, cache_dir=tmp_path/"cache")
    plan = result["capacity"]
    assert plan["candidateConfigurations"] == 8 and plan["baselineRequired"]
    assert plan["scheduledFits"] == 2*(3*2*8+2+plan["scheduledTerminalFits"])
    assert plan["fitAttemptsUpperBound"] == seen[0]
    assert plan["fitAttemptsUpperBound"] >= plan["scheduledFits"]
    assert plan["actualFitAttempts"] == 0
