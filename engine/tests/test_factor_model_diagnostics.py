"""Diagnostic labels, pairwise missingness, bounded empirical joint distributions."""
import copy
import json
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from atlas_quant.statistical_quant.factor_diagnostics import factor_diagnostics, MAX_JOINT_PAIRS
from atlas_quant.statistical_quant import validation
from atlas_quant.statistical_quant.schema import validate
from test_statistical_quant import strategy


def samples(assets=4):
    dates = [f"202401{i:02d}" for i in range(1, 21)]
    meta, features, labels = [], [], []
    for i, date in enumerate(dates):
        for asset in range(assets):
            x = float(i+asset)
            features.append({"factor:a": x, "factor:b": -x, "constant": 1.})
            labels.append({"entry": x*.2, "exit": x*.5+2})
            meta.append({"date": date, "targetId": f"asset_{asset}", "inputValid": True, "targetDate": dates[min(i+1,19)]})
    return SimpleNamespace(X=pd.DataFrame(features), y=pd.DataFrame(labels), meta=pd.DataFrame(meta))


def test_cross_sectional_ic_is_per_date_and_single_asset_never_gets_fake_ic():
    s = samples()
    report = factor_diagnostics(s, "20240111")
    a, b, constant = report["features"]
    assert a["ic"]["dates"] == 10 and a["ic"]["mean"] == pytest.approx(1.)
    assert a["rankIc"]["mean"] == pytest.approx(1.)
    assert b["ic"]["mean"] == pytest.approx(-1.)
    assert constant["ic"]["status"] == "unavailable"
    fit = a["descriptiveFit"]
    assert fit["slope"] == pytest.approx(.5) and fit["intercept"] == pytest.approx(2)
    assert fit["rSquared"] == pytest.approx(1.) and not fit["outOfSampleFit"]
    single = factor_diagnostics(samples(1), "20240111")["features"][0]
    assert single["ic"]["mean"] is None and single["rankIc"]["dates"] == 0
    assert single["timeSeriesCorrelation"]["perTarget"][0]["pearson"] == pytest.approx(1.)
    assert report["significance"]["pValues"] is None and not report["selectionUse"]


def test_missing_and_unmatured_rows_are_reported_not_silently_scored():
    s = samples()
    s.X.loc[40, "factor:a"] = np.nan
    s.y.loc[41, ["entry", "exit"]] = np.nan
    s.meta.loc[42, "inputValid"] = False
    report = factor_diagnostics(s, "20240111")
    a = report["features"][0]
    assert report["origins"] == 40 and report["maturedValidOrigins"] == 38
    assert a["missing"] == {"count": 1, "total": 40, "fraction": .025}
    assert a["distribution"]["count"] == 39
    assert a["descriptiveFit"]["n"] == 37
    # Three invalid pairs leave only one target on day 11; exclude that IC date.
    assert a["ic"]["dates"] == 9 and a["ic"]["unavailableDates"] == 1
    joint = report["dependence"]["jointDistributions"][0]
    assert joint["sampleCount"] == 39 and joint["missingPairCount"] == 1
    assert np.sum(joint["counts"]) == 39
    assert np.sum(joint["probabilities"]) == pytest.approx(1)


def test_joint_bins_are_frozen_in_development_keep_outliers_and_do_not_change_selection():
    s = samples()
    before = factor_diagnostics(s, "20240111")
    changed = copy.deepcopy(s)
    changed.X.loc[changed.meta.date >= "20240111", "factor:a"] *= 100
    after = factor_diagnostics(changed, "20240111")
    first, second = (r["dependence"]["jointDistributions"][0] for r in (before, after))
    assert first["xEdges"] == second["xEdges"]
    assert first["yEdges"] == second["yEdges"]
    assert second["xEdges"][0] is None and second["xEdges"][-1] is None
    assert sum(second["counts"][-1]) == 40
    assert second["sampleCount"] == 40
    assert "not_assumed_joint_density" in second["interpretation"]


def test_pairwise_covariance_counts_and_joint_budget_are_explicit():
    s = samples()
    for i in range(23):
        s.X[f"state{i}"] = s.X["factor:a"]+i
    s.X.loc[40:44, "factor:a"] = np.nan
    report = factor_diagnostics(s, "20240111")
    dep = report["dependence"]
    assert dep["pairCounts"][0][1] == 35
    assert dep["pairCounts"][1][1] == 40
    assert dep["correlation"][0][1] == pytest.approx(-1.)
    assert len(dep["jointDistributions"]) == MAX_JOINT_PAIRS
    assert dep["omittedPairs"] == dep["totalPossiblePairs"]-MAX_JOINT_PAIRS
    assert not dep["positiveSemidefiniteGuaranteed"]
    json.dumps(report, allow_nan=False)


def test_full_profile_all_pairs_match_independent_histograms():
    from itertools import combinations
    s = samples()
    for i in range(17):
        s.X[f"state{i}"] = (s.X["factor:a"]+i) % (i+3)
    # Includes constant axes, ties exactly on edges, tail outliers and missing
    # values. This independently checks every pair, not only the first six.
    s.X.loc[40:42, "state16"] = np.nan
    s.X.loc[43, "state16"] = 1e30
    report = factor_diagnostics(s, "20240111")
    dep = report["dependence"]
    assert dep["totalPossiblePairs"] == 190 and dep["omittedPairs"] == 0
    expected_pairs = list(combinations(list(s.X), 2))
    assert [(j["x"], j["y"]) for j in dep["jointDistributions"]] == expected_pairs
    terminal = s.X.loc[s.meta.date >= "20240111"]
    development = s.X.loc[s.meta.date < "20240111"]
    for joint in dep["jointDistributions"]:
        axes = []
        for name in (joint["x"], joint["y"]):
            source = development[name].dropna().to_numpy()
            bound = max(abs(source))
            axes.append(np.r_[-np.inf, np.unique(np.quantile(source/bound, [.2,.4,.6,.8])*bound), np.inf])
        pair = terminal[[joint["x"], joint["y"]]].dropna()
        counts, _, _ = np.histogram2d(pair.iloc[:,0], pair.iloc[:,1], bins=axes)
        assert joint["counts"] == counts.astype(int).tolist()
        assert joint["sampleCount"] == len(pair)
        assert np.sum(joint["probabilities"]) == pytest.approx(1)


def test_one_standard_error_rule_prefers_predeclared_simplicity_and_labels_it_heuristic(monkeypatch):
    s = samples()
    config = validate(strategy())
    # Two inner folds: complicated MSE {0,4}, simple MSE {2,3}, so the
    # complicated mean 2 has heuristic SE 2 and simple 2.5 is admissible.
    specs = [{"id":"ridge:0", "estimator":"ridge", "params":{"alpha":1.}},
             {"id":"historical_drift:0", "estimator":"historical_drift", "params":{}}]
    monkeypatch.setattr(validation, "folds", lambda *args: [(["20240101"],["20240111"]),(["20240101"],["20240112"])])
    s.y.loc[:, :] = 0.
    class Model:
        def __init__(self, score): self.score = score
        def predict(self, X): return np.full((len(X),2), np.sqrt(self.score))
    def train(samples, spec, train_dates, cutoff, strategy):
        scores = {"ridge": [0.,4.], "historical_drift": [2.,3.]}
        index = 0 if cutoff == "20240111" else 1
        return Model(scores[spec["estimator"]][index]), {"labelEndMax":"20240102"}
    monkeypatch.setattr(validation, "_train", train)
    winner, trials = validation.select(s, specs, sorted(s.meta.date.unique()), config)
    assert winner["estimator"] == "historical_drift"
    assert trials[0]["score"] < trials[1]["score"]
    assert trials[1]["withinHeuristicTolerance"]
    assert all(not t["heuristicIsConfidenceInterval"] for t in trials)


def test_report_contains_real_function_and_declares_both_branch_budgets():
    from atlas_quant.engine import run_research, _prepare_data
    from atlas_quant.fixtures import make_demo_data
    from atlas_quant.statistical_quant.targets import build_samples
    from atlas_quant.statistical_quant.model_function import predict_function
    from test_model_function import rows
    s = strategy()
    s["factors"] = [{"id":"turnover","expression":"vol","role":"predictor"}]
    data, provenance = make_demo_data(s)
    result = run_research(s, data, provenance)
    s = result["strategy"]
    panel, dates, _ = _prepare_data(data, s, provenance)
    sample = build_samples(panel, dates, s)
    artifact = result["forecasts"]
    references = artifact["factorResearch"]["modelFunctions"]
    fits = {f["id"]:f for f in artifact["modelFits"]}
    first = next(r for r in artifact["rows"] if r["status"] == "valid")
    f = fits[first["modelFitId"]]["functionArtifact"]
    index = sample.meta.index[(sample.meta.date == first["date"]) & (sample.meta.targetId == first["targetId"])][0]
    X = sample.X.loc[[index], [v["name"] for v in f["inputSchema"]]]
    actual = predict_function(f, rows(X), current_state=[first["currentState"]], scale=[first["scale"]])
    assert actual["levels"][0]["expectedFuture"] == pytest.approx(first["expectedFuture"], rel=1e-13)
    assert references[0]["artifactId"] == artifact["modelFits"][0]["functionArtifact"]["artifactId"]
    assert all("functionArtifact" not in fit for fit in artifact["diagnostics"]["factorIncrement"]["baselineModelFits"])
    budget = artifact["diagnostics"]["selectionAudit"]["researchFitBudget"]
    assert budget["branches"] == 2 and budget["includesFactorFreeBaseline"]
    assert budget["declaredBeforeFitting"] and budget["sequentialFitAttempts"] <= budget["sequentialFitAttemptCap"]
    diag = artifact["factorResearch"]["diagnostics"]
    assert not diag["selectionUse"] and diag["firstDate"] == result["validation"]["holdoutStart"]
    assert any(row["name"] == "factor:turnover" for row in diag["features"])


def test_factor_correlation_does_not_depend_on_arbitrary_small_input_units():
    s = samples()
    before = factor_diagnostics(s, "20240111")["features"][0]
    s.X["factor:a"] *= 1e-20
    after = factor_diagnostics(s, "20240111")["features"][0]
    assert after["ic"]["mean"] == pytest.approx(before["ic"]["mean"])
    assert after["descriptiveFit"]["rSquared"] == pytest.approx(before["descriptiveFit"]["rSquared"])
    assert after["descriptiveFit"]["slope"] == pytest.approx(before["descriptiveFit"]["slope"]*1e20)
