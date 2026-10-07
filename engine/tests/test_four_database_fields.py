"""Explicit SYNTHETIC fixtures exercise numerical contracts, never hosted data."""
import copy

import numpy as np
import pandas as pd
import pytest

from atlas_quant.engine import ResearchError, _prepare_data, run_research, validate_strategy
from atlas_quant.factors import FactorError, evaluate_expression, validate_expression
from atlas_quant.fixtures import make_demo_data
from atlas_quant.provider import ProviderError, _load, validate_upload


@pytest.fixture(scope="module")
def four_database_fixture():
    strategy = {"schemaVersion": 1, "name": "SYNTHETIC_FOUR_DATABASE_TEST_ONLY",
                "universe": {"symbols": ["000001.SZ", "000002.SZ", "600000.SH"], "start": "20230101", "end": "20241231"},
                "factors": [{"id": "cross_database", "expression": "pcd_margin+ext_growth+model_return+returns(close,5)", "direction": 1}],
                "model": {"mode": "manual", "candidates": ["factor_score"], "horizon": 5}}
    frame, provenance = make_demo_data(strategy)
    day = frame.groupby("ts_code").cumcount().to_numpy()
    symbol = frame.ts_code.map({"000001.SZ": 1., "000002.SZ": 2., "600000.SH": 3.}).to_numpy()
    frame["pcd_margin"] = .1 + symbol*.01
    frame["ext_growth"] = .02 + np.sin(day/23+symbol)*.01
    frame["model_return"] = np.cos(day/11+symbol)*.005
    provenance["source"] = "SYNTHETIC_FOUR_DATABASE_TEST_ONLY"
    provenance["externalFields"] = {}
    for alias, source, path in [("pcd_margin", "SYNTHETIC_PCD", "fixture.disclosure.margin"),
                                ("ext_growth", "SYNTHETIC_EXT", "fixture.consensus.growth"),
                                ("model_return", "SYNTHETIC_MODEL", "fixture.run_001.predictions.return")]:
        frame[alias+"__available_date"] = frame.trade_date
        provenance["externalFields"][alias] = {"source": source, "path": path, "dataType": "number",
            "availabilityPolicy": "point_in_time_asof", "availableDateColumn": alias+"__available_date", "unit": "fraction"}
    return strategy, frame, provenance


def test_four_logical_databases_combine_with_exact_numeric_result(four_database_fixture):
    strategy, frame, provenance = four_database_fixture
    validated, p = validate_upload(strategy, {"rows": frame.to_dict("records"), "provenance": provenance})
    assert p["synthetic"] and p["classification"] == "SYNTHETIC_USER_UPLOAD_UNVERIFIED"
    panel, _, audit = _prepare_data(validated, validate_strategy(strategy), p)
    actual = evaluate_expression(strategy["factors"][0]["expression"], panel)
    expected = panel.pcd_margin+panel.ext_growth+panel.model_return+panel.close/panel.close.groupby(level="ts_code").shift(5)-1
    pd.testing.assert_series_equal(actual, expected, check_names=False)
    assert len(audit["externalFieldAudit"]) == 3
    model = next(a for a in audit["externalFieldAudit"] if a["field"] == "model_return")
    assert model["path"] == "fixture.run_001.predictions.return" and not model["independentTrainingHistoryVerified"]


def test_four_database_actual_engine_report_keeps_synthetic_and_source_audit(four_database_fixture):
    strategy, frame, provenance = four_database_fixture
    validated, p = validate_upload(strategy, {"rows": frame.to_dict("records"), "provenance": provenance})
    report = run_research(strategy, validated, p)
    assert report["provenance"]["synthetic"] is True
    assert {a["field"] for a in report["provenance"]["externalFieldAudit"]} == {"pcd_margin", "ext_growth", "model_return"}
    assert report["selection"]["trialCount"] == 1
    assert not report["selection"]["holdoutUsedForSelection"]


@pytest.mark.parametrize("alias", ["ext_growth", "model_return"])
def test_future_external_or_model_value_is_rejected_at_upload_and_engine(four_database_fixture, alias):
    strategy, frame, provenance = four_database_fixture
    bad = frame.copy(); bad.loc[bad.index[0], alias+"__available_date"] = "20250101"
    with pytest.raises(ProviderError) as error:
        validate_upload(strategy, {"rows": bad.to_dict("records"), "provenance": provenance})
    assert error.value.code == "EXTERNAL_FUTURE_DATA"
    with pytest.raises(ResearchError) as error:
        _prepare_data(bad, validate_strategy(strategy), provenance)
    assert error.value.code == "FUTURE_EXTERNAL_FIELD"


@pytest.mark.parametrize("key", ["source", "path"])
def test_model_input_requires_explicit_nonblank_source_record(four_database_fixture, key):
    strategy, frame, provenance = four_database_fixture
    bad = copy.deepcopy(provenance); bad["externalFields"]["model_return"][key] = " "
    with pytest.raises(ProviderError) as error:
        validate_upload(strategy, {"rows": frame.to_dict("records"), "provenance": bad})
    assert error.value.code == "EXTERNAL_FIELD_MAPPING"
    with pytest.raises(ResearchError) as error:
        _prepare_data(frame, validate_strategy(strategy), bad)
    assert error.value.code == "EXTERNAL_FIELD_PIT_REQUIRED"


@pytest.mark.parametrize("alias", ["ext_growth", "model_return"])
def test_tushare_does_not_make_up_external_or_model_values(four_database_fixture, alias):
    strategy, _, _ = four_database_fixture
    strategy = copy.deepcopy(strategy); strategy["factors"] = [{"id": "external", "expression": alias}]
    class NoNetworkExpected:
        def call(self, *args):
            raise AssertionError("unsupported source must fail before network access")
    with pytest.raises(ProviderError) as error:
        _load(strategy, NoNetworkExpected(), None, "test-only")
    assert error.value.code == "EXTERNAL_DATA_REQUIRED"


@pytest.mark.parametrize("name", ["ext_", "model_", "ext_"+"a"*61, "model_Capital", "model_run.attribute", "ext_source()"])
def test_new_external_namespaces_remain_narrow(name):
    with pytest.raises(FactorError):
        validate_expression(name)
