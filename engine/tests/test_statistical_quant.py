"""Forecast-first causal targets, numerical identities and isolated replay."""
import copy
import json
import subprocess
import shutil
import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from atlas_quant.engine import ResearchError, _prepare_data, run_research
from atlas_quant.fixtures import make_demo_data
from atlas_quant.statistical_quant import execute_forecasts, validate_statistical_quant
from atlas_quant.statistical_quant.targets import build_samples
from atlas_quant.statistical_quant.schema import digest


def strategy(kind="asset_price", estimator="ridge"):
    s = {"schemaVersion": 2, "name": "Forecast research", "universe": {"symbols": ["000001.SZ", "600000.SH", "600036.SH"], "start": "20220101", "end": "20250930"},
         "research": {"mode": "statistical_quant"}, "target": {"kind": kind, "horizonSessions": 5},
         "model": {"family": "mean_reversion", "estimator": estimator}, "execution": {"enabled": False}}
    if kind == "frozen_basket":
        s["target"]["basket"] = {"method": "pair_ols", "symbols": s["universe"]["symbols"][:2], "formationDays": 80}
        s["model"]["family"] = "pair_reversion"
    return s


CONTRACT_CASES = json.loads((Path(__file__).resolve().parents[2]/"tests"/"fixtures"/"statistical-quant-configs.json").read_text())


@pytest.mark.parametrize("case", CONTRACT_CASES["valid"], ids=lambda case: case["name"])
def test_shared_edge_python_valid_contract_and_normalization_idempotence(case):
    normalized = validate_statistical_quant(case["strategy"])
    assert validate_statistical_quant(normalized) == normalized


@pytest.mark.parametrize("case", CONTRACT_CASES["invalid"], ids=lambda case: case["name"])
def test_shared_edge_python_invalid_contract(case):
    with pytest.raises(ResearchError):
        validate_statistical_quant(case["strategy"])


@pytest.fixture(scope="module")
def source():
    return make_demo_data(strategy())


@pytest.fixture(scope="module")
def forecast_result(source):
    data, provenance = source
    return run_research(strategy(), data, provenance)


def test_forecast_only_is_complete_and_immutable_with_two_price_targets(forecast_result):
    r = forecast_result
    assert r["schemaVersion"] == 2 and r["engineVersion"] == "0.4.0" and r["status"] == "completed"
    assert r["metrics"] is None and r["trades"] == r["equity"] == []
    a = r["forecasts"]; rows = a["rows"]
    assert len(rows) == a["totalRows"] and not a["truncated"]
    assert digest({k: v for k, v in a.items() if k != "artifactId"}) == a["artifactId"]
    assert len(set(x["forecastId"] for x in rows)) == len(rows)
    valid = [x for x in rows if x["status"] == "valid"]
    assert valid and len(valid) < len(rows)
    for f in valid:
        assert f["date"] < f["entryDate"] < f["targetDate"]
        assert f["edgeGap"] == pytest.approx(f["currentState"]-f["expectedFuture"])
        assert f["expectedGrossPnl"] == pytest.approx(f["expectedFuture"]-f["expectedEntry"])
        if f["realizedFuture"] is not None:
            assert f["realizedFuture"]-f["currentState"] == pytest.approx(-f["edgeGap"]+f["forecastError"])
    assert any(f["invalidReason"] == "target_outside_available_calendar" for f in rows)
    json.dumps(r, allow_nan=False)
    assert r["validation"]["completeArtifactPath"] == "forecasts.diagnostics"
    assert all("trials" not in fold for fold in r["validation"]["outerFolds"])
    assert all("folds" not in trial for trial in r["selection"]["trials"])
    assert all("trials" in fold for fold in a["diagnostics"]["outerFolds"])


def test_every_training_label_is_mature_at_inner_outer_and_live_fit(forecast_result):
    a = forecast_result["forecasts"]
    for fit in a["modelFits"]:
        assert fit["labelEndMax"] < fit["fitDate"]
    for trial in a["diagnostics"]["finalTrials"]:
        for fold in trial["folds"]:
            assert fold["labelEndMax"] < fold["testStart"]
    for outer in a["diagnostics"]["outerFolds"]:
        assert outer["fit"]["labelEndMax"] < outer["testStart"]
        for trial in outer["trials"]:
            for fold in trial["folds"]:
                assert fold["labelEndMax"] < fold["testStart"] < outer["testStart"]


def test_targets_use_same_frozen_q_and_explicit_next_open_labels(source):
    data, p = source
    s = validate_statistical_quant(strategy("frozen_basket"))
    panel, dates, _ = _prepare_data(data, s, p)
    samples = build_samples(panel, dates, s)
    row = samples.meta.iloc[50]; definition = samples.definitions[row.targetId]
    q = np.asarray(definition["quantities"]); members = definition["symbols"]
    current = panel.close.unstack().loc[row.date, members].to_numpy()
    entry = panel.open.unstack().loc[row.entryDate, members].to_numpy()
    exit_ = panel.open.unstack().loc[row.targetDate, members].to_numpy()
    assert definition["formationEnd"] < row.date
    assert row.currentState == pytest.approx(q @ current)
    assert row.scale == pytest.approx(np.abs(q*current).sum())
    np.testing.assert_allclose(samples.y.iloc[50], [(q@(entry-current))/row.scale, (q@(exit_-current))/row.scale])
    assert row.realizedFuture-row.realizedEntry == pytest.approx(q@(exit_-entry))


def test_pca_quantity_units_exposure_identity_and_future_causality(source):
    data, p = source
    s = strategy("frozen_basket")
    s["model"]["family"] = "mean_reversion"
    s["target"]["basket"] = {"method": "pca_residual", "symbols": s["universe"]["symbols"], "formationDays": 80, "components": 1}
    s = validate_statistical_quant(s)
    panel, dates, _ = _prepare_data(data, s, p)
    original = build_samples(panel, dates, s)
    cutoff = dates[300]
    changed = panel.copy()
    changed.loc[changed.index.get_level_values("trade_date") >= cutoff, "close"] *= 1.7
    altered = build_samples(changed, dates, s)
    prior = original.meta.date < cutoff
    pd.testing.assert_frame_equal(original.X.loc[prior], altered.X.loc[prior])
    assert original.meta.loc[prior, "targetId"].tolist() == altered.meta.loc[prior, "targetId"].tolist()
    for definition in list(original.definitions.values())[:12]:
        prices = panel.close.unstack().loc[definition["formationEnd"], definition["symbols"]].to_numpy()
        dollar = np.asarray(definition["quantities"])*prices
        B = np.asarray(definition["hedgeAudit"]["loadings"])
        np.testing.assert_allclose(B.T @ dollar, 0, atol=1e-10)


def test_later_prices_cannot_change_earlier_predictions(source, forecast_result):
    data, p = source
    rows = forecast_result["forecasts"]["rows"]
    cutoff = sorted(set(x["date"] for x in rows))[40]
    changed = data.copy()
    future = changed.trade_date >= cutoff
    for field in ("open", "high", "low", "close", "raw_close"):
        changed.loc[future, field] *= 1.35
    other = run_research(strategy(), changed, p)
    fields = ("forecastId", "date", "targetId", "modelFitId", "expectedEntry", "expectedFuture", "expectedGrossPnl")
    before = [{k: row[k] for k in fields} for row in rows if row["date"] < cutoff]
    after = [{k: row[k] for k in fields} for row in other["forecasts"]["rows"] if row["date"] < cutoff]
    assert before == after


def test_execution_replay_never_fits_or_reconstructs_targets(source, forecast_result, monkeypatch):
    from atlas_quant.statistical_quant import models, targets, validation
    def forbidden(*args, **kwargs):
        pytest.fail("execution-only replay called prediction code")
    monkeypatch.setattr(models, "fit", forbidden)
    monkeypatch.setattr(targets, "_construct", forbidden)
    monkeypatch.setattr(validation, "forecast", forbidden)
    s = copy.deepcopy(forecast_result["strategy"]); s["execution"]["enabled"] = True
    result = execute_forecasts(s, source[0], forecast_result["forecasts"], source[1])
    assert result["research"]["executionOnly"] and not result["research"]["predictionRefitPerformed"]
    assert result["forecasts"] == forecast_result["forecasts"]
    ids = {x["forecastId"] for x in result["forecasts"]["rows"]}
    assert result["trades"] and all(t["forecastId"] in ids for t in result["trades"])
    assert result["execution"]["forecastArtifactId"] == forecast_result["forecasts"]["artifactId"]


def test_failed_terminal_refit_preserves_all_origins_without_estimator_fallback(source, forecast_result, monkeypatch):
    from atlas_quant.statistical_quant import validation
    original = validation._train
    cutoff = forecast_result["validation"]["holdoutStart"]
    def unavailable(samples, spec, train_dates, date, config):
        if date >= cutoff:
            raise ResearchError("MISSING_MODEL_DATA", "fixture simulates unavailable rolling training inputs")
        return original(samples, spec, train_dates, date, config)
    monkeypatch.setattr(validation, "_train", unavailable)
    r = run_research(strategy(), source[0], source[1])
    assert r["forecasts"]["totalRows"] == forecast_result["forecasts"]["totalRows"]
    assert r["validation"]["invalidModelFits"] > 0
    assert all(row["status"] == "invalid" and row["expectedFuture"] is None for row in r["forecasts"]["rows"])
    assert any(row["invalidReason"] == "model_unavailable" for row in r["forecasts"]["rows"])
    assert r["selection"]["evidenceStatus"] == "NO_VALIDATED_FORECAST_EDGE"


def test_forecast_count_budget_fails_before_expensive_fitting_without_truncation(source, monkeypatch):
    from atlas_quant.statistical_quant import models, validation
    monkeypatch.setattr(validation, "MAX_FORECASTS", 10)
    monkeypatch.setattr(models, "fit", lambda *args: pytest.fail("budget check must run before fitting"))
    with pytest.raises(ResearchError) as caught:
        run_research(strategy(), source[0], source[1])
    assert caught.value.code == "FORECAST_BUDGET"


def test_runner_rejects_oversized_complete_report_without_mutating_it():
    from atlas_quant.runner import MAX_RESULT_BYTES, RunnerError, _validate_result
    oversized = {"forecasts": {"rows": ["x"*MAX_RESULT_BYTES], "totalRows": 1, "truncated": False}}
    with pytest.raises(RunnerError) as caught:
        _validate_result(oversized)
    assert caught.value.code == "RESULT_SIZE"
    assert len(oversized["forecasts"]["rows"][0]) == MAX_RESULT_BYTES and not oversized["forecasts"]["truncated"]


def test_worker_json_parse_stringify_of_complete_report_and_snapshot_replays_exactly(source, forecast_result):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime needed for actual Worker numeric transport test")
    data, p = source
    payload = {"report": forecast_result, "snapshot": {"rows": data.to_dict(orient="records"), "provenance": p}}
    transported = subprocess.run([node, "-e", "let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s))));"],
                                input=json.dumps(payload, allow_nan=False), text=True, capture_output=True, check=True)
    x = json.loads(transported.stdout)
    s = copy.deepcopy(forecast_result["strategy"]); s["execution"]["enabled"] = True
    direct = execute_forecasts(s, data, forecast_result["forecasts"], p)
    replay = execute_forecasts(s, pd.DataFrame(x["snapshot"]["rows"]), x["report"]["forecasts"], x["snapshot"]["provenance"])
    assert direct["forecasts"]["artifactId"] == replay["forecasts"]["artifactId"]
    assert direct["trades"] == replay["trades"] and direct["equity"] == replay["equity"]


def test_signed_zero_does_not_change_snapshot_fingerprint(source):
    data, p = copy.deepcopy(source)
    s = validate_statistical_quant(strategy())
    data.loc[data.index[0], "vol"] = -0.0
    _, _, negative = _prepare_data(data, s, p)
    data.loc[data.index[0], "vol"] = 0.0
    _, _, positive = _prepare_data(data, s, p)
    assert negative["dataSha256"] == positive["dataSha256"]


@pytest.mark.parametrize("corruption", ["record", "data", "calendar", "prediction_config"])
def test_replay_rejects_mutated_artifact_data_or_prediction_config(source, forecast_result, corruption):
    data, p = copy.deepcopy(source)
    s = copy.deepcopy(forecast_result["strategy"]); artifact = copy.deepcopy(forecast_result["forecasts"])
    if corruption == "record":
        artifact["rows"][0]["expectedFuture"] += 1
    elif corruption == "data":
        data.loc[data.index[10], "vol"] += 1
    elif corruption == "calendar":
        p["tradingDates"] = p["tradingDates"][:-1]
        data = data[data.trade_date <= p["tradingDates"][-1]]
    else:
        s["target"]["horizonSessions"] += 1
    with pytest.raises(ResearchError) as caught:
        execute_forecasts(s, data, artifact, p)
    assert caught.value.code == "FORECAST_ARTIFACT_MISMATCH"


@pytest.mark.parametrize("family", ["fundamental", "event"])
def test_data_dependent_model_families_do_not_fabricate_inputs(family):
    s = strategy(); s["model"]["family"] = family
    with pytest.raises(ResearchError) as caught:
        validate_statistical_quant(s)
    assert caught.value.code == "MISSING_MODEL_DATA"


def test_fixed_zero_or_negative_spread_normalizes_by_gross_not_spread(source):
    s = strategy("frozen_basket")
    s["model"]["family"] = "mean_reversion"
    s["target"]["basket"] = {"method": "fixed", "symbols": ["000001.SZ", "600000.SH"], "quantities": {"000001.SZ": 1., "600000.SH": -1.}}
    data, p = copy.deepcopy(source)
    one = data[data.ts_code == "000001.SZ"].set_index("trade_date")
    mask = data.ts_code == "600000.SH"
    for col in ("open", "high", "low", "close", "raw_close"):
        data.loc[mask, col] = data.loc[mask, "trade_date"].map(one[col]).to_numpy()
    normalized = validate_statistical_quant(s); panel, dates, _ = _prepare_data(data, normalized, p)
    samples = build_samples(panel, dates, normalized)
    assert np.allclose(samples.meta.currentState, 0)
    assert (samples.meta.scale > 0).all()
    assert np.isfinite(samples.X.to_numpy()).all()
    assert np.allclose(samples.y.dropna(), 0)


def test_all_finite_estimator_candidates_are_real_two_output_fits():
    from atlas_quant.statistical_quant.models import candidates, fit
    rng = np.random.default_rng(17)
    X = pd.DataFrame(rng.normal(size=(140, 3)), columns=["state_deviation20", "trend5", "risk"])
    y = pd.DataFrame(np.c_[X.iloc[:, 0]*.03, -X.iloc[:, 0]*.1+X.iloc[:, 1]*.02], columns=["entry", "exit"])
    pre = validate_statistical_quant(strategy())["preprocess"]
    for spec in candidates("auto"):
        model = fit(spec, X.iloc[:100], y.iloc[:100], pre)
        pred = model.predict(X.iloc[100:])
        assert pred.shape == (40, 2) and np.isfinite(pred).all()
        if spec["estimator"] == "ridge":
            assert model.audit["stateEffects"][0]["negativeEffectObserved"]
