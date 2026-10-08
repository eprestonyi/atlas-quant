"""Independent input/version counterexamples; synthetic, with no provider calls."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from atlas_quant.financial_statements import (
    ContractError, SourceRef, StatementRecord, TradingCalendar, UnitEvidence,
    build_store, quarter, ttm,
)


SYMBOL = "000001.SZ"
SOURCE = SourceRef("INDEPENDENT_HAND_FIXTURE", "review-1", "2025-01-01T00:00:00Z", "fixture")
UNIT = UnitEvidence("CNY", "CNY", True, "fixture", "independent-hand-unit", True)


def calendar():
    start, end = date(2022, 1, 1), date(2025, 1, 1)
    days = [start + timedelta(days=i) for i in range((end-start).days+1)]
    return TradingCalendar(tuple(d.strftime("%Y%m%d") for d in days if d.weekday()<5),
                           "20220101", "20250101", True, "synthetic-weekdays", "fixture")


def record(period, values, ann, **changes):
    row = StatementRecord(SYMBOL, "income", period, ann, None, "1", "1", values,
                          {name: UNIT for name in values}, SOURCE)
    return replace(row, **changes)


def test_same_available_session_selects_actual_latest_disclosure_not_row_order():
    rows = [record("20230331", {"revenue":100}, "20230428"),
            record("20230331", {"revenue":125}, "20230429", report_type="4")]
    for ordering in (rows, list(reversed(rows))):
        store = build_store(ordering, calendar())
        assert store.value(SYMBOL, "income.revenue", "20230331", "20230428").status == "missing"
        result = store.value(SYMBOL, "income.revenue", "20230331", "20230501")
        assert result.value == 125 and result.available_date == "20230501"
        assert {d.announcement_date for d in result.dependencies} == {"20230429"}


def test_late_old_ytd_revision_changes_only_future_cross_year_ttm():
    rows = [record("20220331", {"revenue":100}, "20220429"),
            record("20220630", {"revenue":220}, "20220826"),
            record("20220930", {"revenue":360}, "20221028"),
            record("20221231", {"revenue":500}, "20230428"),
            record("20230331", {"revenue":130}, "20230428")]
    before_store = build_store(rows, calendar())
    before = ttm(before_store, SYMBOL, "income.revenue", "20230331", "20230501")
    assert before.value == 530  # 130 + (220-100) + (360-220) + (500-360)
    after_store = build_store(rows + [record("20220331", {"revenue":110}, "20230505", report_type="4")], calendar())
    assert ttm(after_store, SYMBOL, "income.revenue", "20230331", "20230501").to_dict() == before.to_dict()
    after = ttm(after_store, SYMBOL, "income.revenue", "20230331", "20230508")
    assert after.value == 520 and after.available_date == "20230508"
    assert after_store.latest_period(SYMBOL, "income.revenue", "20230508") == "20230331"


def test_currency_change_in_latest_revision_cannot_reuse_old_cny_value():
    old = record("20230331", {"revenue":100}, "20230428")
    new = record("20230331", {"revenue":120}, "20230505",
                 units={"revenue":replace(UNIT, currency="USD")})
    value = build_store([old,new],calendar()).value(SYMBOL,"income.revenue","20230331","20230508")
    assert value.status == "missing" and "UNSUPPORTED_CURRENCY" in value.reason_codes


def test_exact_equivalent_native_units_are_not_a_value_conflict():
    a = record("20230331", {"revenue":Decimal("12345.67")}, "20230428")
    b = record("20230331", {"revenue":Decimal("1.234567")}, "20230428",
               units={"revenue":replace(UNIT,native_unit="CNY_10000")})
    value = build_store([a,b],calendar()).value(SYMBOL,"income.revenue","20230331","20230501")
    assert value.status == "ok" and value.value == Decimal("12345.67")


def test_different_raw_values_cannot_hide_a_disclosure_conflict_after_rounding():
    rows = [record("20230331", {"revenue":Decimal(value)}, "20230428")
            for value in ("12345678901234567890123456789012341", "12345678901234567890123456789012342")]
    try:
        value = build_store(rows,calendar()).value(SYMBOL,"income.revenue","20230331","20230501")
    except ContractError:
        return  # An explicit supported-precision contract may reject both inputs.
    assert value.status == "missing" and "CONFLICTING_DISCLOSURES" in value.reason_codes


@pytest.mark.parametrize("raw", ["1e1000000", "1e-1000034"])
def test_extreme_finite_decimal_is_typed_rejection_or_missing_never_crash_or_silent_zero(raw):
    try:
        row = record("20230331", {"revenue":Decimal(raw)}, "20230428")
        value = build_store([row],calendar()).value(SYMBOL,"income.revenue","20230331","20230501")
    except ContractError:
        return
    assert value.status == "missing" and value.value is None


def test_disjoint_field_projections_do_not_erase_each_other():
    rows = [record("20230331", {"revenue":100}, "20230428"),
            record("20230331", {"operate_profit":20}, "20230428")]
    store = build_store(rows,calendar())
    revenue = store.value(SYMBOL,"income.revenue","20230331","20230501")
    profit = store.value(SYMBOL,"income.operate_profit","20230331","20230501")
    assert revenue.status == profit.status == "ok"
    assert revenue.value == 100 and profit.value == 20


def test_explicit_null_projection_blocks_old_value_unlike_unrequested_field():
    rows = [record("20230331", {"revenue":100}, "20230428"),
            record("20230331", {"revenue":None}, "20230505")]
    value = build_store(rows,calendar()).value(SYMBOL,"income.revenue","20230331","20230508")
    assert value.status == "missing" and "NULL_VALUE" in value.reason_codes


def test_direct_quarter_never_falls_back_to_available_ytd():
    rows = [record("20230331", {"revenue":100}, "20230428"),
            record("20230630", {"revenue":250}, "20230825")]
    value = quarter(build_store(rows,calendar()),SYMBOL,"income.revenue","20230630","20230901",flow_basis="quarter")
    assert value.status == "missing"


def test_calendar_evidence_changes_lineage_without_changing_the_financial_value():
    row = record("20230331", {"revenue":100}, "20230428")
    a = calendar()
    b = replace(a,evidence_reference="independent-calendar-evidence-version-2")
    left = build_store([row],a).value(SYMBOL,"income.revenue","20230331","20230501")
    right = build_store([row],b).value(SYMBOL,"income.revenue","20230331","20230501")
    assert left.value == right.value == 100
    assert left.available_date == right.available_date == "20230501"
    assert left.to_dict()["lineageHash"] != right.to_dict()["lineageHash"]


def test_calendar_session_content_is_bound_even_with_same_evidence_label():
    row = record("20230331", {"revenue":100}, "20230428")
    a = calendar()
    b = replace(a,sessions=tuple(d for d in a.sessions if d != "20220502"))
    left = quarter(build_store([row],a),SYMBOL,"income.revenue","20230331","20230501")
    right = quarter(build_store([row],b),SYMBOL,"income.revenue","20230331","20230501")
    assert left.value == right.value == 100
    assert left.to_dict()["lineageHash"] != right.to_dict()["lineageHash"]
