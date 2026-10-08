"""Calendar-quarter operators; all legs use the same as-of version selection."""

from decimal import Decimal

from .contracts import FIELDS, ContractError, is_quarter_end
from .results import derive, missing


def prior_quarter(period_end, count=1):
    if not is_quarter_end(period_end):
        raise ContractError("calendar-quarter period required")
    ends = ("0331", "0630", "0930", "1231")
    coordinate = int(period_end[:4]) * 4 + ends.index(period_end[4:]) - count
    year, quarter = divmod(coordinate, 4)
    return f"{year:04d}{ends[quarter]}"


def quarter(store, symbol, field_id, period_end, as_of, *, scope="consolidated", flow_basis="ytd"):
    if field_id not in FIELDS or flow_basis not in {"ytd", "quarter"}:
        raise ContractError("registered field and explicit flow basis required")
    args = {
        "formula": "calendar_quarter_v1",
        "period_end": period_end,
        "as_of": as_of,
        "scope": scope,
        "basis": "quarter",
        "calendar_evidence": store.calendar_evidence,
    }
    if not is_quarter_end(period_end) or FIELDS[field_id].period_kind != "flow":
        return missing("UNSUPPORTED_PERIOD_KIND", **args)
    current = store.value(symbol, field_id, period_end, as_of, scope=scope, basis=flow_basis)
    if flow_basis == "quarter" or period_end[4:] == "0331":
        return derive([current], lambda x: x, **args)
    previous = store.value(
        symbol, field_id, prior_quarter(period_end), as_of, scope=scope, basis=flow_basis
    )
    result = derive([current, previous], lambda x, y: x - y, **args)
    if FIELDS[field_id].positive_outflow and result.status == "ok" and result.value < 0:
        return missing("NEGATIVE_DERIVED_OUTFLOW", deps=result.dependencies, **args)
    return result


def ttm(store, symbol, field_id, period_end, as_of, *, scope="consolidated", flow_basis="ytd"):
    args = {
        "formula": "four_calendar_quarters_ttm_v1",
        "period_end": period_end,
        "as_of": as_of,
        "scope": scope,
        "basis": "ttm",
        "calendar_evidence": store.calendar_evidence,
    }
    if not is_quarter_end(period_end):
        return missing("UNSUPPORTED_REPORT_PERIOD", **args)
    quarters = [
        quarter(
            store,
            symbol,
            field_id,
            prior_quarter(period_end, n),
            as_of,
            scope=scope,
            flow_basis=flow_basis,
        )
        for n in range(4)
    ]
    return derive(quarters, lambda *values: sum(values, Decimal(0)), **args)


def point(store, symbol, field_id, period_end, as_of, *, scope="consolidated"):
    if field_id not in FIELDS:
        raise ContractError("registered field required")
    if FIELDS[field_id].period_kind != "point":
        return missing(
            "UNSUPPORTED_PERIOD_KIND",
            period_end=period_end,
            as_of=as_of,
            scope=scope,
            calendar_evidence=store.calendar_evidence,
        )
    return store.value(symbol, field_id, period_end, as_of, scope=scope, basis="point")


def average_assets(store, symbol, period_end, as_of, *, scope="consolidated"):
    values = [
        point(store, symbol, "balancesheet.total_assets", date, as_of, scope=scope)
        for date in (period_end, prior_quarter(period_end, 4))
    ]
    return derive(
        values,
        lambda end, start: (end + start) / Decimal(2),
        formula="ttm_begin_end_average_assets_v1",
        period_end=period_end,
        as_of=as_of,
        scope=scope,
        basis="average_point",
    )
