"""Normalize already durable raw receipts; never fetch, repair or add units."""

from dataclasses import asdict
from datetime import timedelta

from ..financial_statements.contracts import TradingCalendar, parse_date
from ..financial_statements.package import freeze_package
from ..financial_statements.results import canonical_hash
from ..financial_statements.store import build_store
from .protocol import (
    PROFILE,
    PACKAGE_BYTES,
    CHUNK_BYTES,
    META_BYTES,
    decode,
    encode,
    keys,
    require,
    sha,
    timestamp,
)


def table(request, receipt):
    raw = receipt["raw"]
    require(
        isinstance(raw, bytes)
        and len(raw) <= PROFILE["maxResponseBytes"]
        and sha(raw) == receipt["sha256"]
        and len(raw) == receipt["byteLength"],
        "ACQUISITION_RECEIPT",
        "Only a complete durable raw receipt can be normalized",
    )
    require(
        receipt["httpStatus"] == 200,
        "PROVIDER_HTTP_STATUS",
        "Provider returned a definite unsuccessful HTTP response",
    )
    value = decode(raw, limit=PROFILE["maxResponseBytes"])
    require(
        isinstance(value, dict)
        and type(value.get("code")) is int
        and value["code"] == 0,
        "PROVIDER_RESPONSE_ERROR",
        "Provider returned a definite unsuccessful API response",
    )
    data = value.get("data")
    require(
        isinstance(data, dict)
        and isinstance(data.get("fields"), list)
        and all(isinstance(f, str) for f in data["fields"])
        and len(data["fields"]) == len(set(data["fields"]))
        and set(data["fields"]) == set(request["fields"])
        and isinstance(data.get("items"), list),
        "PROVIDER_SCHEMA",
        "Provider response columns differ from the fixed request",
    )
    maximum = (
        PROFILE["maxCalendarDays"]
        if request["endpoint"] == "trade_cal"
        else PROFILE["maxStatementRows"] - 1
    )
    require(
        1 <= len(data["items"]) <= maximum,
        "PROVIDER_EMPTY_OR_TRUNCATED",
        "Empty or full-limit response cannot be silently completed",
    )
    rows = []
    for values in data["items"]:
        require(
            isinstance(values, list)
            and len(values) == len(data["fields"])
            and all(
                v is None
                or isinstance(v, (str, int, float))
                and not isinstance(v, bool)
                for v in values
            ),
            "PROVIDER_SCHEMA",
            "Provider row dimension or scalar type differs",
        )
        rows.append(dict(zip(data["fields"], values)))
    return data["fields"], rows


def calendar_from_receipt(request, receipt):
    _, rows = table(request, receipt)
    params = request["params"]
    first, last = parse_date(params["start_date"]), parse_date(params["end_date"])
    expected = [
        (first + timedelta(days=n)).strftime("%Y%m%d")
        for n in range((last - first).days + 1)
    ]
    dates, sessions = [], []
    for row in rows:
        require(
            row["exchange"] == params["exchange"]
            and row["is_open"] in (0, 1, "0", "1"),
            "CALENDAR_RESPONSE",
            "Calendar venue/open flag differs",
        )
        parse_date(row["cal_date"])
        if row["pretrade_date"] not in (None, ""):
            parse_date(row["pretrade_date"])
            require(
                row["pretrade_date"] < row["cal_date"],
                "CALENDAR_RESPONSE",
                "Previous session must precede date",
            )
        dates.append(row["cal_date"])
        if row["is_open"] in (1, "1"):
            sessions.append(row["cal_date"])
    require(
        sorted(dates) == expected and len(set(dates)) == len(expected) and sessions,
        "CALENDAR_RESPONSE",
        "Calendar must cover every requested day exactly once",
    )
    source = receipt["sourceKind"]
    require(
        source in {"provider", "fixture"},
        "ACQUISITION_SOURCE_KIND",
        "Unknown response source kind",
    )
    reference = f"tushare:trade_cal:{params['exchange']}:{params['start_date']}:{params['end_date']}:receipt-sha256:{receipt['sha256']}"
    if source == "fixture":
        reference = "SYNTHETIC_FIXTURE:" + reference
    calendar = TradingCalendar(
        tuple(sorted(sessions)),
        params["start_date"],
        params["end_date"],
        True,
        reference,
        "official" if source == "provider" else "fixture",
    )
    root = build_store([], calendar).calendar_evidence.root
    envelope = {
        "kind": "calendar",
        "registryVersion": 1,
        "payload": asdict(calendar),
        "scope": {"calendarRoot": root},
        "evidenceLevel": (
            "provider_reported_calendar"
            if source == "provider"
            else "EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE"
        ),
    }
    return calendar, envelope, root


def normalize_statement(request, receipt):
    fields, rows = table(request, receipt)
    params = request["params"]
    meta = {
        "ts_code",
        "ann_date",
        "f_ann_date",
        "end_date",
        "report_type",
        "comp_type",
        "update_flag",
    }
    for row in rows:
        require(
            row["ts_code"] == params["ts_code"]
            and row["end_date"] == params["period"]
            and row["report_type"] == params["report_type"]
            and row["comp_type"] == params["comp_type"],
            "STATEMENT_SCOPE",
            "Statement symbol/period/consolidation/company type differs",
        )
        parse_date(row["ann_date"])
        if row["f_ann_date"] is not None:
            parse_date(row["f_ann_date"])
        require(
            all(
                row[f] is None
                or isinstance(row[f], (int, float))
                and not isinstance(row[f], bool)
                for f in fields
                if f not in meta
            ),
            "STATEMENT_VALUES",
            "Financial cells must remain numeric or missing",
        )
    body = {
        "endpoint": request["endpoint"],
        "params": dict(params),
        "fields": fields,
        "rows": rows,
        "retrievedAt": receipt["retrievedAt"],
        "sourceKind": receipt["sourceKind"],
        "sourceProvider": (
            "TUSHARE_PRO"
            if receipt["sourceKind"] == "provider"
            else "SYNTHETIC_TUSHARE_FIXTURE"
        ),
        "representation": "normalized_provider_table_snapshot",
        "wireBytesAvailable": False,
        "wireNumericLexemesAvailable": False,
    }
    return {
        **body,
        "id": canonical_hash(body),
        "rowCount": len(rows),
        "byteLength": len(encode(body)),
    }


def build_publication(job, plan, receipts):
    require(
        set(receipts) == {r["requestKey"] for r in plan["requests"]},
        "ACQUISITION_RECEIPT",
        "Exact request receipt closure required",
    )
    require(
        sum(r["byteLength"] for r in receipts.values()) <= PROFILE["maxTotalBytes"],
        "ACQUISITION_RESPONSE_BUDGET",
        "Aggregate raw response budget exceeded",
    )
    kinds = {r["sourceKind"] for r in receipts.values()}
    require(
        len(kinds) == 1,
        "ACQUISITION_SOURCE_KIND",
        "Synthetic and provider receipts cannot be combined",
    )
    calendar_request = plan["requests"][0]
    calendar, envelope, calendar_root = calendar_from_receipt(
        calendar_request, receipts[calendar_request["requestKey"]]
    )
    snapshots, sources = [], []
    for request in plan["requests"]:
        receipt = receipts[request["requestKey"]]
        timestamp(receipt["retrievedAt"])
        source = {
            k: receipt[k] for k in ("requestKey", "receiptId", "sha256", "byteLength")
        }
        if request["endpoint"] != "trade_cal":
            snapshot = normalize_statement(request, receipt)
            snapshots.append(snapshot)
            source["normalizedSnapshotId"] = snapshot["id"]
        sources.append(source)
    require(
        sum(s["rowCount"] for s in snapshots) <= PROFILE["maxSourceRows"],
        "ACQUISITION_ROW_BUDGET",
        "Source row budget exceeded",
    )
    selection = plan["selection"]
    source_kind = next(iter(kinds))
    package = freeze_package(
        snapshots,
        calendar,
        {"universe": {k: selection[k] for k in ("symbols", "start", "end")}},
        selection["selectedStateIds"],
        {},
        announcement_start=selection["announcementStart"],
        source_kind=source_kind,
        source_provider=(
            "TUSHARE_PRO" if source_kind == "provider" else "SYNTHETIC_TUSHARE_FIXTURE"
        ),
        scope=selection["scope"],
        flow_basis=selection["flowBasis"],
        unit_policy="verified_only",
        trusted_unit_proofs=False,
    )
    documents = {"package": encode(package), "calendar": encode(envelope)}
    require(
        len(documents["package"]) <= PACKAGE_BYTES
        and len(documents["calendar"]) <= META_BYTES,
        "ACQUISITION_PUBLICATION_BUDGET",
        "Frozen package/calendar exceeds its bounded transport",
    )
    manifest = {
        "format": "atlas.quant.financial_acquisition_output",
        "version": 1,
        "jobId": job["id"],
        "executionPlanRoot": plan["executionPlanRoot"],
        "calendarRoot": calendar_root,
        "inputRoot": package["inputRoot"],
        "packRoot": package["packRoot"],
        "sourceReceipts": sources,
    }
    chunks = {}
    for name, raw in documents.items():
        parts = []
        for ordinal, offset in enumerate(range(0, len(raw), CHUNK_BYTES)):
            part = raw[offset : offset + CHUNK_BYTES]
            parts.append(
                {"ordinal": ordinal, "sha256": sha(part), "byteLength": len(part)}
            )
            chunks[(name, ordinal)] = part
        manifest[name] = {"sha256": sha(raw), "byteLength": len(raw), "chunks": parts}
    require(
        len(encode(manifest)) <= 128 * 1024,
        "ACQUISITION_PUBLICATION_BUDGET",
        "Publication manifest too large",
    )
    return manifest, chunks
