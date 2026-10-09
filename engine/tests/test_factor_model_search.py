"""Nonlinear equations, chronological search, baseline preservation and scope."""
import copy
import json
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits
from atlas_quant.statistical_quant.models import candidates, fit, GRIDS
from atlas_quant.statistical_quant.basis_models import NONLINEAR_GRIDS, expand
from atlas_quant.statistical_quant.model_function import export_function, predict_function, edit_function, validate_function, function_digest
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import Samples
from atlas_quant.statistical_quant.validation import forecast, select
from atlas_quant.statistical_quant.core import declared_fit_budget
from atlas_quant.statistical_quant.factor_diagnostics import factor_diagnostics
from test_statistical_quant import strategy


def config(estimator="auto", sharing="pooled"):
    value = strategy(estimator=estimator)
    value["universe"]["symbols"] = ["000001.SZ", "600000.SH"]
    value["model"].update(search={"schema": "factor-model-search/1"}, parameterSharing=sharing, refitDays=126)
    value["validation"] = {"minTrainDates": 40}
    return validate(value)


def source(n=360):
    rng = np.random.default_rng(113)
    dates = pd.bdate_range("20230102", periods=n).strftime("%Y%m%d").tolist()
    features, labels, meta = [], [], []
    definitions = {"target_"+str(i): {"id": "target_"+str(i), "kind": "asset_price", "symbols": [symbol]}
                   for i, symbol in enumerate(("000001.SZ", "600000.SH"))}
    for t, date in enumerate(dates):
        for asset in range(2):
            x, z = rng.normal(size=2)
            future = (1 if asset == 0 else -1)*(.04*x*x+.015*z)
            features.append({"x": x, "z": z})
            labels.append({"entry": .002*z if t+2 < n else np.nan, "exit": future if t+2 < n else np.nan})
            meta.append({"date": date, "dateIndex": t, "targetId": "target_"+str(asset), "inputValid": True,
                "invalidReason": None, "currentState": 100., "scale": 100.,
                "entryDate": dates[t+1] if t+1 < n else None, "targetDate": dates[t+2] if t+2 < n else None,
                "realizedEntry": 100*(1+.002*z) if t+2 < n else np.nan,
                "realizedFuture": 100*(1+future) if t+2 < n else np.nan})
    return Samples(pd.DataFrame(features), pd.DataFrame(labels), pd.DataFrame(meta), definitions, 0, dates, [])


def training_model(name):
    samples = source(180)
    s = config(name)
    spec = candidates(name)[0]
    with threadpool_limits(limits=1):
        fitted = fit(spec, samples.X.iloc[:250], samples.y.iloc[:250], s["preprocess"])
    audit = {**fitted.audit, "trainStart": "20230102", "trainEnd": "20230623", "labelEndMax": "20230627",
             "informationCutoff": "20230703", "trainDates": 125}
    return fitted, export_function(fitted, audit, s), samples


def rows(frame):
    return [{key: None if pd.isna(value) else float(value) for key, value in row.items()}
            for row in frame.to_dict("records")]


@pytest.mark.parametrize("name", list(NONLINEAR_GRIDS))
def test_every_nonlinear_family_exports_exact_equation_and_numeric_edits(name):
    model, artifact, samples = training_model(name)
    assert artifact["schema"] == "atlas-model-function/3"
    X = samples.X.iloc[250:].copy()
    X.iloc[0, :] = np.nan
    X.iloc[1, :] = [1e8, -1e8]
    expected = model.predict(X)
    actual = predict_function(json.loads(json.dumps(artifact)), rows(X))
    np.testing.assert_allclose(actual["normalizedChanges"], expected, rtol=1e-12, atol=1e-13)
    edited = edit_function(artifact, [{"path": "/estimator/intercepts/1", "value": .3}])
    change = np.asarray(predict_function(edited, rows(X))["normalizedChanges"])-expected
    np.testing.assert_allclose(change[:, 1], .3-artifact["estimator"]["intercepts"][1], atol=1e-13)
    assert edited["lineage"]["status"] == "UNVALIDATED_USER_EDIT"


def test_basis_term_semantics_are_explicit_and_exponential_is_bounded():
    terms = [{"kind": "power", "feature": 0, "degree": 2},
             {"kind": "interaction", "features": [0, 1]},
             {"kind": "signed_log1p", "feature": 0}, {"kind": "signed_expm1", "feature": 1}]
    output = expand(np.array([[-2., 99.], [3., -99.]]), terms)
    np.testing.assert_allclose(output, [[4, -198, -np.log(3), np.expm1(3)], [9, -297, np.log(4), -np.expm1(3)]])


@pytest.mark.parametrize("mutation", ["power", "negative_scale", "duplicate", "cap", "input_index", "executable"])
def test_basis_artifact_rejects_invalid_topology_even_after_rehash(mutation):
    _, artifact, _ = training_model("polynomial_ridge")
    e = artifact["estimator"]
    if mutation == "power": e["terms"][0]["degree"] = 9
    elif mutation == "negative_scale": e["termScale"][0] = -1
    elif mutation == "duplicate": e["terms"][1] = copy.deepcopy(e["terms"][0])
    elif mutation == "cap": e["signedExpm1AbsoluteInputCap"] = 4
    elif mutation == "input_index": e["terms"][0]["feature"] = 999
    else: e["expression"] = "eval(x)"
    artifact["artifactId"] = function_digest({key: value for key, value in artifact.items() if key != "artifactId"})
    with pytest.raises(ValueError, match="INVALID_MODEL_FUNCTION"):
        validate_function(artifact)


def test_new_search_is_opt_in_and_protocol_rejects_undefined_variants():
    assert len(candidates("auto")) == 8
    assert len(candidates("auto", {"schema": "factor-model-search/1"})) == 22
    s = strategy()
    assert "search" not in validate(s)["model"]
    for value in ({"schema": "factor-model-search/2"}, {}, {"schema": "factor-model-search/1", "retryUntilWin": True}):
        s["model"]["search"] = value
        with pytest.raises(ValueError): validate(s)


def test_independent_models_cannot_bypass_50_target_admission_with_pooled_profile():
    from atlas_quant.capacity import PROFILE_ID
    value = strategy()
    value["model"]["parameterSharing"] = "per_target"
    value["universe"]["symbols"] = [f"{100000+i:06d}.SZ" for i in range(51)]
    with pytest.raises(ValueError) as error:
        validate(value, capacity_profile=PROFILE_ID)
    assert error.value.code == "MODEL_SCOPE_CAPACITY"


def test_independent_models_recover_opposite_asset_equations_and_keep_fitted_candidates():
    samples = source()
    s = config("polynomial_ridge", "per_target")
    with threadpool_limits(limits=1):
        records, fits, report = forecast(samples, s)
    assert len(records) == 144
    assert report["parameterSharing"] == "per_target" and report["modelGroups"] == 2
    assert report["metrics"]["relativeMseImprovement"] > .95
    assert len({fit["id"] for fit in fits}) == len(fits)
    assert {fit["targetId"] for fit in fits} == set(samples.definitions)
    search = report["modelSearch"]
    assert len(search["candidates"]) == 6 and len(search["researchCandidateIds"]) == 2
    for item in search["candidates"]:
        assert item["functionArtifact"]["scope"]["symbols"] == item["symbols"]
        assert item["fit"]["labelEndMax"] < item["fit"]["fitDate"]
        assert item["trainingPlot"]["sample"] == "training_in_sample"
    assert declared_fit_budget(s, samples)["candidateFunctionFitCap"] == 6


def test_future_labels_cannot_change_candidate_selection_or_frozen_functions():
    samples = source()
    s = config("transformed_ridge")
    with threadpool_limits(limits=1):
        before = forecast(samples, s)[2]
        changed = copy.deepcopy(samples)
        mask = changed.meta.date >= before["holdoutStart"]
        changed.y.loc[mask] *= 100
        changed.meta.loc[mask, "realizedFuture"] *= 10
        after = forecast(changed, s)[2]
    assert before["modelSearch"] == after["modelSearch"]
    assert before["selectedModel"] == after["selectedModel"]
    assert before["finalTrials"] == after["finalTrials"]


def test_baseline_victory_does_not_erase_nonbaseline_candidate_equations():
    samples = source()
    samples.y.loc[:, :] = 0.
    samples.meta.loc[:, "realizedFuture"] = samples.meta.currentState
    samples.meta.loc[:, "realizedEntry"] = samples.meta.currentState
    with threadpool_limits(limits=1):
        _, _, report = forecast(samples, config())
    search = report["modelSearch"]
    assert report["selectedModel"]["estimator"] == "no_change"
    assert search["researchCandidateId"] is not None
    item = next(item for item in search["candidates"] if item["id"] == search["researchCandidateId"])
    assert not item["baseline"] and item["functionArtifact"] is not None
    assert not item["selected"] and not search["researchCandidateIsDeploymentQualified"]


def test_ai_hook_is_final_development_only_and_cannot_force_unvalidated_id():
    samples = source()
    calls = []
    class Reviewer:
        def before_fit(self, *args): pass
        def after_fit(self): pass
        def review_candidates(self, payload):
            calls.append(copy.deepcopy(payload))
            return {"candidateId": payload["defaultCandidateId"], "receipt": {"provider": "test_double"}}
    with threadpool_limits(limits=1):
        report = forecast(samples, config("ridge"), runtime=Reviewer())[2]
    assert len(calls) == 1
    assert calls[0]["developmentEnd"] < report["holdoutStart"]
    assert calls[0]["outerOrTerminalDataIncluded"] is False
    assert not report["selectionAudit"]["outerFoldsEvaluateAIReviewer"]
    class InvalidReviewer(Reviewer):
        def review_candidates(self, payload): return {"candidateId": "invented_model"}
    with threadpool_limits(limits=1), pytest.raises(ValueError, match="模型审阅"):
        forecast(samples, config("ridge"), runtime=InvalidReviewer())


def test_contemporary_exposure_is_not_future_predictive_r_squared():
    samples = source()
    rng = np.random.default_rng(83)
    contemporary = rng.normal(0, .02, len(samples.X))
    samples.X["change1"] = contemporary/(1+contemporary)
    samples.X["factor:market"] = contemporary*10
    result = factor_diagnostics(samples, samples.dates[280])
    factor = next(item for item in result["features"] if item["name"] == "factor:market")
    assert factor["contemporaneousFit"]["pooled"]["rSquared"] == pytest.approx(1.)
    assert factor["descriptiveFit"]["rSquared"] < .1
    assert factor["contemporaneousFit"]["target"] == "current_session_asset_return"


def test_training_r_squared_uses_weighted_training_mean_not_zero_change_baseline():
    from atlas_quant.statistical_quant.model_search import training_metrics
    # Unequal cross-section sizes: date one has one observation, date two two.
    actual = np.array([[3., 12.], [1., 10.], [1., 10.]])
    predicted = np.tile([2., 11.], (3, 1))
    result = training_metrics(predicted, actual, ["20240101", "20240102", "20240102"])
    assert result["rSquared"] == pytest.approx(0.)
    assert result["entryRSquared"] == pytest.approx(0.)
    assert result["relativeMseImprovement"] > .95


def test_factorwise_duplicate_terms_removed_with_train_only_audit():
    s = source(160)
    X = pd.DataFrame({"a": s.X.x, "duplicate_a": s.X.x})
    y = pd.DataFrame({"entry": .08*X.a, "exit": .08*X.a**2})
    preprocess = {**config()["preprocess"], "decorrelation": "none"}
    with threadpool_limits(limits=1):
        model = fit(candidates("factorwise_basis")[0], X, y, preprocess)
    basis = model.audit["basisFit"]
    assert basis["route"] == "factorwise_then_joint"
    assert basis["redundantTerms"] and len(basis["factorSelections"]) == 2
    assert basis["termSelectionUsesTrainingOnly"]


def test_new_protocol_full_envelope_includes_functions_and_compact_summary(monkeypatch):
    from atlas_quant.fixtures import make_demo_data
    from atlas_quant.engine import run_research
    monkeypatch.delenv("ATLAS_QUANT_CODEX_REVIEW", raising=False)
    value = config("polynomial_ridge")
    value["factors"] = [{"id": "price", "expression": "close", "direction": 1, "role": "predictor"}]
    value["preprocess"]["automatic"] = {"schema": "auto-factor-preprocess/1"}
    data, provenance = make_demo_data(value)
    with threadpool_limits(limits=1):
        report = run_research(value, data, provenance)
    research = report["forecasts"]["diagnostics"]["modelSearch"]
    assert len(research["candidates"]) == 3
    assert len(report["forecasts"]["factorResearch"]["candidateModelFunctions"]) == 3
    assert "candidates" not in report["validation"]["modelSearch"]
    selected = next(trial for trial in report["forecasts"]["diagnostics"]["finalTrials"] if trial["selected"])
    assert selected["review"]["receipt"]["status"] == "not_configured"
    assert "review" not in report["forecasts"]["diagnostics"]["factorIncrement"]["baselineValidation"]["finalTrials"][0]
    json.dumps(report, allow_nan=False)


def test_ai_refinement_really_fits_all_predeclared_reserve_after_review():
    from atlas_quant.statistical_quant.models import is_reserve
    s = config()
    specs = candidates("auto", s["model"]["search"])
    events, packets = [], []
    class Reviewer:
        enabled = True
        def before_fit(self, spec, cutoff, rows): events.append(spec["id"])
        def after_fit(self): pass
        def review_candidates(self, payload):
            packets.append(copy.deepcopy(payload))
            events.append("review")
            return {"candidateId": payload["defaultCandidateId"], "refinementCandidateIds": ["ridge:3"],
                    "receipt": {"status": "reviewed", "provider": "test_double"}}
    samples = source()
    with threadpool_limits(limits=1):
        _, _, report = forecast(samples, s, runtime=Reviewer())
    assert len(packets) == 1 and len(packets[0]["trials"]) == 16
    assert len(packets[0]["reserveCandidates"]) == 6 and packets[0]["reserveEvaluationPending"]
    after = events[events.index("review")+1:]
    expected = [spec["id"] for spec in specs if is_reserve(spec) for _ in range(s["validation"]["innerFolds"])]
    assert after[:12] == expected
    assert len(report["finalTrials"]) == 22
    assert all(len(fold["trials"]) == 22 for fold in report["outerFolds"])
    assert report["selectionAudit"]["candidateSetExpandedUsingOutcomes"] is False
    assert len(events)-1 <= declared_fit_budget(s, samples)["maximumFitAttempts"]
    receipt = next(trial["review"] for trial in report["finalTrials"] if trial["selected"])
    assert receipt["refinementCandidateIds"] == ["ridge:3"] and len(receipt["evaluatedReserveCandidateIds"]) == 6


def test_interrupted_candidate_export_retains_completed_functions_without_retry():
    from atlas_quant.engine import ResearchError
    s = config("ridge")
    candidate_count = len(candidates("ridge", s["model"]["search"]))
    before_exports = (s["validation"]["outerFolds"]+1)*s["validation"]["innerFolds"]*candidate_count+s["validation"]["outerFolds"]
    class Runtime:
        calls = 0
        def before_fit(self, *args):
            self.calls += 1
            if self.calls > before_exports+1:
                raise ResearchError("CAPACITY_FITS", "test fit budget")
        def after_fit(self): pass
    runtime = Runtime()
    with threadpool_limits(limits=1), pytest.raises(ResearchError) as failure:
        forecast(source(), s, runtime=runtime)
    partial = failure.value.forecast_partial
    kept = partial["diagnostics"]["modelSearch"]["candidates"]
    assert len(kept) == 2 and kept[0]["functionArtifact"] is not None
    assert kept[1]["status"] == "interrupted"
    assert not partial["complete"] and not partial["diagnostics"]["publishable"]
    assert runtime.calls == before_exports+2
