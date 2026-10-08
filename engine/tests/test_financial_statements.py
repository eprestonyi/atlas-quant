"""Hand-computable fixture statements. No provider or production coverage claim."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import random

import pytest

from atlas_quant.financial_statements import (
    FIELDS,
    RECIPES,
    ContractError,
    StatementRecord,
    SourceRef,
    UnitEvidence,
    TradingCalendar,
    build_store,
    quarter,
    ttm,
    compute_states,
)

SYMBOL = "600000.SH"
ASOF = "20240430"
SOURCE = SourceRef("HAND_CALCULATED", "fixture-v1", "2025-01-01T00:00:00+00:00", "fixture")
UNIT = UnitEvidence("CNY", "CNY", True, "fixture", "hand-fixture-CNY", True)


def calendar(start="20210101", end="20251231", complete=True):
    first = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}")
    last = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    sessions = []
    while first <= last:
        if first.weekday() < 5:
            sessions.append(first.strftime("%Y%m%d"))
        first += timedelta(days=1)
    return TradingCalendar(
        tuple(sessions),
        start,
        end,
        complete,
        "synthetic-weekday-calendar-not-market-evidence",
        "fixture",
    )


def record(endpoint, period, values, announced=None, **changes):
    next_year = str(int(period[:4]) + 1)
    default_ann = (
        next_year + "0426"
        if period[4:] == "1231"
        else period[:4] + {"0331": "0426", "0630": "0825", "0930": "1027"}.get(period[4:], "1231")
    )
    result = StatementRecord(
        SYMBOL,
        endpoint,
        period,
        announced or default_ann,
        None,
        "1",
        "1",
        values,
        {k: UNIT for k in values},
        SOURCE,
    )
    return replace(result, **changes)


def manual_records():
    rows = []
    # 2022 revenue=500; 2023=600. 2023 Q4=180 versus 150 one year before.
    for year, revenues in [(2022, [100, 110, 140, 150]), (2023, [120, 140, 160, 180])]:
        running = {
            k: Decimal(0) for k in ("revenue", "operate_profit", "n_income", "n_income_attr_p")
        }
        cash = {k: Decimal(0) for k in ("n_cashflow_act", "c_pay_acq_const_fiolta")}
        for n, end in enumerate(("0331", "0630", "0930", "1231")):
            values = {
                "revenue": revenues[n],
                "operate_profit": [24, 28, 32, 36][n],
                "n_income": [16, 18, 22, 24][n],
                "n_income_attr_p": [12, 14, 16, 18][n],
            }
            for key, value in values.items():
                running[key] += value
            cash["n_cashflow_act"] += [15, 20, 25, 30][n]
            cash["c_pay_acq_const_fiolta"] += [6, 7, 8, 9][n]
            rows.append(record("income", f"{year}{end}", dict(running)))
            rows.append(record("cashflow", f"{year}{end}", dict(cash)))
    rows += [
        record("balancesheet", "20221231", {"total_assets": 900}),
        record(
            "balancesheet",
            "20231231",
            {
                "total_assets": 1100,
                "total_liab": 440,
                "total_cur_assets": 330,
                "total_cur_liab": 110,
                "money_cap": 110,
                "accounts_receiv": 55,
                "goodwill": 22,
                "st_borr": 55,
                "lt_borr": 55,
            },
        ),
    ]
    return rows


EXPECTED = {
    "revenue_quarter_yoy": "0.2",
    "revenue_ttm_yoy": "0.2",
    "operating_margin": "0.2",
    "parent_net_margin": "0.1",
    "cash_revenue_ratio": "0.15",
    "profit_cash_asset_gap": "-0.01",
    "cash_assets_ratio": "0.09",
    "capex_revenue_ratio": "0.05",
    "cash_less_capex_assets": "0.06",
    "assets_yoy": "0.222222222222222222222222222222222",
    "cash_asset_share": "0.1",
    "current_coverage": "3",
    "liability_asset_share": "0.4",
    "borrowings_asset_share": "0.1",
    "receivable_asset_share": "0.05",
    "goodwill_asset_share": "0.02",
}


@pytest.mark.parametrize("suffix,expected", EXPECTED.items())
def test_every_formula_matches_independent_hand_calculation(suffix, expected):
    id = "model_fin_" + suffix
    value = compute_states(build_store(manual_records(), calendar()), SYMBOL, ASOF, [id])[id]
    assert value.status == "ok", value.reason_codes
    assert value.value == Decimal(expected)
    serialized = value.to_dict()
    assert serialized["decimalPrecision"] == 34 and serialized["rounding"] == "ROUND_HALF_EVEN"
    assert serialized["periodEnd"] == "20231231" and serialized["availableDate"] == "20240429"
    assert serialized["unit"] == "ratio" and len(serialized["lineageHash"]) == 64
    assert serialized["revisionHistory"].endswith("UNVERIFIED")
    assert all(d.source_kind == "fixture" and d.raw_decimal is not None for d in value.dependencies)


@pytest.mark.parametrize("id", RECIPES)
def test_every_formula_has_a_missing_dependency_counterexample(id):
    field = FIELDS[RECIPES[id].numerator[0][1]]
    records = []
    for row in manual_records():
        if row.endpoint == field.endpoint:
            values = dict(row.values)
            values.pop(field.provider_field, None)
            row = replace(row, values=values)
        records.append(row)
    result = compute_states(build_store(records, calendar()), SYMBOL, ASOF, [id])[id]
    assert result.status == "missing" and result.value is None
    assert "FIELD_MISSING" in result.reason_codes


def test_cumulative_quarter_and_ttm_are_not_daily_rolling_sums():
    store = build_store(manual_records(), calendar())
    assert quarter(store, SYMBOL, "income.revenue", "20231231", ASOF).value == 180
    assert ttm(store, SYMBOL, "income.revenue", "20231231", ASOF).value == 600
    assert ttm(store, SYMBOL, "income.revenue", "20230930", ASOF).value == 570
    assert ttm(store, SYMBOL, "balancesheet.total_assets", "20231231", ASOF).status == "missing"


def test_late_actual_announcement_and_weekend_use_next_covered_session():
    row = record("income", "20230331", {"revenue": 100}, "20230425", f_ann_date="20230428")
    store = build_store([row], calendar())
    assert store.value(SYMBOL, "income.revenue", "20230331", "20230428").status == "missing"
    result = store.value(SYMBOL, "income.revenue", "20230331", "20230501")
    assert result.value == 100 and result.available_date == "20230501"
    assert result.dependencies[0].announcement_date == "20230428"


def test_revision_selects_each_period_at_same_asof_and_preserves_previous_evidence():
    rows = [
        record("income", "20230331", {"revenue": 100}),
        record("income", "20230630", {"revenue": 300}),
    ]
    before = quarter(
        build_store(rows, calendar()), SYMBOL, "income.revenue", "20230630", "20230901"
    )
    revised = record("income", "20230331", {"revenue": 120}, "20230908", report_type="4")
    after_store = build_store(rows + [revised], calendar())
    assert (
        quarter(after_store, SYMBOL, "income.revenue", "20230630", "20230901").to_dict()
        == before.to_dict()
    )
    after = quarter(after_store, SYMBOL, "income.revenue", "20230630", "20230911")
    assert before.value == 200 and after.value == 180
    assert {d.report_type for d in after.dependencies} == {"1", "4"}
    assert after_store.latest_period(SYMBOL, "income.revenue", "20230911") == "20230630"


def test_same_day_conflicts_nulls_and_row_order_never_pick_update_flag():
    rows = [
        record("income", "20230331", {"revenue": 100}, update_flag="0"),
        record("income", "20230331", {"revenue": 120}, update_flag="1"),
    ]
    a = build_store(rows, calendar()).value(SYMBOL, "income.revenue", "20230331", "20230501")
    b = build_store(list(reversed(rows)), calendar()).value(
        SYMBOL, "income.revenue", "20230331", "20230501"
    )
    assert a.to_dict() == b.to_dict() and "CONFLICTING_DISCLOSURES" in a.reason_codes
    empty = replace(rows[0], ann_date="20230505", values={"revenue": None})
    value = build_store([rows[0], empty], calendar()).value(
        SYMBOL, "income.revenue", "20230331", "20230508"
    )
    assert value.status == "missing" and "NULL_VALUE" in value.reason_codes


def test_future_perturbation_and_shuffling_preserve_all_past_states_and_hashes():
    rows = manual_records()
    before = {
        k: v.to_dict()
        for k, v in compute_states(build_store(rows, calendar()), SYMBOL, ASOF).items()
    }
    rows += [
        record("income", "20231231", {"revenue": 999999}, "20240510"),
        record("balancesheet", "20240331", {"total_assets": 1}, "20240510"),
    ]
    random.Random(42).shuffle(rows)
    after = {
        k: v.to_dict()
        for k, v in compute_states(build_store(rows, calendar()), SYMBOL, ASOF).items()
    }
    assert after == before


def test_new_incomplete_anchor_does_not_fall_back_to_an_old_complete_period():
    rows = manual_records() + [record("income", "20240331", {"revenue": None}, "20240503")]
    result = compute_states(
        build_store(rows, calendar()), SYMBOL, "20240506", ["model_fin_operating_margin"]
    )["model_fin_operating_margin"]
    assert result.period_end == "20240331" and result.status == "missing"


@pytest.mark.parametrize(
    "unit,reason",
    [
        (replace(UNIT, verified=False), "UNIT_UNVERIFIED"),
        (replace(UNIT, currency="USD"), "UNSUPPORTED_CURRENCY"),
        (replace(UNIT, native_unit="shares_10000"), "UNSUPPORTED_UNIT"),
    ],
)
def test_unknown_unit_currency_or_dimension_cannot_cancel_inside_ratio(unit, reason):
    row = record(
        "balancesheet",
        "20231231",
        {"total_assets": 100, "money_cap": 10},
        units={"total_assets": unit, "money_cap": unit},
    )
    value = compute_states(
        build_store([row], calendar()), SYMBOL, ASOF, ["model_fin_cash_asset_share"]
    )["model_fin_cash_asset_share"]
    assert value.status == "missing" and reason in value.reason_codes


def test_explicit_unit_conversion_and_fixture_production_separation():
    row = record(
        "balancesheet",
        "20231231",
        {"total_assets": 2, "money_cap": 5000},
        units={"total_assets": replace(UNIT, native_unit="CNY_10000"), "money_cap": UNIT},
    )
    value = compute_states(
        build_store([row], calendar()), SYMBOL, ASOF, ["model_fin_cash_asset_share"]
    )["model_fin_cash_asset_share"]
    assert value.value == Decimal("0.25")
    assert {d.raw_decimal for d in value.dependencies} == {"2", "5000"}
    with pytest.raises(ContractError, match="fixture unit"):
        replace(row, source=replace(SOURCE, kind="provider"))


@pytest.mark.parametrize("denominator", [0, -1])
def test_nonpositive_denominator_is_missing_not_zero_or_absolute_value(denominator):
    row = record("balancesheet", "20231231", {"total_assets": denominator, "money_cap": 10})
    value = compute_states(
        build_store([row], calendar()), SYMBOL, ASOF, ["model_fin_cash_asset_share"]
    )["model_fin_cash_asset_share"]
    assert value.status == "missing" and "DENOMINATOR_NONPOSITIVE" in value.reason_codes


def test_cash_spending_sign_requires_evidence_and_quarter_cannot_be_negative():
    rows = [
        record("cashflow", "20230331", {"c_pay_acq_const_fiolta": 10}),
        record("cashflow", "20230630", {"c_pay_acq_const_fiolta": 5}),
    ]
    result = quarter(
        build_store(rows, calendar()),
        SYMBOL,
        "cashflow.c_pay_acq_const_fiolta",
        "20230630",
        "20230901",
    )
    assert "NEGATIVE_DERIVED_OUTFLOW" in result.reason_codes
    unknown = replace(
        rows[0], units={"c_pay_acq_const_fiolta": replace(UNIT, positive_outflow=None)}
    )
    assert (
        "OUTFLOW_SIGN_UNVERIFIED"
        in quarter(
            build_store([unknown], calendar()),
            SYMBOL,
            "cashflow.c_pay_acq_const_fiolta",
            "20230331",
            "20230501",
        ).reason_codes
    )


def test_scope_basis_and_industry_are_not_silently_mixed():
    rows = [
        record("income", "20230331", {"revenue": 100}),
        record("income", "20230630", {"revenue": 300}, report_type="6"),
    ]
    assert (
        quarter(
            build_store(rows, calendar()), SYMBOL, "income.revenue", "20230630", "20230901"
        ).status
        == "missing"
    )
    single = record("income", "20230630", {"revenue": 75}, report_type="2")
    store = build_store([single], calendar())
    assert (
        quarter(store, SYMBOL, "income.revenue", "20230630", "20230901", flow_basis="quarter").value
        == 75
    )
    assert (
        quarter(store, SYMBOL, "income.revenue", "20230630", "20230901", flow_basis="ytd").status
        == "missing"
    )
    bank = [replace(row, company_type="2") for row in manual_records()]
    values = compute_states(build_store(bank, calendar()), SYMBOL, ASOF)
    assert all(
        v.status == "missing" and "UNSUPPORTED_COMPANY_TYPE" in v.reason_codes
        for v in values.values()
    )


def test_calendar_coverage_completeness_and_nonstandard_fiscal_periods():
    row = record("income", "20221231", {"revenue": 100}, "20221231")
    result = build_store([row], calendar(start="20230101")).value(
        SYMBOL, "income.revenue", "20221231", "20230501"
    )
    assert "CALENDAR_OUTSIDE_COVERAGE" in result.reason_codes
    assert build_store([row], calendar(complete=False)).value(
        SYMBOL, "income.revenue", "20221231", ASOF
    ).reason_codes == ("CALENDAR_INCOMPLETE",)
    for row in [
        record("income", "20230330", {"revenue": 100}),
        record("income", "20230331", {"revenue": 100}, fiscal_year_end="0630"),
    ]:
        value = build_store([row], calendar()).value(SYMBOL, "income.revenue", row.period_end, ASOF)
        assert "UNSUPPORTED_REPORT_PERIOD" in value.reason_codes


def test_input_immutability_dedup_and_decimal_context_independence():
    rows = manual_records()
    store = build_store(rows + rows, calendar())
    assert len(store.observations) == len(build_store(rows, calendar()).observations)
    with pytest.raises(TypeError):
        rows[0].values["revenue"] = 99
    with localcontext() as context:
        context.prec = 6
        result = compute_states(store, SYMBOL, ASOF, ["model_fin_assets_yoy"])[
            "model_fin_assets_yoy"
        ]
    assert str(result.value) == EXPECTED["assets_yoy"]
    assert len(FIELDS) == 15 and len(RECIPES) == 16


def test_core_aliases_are_new_and_existing_readonly_endpoints_cover_each_dependency():
    from atlas_quant.connectors import EXTRA_DATASETS, FINANCIAL_ALIASES

    assert not {field.alias for field in FIELDS.values()}.intersection(FINANCIAL_ALIASES)
    for field in FIELDS.values():
        assert field.provider_field in EXTRA_DATASETS[field.endpoint].split(",")
    assert len(FINANCIAL_ALIASES) == 21


def test_explicit_requested_missing_anchor_never_falls_back_after_sparse_projection():
    row = record(
        "income", "20240331", {}, "20240503", requested_fields=("revenue",), units={"revenue": UNIT}
    )
    store = build_store(manual_records() + [row], calendar())
    value = compute_states(store, SYMBOL, "20240506", ["model_fin_operating_margin"])[
        "model_fin_operating_margin"
    ]
    assert value.period_end == "20240331" and value.status == "missing"
    assert "FIELD_MISSING" in value.reason_codes


def test_exact_source_normalization_and_rounded_derived_identity_are_distinct():
    raw = Decimal("12345678901234567890123456789012345")
    store = build_store([record("income", "20230331", {"revenue": raw})], calendar())
    source = store.value(SYMBOL, "income.revenue", "20230331", "20230501")
    derived = quarter(store, SYMBOL, "income.revenue", "20230331", "20230501")
    assert source.value == raw
    assert derived.value != raw and len(derived.value.as_tuple().digits) == 34
    assert source.to_dict()["valueRepresentation"] == "source_exact_decimal_normalization"
    assert derived.to_dict()["valueRepresentation"] == "derived_decimal34"


def test_calendar_and_mapping_contracts_are_part_of_saved_lineage():
    rows = manual_records()
    first = compute_states(build_store(rows, calendar()), SYMBOL, ASOF)[
        "model_fin_operating_margin"
    ].to_dict()
    alternative = replace(calendar(), evidence_reference="separate-calendar-snapshot")
    second = compute_states(build_store(rows, alternative), SYMBOL, ASOF)[
        "model_fin_operating_margin"
    ].to_dict()
    assert first["decimalValue"] == second["decimalValue"]
    assert first["lineageHash"] != second["lineageHash"]
    assert first["calendar"]["root"] != second["calendar"]["root"]
    assert first["calendar"]["sessions_hash"] == second["calendar"]["sessions_hash"]
    assert first["mappingVersions"] == ["native_statement_fields_v1"]
