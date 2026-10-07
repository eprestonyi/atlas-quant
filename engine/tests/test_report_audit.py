"""The standalone auditor must detect corruption independently of the engine."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from atlas_quant.engine import run_research
from atlas_quant.fixtures import make_demo_data

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("report_audit", ROOT / "scripts/audit-report.py")
AUDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDITOR)


@pytest.fixture(scope="module")
def report():
    strategy = json.loads((ROOT / "engine/examples/statistical-quant.json").read_text())
    strategy["execution"]["enabled"] = True
    data, provenance = make_demo_data(strategy)
    return run_research(strategy, data, provenance)


def test_exported_cash_ledger_reconciles_without_engine_helpers(report):
    checked = AUDITOR.audit(report)
    assert checked["trades"] > 0 and checked["checks"] > 1000


def test_display_sort_must_not_mutate_artifact(report):
    bad = copy.deepcopy(report)
    bad["forecasts"]["rows"].reverse()
    with pytest.raises(AssertionError, match="content/order hash"):
        AUDITOR.audit(bad)


@pytest.mark.parametrize("section,key", [("trade", "cashAfter"), ("ledger", "equity"), ("trade", "commission")])
def test_independent_reconciliation_rejects_tampered_amounts(report, section, key):
    bad = copy.deepcopy(report)
    rows = bad["trades"] if section == "trade" else bad["execution"]["ledger"]
    rows[0][key] += 1
    with pytest.raises(AssertionError):
        AUDITOR.audit(bad)


def test_trade_without_frozen_forecast_is_rejected(report):
    bad = copy.deepcopy(report)
    bad["trades"][0]["forecastId"] = "not_present"
    with pytest.raises(AssertionError, match="Trade without forecast"):
        AUDITOR.audit(bad)


def test_chart_cannot_disagree_with_a_correct_execution_ledger(report):
    bad = copy.deepcopy(report)
    bad["equity"][0]["equity"] += 500
    with pytest.raises(AssertionError, match="chart/ledger equity"):
        AUDITOR.audit(bad)


def test_omitted_idle_session_is_not_a_complete_report(report):
    bad = copy.deepcopy(report)
    # The first origin precedes its next-open entry and has no trades.
    assert not any(t["date"] == bad["execution"]["ledger"][0]["date"] for t in bad["trades"])
    bad["execution"]["ledger"].pop(0)
    bad["equity"].pop(0)
    with pytest.raises(AssertionError, match="every declared trading session"):
        AUDITOR.audit(bad)
