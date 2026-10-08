"""Independent exported-bundle audit catches validly rehashed semantic damage."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from atlas_quant.bundle import build_bundle
from atlas_quant.fixtures import make_demo_data
from atlas_quant.runner_artifacts import freeze_input
from atlas_quant.statistical_quant import execute_forecasts, validate_statistical_quant
from atlas_quant.statistical_quant.core import run_statistical_quant
from atlas_quant.statistical_quant.schema import digest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("independent_bundle_audit", ROOT / "scripts/bundle_audit.py")
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


@pytest.fixture(scope="module")
def research():
    strategy = json.loads((ROOT / "engine/examples/statistical-quant.json").read_text())
    strategy["factors"] = [{"id": "momentum", "expression": "returns(close,20)",
                            "direction": 1, "role": "predictor"}]
    strategy = validate_statistical_quant(strategy)
    data, provenance = make_demo_data(strategy)
    plans = []
    forecast = run_statistical_quant(strategy, data, provenance, plan_sink=plans.append)
    snapshot = freeze_input(strategy, data, provenance)
    execution_strategy = copy.deepcopy(strategy)
    execution_strategy["execution"]["enabled"] = True
    execution = execute_forecasts(execution_strategy, data, forecast["forecasts"], provenance)
    return forecast, execution, snapshot, plans[0]


def export(path, report, snapshot, plan, chunk_target=16384):
    path.mkdir()

    def write(name, ordinal, raw):
        target = path / "chunks" / name / f"{ordinal}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    def read(name, ordinal):
        return (path / "chunks" / name / f"{ordinal}.json").read_bytes()

    raw = build_bundle(report, snapshot, plan, write, read, chunk_target=chunk_target)
    (path / "manifest.json").write_bytes(raw)
    return path


def reidentify(report):
    artifact = report["forecasts"]
    artifact["artifactId"] = digest({k: v for k, v in artifact.items() if k != "artifactId"})
    report["execution"]["forecastArtifactId"] = artifact["artifactId"]


def test_full_factors_baseline_and_independent_execution_audit(tmp_path, research):
    forecast, execution, snapshot, plan = research
    original = export(tmp_path / "forecast", forecast, snapshot, plan)
    replay = export(tmp_path / "execution", execution, None, plan)
    result = audit.audit_bundle(replay, original)
    assert result["status"] == "passed" and result["trades"] > 0
    assert result["baselineRows"] == result["forecastRows"] > 0
    assert result["sourceForecastAndPlanIdentical"] is True
    assert result["maxChunkBytes"] <= 8 * 1024 * 1024
    completed = subprocess.run([sys.executable, str(ROOT / "scripts/audit-bundle.py"), str(replay),
                                "--source-bundle", str(original)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["engineImports"] is False


@pytest.mark.parametrize("damage,reason", [
    ("omit_tail", "Plan/forecast count"),
    ("bad_prediction", "forecast error"),
    ("bad_target_reference", "Missing targets reference"),
    ("future_fit", "future model fit"),
    ("duplicate", "UNIQUE constraint failed"),
    ("missing_baseline", "Missing baseline predictions"),
    ("changed_plan", "independent origin plan"),
])
def test_rehashed_semantic_damage_is_not_accepted(tmp_path, research, damage, reason):
    report, _, snapshot, original_plan = research
    report, plan = copy.deepcopy(report), copy.deepcopy(original_plan)
    artifact = report["forecasts"]
    row = next(r for r in artifact["rows"] if r["status"] == "valid")
    if damage == "omit_tail":
        artifact["rows"].pop()
        artifact["totalRows"] -= 1
    elif damage == "bad_prediction":
        row["forecastError"] += 1
    elif damage == "bad_target_reference":
        row["targetId"] = "target_unknown"
        plan["origins"][0]["targetId"] = "target_unknown"
    elif damage == "future_fit":
        fit = next(f for f in artifact["modelFits"] if f["id"] == row["modelFitId"])
        fit["labelEndMax"] = "20990101"
    elif damage == "duplicate":
        artifact["rows"][1] = copy.deepcopy(artifact["rows"][0])
        plan["origins"][1] = copy.deepcopy(plan["origins"][0])
    elif damage == "missing_baseline":
        artifact["diagnostics"]["factorIncrement"]["baselineRows"].pop()
    else:
        plan["origins"][0]["entryDate"] = "20990101"
    reidentify(report)
    exported = export(tmp_path / "damage", report, snapshot, plan)
    with pytest.raises((ValueError, sqlite3.IntegrityError), match=reason):
        audit.audit_bundle(exported)


def test_rehashed_cash_ledger_damage_is_not_accepted(tmp_path, research):
    _, report, _, plan = research
    report = copy.deepcopy(report)
    report["execution"]["ledger"][3]["cash"] += 100
    report["equity"][3]["cash"] += 100
    exported = export(tmp_path / "cash", report, None, plan)
    with pytest.raises(ValueError, match="daily cash"):
        audit.audit_bundle(exported)


def test_actual_collection_path_is_checked(tmp_path, research):
    report, _, snapshot, plan = research
    exported = export(tmp_path / "layout", report, snapshot, plan)
    manifest = json.loads((exported / "manifest.json").read_text())
    for part in manifest["documents"]["forecast"]["parts"]:
        if "literal" in part:
            part["literal"] = part["literal"].replace('"rows":', '"shadowRows":')
    (exported / "manifest.json").write_bytes(audit.canonical(manifest))
    with pytest.raises(ValueError, match="Actual collection layout"):
        audit.audit_bundle(exported)


def test_tampered_chunk_rejected_before_readback(tmp_path, research):
    report, _, snapshot, plan = research
    exported = export(tmp_path / "bytes", report, snapshot, plan)
    file = exported / "chunks/forecasts/0.json"
    file.write_bytes(file.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Chunk length/hash"):
        audit.audit_bundle(exported)


def test_export_path_escape_is_rejected(tmp_path, research):
    report, _, snapshot, plan = research
    exported = export(tmp_path / "symlink", report, snapshot, plan)
    file = exported / "chunks/forecasts/0.json"
    external = tmp_path / "external.json"
    external.write_bytes(file.read_bytes())
    file.unlink()
    file.symlink_to(external)
    with pytest.raises(ValueError, match="escapes export directory"):
        audit.audit_bundle(exported)


@pytest.mark.parametrize("kind", ["single_asset", "fixed"])
def test_explicit_targets_do_not_invent_an_estimated_formation_window(tmp_path, research, kind):
    strategy = copy.deepcopy(research[0]["strategy"])
    strategy["model"]["family"] = "mean_reversion"
    strategy["factors"] = []
    if kind == "single_asset":
        strategy["target"] = {"kind": "asset_price", "horizonSessions": 5}
    else:
        symbols = strategy["universe"]["symbols"]
        strategy["target"] = {"kind": "frozen_basket", "horizonSessions": 5,
            "basket": {"method": "fixed", "symbols": symbols,
                       "quantities": {symbol: 1 if i == 0 else -1 for i, symbol in enumerate(symbols)}}}
    strategy = validate_statistical_quant(strategy)
    data, provenance = make_demo_data(strategy)
    plans = []
    result = run_statistical_quant(strategy, data, provenance, plan_sink=plans.append)
    snapshot = freeze_input(strategy, data, provenance)
    assert all(d["formationEnd"] is None for d in result["forecasts"]["targetDefinitions"])
    output = export(tmp_path / kind, result, snapshot, plans[0])
    assert audit.audit_bundle(output)["status"] == "passed"
