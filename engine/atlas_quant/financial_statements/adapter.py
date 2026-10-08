"""Bounded injected-client acquisition; all state computation uses pure prepare."""

from datetime import datetime, timedelta, timezone
import pandas as pd

from .contracts import FIELDS, ContractError, SourceRef, TradingCalendar, parse_date
from .results import canonical_hash
from .unit_bindings import validate_unit_contract
from .prepare import (
    AdapterBudget,
    AdapterError,
    AdapterResult,
    required_fields,
    _safe_rows,
    _json_bytes,
    prepare_statement_states,
    validate_selection,
    METADATA_FIELDS,
)


def load_statement_states(
    client,
    strategy,
    selected_ids,
    calendar,
    unit_contract,
    *,
    announcement_start,
    budget=None,
    source_kind="provider",
    source_provider="TUSHARE_PRO",
    scope="consolidated",
    flow_basis="ytd",
    retrieved_at=None,
    unit_policy="verified_only",
):
    """Return daily state columns plus bounded event evidence, or a typed failure.

    client.call(endpoint, params) follows the existing read-only Tushare client
    interface. No credentials, endpoint URLs or retries are managed here.
    announcement_start is a declared coverage bound, never a completeness claim.
    """
    selected_ids = tuple(selected_ids)
    unit_contract = validate_unit_contract(unit_contract, source_kind)
    budget = validate_selection(
        strategy,
        selected_ids,
        calendar,
        announcement_start=announcement_start,
        budget=budget,
        source_kind=source_kind,
        scope=scope,
        flow_basis=flow_basis,
        unit_policy=unit_policy,
    )
    fields = required_fields(selected_ids)
    if (
        not isinstance(source_provider, str)
        or not 1 <= len(source_provider.strip()) <= 80
    ):
        raise ContractError("source provider must be explicitly named and bounded")
    if retrieved_at is not None:
        SourceRef(source_provider, "pending_snapshot", retrieved_at, source_kind)
    symbols = strategy["universe"]["symbols"]
    end = strategy["universe"]["end"]
    by_endpoint = {}
    for field_id in fields:
        field = FIELDS[field_id]
        by_endpoint.setdefault(field.endpoint, []).append(field)
    snapshots, requests = [], []
    source_rows = source_bytes = 0

    def partial():
        return {
            "status": "failed",
            "panelPublished": False,
            "requests": list(requests),
            "sourceRows": source_rows,
            "snapshotMetadata": [
                {
                    key: item[key]
                    for key in (
                        "id",
                        "endpoint",
                        "params",
                        "retrievedAt",
                        "rowCount",
                        "byteLength",
                    )
                }
                for item in snapshots
            ],
        }

    def fail(code, message):
        if requests and requests[-1]["status"] == "requested":
            requests[-1]["status"] = "failed"
        raise AdapterError(code, message, partial())

    def fetch(endpoint, symbol, first, last):
        nonlocal source_rows, source_bytes
        if len(requests) >= budget.max_requests:
            fail(
                "REQUEST_BUDGET",
                "Statement history request budget reached; no partial panel was published",
            )
        params = {"ts_code": symbol, "start_date": first, "end_date": last}
        request = {"endpoint": endpoint, "params": dict(params), "status": "requested"}
        requests.append(request)
        truncated = False
        try:
            frame = client.call(endpoint, params)
        except Exception as error:
            if getattr(error, "code", None) != "TUSHARE_TRUNCATED":
                request["status"] = "failed"
                fail(
                    getattr(error, "code", "PROVIDER_READ_FAILED"),
                    "Read-only statement request failed; raw provider error is not exposed",
                )
            frame = None
            truncated = True
        if truncated or (
            isinstance(frame, pd.DataFrame) and len(frame) >= budget.response_row_limit
        ):
            request["status"] = "truncated"
            if first == last:
                fail(
                    "UNRESOLVED_TRUNCATION",
                    "A single announcement date still exceeds the row limit",
                )
            a, b = parse_date(first), parse_date(last)
            midpoint = a + (b - a) // 2
            fetch(endpoint, symbol, first, midpoint.strftime("%Y%m%d"))
            fetch(
                endpoint,
                symbol,
                (midpoint + timedelta(days=1)).strftime("%Y%m%d"),
                last,
            )
            return
        if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any():
            fail(
                "RESPONSE_SCHEMA", "Statement response must have unique tabular columns"
            )
        requested = tuple(field.provider_field for field in by_endpoint[endpoint])
        if not set((*METADATA_FIELDS, *requested)).issubset(frame.columns):
            fail(
                "RESPONSE_COLUMNS",
                "Requested metadata or financial columns are absent; this is not a null disclosure",
            )
        try:
            rows = _safe_rows(frame)
        except ContractError:
            fail(
                "RESPONSE_VALUES", "Statement response contains unsupported cell values"
            )
        if source_rows + len(rows) > budget.max_source_rows:
            fail(
                "SOURCE_ROW_BUDGET",
                "Source row budget exceeded; response was not silently truncated",
            )
        stamp = retrieved_at or datetime.now(timezone.utc).isoformat()
        body = {
            "endpoint": endpoint,
            "params": dict(params),
            "fields": list(frame.columns),
            "rows": rows,
            "retrievedAt": stamp,
            "sourceKind": source_kind,
            "sourceProvider": source_provider,
            "representation": "normalized_provider_table_snapshot",
            "wireBytesAvailable": False,
            "wireNumericLexemesAvailable": False,
        }
        size = _json_bytes(body)
        if source_bytes + size > budget.max_source_bytes:
            fail("SOURCE_BYTE_BUDGET", "Source snapshot byte budget exceeded")
        identity = canonical_hash(body)
        snapshots.append(
            {"id": identity, **body, "rowCount": len(rows), "byteLength": size}
        )
        source_rows += len(rows)
        source_bytes += size
        request["status"] = "complete"
        request["snapshotId"] = identity

    first, final = parse_date(announcement_start), parse_date(end)
    for symbol in symbols:
        for endpoint in sorted(by_endpoint):
            cursor = first
            while cursor <= final:
                last = min(final, cursor + timedelta(days=budget.max_interval_days - 1))
                fetch(
                    endpoint, symbol, cursor.strftime("%Y%m%d"), last.strftime("%Y%m%d")
                )
                cursor = last + timedelta(days=1)
    return prepare_statement_states(
        snapshots,
        strategy,
        selected_ids,
        calendar,
        unit_contract,
        announcement_start=announcement_start,
        budget=budget,
        source_kind=source_kind,
        source_provider=source_provider,
        scope=scope,
        flow_basis=flow_basis,
        unit_policy=unit_policy,
        requests=requests,
    )
