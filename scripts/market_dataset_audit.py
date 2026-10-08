"""Independent stdlib audit of exact delivered market responses and normalized rows.

No production modules, provider calls, extraction, model fitting or evaluation.
Integrity and normalization do not establish vendor authority or historical membership.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import ast
import json
import math
import os
import sqlite3
import tempfile
from pathlib import Path
import re
import tarfile

try:
    from .dataset_audit import (
        AuditError,
        Checks,
        date,
        encode,
        identity,
        integer,
        read_file,
        regular,
        root,
        sha,
        tar_header,
        without,
    )
except ImportError:
    from dataset_audit import (
        AuditError,
        Checks,
        date,
        encode,
        identity,
        integer,
        read_file,
        regular,
        root,
        sha,
        tar_header,
        without,
    )

try:
    from .bundle_audit import (
        BundleAudit,
        LIMIT_MANIFEST as RESULT_MANIFEST,
        LIMIT_CHUNK as RESULT_CHUNK,
        LIMIT_TOTAL as RESULT_TOTAL,
    )
except ImportError:
    from bundle_audit import (
        BundleAudit,
        LIMIT_MANIFEST as RESULT_MANIFEST,
        LIMIT_CHUNK as RESULT_CHUNK,
        LIMIT_TOTAL as RESULT_TOTAL,
    )

MIB = 1024 * 1024
MANIFEST_BYTES = SCOPE_BYTES = 256 * 1024
PLAN_BYTES = 1536 * 1024
PART_BYTES = 512 * 1024
RAW_PART_BYTES = 4 * MIB
DATA_BYTES = 128 * MIB
RAW_BYTES = 512 * MIB
MAX_PARTS, MAX_RAW_PARTS = 320, 256
TAR_MAX = (
    DATA_BYTES
    + RAW_BYTES
    + MANIFEST_BYTES
    + PLAN_BYTES
    + SCOPE_BYTES
    + 579 * 1023
    + 1024
)
PROFILE = "pooled_asset_1000_v1"
BASE_FIELDS = "open high low close raw_close vol amount adj_factor".split()
BASIC_FIELDS = "turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv".split()
LIMITS = dict(
    maxSymbols=1000,
    maxCalendarDays=366,
    maxRows=300000,
    maxRequests=3002,
    maxRawBytes=RAW_BYTES,
    maxNormalizedBytes=DATA_BYTES,
    maxRequestSeconds=30,
    maxWallSeconds=7200,
    leaseSeconds=120,
    heartbeatSeconds=20,
    requestsPerMinute=60,
    maxActualAttemptsPerRequest=1,
)


def decode(raw, ceiling, check, canonical=False):
    """Raw endpoint JSON may have whitespace; its original bytes remain hashed."""
    check.require(
        isinstance(raw, bytes) and 0 < len(raw) <= ceiling, "JSON byte budget", "BUDGET"
    )
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            check.require(depth <= 64, "JSON nesting exceeds 64", "JSON_DEPTH")
        elif byte in (93, 125):
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            check.require(key not in result, "Duplicate JSON key", "JSON")
            result[key] = value
        return result

    def number(token):
        value = float(token)
        check.require(math.isfinite(value), "Nonfinite JSON number", "JSON")
        return value

    def constant(_):
        raise AuditError("JSON", "Nonfinite JSON constant")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=number,
            parse_constant=constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, AuditError):
            raise
        raise AuditError("JSON", "Malformed bounded UTF-8 JSON") from exc
    if canonical:
        check.require(encode(value) == raw, "Noncanonical document JSON", "CANONICAL")
    return value


def symbol_scope(scope, check):
    check.keys(scope, {"symbols", "start", "end", "symbolCount", "scopeRoot"})
    symbols = scope["symbols"]
    check.require(
        isinstance(symbols, list)
        and 1 <= len(symbols) <= 1000
        and all(
            isinstance(s, str) and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", s)
            for s in symbols
        )
        and symbols == sorted(set(symbols)),
        "Complete sorted SH/SZ scope required",
        "SCOPE",
    )
    check.require(
        type(scope["symbolCount"]) is int and scope["symbolCount"] == len(symbols),
        "Symbol count differs",
        "SCOPE",
    )
    date(scope["start"], check)
    date(scope["end"], check)
    days = (
        datetime.strptime(scope["end"], "%Y%m%d")
        - datetime.strptime(scope["start"], "%Y%m%d")
    ).days + 1
    integer(days, 1, 366, check)
    check.require(scope["start"] >= "20000101", "Unsupported historical date", "SCOPE")
    identity(scope["scopeRoot"], check)
    return days


def validate_manifest(raw, check, expected_root=None):
    m = decode(raw, MANIFEST_BYTES, check, True)
    check.keys(
        m,
        {
            "format",
            "version",
            "profile",
            "planRoot",
            "universeScopeRef",
            "scope",
            "calendar",
            "fields",
            "rowCount",
            "sourceKind",
            "collections",
            "rawArchive",
        },
    )
    check.require(
        m["format"] == "atlas.quant.market_dataset"
        and type(m["version"]) is int
        and m["version"] == 1
        and m["profile"] == PROFILE,
        "Unsupported market dataset",
        "FORMAT",
    )
    if expected_root is not None:
        check.require(
            sha(raw) == identity(expected_root, check),
            "Pinned dataset root differs",
            "ROOT",
        )
    identity(m["planRoot"], check)
    ref = m["universeScopeRef"]
    check.keys(ref, {"scopeId", "scopeRoot", "format", "version"})
    identity(ref["scopeId"], check, True)
    identity(ref["scopeRoot"], check)
    check.require(
        ref["format"] == "atlas.quant.universe_scope"
        and type(ref["version"]) is int
        and ref["version"] == 1,
        "Unknown scope reference",
        "SCOPE",
    )
    symbol_scope(m["scope"], check)
    check.require(
        m["scope"]["scopeRoot"] == ref["scopeRoot"], "Scope root differs", "ROOT"
    )
    fields = m["fields"]
    check.require(
        isinstance(fields, list)
        and all(isinstance(f, str) for f in fields)
        and fields == sorted(set(fields))
        and set(BASE_FIELDS) <= set(fields) <= set(BASE_FIELDS + BASIC_FIELDS),
        "Unknown normalized fields",
        "FIELDS",
    )
    sessions = m["calendar"]
    check.require(
        isinstance(sessions, list)
        and 1 <= len(sessions) <= 366
        and all(isinstance(d, str) for d in sessions)
        and sessions == sorted(set(sessions)),
        "Calendar must be unique and ordered",
        "CALENDAR",
    )
    for d in sessions:
        date(d, check)
        check.require(
            m["scope"]["start"] <= d <= m["scope"]["end"],
            "Calendar outside scope",
            "CALENDAR",
        )
    check.require(
        len(sessions) * m["scope"]["symbolCount"] <= 300000,
        "Complete grid exceeds profile",
        "BUDGET",
    )
    integer(m["rowCount"], 1, 300000, check)
    check.require(
        m["sourceKind"] in {"fixture", "provider"}, "Unknown source kind", "SOURCE"
    )
    check.keys(m["collections"], {"rows", "receipts", "provenance"})
    total, count = 0, 0
    for name in sorted(m["collections"]):
        c = m["collections"][name]
        check.keys(c, {"chunks", "byteLength", "rowCount"})
        integer(c["byteLength"], 1, DATA_BYTES, check)
        integer(
            c["rowCount"],
            1,
            {"rows": 300000, "receipts": 3002, "provenance": 1}[name],
            check,
        )
        check.require(
            isinstance(c["chunks"], list) and 1 <= len(c["chunks"]) <= MAX_PARTS,
            "Normalized chunk count",
            "BUDGET",
        )
        nbytes, nrows = 0, 0
        for i, p in enumerate(c["chunks"]):
            check.keys(p, {"ordinal", "sha256", "byteLength", "rowCount"})
            check.require(
                type(p["ordinal"]) is int and p["ordinal"] == i,
                "Noncontinuous ordinal",
                "PARTS",
            )
            identity(p["sha256"], check)
            integer(p["byteLength"], 2, PART_BYTES, check)
            integer(p["rowCount"], 1, 10000, check)
            nbytes += p["byteLength"]
            nrows += p["rowCount"]
        check.require(
            (nbytes, nrows) == (c["byteLength"], c["rowCount"]),
            "Collection totals differ",
            "PARTS",
        )
        total += nbytes
        count += len(c["chunks"])
    check.require(
        total <= DATA_BYTES
        and count <= MAX_PARTS
        and m["rowCount"] == m["collections"]["rows"]["rowCount"],
        "Normalized parent budget/count",
        "BUDGET",
    )
    rawc = m["rawArchive"]
    check.keys(rawc, {"chunks", "byteLength", "receiptCount"})
    integer(rawc["byteLength"], 1, RAW_BYTES, check)
    integer(rawc["receiptCount"], 1, 3002, check)
    check.require(
        rawc["receiptCount"] == m["collections"]["receipts"]["rowCount"],
        "Receipt counts differ",
        "PARTS",
    )
    check.require(
        isinstance(rawc["chunks"], list) and 1 <= len(rawc["chunks"]) <= MAX_RAW_PARTS,
        "Raw chunk budget",
        "BUDGET",
    )
    total = 0
    for i, p in enumerate(rawc["chunks"]):
        check.keys(p, {"ordinal", "sha256", "byteLength"})
        check.require(
            type(p["ordinal"]) is int and p["ordinal"] == i,
            "Raw ordinal differs",
            "PARTS",
        )
        identity(p["sha256"], check)
        integer(p["byteLength"], 1, RAW_PART_BYTES, check)
        total += p["byteLength"]
    check.require(total == rawc["byteLength"], "Raw byte total differs", "PARTS")
    return m


def validate_documents(plan_raw, scope_raw, m, check):
    p = decode(plan_raw, PLAN_BYTES, check, True)
    check.keys(
        p,
        {
            "format",
            "version",
            "profile",
            "universeScopeRef",
            "membershipPolicy",
            "scope",
            "catalog",
            "fields",
            "authorizationScope",
            "requests",
            "blockedReasons",
            "budget",
            "completeness",
            "sourcePolicy",
            "planRoot",
        },
    )
    check.require(
        p["format"] == "atlas.quant.market_acquisition_plan"
        and type(p["version"]) is int
        and p["version"] == 1
        and p["profile"] == PROFILE
        and p["membershipPolicy"] == "complete_filtered_set"
        and p["blockedReasons"] == [],
        "Unsupported or blocked source plan",
        "PLAN",
    )
    check.require(
        p["planRoot"] == m["planRoot"] == root(without(p, "planRoot")),
        "Plan content root differs",
        "ROOT",
    )
    for name in ("scope", "universeScopeRef", "fields"):
        check.equal(p[name], m[name], "Plan/manifest " + name + " differs")
    check.require(
        isinstance(p["authorizationScope"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", p["authorizationScope"]),
        "Invalid authorization scope assertion",
        "PLAN",
    )
    check.keys(
        p["catalog"], {"snapshotHash", "resolutionHash", "historicalMembershipVerified"}
    )
    for name in ("snapshotHash", "resolutionHash"):
        identity(p["catalog"][name], check)
    check.require(
        p["catalog"]["historicalMembershipVerified"] is False,
        "Historical membership claim unsupported",
        "SOURCE",
    )
    s = decode(scope_raw, SCOPE_BYTES, check)
    check.keys(
        s,
        {
            "format",
            "version",
            "membershipPolicy",
            "symbols",
            "symbolCount",
            "start",
            "end",
            "snapshotHash",
            "resolutionHash",
            "selection",
            "catalogSnapshot",
            "sourceUniverses",
            "steps",
            "algorithmVersion",
            "historicalMembershipVerified",
        },
    )
    check.require(
        sha(scope_raw) == m["universeScopeRef"]["scopeRoot"],
        "Exact scope document root differs",
        "ROOT",
    )
    check.require(
        s["format"] == "atlas.quant.universe_scope"
        and type(s["version"]) is int
        and s["version"] == 1
        and s["membershipPolicy"] == "complete_filtered_set"
        and s["historicalMembershipVerified"] is False,
        "Scope membership contract differs",
        "SCOPE",
    )
    for name in ("symbols", "symbolCount", "start", "end"):
        check.equal(
            s[name], m["scope"][name], "Scope omitted or changed full membership"
        )
    for name in ("snapshotHash", "resolutionHash"):
        check.require(
            s[name] == p["catalog"][name], "Catalog evidence roots differ", "ROOT"
        )
    check.require(
        isinstance(s["selection"], dict)
        and isinstance(s["sourceUniverses"], list)
        and isinstance(s["steps"], list)
        and isinstance(s["algorithmVersion"], str)
        and isinstance(s["catalogSnapshot"], dict)
        and s["catalogSnapshot"].get("hash") == s["snapshotHash"]
        and s["catalogSnapshot"].get("historicalMembershipVerified") is False,
        "Scope evidence shape differs",
        "SCOPE",
    )
    expected = []
    common = {"start_date": s["start"], "end_date": s["end"]}
    for exchange in sorted(
        {"SSE" if sym.endswith(".SH") else "SZSE" for sym in s["symbols"]}
    ):
        expected.append(
            (
                "trade_cal",
                {"exchange": exchange, **common},
                "exchange,cal_date,is_open,pretrade_date",
                65536,
            )
        )
    basic = sorted(set(p["fields"]) & set(BASIC_FIELDS))
    for sym in s["symbols"]:
        params = {"ts_code": sym, **common}
        expected.extend(
            [
                (
                    "daily",
                    params,
                    "ts_code,trade_date,open,high,low,close,vol,amount",
                    131072,
                ),
                ("adj_factor", params, "ts_code,trade_date,adj_factor", 65536),
            ]
        )
        if basic:
            expected.append(
                (
                    "daily_basic",
                    params,
                    ",".join(["ts_code", "trade_date", *basic]),
                    262144,
                )
            )
    check.require(
        isinstance(p["requests"], list)
        and len(p["requests"]) == len(expected) == m["rawArchive"]["receiptCount"],
        "Incomplete whole-scope request set",
        "PLAN",
    )
    for i, (r, (api, params, fields, ceiling)) in enumerate(
        zip(p["requests"], expected)
    ):
        check.keys(
            r,
            {
                "ordinal",
                "requestKey",
                "provider",
                "authorizationScope",
                "apiName",
                "params",
                "fields",
                "responseBytes",
                "maxAttempts",
            },
        )
        definition = dict(
            provider="TUSHARE_PRO",
            authorizationScope=p["authorizationScope"],
            apiName=api,
            params=params,
            fields=fields,
            responseBytes=ceiling,
            maxAttempts=1,
        )
        check.equal(
            r,
            {"ordinal": i, "requestKey": root(definition), **definition},
            "Exact ordered request definition/hash differs",
        )
    days = symbol_scope(m["scope"], check)
    check.equal(
        p["budget"],
        {
            **LIMITS,
            "calendarDays": days,
            "declaredRequests": len(expected),
            "materializedRequests": len(expected),
            "rawResponseCeilingBytes": sum(r[3] for r in expected),
        },
        "Plan resource limits differ",
    )
    check.require(
        p["budget"]["rawResponseCeilingBytes"] <= RAW_BYTES,
        "Raw declared ceiling",
        "BUDGET",
    )
    check.equal(
        p["completeness"],
        dict(
            zeroRowsForAnySymbol="reject_entire_scope",
            missingSessions="preserve_missing_mask",
            crossExchangeCalendars="require_exact_session_equality",
            unknownRequest="sticky_manual_review_no_retry",
        ),
        "Completeness policy differs",
    )
    check.equal(
        p["sourcePolicy"],
        dict(
            responseBytes="exact_delivered_endpoint_bytes",
            originalProviderWireAvailable=False,
            adjustment="adj_factor_divided_by_first_observed_factor_per_symbol",
            volumeUnit="hands",
            amountUnit="CNY_thousands",
        ),
        "Source policy differs",
    )
    return p, s


class Archive:
    def __init__(self, stream, check):
        self.stream, self.check, self.consumed = stream, check, 0

    def exact(self, n):
        self.check.require(
            0 <= n <= RAW_PART_BYTES and self.consumed + n <= TAR_MAX,
            "Archive read budget",
            "BUDGET",
        )
        raw = self.stream.read(n)
        self.consumed += len(raw)
        self.check.require(len(raw) == n, "Truncated archive", "TAR_TRUNCATED")
        return raw

    def member(self, name, ceiling, size=None):
        header = self.exact(512)
        try:
            item = tarfile.TarInfo.frombuf(header, "ascii", "strict")
        except (tarfile.HeaderError, UnicodeError, ValueError) as exc:
            raise AuditError("TAR_HEADER", "Invalid USTAR checksum/header") from exc
        self.check.require(
            item.type == b"0"
            and 0 < item.size <= ceiling
            and (size is None or item.size == size)
            and header == tar_header(name, item.size),
            "Unexpected USTAR name/order/type/size/metadata",
            "TAR_HEADER",
        )
        raw = self.exact(item.size)
        padding = (-item.size) % 512
        self.check.require(
            self.exact(padding) == bytes(padding),
            "Nonzero USTAR padding",
            "TAR_PADDING",
        )
        return raw

    def finish(self):
        self.check.require(
            self.exact(1024) == bytes(1024) and self.stream.read(1) == b"",
            "Exact two-block EOF required without trailer",
            "TAR_EOF",
        )


def number(value, check, *, positive=False, nullable=False):
    if value is None and nullable:
        return None
    check.require(type(value) in {int, float}, "Numeric field has wrong type", "NUMBER")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise AuditError("NUMBER", "Nonfinite numeric field") from exc
    check.require(
        math.isfinite(result) and (not positive or result > 0),
        "Nonfinite or invalid numeric field",
        "NUMBER",
    )
    # Never hide a changed integer behind binary64 rounding. Signed zero is the
    # same numerical value in the registered legacy forecast codec.
    check.require(
        type(value) is not int or result == value,
        "Integer loses precision as a normalized numeric value",
        "NUMBER",
    )
    return 0.0 if result == 0 else result


class Semantics:
    def __init__(self, m, p, check, paired_rows=None):
        self.m, self.p, self.check = m, p, check
        self.paired_rows = paired_rows
        self.rows, self.receipts, self.provenance = {}, [], []
        self.previous = ""
        self.current_symbol, self.tables = None, {}
        self.seen_symbols, self.calendar_count, self.compared = set(), 0, 0
        self.raw_index = 0

    def normalized(self, collection, raw, descriptor):
        c = self.check
        c.require(
            len(raw) == descriptor["byteLength"] and sha(raw) == descriptor["sha256"],
            "Normalized part hash differs",
            "PART_HASH",
        )
        values = decode(raw, PART_BYTES, c)
        c.require(
            isinstance(values, list) and len(values) == descriptor["rowCount"],
            "Normalized chunk row count differs",
            "ROWS",
        )
        if collection == "receipts":
            self.receipts.extend(values)
        elif collection == "provenance":
            self.provenance.extend(values)
        else:
            for row in values:
                c.keys(row, {"ts_code", "trade_date", *self.m["fields"]})
                c.require(
                    isinstance(row["ts_code"], str)
                    and isinstance(row["trade_date"], str),
                    "Row identity types differ",
                    "ROWS",
                )
                sym, day = row["ts_code"], row["trade_date"]
                key = day + ":" + sym
                c.require(
                    sym in self.m["scope"]["symbols"]
                    and day in self.m["calendar"]
                    and key > self.previous,
                    "Row duplicate/order/outside full scope",
                    "ROWS",
                )
                self.previous = key
                value = {"ts_code": sym, "trade_date": day}
                for field in self.m["fields"]:
                    value[field] = number(
                        row[field],
                        c,
                        positive=field in set(BASE_FIELDS) - {"vol", "amount"},
                        nullable=field in BASIC_FIELDS,
                    )
                c.require(
                    value["vol"] >= 0
                    and value["amount"] >= 0
                    and value["low"] <= min(value["open"], value["close"])
                    and value["high"] >= max(value["open"], value["close"])
                    and value["low"] <= value["high"],
                    "Normalized OHLC/volume envelope invalid",
                    "ROWS",
                )
                self.rows[sym, day] = encode(value)
                if self.paired_rows is not None:
                    other = next(self.paired_rows, None)
                    c.keys(other, {"ts_code", "trade_date", *self.m["fields"]})
                    compared = {
                        "ts_code": other["ts_code"],
                        "trade_date": other["trade_date"],
                    }
                    for field in self.m["fields"]:
                        compared[field] = number(
                            other[field], c, nullable=field in BASIC_FIELDS
                        )
                    c.require(
                        encode(value) == encode(compared),
                        "Result snapshot order/value/null differs from independently reconstructed source",
                        "RESULT_SOURCE_ROWS",
                    )

    def before_raw(self):
        c, m = self.check, self.m
        if self.paired_rows is not None:
            c.require(
                next(self.paired_rows, None) is None,
                "Result snapshot has extra rows",
                "RESULT_SOURCE_ROWS",
            )
        c.require(
            len(self.rows) == m["rowCount"]
            and len(self.receipts) == len(self.p["requests"])
            and len(self.provenance) == 1,
            "Collection record totals differ",
            "ROWS",
        )
        ids = set()
        for r, request in zip(self.receipts, self.p["requests"]):
            c.keys(
                r,
                {
                    "requestKey",
                    "receiptId",
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                    "rawLocation",
                },
            )
            identity(r["receiptId"], c, True)
            identity(r["sha256"], c)
            c.require(
                r["receiptId"] not in ids
                and r["requestKey"] == request["requestKey"]
                and type(r["httpStatus"]) is int
                and r["httpStatus"] == 200
                and r["sourceKind"] == m["sourceKind"],
                "Receipt identity/status/source differs",
                "RECEIPT",
            )
            ids.add(r["receiptId"])
            integer(r["byteLength"], 1, request["responseBytes"], c)
            try:
                dt = datetime.fromisoformat(r["retrievedAt"].replace("Z", "+00:00"))
                valid_time = (
                    dt.tzinfo is not None
                    and isinstance(r["retrievedAt"], str)
                    and len(r["retrievedAt"]) <= 40
                    and re.search(r"T.*(?:Z|[+-][0-9]{2}:[0-9]{2})$", r["retrievedAt"])
                    is not None
                )
            except (ValueError, TypeError, AttributeError):
                valid_time = False
            c.require(
                valid_time, "Receipt requires an explicit retrieval timezone", "RECEIPT"
            )
            loc = r["rawLocation"]
            c.keys(loc, {"ordinal", "offset", "byteLength"})
            integer(loc["ordinal"], 0, len(m["rawArchive"]["chunks"]) - 1, c)
            integer(loc["offset"], 0, RAW_PART_BYTES, c)
            c.require(
                type(loc["byteLength"]) is int and loc["byteLength"] == r["byteLength"],
                "Raw location length differs",
                "RAW_LOCATION",
            )
        basic = sorted(set(m["fields"]) & set(BASIC_FIELDS))
        expected = dict(
            source=(
                "SYNTHETIC_MARKET_FIXTURE"
                if m["sourceKind"] == "fixture"
                else "TUSHARE_PRO"
            ),
            synthetic=m["sourceKind"] == "fixture",
            transport="one_attempt_authorized_endpoint",
            originalProviderWireAvailable=False,
            tradingDates=m["calendar"],
            planRoot=m["planRoot"],
            universeScopeRoot=m["universeScopeRef"]["scopeRoot"],
            membershipPolicy="complete_filtered_set",
            historicalMembershipVerified=False,
            adjustment="OHLC multiplied by adj_factor / first observed adj_factor per symbol",
            volumeUnit="hands",
            amountUnit="CNY_thousands",
            missingSessions="preserved_no_price_fill",
            observedColumns=["raw_close", "vol", "amount", "adj_factor", *basic],
            derivedColumns={
                f: dict(
                    formula="raw_" + f + " * adj_factor / first_adj_factor",
                    classification="CORPORATE_ACTION_ADJUSTED",
                )
                for f in ("open", "high", "low", "close")
            },
        )
        c.equal(self.provenance[0], expected, "Normalization provenance differs")

    def raw_part(self, raw, descriptor):
        c = self.check
        c.require(
            len(raw) == descriptor["byteLength"] and sha(raw) == descriptor["sha256"],
            "Raw part hash differs",
            "PART_HASH",
        )
        offset, count = 0, 0
        while self.raw_index < len(self.receipts):
            r = self.receipts[self.raw_index]
            loc = r["rawLocation"]
            if loc["ordinal"] != descriptor["ordinal"]:
                break
            c.require(
                loc["offset"] == offset and offset + r["byteLength"] <= len(raw),
                "Raw slices overlap or leave a gap",
                "RAW_LOCATION",
            )
            body = raw[offset : offset + r["byteLength"]]
            c.require(
                sha(body) == r["sha256"],
                "Exact delivered response hash differs",
                "RECEIPT_HASH",
            )
            self.response(self.p["requests"][self.raw_index], body)
            offset += r["byteLength"]
            count += 1
            self.raw_index += 1
        c.require(
            count > 0 and offset == len(raw),
            "Raw chunk is unreferenced or has undeclared bytes",
            "RAW_LOCATION",
        )

    def response(self, request, raw):
        c = self.check
        value = decode(raw, request["responseBytes"], c)
        c.require(
            isinstance(value, dict)
            and type(value.get("code")) is int
            and value["code"] == 0,
            "Provider reports an error",
            "PROVIDER_RESPONSE",
        )
        data = value.get("data")
        fields = request["fields"].split(",")
        c.require(
            isinstance(data, dict)
            and isinstance(data.get("fields"), list)
            and all(isinstance(f, str) for f in data["fields"])
            and len(data["fields"]) == len(set(data["fields"]))
            and set(data["fields"]) == set(fields)
            and isinstance(data.get("items"), list)
            and (0 if request["apiName"] == "daily_basic" else 1)
            <= len(data["items"])
            <= 366,
            "Provider table shape/fields/count differs",
            "TABLE",
        )
        rows = []
        for row in data["items"]:
            c.require(
                isinstance(row, list) and len(row) == len(fields),
                "Provider row shape differs",
                "TABLE",
            )
            rows.append(dict(zip(data["fields"], row)))
        if request["apiName"] == "trade_cal":
            expected_days = {
                (
                    datetime.strptime(self.p["scope"]["start"], "%Y%m%d")
                    + timedelta(days=i)
                ).strftime("%Y%m%d")
                for i in range(self.p["budget"]["calendarDays"])
            }
            seen, sessions = set(), []
            for row in rows:
                d = date(row["cal_date"], c)
                c.require(
                    d in expected_days
                    and d not in seen
                    and row["exchange"] == request["params"]["exchange"]
                    and type(row["is_open"]) in {str, int}
                    and row["is_open"] in {0, 1, "0", "1"},
                    "Incomplete/invalid exchange calendar",
                    "CALENDAR",
                )
                if row["pretrade_date"] not in (None, ""):
                    date(row["pretrade_date"], c)
                    c.require(
                        row["pretrade_date"] < d, "Invalid previous session", "CALENDAR"
                    )
                seen.add(d)
                if int(row["is_open"]):
                    sessions.append(d)
            c.require(
                seen == expected_days and sorted(sessions) == self.m["calendar"],
                "Exact complete exchange sessions differ",
                "CALENDAR",
            )
            self.calendar_count += 1
            return
        symbol = request["params"]["ts_code"]
        if symbol != self.current_symbol:
            self.finish_symbol()
            self.current_symbol = symbol
        table = {}
        for row in rows:
            d = row["trade_date"]
            c.require(
                isinstance(d, str)
                and row["ts_code"] == symbol
                and d in self.m["calendar"]
                and d not in table,
                "Provider symbol/date duplicate or outside calendar",
                "TABLE",
            )
            table[d] = row
        self.tables[request["apiName"]] = table

    def finish_symbol(self):
        if self.current_symbol is None:
            return
        c = self.check
        daily, adjustment = self.tables.get("daily", {}), self.tables.get(
            "adj_factor", {}
        )
        c.require(
            daily and set(daily) <= set(adjustment),
            "Whole symbol missing daily/adjustment rows",
            "NORMALIZATION",
        )
        first = number(adjustment[min(daily)]["adj_factor"], c, positive=True)
        for day, row in daily.items():
            factor = number(adjustment[day]["adj_factor"], c, positive=True)
            expected = dict(
                ts_code=self.current_symbol,
                trade_date=day,
                adj_factor=factor,
                raw_close=number(row["close"], c, positive=True),
            )
            for f in ("open", "high", "low", "close"):
                expected[f] = number(
                    number(row[f], c, positive=True) * factor / first, c, positive=True
                )
            for f in ("vol", "amount"):
                expected[f] = number(row[f], c)
                c.require(expected[f] >= 0, "Negative source volume/amount", "NUMBER")
            for f in set(self.m["fields"]) & set(BASIC_FIELDS):
                expected[f] = number(
                    self.tables.get("daily_basic", {}).get(day, {}).get(f),
                    c,
                    nullable=True,
                )
            key = self.current_symbol, day
            c.require(
                self.rows.pop(key, None) == encode(expected),
                "Raw response recomputation differs from normalized row or missing mask",
                "NORMALIZATION",
            )
            self.compared += 1
        self.seen_symbols.add(self.current_symbol)
        self.tables = {}

    def finish(self):
        self.finish_symbol()
        self.check.require(
            self.raw_index == len(self.receipts)
            and not self.rows
            and self.compared == self.m["rowCount"]
            and self.seen_symbols == set(self.m["scope"]["symbols"])
            and self.calendar_count == len({s[-2:] for s in self.seen_symbols}),
            "Incomplete raw closure, full-symbol coverage, or missing mask",
            "NORMALIZATION",
        )


def _audit_market_dataset(
    path, *, expected_root=None, result_audit=None, result_report=None
):
    """Audit a strict market USTAR or equivalent directory, without extracting it."""
    c = Checks()
    path = Path(path)
    c.require(not path.is_symlink(), "Input symlink forbidden", "FILE")

    def process(read, directory=None):
        raw = read("manifest.json", MANIFEST_BYTES)
        m = validate_manifest(raw, c, expected_root)
        p, scope = validate_documents(
            read("plan.json", PLAN_BYTES), read("scope.json", SCOPE_BYTES), m, c
        )
        if directory is not None:
            c.require(
                {x.name for x in directory.iterdir()}
                == {"manifest.json", "plan.json", "scope.json", "parts"},
                "Unexpected root directory members",
                "DIRECTORY",
            )
            parts = directory / "parts"
            c.require(
                not parts.is_symlink()
                and parts.is_dir()
                and {x.name for x in parts.iterdir()}
                == {"rows", "receipts", "provenance", "raw"},
                "Unexpected collection directories",
                "DIRECTORY",
            )
            for name, coll in {**m["collections"], "raw": m["rawArchive"]}.items():
                folder = parts / name
                c.require(
                    not folder.is_symlink()
                    and folder.is_dir()
                    and {x.name for x in folder.iterdir()}
                    == {f"{d['ordinal']}.bin" for d in coll["chunks"]},
                    "Missing/extra collection parts",
                    "DIRECTORY",
                )
        pair = (
            validate_result_binding(result_audit, m, scope, sha(raw), c)
            if result_audit is not None
            else None
        )
        audit = Semantics(m, p, c, result_audit.rows("snapshotRows") if pair else None)
        for name in sorted(m["collections"]):
            for d in m["collections"][name]["chunks"]:
                audit.normalized(
                    name,
                    read(
                        f"parts/{name}/{d['ordinal']}.bin", PART_BYTES, d["byteLength"]
                    ),
                    d,
                )
        audit.before_raw()
        for d in m["rawArchive"]["chunks"]:
            audit.raw_part(
                read(f"parts/raw/{d['ordinal']}.bin", RAW_PART_BYTES, d["byteLength"]),
                d,
            )
        audit.finish()
        return dict(
            status="PASS",
            pairedResult=({**pair, "bundleAudit": result_report} if pair else None),
            auditor="atlas.market_dataset.stdlib_audit/1",
            checks=c.count,
            marketDatasetRoot=sha(raw),
            planRoot=m["planRoot"],
            universeScopeRoot=m["universeScopeRef"]["scopeRoot"],
            profile=m["profile"],
            symbolCount=m["scope"]["symbolCount"],
            rowCount=m["rowCount"],
            receiptCount=len(p["requests"]),
            rawBytes=m["rawArchive"]["byteLength"],
            sourceKind=m["sourceKind"],
            integrityVerified=True,
            normalizationVerified=True,
            fullScopeVerified=True,
            missingMaskVerified=True,
            rootPinned=expected_root is not None,
            trustStatus="unverified_source_authority",
            sourceAuthorityVerified=False,
            historicalMembershipVerified=False,
            catalogResolutionVerified=False,
            responseBytes="exact_delivered_endpoint_bytes",
            originalProviderWireAvailable=False,
            providerCalls=0,
            modelFitted=False,
        )

    if path.is_dir():

        def directory_read(name, ceiling, size=None):
            value = read_file(path / name, ceiling)
            c.require(
                size is None or len(value) == size, "Part length differs", "PART_HASH"
            )
            return value

        report = process(directory_read, path)
    else:
        with regular(path, TAR_MAX) as stream:
            archive = Archive(stream, c)
            report = process(archive.member)
            archive.finish()
    report["checks"] = c.count
    return report


def audit_market_dataset(path, *, expected_root=None, result_bundle=None):
    """Reject malformed external shapes uniformly, without exposing internals."""
    try:
        if result_bundle is not None:
            with open_result_bundle(result_bundle) as (audit, result_report):
                return _audit_market_dataset(
                    path,
                    expected_root=expected_root,
                    result_audit=audit,
                    result_report=result_report,
                )
        return _audit_market_dataset(path, expected_root=expected_root)
    except AuditError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        UnicodeError,
        RecursionError,
        sqlite3.DatabaseError,
    ) as exc:
        raise AuditError("SHAPE", "Invalid bounded source-closure structure") from exc


RESULT_TAR_MAX = RESULT_TOTAL + RESULT_MANIFEST + 257 * 1023 + 1024
RESEARCH_PROFILES = {"pooled_asset_1000_v1", "pooled_asset_1000_auto_candidate_v1"}


class ResultArchive(Archive):
    def exact(self, n):
        self.check.require(
            0 <= n <= RESULT_CHUNK and self.consumed + n <= RESULT_TAR_MAX,
            "Result archive byte budget",
            "BUDGET",
        )
        raw = self.stream.read(n)
        self.consumed += len(raw)
        self.check.require(len(raw) == n, "Truncated result archive", "TAR_TRUNCATED")
        return raw


@contextmanager
def open_result_bundle(path):
    """Copy only bounded registered files to private scratch, then run the full auditor.

    No general tar extractor or source-controlled path is used. The immutable
    scratch copy also prevents a changed source directory between audit/compare.
    """
    c, path = Checks(), Path(path)
    c.require(not path.is_symlink(), "Result input symlink forbidden", "FILE")
    with tempfile.TemporaryDirectory(prefix="atlas-market-pair-audit-") as temporary:
        directory = Path(temporary) / "result"
        directory.mkdir(mode=0o700)

        def save(name, raw):
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)

        with sqlite3.connect(str(Path(temporary) / "audit.sqlite")) as database:

            def copy(read, source_dir=None):
                save("manifest.json", read("manifest.json", RESULT_MANIFEST))
                audit = BundleAudit(directory, database)
                c.require(
                    audit.manifest["kind"] == "forecast",
                    "Paired market result must be forecast-only",
                    "RESULT_KIND",
                )
                if source_dir is not None:
                    c.require(
                        {x.name for x in source_dir.iterdir()}
                        == {"manifest.json", "chunks"},
                        "Unexpected result directory files",
                        "DIRECTORY",
                    )
                    chunks = source_dir / "chunks"
                    c.require(
                        not chunks.is_symlink() and chunks.is_dir(),
                        "Result chunks must be a directory",
                        "DIRECTORY",
                    )
                    existing = {x.name for x in chunks.iterdir()}
                    declared = set(audit.collections)
                    required = {
                        name
                        for name, collection in audit.collections.items()
                        if collection["chunks"]
                    }
                    c.require(
                        required <= existing <= declared,
                        "Missing/extra result chunk directory",
                        "DIRECTORY",
                    )
                    for name in existing:
                        folder = chunks / name
                        c.require(
                            not folder.is_symlink()
                            and folder.is_dir()
                            and {x.name for x in folder.iterdir()}
                            == {
                                str(d["ordinal"]) + ".json"
                                for d in audit.collections[name]["chunks"]
                            },
                            "Result part directory differs",
                            "DIRECTORY",
                        )
                for name, collection in audit.collections.items():
                    for d in collection["chunks"]:
                        target = f"chunks/{name}/{d['ordinal']}.json"
                        raw = read(target, RESULT_CHUNK, d["byteLength"])
                        c.require(
                            len(raw) == d["byteLength"] and sha(raw) == d["sha256"],
                            "Result part hash differs",
                            "RESULT_HASH",
                        )
                        save(target, raw)
                return audit

            if path.is_dir():

                def read(name, ceiling, size=None):
                    raw = read_file(path / name, ceiling)
                    c.require(
                        size is None or len(raw) == size,
                        "Result part length differs",
                        "RESULT_HASH",
                    )
                    return raw

                audit = copy(read, path)
            else:
                with regular(path, RESULT_TAR_MAX) as stream:
                    archive = ResultArchive(stream, c)
                    audit = copy(archive.member)
                    archive.finish()
            report = audit.run()
            yield audit, report


def factor_lookback(expression, fields, check):
    """Independently interpret only the registered DSL's causal window lengths.

    No evaluator, production parser, feature cache, or reported lookback is trusted.
    Rolling windows include the current session; lag/delta/returns count intervals.
    """
    check.require(
        isinstance(expression, str) and 0 < len(expression) <= 500,
        "Invalid declared factor expression",
        "RESULT_CLOCK",
    )
    token = re.compile(
        r"(?:0|[1-9][0-9]*)(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?"
        r"|\.[0-9]+(?:[eE][+-]?[0-9]+)?|[A-Za-z_][A-Za-z_0-9]*|[()+\-*/,]"
    )
    tokens, position, nesting = [], 0, 0
    while position < len(expression):
        if expression[position] in " \t\r\n":
            position += 1
            continue
        match = token.match(expression, position)
        check.require(match is not None, "Unsupported factor token", "RESULT_CLOCK")
        value = match.group()
        if value == "(":
            nesting += 1
            check.require(
                nesting <= 128,
                "Factor parentheses exceed language budget",
                "RESULT_CLOCK",
            )
        elif value == ")":
            nesting -= 1
        check.require(
            not (value == ")" and tokens and tokens[-1] == ","),
            "Trailing factor argument",
            "RESULT_CLOCK",
        )
        tokens.append(value)
        position = match.end()
    try:
        tree = ast.parse(" ".join(tokens), mode="eval").body
    except (SyntaxError, RecursionError) as exc:
        raise AuditError("RESULT_CLOCK", "Invalid declared factor syntax") from exc
    pending, count = [(tree, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        check.require(
            count <= 128 and depth <= 16,
            "Declared factor tree exceeds bounded language",
            "RESULT_CLOCK",
        )
        if isinstance(node, ast.Call):
            children = node.args
        elif isinstance(node, ast.BinOp):
            children = [node.left, node.right]
        elif isinstance(node, ast.UnaryOp):
            children = [node.operand]
        else:
            children = []
        pending.extend((child, depth + 1) for child in children)

    def constant(node):
        sign = 1
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            sign = -1 if isinstance(node.op, ast.USub) else 1
            node = node.operand
        check.require(
            isinstance(node, ast.Constant)
            and type(node.value) in (int, float)
            and math.isfinite(node.value)
            and abs(node.value) <= 1000000,
            "Invalid factor numeric constant",
            "RESULT_CLOCK",
        )
        return sign * node.value

    observed = set()
    intervals = {"lag", "delta", "returns"}
    rolling = {"ts_mean", "ts_std", "ts_min", "ts_max", "ts_sum", "ts_rank"}

    def visit(node):
        if isinstance(node, ast.Constant):
            constant(node)
            return 0
        if isinstance(node, ast.Name):
            check.require(
                node.id in fields,
                "Factor field absent from frozen source",
                "RESULT_CLOCK",
            )
            observed.add(node.id)
            return 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand)
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            return max(visit(node.left), visit(node.right))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and not node.keywords
        ):
            name, args = node.func.id, node.args
            if name in intervals | rolling and len(args) == 2:
                window = constant(args[1])
                check.require(
                    int(window) == window and 1 <= window <= 252,
                    "Invalid factor window",
                    "RESULT_CLOCK",
                )
                return visit(args[0]) + int(window) - (name in rolling)
            if (
                name in {"rank", "zscore", "log", "sqrt", "abs", "sign"}
                and len(args) == 1
            ):
                return visit(args[0])
            if name in {"min", "max"} and len(args) == 2:
                return max(visit(args[0]), visit(args[1]))
            if name == "clip" and len(args) == 3:
                check.require(
                    constant(args[1]) < constant(args[2]),
                    "Invalid factor clipping bounds",
                    "RESULT_CLOCK",
                )
                return visit(args[0])
        raise AuditError("RESULT_CLOCK", "Unsupported declared factor operation")

    lookback = visit(tree)
    check.require(
        observed and lookback <= 504, "Invalid factor lookback", "RESULT_CLOCK"
    )
    return lookback


def asset_target_definition(symbol):
    """Registered unit-asset identity, derived from a frozen source member only."""
    content = dict(
        kind="asset_price",
        symbols=[symbol],
        quantities=[1],
        unit="CNY_adjusted_research_price",
        construction="single_asset",
        formationStart=None,
        formationEnd=None,
        hedgeAudit={},
    )
    return {"id": "target_" + sha(encode(content))[:24], **content}


def validate_asset_hedge_fits(
    audit, calendar, symbols, start, observation, refit, check
):
    """Check complete construction refs on every scheduled refit, before holdout too.

    Unit-asset definitions exist even when source observations or future endpoints
    are missing. Their construction status therefore stays valid and all member
    IDs remain present; an invalid numerical sample is not a missing target.
    """
    check.require(
        type(refit) is int and 20 <= refit <= 126,
        "Invalid declared asset construction refit clock",
        "RESULT_CLOCK",
    )
    expected_ids = [asset_target_definition(s)["id"] for s in sorted(symbols)]
    positions, last = [], None
    for t in range(start, len(calendar), observation):
        if last is None or t - last >= refit:
            positions.append(t)
            last = t
    check.require(
        audit.count("hedgeFits") == len(positions),
        "Asset construction fits omit or add full-source refit dates",
        "RESULT_HEDGE_REFERENCES",
    )
    rows = iter(audit.rows("hedgeFits"))
    for t in positions:
        row = next(rows, None)
        check.require(
            isinstance(row, dict)
            and set(row) == {"date", "informationCutoff", "targetIds", "status"}
            and row["date"] == calendar[t]
            and row["informationCutoff"] == calendar[t - 1]
            and row["status"] == "valid"
            and isinstance(row["targetIds"], list)
            and row["targetIds"] == expected_ids,
            "Asset construction clock/status or complete ordered raw target references differ",
            "RESULT_HEDGE_REFERENCES",
        )
    check.require(
        next(rows, None) is None,
        "Extra raw asset construction fits",
        "RESULT_HEDGE_REFERENCES",
    )
    return dict(
        hedgeFitClockVerified=True,
        fullHedgeTargetReferencesVerified=True,
        expectedHedgeFits=len(positions),
        hedgeFitTargetCount=len(expected_ids),
    )


def validate_asset_coverage(audit, manifest, strategy, check):
    """Derive the full symbol × terminal-origin grid without trusting plan/targets."""
    calendar, symbols = manifest["calendar"], manifest["scope"]["symbols"]
    observation = strategy["research"]["observationDays"]
    horizon = strategy["target"]["horizonSessions"]
    fraction = strategy["validation"]["holdoutFraction"]
    check.require(
        type(observation) is int
        and 1 <= observation <= 60
        and type(horizon) is int
        and 1 <= horizon <= 60
        and type(fraction) in (int, float)
        and math.isfinite(fraction)
        and 0.1 <= fraction <= 0.4,
        "Invalid declared observation, horizon or holdout clock",
        "RESULT_CLOCK",
    )
    factors = strategy["factors"]
    check.require(
        all(
            isinstance(f, dict) and f.get("role") in {"predictor", "event"}
            for f in factors
        ),
        "Asset-price factors cannot declare hedge or unknown roles",
        "RESULT_PROFILE",
    )
    warmup = max(
        (factor_lookback(f["expression"], manifest["fields"], check) for f in factors),
        default=0,
    )
    start = max(61, warmup + 1)
    eligible = len(calendar) - start
    boundary = int(eligible * (1 - fraction))
    check.require(
        eligible > 0 and boundary >= 1 and eligible - boundary >= 10,
        "Frozen calendar cannot support declared terminal holdout",
        "RESULT_CLOCK",
    )
    holdout = calendar[start + boundary]
    positions = [
        t for t in range(start, len(calendar), observation) if calendar[t] >= holdout
    ]
    expected_count = len(positions) * len(symbols)
    sample_count = len(range(start, len(calendar), observation)) * len(symbols)
    check.require(
        0 < expected_count <= 80000 and sample_count <= 300000,
        "Complete declared asset grid exceeds profile or is empty",
        "RESULT_COVERAGE",
    )
    report, forecast, coverage = (
        audit.documents[k] for k in ("report", "forecast", "coverage")
    )
    baseline = bool(factors)
    check.require(
        coverage["source"] == "samples_before_model_fitting"
        and coverage["baselineRequired"] is baseline
        and coverage["holdoutStart"] == holdout
        and type(report["research"].get("observationDays")) is int
        and report["research"].get("observationDays") == observation,
        "Coverage clock or baseline declaration differs from source and strategy",
        "RESULT_CLOCK",
    )
    diagnostics = [forecast["diagnostics"], report["validation"]]
    if baseline:
        diagnostics.append(
            forecast["diagnostics"]["factorIncrement"]["baselineValidation"]
        )
    for declared in diagnostics:
        check.require(
            isinstance(declared, dict)
            and declared.get("holdoutStart") == holdout
            and declared.get("holdoutEnd") == calendar[-1],
            "Forecast/report/baseline holdout clock differs from independently derived source clock",
            "RESULT_CLOCK",
        )
        selection = declared.get("selectionAudit")
        if selection is not None:
            check.require(
                isinstance(selection, dict)
                and selection.get("terminalSelectionCutoff") == holdout,
                "Declared model-selection cutoff differs from terminal holdout",
                "RESULT_CLOCK",
            )
    capacity = report.get("capacity")
    if capacity is not None:
        check.require(
            isinstance(capacity, dict)
            and capacity.get("holdoutStart") == holdout
            and capacity.get("symbols") == symbols
            and capacity.get("sampleRows") == sample_count
            and capacity.get("completeGridRows") == len(calendar) * len(symbols)
            and capacity.get("inputRows") == manifest["rowCount"]
            and capacity.get("forecastRows") == expected_count
            and capacity.get("baselineRequired") is baseline,
            "Declared capacity plan differs from complete asset grid",
            "RESULT_CLOCK",
        )
    check.require(
        audit.count("targets") == len(symbols),
        "Targets omit or add frozen symbols",
        "RESULT_TARGETS",
    )
    targets = {}
    for target in audit.rows("targets"):
        members = target.get("symbols")
        check.require(
            isinstance(members, list)
            and len(members) == 1
            and members[0] in symbols
            and members[0] not in targets,
            "Target is not a unique frozen single asset",
            "RESULT_TARGETS",
        )
        expected = asset_target_definition(members[0])
        # Registered bundle canonical numbers equate 1 and 1.0; never bool.
        quantities = target.get("quantities")
        check.require(
            isinstance(quantities, list)
            and len(quantities) == 1
            and type(quantities[0]) in (int, float)
            and quantities[0] == 1
            and set(target) == set(expected)
            and all(target[k] == v for k, v in expected.items()),
            "Target definition or identity differs from unit single asset",
            "RESULT_TARGETS",
        )
        targets[members[0]] = target["id"]
    collections = ["plannedOrigins", "forecasts"] + (
        ["baselineRows"] if baseline else []
    )
    if not baseline:
        check.require(
            audit.count("baselineRows") == 0 and audit.count("baselineModelFits") == 0,
            "Undeclared factor-free baseline",
            "RESULT_COVERAGE",
        )
    for name in collections:
        check.require(
            audit.count(name) == expected_count,
            "Incomplete full asset grid: " + name,
            "RESULT_COVERAGE",
        )
        rows = iter(audit.rows(name))
        for t in positions:
            entry = calendar[t + 1] if t + 1 < len(calendar) else None
            end = calendar[t + 1 + horizon] if t + 1 + horizon < len(calendar) else None
            for symbol in symbols:
                row = next(rows, None)
                check.require(
                    row is not None
                    and tuple(
                        row.get(k)
                        for k in ("date", "targetId", "entryDate", "targetDate")
                    )
                    == (calendar[t], targets[symbol], entry, end),
                    "Origin order/member/endpoint differs from complete source grid: "
                    + name,
                    "RESULT_COVERAGE",
                )
                if name != "plannedOrigins":
                    check.require(
                        type(row.get("horizonSessions")) is int
                        and row["horizonSessions"] == horizon
                        and row.get("informationCutoff") == calendar[t] + "_AFTER_CLOSE"
                        and (
                            entry is not None
                            and end is not None
                            or row.get("status") == "invalid"
                        ),
                        "Forecast clock or retained invalid tail differs: " + name,
                        "RESULT_CLOCK",
                    )
        check.require(
            next(rows, None) is None, "Extra asset origins", "RESULT_COVERAGE"
        )
    hedge_fits = validate_asset_hedge_fits(
        audit, calendar, symbols, start, observation, strategy["model"]["refitDays"], check
    )
    return dict(
        **hedge_fits,
        fullAssetCoverageVerified=True,
        originClockVerified=True,
        targetDefinitionsVerified=True,
        expectedForecastRows=expected_count,
        expectedOriginDates=len(positions),
        holdoutStart=holdout,
        sampleStartIndex=start,
        factorLookback=warmup,
        baselineRequired=baseline,
        invalidTailOriginsRetained=True,
        coverageBasis=(
            "frozen source calendar and all symbols; "
            "independently interpreted declared strategy clock"
        ),
        originalRequestedStrategyAuthenticated=False,
    )


def validate_result_binding(audit, manifest, scope, dataset_root, check):
    """Bind content independently; server ownership and admission are not authenticated."""
    report, snapshot, forecast = (
        audit.documents[k] for k in ("report", "snapshot", "forecast")
    )
    evidence = report["provenance"].get("marketSource")
    check.keys(
        evidence,
        {"marketDatasetRef", "universeScopeRef", "admissionProfile", "rowValueRoot"},
    )
    check.equal(
        snapshot["provenance"].get("marketSource"),
        evidence,
        "Report/snapshot marketSource assertions differ",
    )
    identity(evidence["rowValueRoot"], check)
    ref = evidence["marketDatasetRef"]
    check.keys(ref, {"datasetId", "datasetRoot", "format", "version"})
    identity(ref["datasetId"], check, True)
    check.require(
        ref["datasetRoot"] == dataset_root
        and ref["format"] == "atlas.quant.market_dataset"
        and type(ref["version"]) is int
        and ref["version"] == 1,
        "Result refers to a different market source root",
        "RESULT_SOURCE_ROOT",
    )
    check.equal(
        evidence["universeScopeRef"],
        manifest["universeScopeRef"],
        "Result full-scope reference differs",
    )
    profile = evidence["admissionProfile"]
    check.require(
        isinstance(profile, str) and profile in RESEARCH_PROFILES,
        "Unregistered research admission assertion",
        "RESULT_PROFILE",
    )
    strategy = forecast["sourceStrategy"]
    check.equal(
        report["strategy"], strategy, "Result report and forecast strategies differ"
    )
    universe = strategy["universe"]
    for key in (
        "symbols",
        "start",
        "end",
        "selection",
        "snapshotHash",
        "resolutionHash",
    ):
        check.equal(
            universe.get(key),
            scope[key],
            "Result strategy changed complete frozen scope evidence",
        )
    check.require(
        universe.get("subsetPolicy") == "all",
        "Result strategy is not the whole frozen scope",
        "RESULT_PROFILE",
    )
    automatic = profile == "pooled_asset_1000_auto_candidate_v1"
    model = strategy["model"]
    check.require(
        strategy["target"]["kind"] == "asset_price"
        and strategy["execution"]["enabled"] is False
        and isinstance(strategy["factors"], list)
        and len(strategy["factors"]) <= 16
        and model["estimator"] == ("auto" if automatic else "ridge")
        and model["family"]
        in (["mean_reversion"] if automatic else ["mean_reversion", "trend"])
        and type(model["refitDays"]) is int
        and model["refitDays"] >= 20
        and type(strategy["validation"]["innerFolds"]) is int
        and strategy["validation"]["innerFolds"] == 2
        and type(strategy["validation"]["outerFolds"]) is int
        and strategy["validation"]["outerFolds"] == 2,
        "Result strategy does not meet registered market profile",
        "RESULT_PROFILE",
    )
    check.require(
        audit.count("forecasts") <= 80000
        and audit.count("targets") <= 300000
        and audit.count("modelFits") <= 300000
        and audit.count("snapshotRows") == manifest["rowCount"],
        "Result numerical limits or complete snapshot count differ",
        "RESULT_PROFILE",
    )
    for provenance in (report["provenance"], snapshot["provenance"]):
        check.require(
            provenance.get("synthetic") is (manifest["sourceKind"] == "fixture")
            and provenance.get("source")
            == (
                "SYNTHETIC_MARKET_FIXTURE"
                if manifest["sourceKind"] == "fixture"
                else "TUSHARE_PRO"
            ),
            "Result source kind assertion differs",
            "RESULT_SOURCE_KIND",
        )
    check.equal(
        snapshot["provenance"].get("tradingDates"),
        manifest["calendar"],
        "Result trading calendar differs",
    )
    coverage = validate_asset_coverage(audit, manifest, strategy, check)
    return dict(
        **coverage,
        bundleId=audit.bundle_id,
        sourceContentRootVerified=True,
        universeScopeVerified=True,
        registeredProfileVerified=True,
        admissionProfile=profile,
        fullSnapshotRowsVerified=True,
        snapshotRows=manifest["rowCount"],
        rowValueRootInternallyConsistent=True,
        rowValueRootRecomputed=False,
        marketDatasetId=ref["datasetId"],
        datasetIdAuthenticated=False,
        ownershipVerified=False,
        serverAdmissionAuthenticated=False,
        comparison="all ordered source fields; exact finite numeric values and nulls",
    )
