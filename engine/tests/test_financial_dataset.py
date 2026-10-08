"""Offline composition acceptance: bounded synthetic inputs, zero provider reads."""

from copy import deepcopy

import pandas as pd
import pytest

from atlas_quant.fixtures import make_demo_data
from atlas_quant.financial_statements import ContractError, RECIPES
from atlas_quant.financial_statements.dataset import (
    compose_financial_dataset,
    DatasetBudget,
)
from atlas_quant.financial_statements.prepare import AdapterError, _safe_rows
from test_financial_adapter import run, STRATEGY
from test_financial_package import freeze, declarations


@pytest.fixture(scope="module")
def sources():
    prepared = run()
    frame, p = make_demo_data(STRATEGY)
    return prepared, {"schemaVersion": 1, "rows": _safe_rows(frame), "provenance": p}


def compose(sources, *, market=None, package=None, **kwargs):
    prepared, original = sources
    return compose_financial_dataset(
        STRATEGY,
        original if market is None else market,
        [freeze(prepared)] if package is None else [package],
        trusted_unit_proofs=True,
        **kwargs
    )


def test_all_sixteen_prepared_states_join_exactly_with_complete_private_lineage(
    sources,
):
    prepared, market = sources
    result = compose(sources)
    expected = prepared.panel.set_index(["ts_code", "trade_date"])
    actual = result.data.set_index(["ts_code", "trade_date"])
    pd.testing.assert_frame_equal(
        actual[expected.columns], expected, check_dtype=False, check_exact=True
    )
    assert len(result.data) == len(market["rows"])
    artifact = result.financial_artifacts[0]
    assert artifact.prepared.state_events == prepared.state_events
    assert artifact.summary["stateEventCount"] == len(prepared.state_events)
    assert len(artifact.summary["securities"][0]["states"]) == 16
    assert result.provenance["synthetic"] is True
    for state in RECIPES:
        meta = result.provenance["externalFields"][state]
        assert meta["semanticKind"] == "native_statement_state"
        assert (
            meta["preparedInputs"][0]["preparedRoot"]
            == artifact.prepared.provenance["preparedRoot"]
        )
    assert "stateEvents" not in result.to_dataset()["provenance"]


def test_absent_market_sessions_are_not_created_by_financial_carry(sources):
    _, original = sources
    market = deepcopy(original)
    missing = market["rows"].pop(2)
    result = compose(sources, market=market)
    assert len(result.data) == len(market["rows"])
    assert missing["trade_date"] not in set(result.data.trade_date)
    assert len(result.financial_artifacts[0].prepared.panel) > len(result.data)


def test_public_packages_cannot_import_reviewed_proofs(sources):
    prepared, market = sources
    with pytest.raises(ContractError, match="trusted reviewer"):
        compose_financial_dataset(STRATEGY, market, [freeze(prepared)])


def test_declared_units_remain_unverified_through_join_and_summary(sources):
    prepared, market = sources
    package = freeze(
        prepared,
        declarations(prepared),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )
    result = compose_financial_dataset(STRATEGY, market, [package])
    assert result.financial_artifacts[0].summary["qualityFlags"] == [
        "USER_DECLARED_UNIT_ASSUMPTION"
    ]
    for state in RECIPES:
        assert result.provenance["externalFields"][state]["qualityFlags"] == [
            "USER_DECLARED_UNIT_ASSUMPTION"
        ]
    for event in result.financial_artifacts[0].prepared.state_events:
        if event["result"]["dependencies"]:
            assert event["result"]["unitVerified"] is False
            assert event["result"]["declarationHashes"]


def test_strict_declaration_policy_does_not_upgrade_missing_states(sources):
    prepared, market = sources
    package = freeze(prepared, declarations(prepared), trusted_unit_proofs=False)
    result = compose_financial_dataset(STRATEGY, market, [package])
    assert result.data[list(RECIPES)].isna().all().all()
    assert all(
        state["status"] == "missing"
        for state in result.financial_artifacts[0].summary["securities"][0]["states"]
    )


@pytest.mark.parametrize("where", ["row", "registry", "root"])
def test_forged_financial_metadata_never_substitutes_for_preparation(sources, where):
    _, original = sources
    market = deepcopy(original)
    if where == "row":
        market["rows"][0]["model_fin_cash_asset_share"] = 123.0
    elif where == "registry":
        market["provenance"]["externalFields"] = {
            "model_fin_cash_asset_share": {
                "semanticKind": "native_statement_state",
                "unitVerified": True,
            }
        }
    else:
        market["provenance"]["preparedRoot"] = "a" * 64
    with pytest.raises(AdapterError) as error:
        compose(sources, market=market)
    assert error.value.code == "FINANCIAL_FIELD_COLLISION"


def test_calendar_must_match_exactly_even_when_remaining_prices_are_valid(sources):
    _, original = sources
    market = deepcopy(original)
    removed = market["provenance"]["tradingDates"].pop()
    market["rows"] = [row for row in market["rows"] if row["trade_date"] != removed]
    with pytest.raises(AdapterError) as error:
        compose(sources, market=market)
    assert error.value.code == "FINANCIAL_CALENDAR_MISMATCH"


def test_prepared_and_package_roots_replay_without_wall_clock_drift(sources):
    a, b = compose(sources), compose(sources)
    assert a.to_dataset() == b.to_dataset()
    assert a.financial_artifacts[0].summary == b.financial_artifacts[0].summary
    assert a.provenance["financialDatasetRoot"] == b.provenance["financialDatasetRoot"]


def test_input_budget_fails_before_any_preparation(sources, monkeypatch):
    import atlas_quant.financial_statements.dataset as module

    def forbidden(*args, **kwargs):
        pytest.fail("preparation must not start when known inputs exceed parent budget")

    monkeypatch.setattr(module, "prepare_package", forbidden)
    with pytest.raises(AdapterError) as error:
        compose(sources, budget=DatasetBudget(max_total_bytes=1))
    assert error.value.code == "FINANCIAL_DATASET_BUDGET"


def test_no_duplicate_or_overlapping_sources(sources):
    prepared, market = sources
    original = freeze(prepared)
    changed = freeze(prepared, unit_policy="allow_declared")
    for packages, code in [
        ([original, original], "DUPLICATE_FINANCIAL_INPUT"),
        ([original, changed], "FINANCIAL_FIELD_COLLISION"),
    ]:
        with pytest.raises(AdapterError) as error:
            compose_financial_dataset(
                STRATEGY, market, packages, trusted_unit_proofs=True
            )
        assert error.value.code == code


def test_summary_age_and_pre_disclosure_missingness_have_defined_semantics(sources):
    result = compose(sources)
    state = next(
        s
        for s in result.financial_artifacts[0].summary["securities"][0]["states"]
        if s["stateId"] == "model_fin_cash_asset_share"
    )
    assert state["latestPeriodEnd"] == "20231231"
    assert (
        state["lastPreparedDate"] == "20240503"
        and state["latestAgeCalendarDays"] == 124
    )
    assert state["firstAvailable"] == "20240429"
    assert state["missingRows"] == 1
    assert pd.isna(result.data.iloc[0].model_fin_cash_asset_share)


def test_package_root_tampering_blocks_composition(sources):
    package = freeze(sources[0])
    package["packRoot"] = "f" * 64
    with pytest.raises(ContractError, match="root"):
        compose(sources, package=package)
