"""Injected fake-provider contract tests. No credentials or network are used."""

from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from atlas_quant.connectors import EXTRA_DATASETS, FINANCIAL_ALIASES
from atlas_quant.financial_statements import FIELDS, RECIPES, ContractError
from atlas_quant.financial_statements.adapter import (
    AdapterBudget,
    AdapterError,
    load_statement_states,
)
from atlas_quant.financial_statements.unit_bindings import FixtureUnitBinding
from test_financial_statements import manual_records, record, calendar, UNIT, EXPECTED, SYMBOL

STRATEGY = {"universe": {"symbols": [SYMBOL], "start": "20240426", "end": "20240503"}}
UNITS = {id: FixtureUnitBinding(UNIT, id, "HAND_FAKE_PROVIDER") for id in FIELDS}


class Truncated(Exception):
    code = "TUSHARE_TRUNCATED"


class FakeProvider:
    def __init__(self, rows=None, transform=None, truncate=None):
        self.rows = manual_records() if rows is None else rows
        self.calls = []
        self.transform = transform
        self.truncate = truncate

    def call(self, endpoint, params):
        assert endpoint in {"income", "balancesheet", "cashflow"}
        assert set(params) == {"ts_code", "start_date", "end_date"}
        self.calls.append((endpoint, dict(params)))
        if self.truncate and self.truncate(endpoint, params):
            raise Truncated()
        rows = []
        for source in self.rows:
            if (
                source.endpoint != endpoint
                or not params["start_date"] <= source.ann_date <= params["end_date"]
            ):
                continue
            rows.append(
                {
                    "ts_code": source.symbol,
                    "ann_date": source.ann_date,
                    "f_ann_date": source.f_ann_date,
                    "end_date": source.period_end,
                    "report_type": source.report_type,
                    "comp_type": source.company_type,
                    "update_flag": source.update_flag,
                    **{
                        key: float(value) if value is not None else None
                        for key, value in source.values.items()
                    },
                }
            )
        frame = pd.DataFrame(rows, columns=EXTRA_DATASETS[endpoint].split(","))
        return self.transform(endpoint, params, frame) if self.transform else frame


def run(client=None, **kwargs):
    params = {
        "announcement_start": "20210101",
        "source_kind": "fixture",
        "source_provider": "HAND_FAKE_PROVIDER",
        "retrieved_at": "2025-01-01T00:00:00Z",
        **kwargs,
    }
    return load_statement_states(
        client or FakeProvider(),
        params.pop("strategy", STRATEGY),
        params.pop("selected_ids", list(RECIPES)),
        calendar(),
        params.pop("unit_contract", UNITS),
        **params
    )


def test_all_16_states_are_computed_from_raw_table_responses_and_hand_values():
    client = FakeProvider()
    output = run(client)
    latest = output.panel[output.panel.trade_date == "20240430"].iloc[0]
    for suffix, expected in EXPECTED.items():
        id = "model_fin_" + suffix
        assert latest[id] == pytest.approx(float(Decimal(expected)))
        assert latest[id + "__available_date"] == "20240429"
        assert output.coverage[id]["okRows"] > 0
    assert output.provenance["sourceKind"] == "fixture"
    assert output.provenance["financialRevisionHistory"].endswith("UNVERIFIED")
    assert len(output.state_events) == 32 < len(output.panel) * 16
    assert output.assignments[-1]["from"] == "20240429"
    assert output.assignments[-1]["through"] == "20240503"
    assert {endpoint for endpoint, _ in client.calls} == {"income", "balancesheet", "cashflow"}
    assert all("snapshotId" in request for request in output.provenance["requests"])


def test_selected_stock_state_never_requests_unneeded_flow_tables_or_indicator_aliases():
    client = FakeProvider()
    output = run(client, selected_ids=["model_fin_cash_asset_share"])
    assert {endpoint for endpoint, _ in client.calls} == {"balancesheet"}
    assert len(FINANCIAL_ALIASES) == 21
    assert set(output.provenance["externalFields"]) == {"model_fin_cash_asset_share"}


def test_selected_id_generator_is_materialized_once_and_cannot_return_empty_states():
    ids = (id for id in ["model_fin_cash_asset_share", "model_fin_goodwill_asset_share"])
    output = run(selected_ids=ids)
    assert set(output.coverage) == {"model_fin_cash_asset_share", "model_fin_goodwill_asset_share"}
    assert output.panel.iloc[-1].model_fin_cash_asset_share == pytest.approx(0.1)
    assert output.panel.iloc[-1].model_fin_goodwill_asset_share == pytest.approx(0.02)


def test_table_snapshot_does_not_claim_original_wire_or_numeric_lexeme_identity():
    output = run(selected_ids=["model_fin_cash_asset_share"])
    assert output.provenance["snapshotRepresentation"] == "normalized_provider_table_snapshot"
    assert output.provenance["wireEvidence"] == {
        "availability": "UNKNOWN",
        "wireBytesAvailable": False,
        "wireNumericLexemesAvailable": False,
    }
    assert "does not restore" in output.provenance["inputNumericPrecision"]
    assert all(
        snapshot["representation"] == "normalized_provider_table_snapshot"
        and not snapshot["wireBytesAvailable"]
        for snapshot in output.snapshots
    )


def test_cross_year_announcement_is_fetched_by_announcement_not_report_period():
    output = run(announcement_start="20240401", selected_ids=["model_fin_cash_asset_share"])
    assert output.panel.iloc[-1].model_fin_cash_asset_share == pytest.approx(0.1)
    assert any(
        row["end_date"] == "20231231" for snapshot in output.snapshots for row in snapshot["rows"]
    )
    unavailable = run(announcement_start="20240401", selected_ids=["model_fin_revenue_ttm_yoy"])
    assert unavailable.panel.model_fin_revenue_ttm_yoy.isna().all()
    assert unavailable.coverage["model_fin_revenue_ttm_yoy"]["missingRows"] == len(
        unavailable.panel
    )


def test_truncated_interval_is_split_without_using_a_partial_page():
    def split(endpoint, params):
        return params["start_date"] == "20210101" and params["end_date"] == "20220101"

    client = FakeProvider(truncate=split)
    output = run(client)
    assert any(request["status"] == "truncated" for request in output.provenance["requests"])
    assert output.panel.iloc[-1].model_fin_operating_margin == pytest.approx(0.2)
    pairs = [params for _, params in client.calls]
    assert {"ts_code": SYMBOL, "start_date": "20210101", "end_date": "20210702"} in pairs
    assert {"ts_code": SYMBOL, "start_date": "20210703", "end_date": "20220101"} in pairs


def test_unresolved_single_date_truncation_fails_instead_of_successful_partial_panel():
    with pytest.raises(AdapterError) as caught:
        run(FakeProvider(truncate=lambda _api, _params: True))
    assert caught.value.code == "UNRESOLVED_TRUNCATION"
    assert caught.value.partial["panelPublished"] is False
    assert caught.value.partial["requests"]


@pytest.mark.parametrize("drop", ["f_ann_date", "report_type", "revenue"])
def test_missing_response_column_is_not_a_null_or_unrequested_field(drop):
    def transform(endpoint, params, frame):
        return frame.drop(columns=drop) if endpoint == "income" else frame

    with pytest.raises(AdapterError) as caught:
        run(FakeProvider(transform=transform), selected_ids=["model_fin_revenue_quarter_yoy"])
    assert caught.value.code == "RESPONSE_COLUMNS"


def test_explicit_null_is_retained_as_missing_not_backfilled():
    rows = manual_records() + [record("income", "20231231", {"revenue": None}, "20240430")]
    output = run(FakeProvider(rows), selected_ids=["model_fin_revenue_quarter_yoy"])
    before = output.panel[output.panel.trade_date == "20240430"].iloc[0]
    after = output.panel[output.panel.trade_date == "20240501"].iloc[0]
    assert before.model_fin_revenue_quarter_yoy == pytest.approx(0.2)
    assert pd.isna(after.model_fin_revenue_quarter_yoy)
    assert output.coverage["model_fin_revenue_quarter_yoy"]["reasons"]["NULL_VALUE"] > 0


def test_revision_changes_future_quarter_without_backdating_disclosure():
    rows = manual_records() + [
        record("income", "20230930", {"revenue": 430}, "20240430", report_type="4")
    ]
    output = run(FakeProvider(rows), selected_ids=["model_fin_revenue_quarter_yoy"])
    by_date = output.panel.set_index("trade_date")
    assert by_date.loc["20240430", "model_fin_revenue_quarter_yoy"] == pytest.approx(0.2)
    assert by_date.loc["20240501", "model_fin_revenue_quarter_yoy"] == pytest.approx(170 / 150 - 1)
    assert by_date.loc["20240501", "model_fin_revenue_quarter_yoy__available_date"] == "20240501"


def test_unknown_units_never_default_to_verified_cny():
    output = run(unit_contract={})
    for id in RECIPES:
        assert output.panel[id].isna().all()
        assert output.coverage[id]["okRows"] == 0
        assert output.coverage[id]["reasons"].get("UNIT_UNVERIFIED", 0) > 0
    assert all(event["result"]["status"] == "missing" for event in output.state_events)
    with pytest.raises(ContractError, match="fixture unit"):
        run(source_kind="provider")


def test_wrong_symbol_and_out_of_range_announcement_are_rejected():
    for mutate, expected in [
        (lambda frame: frame.assign(ts_code="000001.SZ"), "PROVIDER_IDENTITY"),
        (lambda frame: frame.assign(ann_date="20250101"), "PROVIDER_DATE_RANGE"),
    ]:

        def transform(endpoint, params, frame):
            return mutate(frame) if not frame.empty else frame

        with pytest.raises(AdapterError) as caught:
            run(FakeProvider(transform=transform))
        assert caught.value.code == expected


@pytest.mark.parametrize(
    "budget,code",
    [
        (AdapterBudget(max_requests=2), "REQUEST_BUDGET"),
        (AdapterBudget(max_source_rows=1), "SOURCE_ROW_BUDGET"),
        (AdapterBudget(max_source_bytes=1), "SOURCE_BYTE_BUDGET"),
        (AdapterBudget(max_state_events=1), "STATE_EVENT_BUDGET"),
        (AdapterBudget(max_audit_bytes=1), "AUDIT_BYTE_BUDGET"),
    ],
)
def test_budget_failure_preserves_completed_metadata_and_never_publishes_truncated_panel(
    budget, code
):
    with pytest.raises(AdapterError) as caught:
        run(budget=budget)
    assert caught.value.code == code
    assert caught.value.partial["status"] == "failed"
    assert caught.value.partial["panelPublished"] is False
    assert "panel" not in caught.value.partial
    assert caught.value.partial["requests"]
    if code in {"STATE_EVENT_BUDGET", "AUDIT_BYTE_BUDGET"}:
        assert caught.value.partial["sourceRows"] > 0
        assert caught.value.partial["snapshotMetadata"]


def test_empty_history_is_explicit_coverage_missing_not_invented_values():
    output = run(FakeProvider(rows=[]))
    assert output.provenance["sourceRows"] == 0
    for id in RECIPES:
        assert output.panel[id].isna().all()
        assert output.coverage[id]["reasons"] == {"NO_AVAILABLE_ANCHOR_PERIOD": len(output.panel)}
