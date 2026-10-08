"""Predeclared tiny synthetic unit fits, never a market/capacity canary.

Six cases reuse one 5-U/262-date/two-target fixture and the existing eight auto
candidates. Each dual branch case is capped at 182 delegated fit attempts; the
single-branch case is capped at 91. A case executes at most once per test pass.
"""
from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from pair_fit_fixture import fixture, MODEL, PREPROCESS, VALIDATION, RESOURCES
from test_pair_research_samples import fixed_source  # Tiny 85-day insufficient-data case.
from atlas_quant.engine import ResearchError, _finite_json
from atlas_quant.pair_research import PairContractError, declare_targets
from atlas_quant.pair_research.contract import digest
from atlas_quant.pair_research.fit_contract import declare_fit_contract, validate_fit_contract, PORTABLE_STATUS
from atlas_quant.pair_research.research import run_pair_research
from atlas_quant.pair_research.research_contract import declare_research
from atlas_quant.statistical_quant import models, validation


@pytest.fixture(scope="module")
def inputs():
    return fixture()[:3]


def fit_contract(inputs, **changes):
    args = {"model": MODEL, "preprocess": PREPROCESS, "validation": VALIDATION, "resources": RESOURCES}
    args.update(changes)
    return declare_fit_contract(*inputs, **args)


@pytest.fixture(scope="module")
def declared(inputs):
    return fit_contract(inputs)


@pytest.fixture(autouse=True)
def forbid_io_and_old_function_export(monkeypatch):
    import requests
    from atlas_quant.statistical_quant import model_function, targets

    def forbidden(*args, **kwargs):
        pytest.fail("Pair Stage 2B attempted I/O, old target formation, or v1 function export")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(model_function, "export_function", forbidden)
    monkeypatch.setattr(targets, "_construct", forbidden)


@pytest.fixture(scope="module")
def cases():
    """Retain receipts on request; public CI needs no host-specific golden."""
    cached = {}

    def run(name):
        if name in cached:
            return cached[name]
        source, declaration, research, _ = fixture(
            future=name == "future_perturbation", factors=[] if name == "pair_without_predictors" else None)
        contract = fit_contract((source, declaration, research))
        prepared = validate_fit_contract(contract, source, declaration, research)
        cap = prepared.plan["fitBudget"]["maximumFitAttempts"]
        assert cap == (91 if name == "pair_without_predictors" else 182)
        trace, active, models_seen, first_samples, plans = [], {}, [], {}, []
        old_fit, old_train = models.fit, validation._train

        def train(samples, spec, train_dates, cutoff, config, runtime=None):
            active.update(samples=samples, cutoff=cutoff, config=config, train_dates=train_dates)
            if any(c.startswith("factor:") for c in samples.X):
                first_samples.setdefault("main", samples)
            elif "main" in first_samples:
                # The baseline changes only X. Labels, masks, q/definitions and
                # date grid are literally shared objects in the common helper.
                main = first_samples["main"]
                assert samples.y is main.y and samples.meta is main.meta
                assert samples.definitions is main.definitions and samples.dates is main.dates
                assert list(samples.X) == [c for c in main.X if not c.startswith("factor:")]
            return old_train(samples, spec, train_dates, cutoff, config, runtime=runtime)

        def fitted(spec, X, y, pre):
            assert len(plans) == 1, "No fitting before the immutable full plan sink"
            assert len(trace) < cap, "Predeclared tiny unit-test fit cap exceeded"
            samples, cutoff, config = active["samples"], active["cutoff"], active["config"]
            mask = validation.mature_mask(samples, cutoff, active["train_dates"], config["model"]["trainWindow"])
            train_meta = samples.meta.loc[mask]
            assert X.index.equals(train_meta.index) and y.index.equals(train_meta.index)
            assert (train_meta.date < cutoff).all()
            assert (train_meta.entryDate < cutoff).all() and (train_meta.targetDate < cutoff).all()
            assert y.notna().all().all() and train_meta.inputValid.all()
            branch = "main" if any(c.startswith("factor:") for c in X) else "baseline"
            if name == "pair_without_predictors": branch = "main"
            row = {"ordinal": len(trace), "branch": branch, "specId": spec["id"], "cutoff": cutoff,
                   "XHash": digest(_finite_json(X.to_dict("records"))), "yHash": digest(_finite_json(y.to_dict("records"))),
                   "indexHash": digest(list(map(int, X.index))), "features": list(X), "trainRows": len(X),
                   "entryMax": train_meta.entryDate.max(), "exitMax": train_meta.targetDate.max(), "status": "started"}
            trace.append(row)
            try:
                if name == "all_candidates_fail" or (name == "baseline_fails_after_main" and branch == "baseline"):
                    raise ResearchError("MODEL_DID_NOT_CONVERGE", "declared unit failure injection")
                if name == "terminal_model_failure" and cutoff >= prepared.plan["holdoutStart"]:
                    raise ResearchError("MISSING_MODEL_DATA", "declared terminal unit failure injection")
                result = old_fit(spec, X, y, pre)
                assert all(result is not previous for previous in models_seen), "A branch reused a fitted instance"
                models_seen.append(result)
                row.update(status="completed", audit=deepcopy(result.audit))
                return result
            except Exception as exc:
                row.update(status="failed", code=getattr(exc, "code", type(exc).__name__))
                raise

        models.fit, validation._train = fitted, train
        try:
            result = run_pair_research(source, declaration, research, contract, plan_sink=plans.append)
        finally:
            models.fit, validation._train = old_fit, old_train
        assert result["fitEvidence"]["actualFitAttempts"] == len(trace)
        assert all(event["branch"] == row["branch"] for event, row in zip(result["fitEvidence"]["events"], trace))
        receipt = {"case": name, "maxFitAttempts": cap, "actualModelFitCalls": len(trace),
                   "fitContractRoot": contract["fitContractRoot"], "fitPlanRoot": prepared.plan["planRoot"],
                   "sourceRoot": source.domain["marketDatasetRef"]["datasetRoot"],
                   "candidateSet": models.candidates("auto"), "resultStatus": result["status"],
                   "trace": trace, "providerCalls": 0, "networkCalls": 0, "cloudMutations": 0, "quantityFits": 0}
        directory = os.environ.get("ATLAS_PAIR_UNIT_RECEIPT_DIR")
        if directory:
            path = Path(directory) / (name + ".json")
            raw = (json.dumps(_finite_json(receipt), ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(raw); f.flush(); os.fsync(f.fileno())
        cached[name] = (result, receipt)
        return cached[name]
    return run


def test_true_shared_scheduler_main_baseline_and_complete_domain(cases):
    result, receipt = cases("pair_main_baseline")
    assert result["complete"] and result["status"] == "completed_local_forecast"
    assert not result["publishable"] and not result["deploymentQualified"] and not result["executionEnabled"]
    assert result["portableFunctionStatus"] == PORTABLE_STATUS
    assert receipt["actualModelFitCalls"] == 106
    assert len(result["sampleStatusRows"]) == 402 and len(result["targets"]["rows"]) == 402
    assert result["targets"]["coverage"]["universeMembers"] == 5
    assert sum(m["status"] == "unmatched" for m in result["targets"]["memberStates"]) == 2
    main, baseline = result["main"], result["baseline"]
    assert len(main["rows"]) == len(baseline["rows"]) == 82
    assert main["fits"] and baseline["fits"] and result["baselineStatus"] == "completed"
    assert main["diagnostics"]["factorIncrement"]["baselineRows"] == baseline["rows"]
    for branch in (main, baseline):
        assert all("functionArtifact" not in fit for fit in branch["fits"])
        assert len(branch["diagnostics"]["finalTrials"]) == 8
        assert branch["diagnostics"]["metrics"]["weighting"] == "equal_weight_daily_average"
        assert branch["diagnostics"]["rollingRefitsUseMaturedPastHoldoutLabels"]
    assert result["factorResearch"]["modelFunctions"] == []
    assert all(feature["ic"]["status"] == "unavailable" for feature in result["factorResearch"]["diagnostics"]["features"])
    a = {(r["specId"], r["cutoff"]): r for r in receipt["trace"] if r["branch"] == "main"}
    b = {(r["specId"], r["cutoff"]): r for r in receipt["trace"] if r["branch"] == "baseline"}
    assert len(a.keys() & b.keys()) >= 48
    for key in a.keys() & b.keys():
        assert a[key]["yHash"] == b[key]["yHash"] and a[key]["indexHash"] == b[key]["indexHash"]
        assert b[key]["features"] == [c for c in a[key]["features"] if not c.startswith("factor:")]


def test_signed_s_g_level_and_error_identities_not_profit(cases):
    result, _ = cases("pair_main_baseline")
    rows = result["main"]["rows"]
    assert any(r["currentState"] == 0 for r in rows)
    assert any(r["currentState"] is not None and r["currentState"] < 0 for r in rows)
    assert all("expectedGrossPnl" not in r and "expectedGrossBps" not in r for r in rows)
    for row in rows:
        if row["status"] != "valid": continue
        s, g, entry, exit_ = (row[k] for k in ("currentState", "scale", "expectedEntry", "expectedFuture"))
        assert g > 0
        assert row["edgeGap"] == pytest.approx(s - exit_)
        assert row["expectedChange"] == pytest.approx(exit_ - s)
        assert row["expectedRemainingChange"] == pytest.approx(exit_ - entry)
        assert row["expectedRemainingChangeOverGrossBps"] == pytest.approx((exit_ - entry) / g * 10000, abs=1e-9)
        if row["realizedFuture"] is not None:
            assert row["forecastError"] == pytest.approx(row["realizedFuture"] - exit_)
    # Missing required state and the full final calendar tail survive both branches.
    assert any(r["invalidReason"] == "current_missing_legs" for r in rows)
    assert rows[-1]["entryDate"] is None and rows[-1]["targetDate"] is None
    assert len(rows) == result["plan"]["forecastRowsPerBranch"]


def test_future_change_preserves_prior_training_transforms_and_predictions(cases):
    base, before = cases("pair_main_baseline")
    changed, after = cases("future_perturbation")
    cutoff = fixture()[0].domain["calendar"][230]
    for branch in ("main", "baseline"):
        def numerical(rows):
            return [{k: r[k] for k in ("date", "currentState", "scale", "expectedEntry", "expectedFuture", "edgeGap")}
                    for r in rows if r["date"] <= cutoff]
        assert numerical(base[branch]["rows"]) == numerical(changed[branch]["rows"])
    aa = [r for r in before["trace"] if r["cutoff"] <= cutoff]
    bb = [r for r in after["trace"] if r["cutoff"] <= cutoff]
    assert aa == bb  # Includes actual train-only imputer/winsor/scaler/decorrelation audits.


def test_terminal_failure_never_falls_back_or_discards_origins(cases):
    result, receipt = cases("terminal_model_failure")
    assert result["complete"] and receipt["actualModelFitCalls"] == 182
    assert sum(r["status"] == "failed" for r in receipt["trace"]) == 82
    for branch in (result["main"], result["baseline"]):
        assert len(branch["rows"]) == 82 and len(branch["fits"]) == 41
        assert all(row["expectedFuture"] is None for row in branch["rows"])
        assert any(row["invalidReason"] == "model_unavailable" for row in branch["rows"])
        assert all(fit["status"] == "invalid" for fit in branch["fits"])
        assert len({fit["estimator"] for fit in branch["fits"]}) == 1
    assert result["main"]["diagnostics"]["factorIncrement"]["status"] == "unavailable"


@pytest.mark.parametrize("name,stage,count", [("all_candidates_fail", "main", 8),
                                             ("baseline_fails_after_main", "baseline", 61)])
def test_failed_branch_keeps_diagnostics_without_claiming_complete_validation(cases, name, stage, count):
    result, receipt = cases(name)
    assert not result["complete"] and result["status"] == "failed_" + stage
    assert not result["publishable"] and not result["deploymentQualified"]
    assert result["failure"]["code"] == "INSUFFICIENT_FORECAST_DATA"
    assert result["failure"]["message"] == "没有候选完成全部时间验证折"
    assert len(result["failure"]["selectionTrials"]) == 8
    assert all(t["status"] == "invalid" for t in result["failure"]["selectionTrials"])
    assert receipt["actualModelFitCalls"] == count
    assert len(result["sampleStatusRows"]) == len(result["targets"]["rows"]) == 402
    assert result["factorIncrement"]["status"] == "unavailable"
    assert not result[stage]["complete"]
    assert result[stage]["diagnostics"]["currentOuter"]["trials"] == result["failure"]["selectionTrials"]
    if stage == "baseline":
        assert len(result["main"]["rows"]) == 82 and result["baseline"]["rows"] == []
        assert result["main"]["diagnostics"]["factorIncrement"]["status"] == "unavailable"


def test_no_predictors_is_one_branch_not_a_fake_increment(cases):
    result, receipt = cases("pair_without_predictors")
    assert result["complete"] and result["baseline"] is None
    assert result["baselineStatus"] == "not_applicable_no_predictors"
    assert result["main"]["diagnostics"]["factorIncrement"]["status"] == "not_applicable"
    assert receipt["actualModelFitCalls"] == 53
    assert result["plan"]["fitBudget"]["maximumFitAttempts"] == 91


@pytest.mark.parametrize("field", ["researchRoot", "targetDeclarationRoot", "targetScopeRoot", "sourceDomainRoot",
                                    "priceProjectionRoot", "featureInputRoot", "candidateSetHash", "fitPlanRoot",
                                    "fitContractRoot", "targetTiming", "portableFunctionStatus", "format", "stage"])
def test_stale_identity_rejects_before_any_fit(inputs, declared, field, monkeypatch):
    value = deepcopy(declared); value[field] = "changed"
    monkeypatch.setattr(models, "fit", lambda *a: pytest.fail("Invalid identity started fitting"))
    with pytest.raises(PairContractError):
        run_pair_research(*inputs, value)


@pytest.mark.parametrize("change", ["unknown", "execute", "version_bool", "horizon_bool", "family", "estimator",
                                      "candidate_override", "train_window", "folds", "holdout_nan", "preprocess_extra",
                                      "resource_bool", "resource_limit", "resource_fits", "resource_bytes", "resource_rows", "huge_integer"])
def test_strict_controls_and_resource_refusal_are_zero_fit(inputs, declared, change, monkeypatch):
    value = deepcopy(declared)
    if change == "unknown": value["target"] = {"kind": "frozen_basket"}
    if change == "execute": value["execution"]["enabled"] = True
    if change == "version_bool": value["version"] = True
    if change == "horizon_bool": value["horizonSessions"] = True
    if change == "family": value["model"]["family"] = "mean_reversion"
    if change == "estimator": value["model"]["estimator"] = "ridge"
    if change == "candidate_override": value["model"]["candidates"] = ["ridge"]
    if change == "train_window": value["model"]["trainWindow"] = 119
    if change == "folds": value["validation"]["innerFolds"] = 4
    if change == "holdout_nan": value["validation"]["holdoutFraction"] = float("nan")
    if change == "preprocess_extra": value["preprocess"]["fitOnFullDataset"] = True
    if change == "resource_bool": value["resources"]["maxFitAttempts"] = True
    if change == "resource_limit": value["resources"]["maxRssBytes"] *= 2
    if change == "resource_fits": value["resources"]["maxFitAttempts"] = 181
    if change == "resource_bytes": value["resources"]["maxResultBytes"] = 1
    if change == "resource_rows": value["resources"]["maxForecastRows"] = 1
    if change == "huge_integer": value["model"]["refitDays"] = 10 ** 400
    monkeypatch.setattr(models, "fit", lambda *a: pytest.fail("Invalid contract started fitting"))
    with pytest.raises((PairContractError, ResearchError)):
        run_pair_research(*inputs, value)


def test_insufficient_dates_and_empty_t_are_zero_fit(inputs, fixed_source, monkeypatch):
    from test_pair_research_samples import prepare
    monkeypatch.setattr(models, "fit", lambda *a: pytest.fail("Insufficient/empty domain started fitting"))
    declaration, research, _ = prepare(fixed_source, horizon=1)
    with pytest.raises((PairContractError, ResearchError)):
        fit_contract((fixed_source, declaration, research))
    source, declaration, research = inputs
    empty = declare_targets(source.price_input, [], quantity_cutoff=declaration["targetScope"]["quantityCutoff"],
                            origins=declaration["origins"], horizon_sessions=1)
    empty_research = declare_research(source, empty, factors=research["factors"], observation_days=1)
    with pytest.raises(PairContractError, match="Empty T"):
        fit_contract((source, empty, empty_research))


def test_legacy_validator_does_not_admit_the_new_contract(declared):
    from atlas_quant.statistical_quant.schema import validate
    with pytest.raises(ResearchError):
        validate(declared)


def test_final_byte_limit_includes_runtime_evidence_and_preserves_failed_partial():
    from atlas_quant.pair_research.research import _bounded_result
    value = {"status": "completed_local_forecast", "complete": True, "publishable": False,
             "main": {"rows": [{"evidence": "x" * 1000}], "diagnostics": {}},
             "fitEvidence": {"actualFitAttempts": 1, "events": [{"retained": "y" * 1000}]}}
    with pytest.raises(PairContractError) as exc:
        _bounded_result(value, 100)
    partial = exc.value.partial_result
    assert exc.value.code == "CAPACITY_PAIR_RESULT" and not partial["complete"]
    assert partial["status"] == "failed_result_size" and not partial["publishable"]
    assert partial["main"]["rows"] == value["main"]["rows"]
    assert partial["fitEvidence"] == value["fitEvidence"]
    assert partial["main"]["diagnostics"]["factorIncrement"]["status"] == "unavailable"
    valid = _bounded_result(value, 10000)
    assert valid["complete"] and valid["resultRoot"] == digest({k: v for k, v in valid.items() if k != "resultRoot"})


@pytest.fixture
def constant_fit_stub(monkeypatch):
    """Interruption tests never delegate to an estimator, OLS or provider."""
    from types import SimpleNamespace
    from atlas_quant.statistical_quant import factor_diagnostics
    calls = []

    def stub(spec, X, y, preprocess):
        calls.append(spec["id"])
        return SimpleNamespace(audit={"estimator": spec["estimator"], "params": spec["params"],
                                     "featureNames": list(X), "trainRows": len(X)},
                               predict=lambda frame: np.zeros((len(frame), 2)))

    monkeypatch.setattr(models, "fit", stub)
    monkeypatch.setattr(factor_diagnostics, "factor_diagnostics",
                        lambda *a, **kw: pytest.fail("Interrupted forecast reached factor diagnostics"))
    return calls


def assert_incomplete_evidence(result, stage):
    assert result["status"] == "failed_" + stage and not result["complete"]
    assert not result["publishable"] and not result["deploymentQualified"]
    assert result["factorIncrement"] == {"status": "unavailable", "reason": "incomplete_research"}
    assert len(result["targets"]["rows"]) == len(result["sampleStatusRows"]) == 402
    partial = result[stage]
    assert partial["status"] == "interrupted" and not partial["complete"]
    assert partial["diagnostics"]["factorIncrement"]["status"] == "unavailable"
    return partial


@pytest.mark.parametrize("branch,call,phase,outer_count,rows,fits", [
    ("baseline", 3, "outer_selection", 0, 0, 0),  # Original independent counterexample.
    ("main", 52, "terminal_fit", 2, 40, 1),      # Original independent counterexample.
    ("baseline", 52, "terminal_fit", 2, 40, 1),
    ("main", 2, "outer_selection", 0, 0, 0),
    ("main", 17, "outer_fit", 0, 0, 0),
    ("main", 18, "outer_selection", 1, 0, 0),
    ("main", 36, "final_selection", 2, 0, 0),
])
def test_stub_interruptions_keep_prior_evidence_without_retry(
        inputs, declared, monkeypatch, constant_fit_stub, branch, call, phase, outer_count, rows, fits):
    original = validation._train
    counts = {"main": 0, "baseline": 0}
    error = ResearchError("CAPACITY_PAIR_TIMEOUT", "predeclared stub interruption")

    def interrupted(samples, *args, **kwargs):
        which = "main" if any(c.startswith("factor:") for c in samples.X) else "baseline"
        counts[which] += 1
        if which == branch and counts[which] == call:
            raise error
        return original(samples, *args, **kwargs)

    monkeypatch.setattr(validation, "_train", interrupted)
    result = run_pair_research(*inputs, declared)
    partial = assert_incomplete_evidence(result, branch)
    diagnostics = partial["diagnostics"]
    assert result["failure"]["code"] == error.code and result["failure"]["message"] == str(error)
    assert diagnostics["phase"] == result["failure"]["forecastPhase"] == phase
    assert len(diagnostics["outerFolds"]) == outer_count
    assert len(partial["rows"]) == rows and len(partial["fits"]) == fits
    assert counts[branch] == call  # No replay, fallback or recovery fitting.
    assert result["fitEvidence"]["actualFitAttempts"] == len(constant_fit_stub) == call - 1 + (53 if branch == "baseline" else 0)
    if branch == "baseline":
        assert len(result["main"]["rows"]) == 82
        assert result["main"]["diagnostics"]["factorIncrement"]["status"] == "unavailable"
    if phase.endswith("selection"):
        trials = result["failure"]["selectionTrials"]
        assert trials[-1]["status"] == "interrupted" and trials[-1]["score"] is None
        assert "selected" not in trials[-1]
        expected_folds = 1 if call in {2, 36} else 0
        assert len(trials[-1]["folds"]) == len(trials[-1]["completedFoldScores"]) == expected_folds
        if branch == "baseline" and call == 3:
            assert trials[0]["status"] == "valid" and len(trials[0]["folds"]) == 2
            assert all("labelEndMax" in fold and "score" in fold for fold in trials[0]["folds"])
    else:
        assert result["failure"]["selectionTrials"] is None
        if phase == "outer_fit":
            assert len(diagnostics["currentOuter"]["trials"]) == 8
            assert diagnostics["currentOuter"]["selection"]
        else:
            assert len(diagnostics["finalTrials"]) == 8 and diagnostics["selectedModel"]
            assert all(r["modelFitId"] == partial["fits"][0]["id"] for r in partial["rows"])


@pytest.mark.parametrize("call,error_type,phase", [(2, MemoryError, "outer_selection"),
                                                  (17, ResearchError, "outer_fit"),
                                                  (51, ResearchError, "terminal_fit")])
def test_stub_after_fit_interrupt_retains_completed_audit_and_attempt_count(
        inputs, declared, monkeypatch, constant_fit_stub, call, error_type, phase):
    from atlas_quant.capacity.core import FitRuntime
    original = FitRuntime.after_fit
    error = MemoryError("stub memory interruption") if error_type is MemoryError else ResearchError("CAPACITY_PAIR_MEMORY", "stub RSS interruption")

    def interrupted(runtime):
        original(runtime)
        if len(runtime.events) == call:
            raise error

    monkeypatch.setattr(FitRuntime, "after_fit", interrupted)
    result = run_pair_research(*inputs, declared)
    partial = assert_incomplete_evidence(result, "main")
    diagnostics = partial["diagnostics"]
    assert diagnostics["phase"] == phase
    assert result["fitEvidence"]["actualFitAttempts"] == len(constant_fit_stub) == call
    assert result["failure"]["code"] == ("MemoryError" if error_type is MemoryError else error.code)
    current = diagnostics["currentTerminal"] or diagnostics["currentOuter"]
    audit = current["interruptedFitAudit"]
    assert audit["trainRows"] > 0 and audit["labelEndMax"] < audit["informationCutoff"]
    if phase == "outer_selection":
        trial = result["failure"]["selectionTrials"][-1]
        assert len(trial["folds"]) == 1
        assert trial["interruptedFold"]["fit"] == audit
    if phase == "terminal_fit":
        assert len(diagnostics["outerFolds"]) == 2 and len(diagnostics["finalTrials"]) == 8
        assert not partial["fits"]  # Resource-rejected fit is evidence, never an admitted model.


@pytest.mark.parametrize("where,rows,fits,phase", [("outer_score", 0, 0, "outer_score"),
                                                  ("record", 1, 1, "terminal_records"),
                                                  ("uncertainty", 82, 3, "terminal_uncertainty")])
def test_stub_scoring_or_record_interruption_keeps_completed_outputs(
        inputs, declared, monkeypatch, constant_fit_stub, where, rows, fits, phase):
    error = ResearchError("CAPACITY_PAIR_TIMEOUT", "stub output interruption")
    if where == "outer_score":
        def interrupted(*a, **kw): raise error
        monkeypatch.setattr(models, "metrics", interrupted)
    elif where == "record":
        original = validation.digest
        count = 0

        def interrupted(value):
            nonlocal count
            if "modelFitId" in value:  # Row two fails after row one was produced.
                count += 1
                if count == 2: raise error
            return original(value)
        monkeypatch.setattr(validation, "digest", interrupted)
    else:
        from atlas_quant.statistical_quant import inference
        def interrupted(*a, **kw): raise error
        monkeypatch.setattr(inference, "evaluate_forecast_uncertainty", interrupted)
    result = run_pair_research(*inputs, declared)
    partial = assert_incomplete_evidence(result, "main")
    diagnostics = partial["diagnostics"]
    assert diagnostics["phase"] == phase
    assert len(partial["rows"]) == rows and len(partial["fits"]) == fits
    if where == "outer_score":
        assert diagnostics["currentOuter"]["fit"]["trainRows"] > 0
        assert len(diagnostics["currentOuter"]["trials"]) == 8
    if where == "uncertainty":
        assert diagnostics["metrics"] and len(diagnostics["outerFolds"]) == 2
    assert result["fitEvidence"]["actualFitAttempts"] == len(constant_fit_stub)
