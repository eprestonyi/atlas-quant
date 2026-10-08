"""Only normalize durable immutable receipts; missing sessions remain missing."""

from datetime import timedelta
import math
from .protocol import *


def table(request, receipt):
    raw = receipt["raw"]
    require(
        isinstance(raw, bytes)
        and len(raw) <= request["responseBytes"]
        and len(raw) == receipt["byteLength"]
        and sha(raw) == receipt["sha256"],
        "MARKET_RECEIPT",
        "Durable receipt identity differs",
    )
    require(
        receipt["httpStatus"] == 200,
        "PROVIDER_HTTP_STATUS",
        "Definite provider HTTP failure retained",
    )
    value = decode(raw, limit=request["responseBytes"])
    require(
        isinstance(value, dict)
        and type(value.get("code")) is int
        and value["code"] == 0,
        "PROVIDER_RESPONSE_ERROR",
        "Definite provider API failure retained",
    )
    d = value.get("data")
    fields = request["fields"].split(",")
    require(
        isinstance(d, dict)
        and isinstance(d.get("fields"), list)
        and len(set(d["fields"])) == len(d["fields"])
        and set(d["fields"]) == set(fields)
        and isinstance(d.get("items"), list),
        "MARKET_TABLE",
        "Provider field contract differs",
    )
    require(
        (0 if request["apiName"] == "daily_basic" else 1) <= len(d["items"]) <= 366,
        "MARKET_TABLE",
        "Empty/truncated/oversized provider table",
    )
    rows = []
    for row in d["items"]:
        require(
            isinstance(row, list) and len(row) == len(fields),
            "MARKET_TABLE",
            "Provider row shape differs",
        )
        rows.append(dict(zip(d["fields"], row)))
    return rows


def calendar(request, receipt):
    rows = table(request, receipt)
    p = request["params"]
    start, end = date(p["start_date"]), date(p["end_date"])
    expected = {
        (start + timedelta(days=i)).strftime("%Y%m%d")
        for i in range((end - start).days + 1)
    }
    seen = set()
    sessions = []
    for r in rows:
        d = r["cal_date"]
        date(d)
        require(
            d not in seen
            and d in expected
            and r["exchange"] == p["exchange"]
            and type(r["is_open"]) in {str, int}
            and r["is_open"] in {0, 1, "0", "1"},
            "MARKET_CALENDAR",
            "Incomplete or mismatched calendar",
        )
        seen.add(d)
        if r["pretrade_date"] not in ("", None):
            date(r["pretrade_date"])
            require(
                r["pretrade_date"] < d, "MARKET_CALENDAR", "Invalid previous session"
            )
        if int(r["is_open"]):
            sessions.append(d)
    require(
        seen == expected and sessions,
        "MARKET_CALENDAR",
        "Calendar does not cover exact requested days",
    )
    return sorted(sessions)


def keyed(request, receipt, sessions):
    result = {}
    for r in table(request, receipt):
        d = r.get("trade_date")
        require(
            r.get("ts_code") == request["params"]["ts_code"]
            and d in sessions
            and d not in result,
            "MARKET_IDENTITY",
            "Provider row symbol/date duplicate or outside official sessions",
        )
        result[d] = r
    return result


def number(x, *, positive=False, nullable=False):
    if x is None and nullable:
        return None
    require(
        type(x) in {int, float} and math.isfinite(x) and (not positive or x > 0),
        "MARKET_NUMBER",
        "Provider numeric value invalid",
    )
    return float(x)


def symbol_rows(requests, read_receipt, sessions, fields):
    by = {r["apiName"]: keyed(r, read_receipt(r), sessions) for r in requests}
    daily, adjustment = by["daily"], by["adj_factor"]
    require(
        daily and set(daily) <= set(adjustment),
        "MARKET_ADJUSTMENT",
        "Missing daily adjustment for a complete symbol",
    )
    anchor = number(adjustment[min(daily)]["adj_factor"], positive=True)
    for d, row in sorted(daily.items()):
        adj = number(adjustment[d]["adj_factor"], positive=True)
        out = {
            "ts_code": row["ts_code"],
            "trade_date": d,
            "adj_factor": adj,
            "raw_close": number(row["close"], positive=True),
        }
        for k in ["open", "high", "low", "close"]:
            out[k] = number(number(row[k], positive=True) * adj / anchor, positive=True)
        for k in ["vol", "amount"]:
            out[k] = number(row[k])
            require(out[k] >= 0, "MARKET_NUMBER", "Volume/amount cannot be negative")
        require(
            out["low"] <= min(out["open"], out["close"])
            and out["high"] >= max(out["open"], out["close"])
            and out["low"] <= out["high"],
            "MARKET_NUMBER",
            "Invalid OHLC envelope",
        )
        for k in set(fields) & set(BASIC_FIELDS):
            out[k] = number(by.get("daily_basic", {}).get(d, {}).get(k), nullable=True)
        yield out


class ChunkWriter:
    def __init__(self, write):
        self.write = write
        self.total = 0
        self.count = 0

    def collection(self, name, rows):
        chunks = []
        pending = []
        size = 2
        total_rows = 0

        def flush():
            nonlocal pending, size, total_rows
            if not pending:
                return
            raw = b"[" + b",".join(pending) + b"]"
            self.total += len(raw)
            self.count += 1
            require(
                self.total <= DATA_BYTES and self.count <= MAX_CHUNKS,
                "MARKET_OUTPUT_BUDGET",
                "Complete normalized source exceeds declared budget",
            )
            p = {
                "ordinal": len(chunks),
                "sha256": sha(raw),
                "byteLength": len(raw),
                "rowCount": len(pending),
            }
            self.write(name, p["ordinal"], raw)
            chunks.append(p)
            total_rows += len(pending)
            pending = []
            size = 2

        for row in rows:
            raw = encode(row)
            require(
                len(raw) + 2 <= CHUNK_BYTES,
                "MARKET_OUTPUT_BUDGET",
                "A source record exceeds one chunk",
            )
            if pending and (size + len(raw) + 1 > CHUNK_BYTES or len(pending) >= 10000):
                flush()
            pending.append(raw)
            size += len(raw) + (1 if len(pending) > 1 else 0)
        flush()
        require(chunks, "MARKET_OUTPUT_BUDGET", "Empty required source collection")
        return {
            "chunks": chunks,
            "byteLength": sum(p["byteLength"] for p in chunks),
            "rowCount": total_rows,
        }


def build_publication(job, plan, read_receipt, write_chunk):
    raw_chunks = []
    pending = bytearray()

    def flush_raw():
        if not pending:
            return
        require(
            len(raw_chunks) < RAW_CHUNKS,
            "MARKET_RAW_BUDGET",
            "Raw archive chunk budget exceeded",
        )
        raw = bytes(pending)
        ordinal = len(raw_chunks)
        write_chunk("raw", ordinal, raw)
        raw_chunks.append(
            {"ordinal": ordinal, "sha256": sha(raw), "byteLength": len(raw)}
        )
        pending.clear()

    kinds = set()
    raw_total = 0
    receipt_meta = []
    for r in plan["requests"]:
        rec = read_receipt(r)
        table(r, rec)
        kinds.add(rec["sourceKind"])
        raw_total += rec["byteLength"]
        require(
            raw_total <= RAW_BYTES,
            "MARKET_RAW_BUDGET",
            "Raw parent budget exceeded before archive write",
        )
        if pending and len(pending) + rec["byteLength"] > RAW_CHUNK_BYTES:
            flush_raw()
        location = {
            "ordinal": len(raw_chunks),
            "offset": len(pending),
            "byteLength": rec["byteLength"],
        }
        pending.extend(rec["raw"])
        receipt_meta.append(
            {
                k: rec[k]
                for k in [
                    "requestKey",
                    "receiptId",
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                ]
            }
        )
        receipt_meta[-1]["rawLocation"] = location
    flush_raw()
    require(
        raw_total <= RAW_BYTES and len(kinds) == 1 and kinds <= {"fixture", "provider"},
        "MARKET_SOURCE",
        "Mixed/unknown source kind or parent raw budget exceeded",
    )
    calendars = [
        calendar(r, read_receipt(r))
        for r in plan["requests"]
        if r["apiName"] == "trade_cal"
    ]
    require(
        calendars and all(c == calendars[0] for c in calendars),
        "MARKET_CALENDAR",
        "Exchange calendars differ; no assumed alignment",
    )
    sessions = calendars[0]
    require(
        len(sessions) * plan["scope"]["symbolCount"] <= 300000,
        "MARKET_ROW_BUDGET",
        "Complete grid exceeds profile",
    )
    source_kind = next(iter(kinds))
    synthetic = source_kind == "fixture"
    groups = {s: [] for s in plan["scope"]["symbols"]}
    for r in plan["requests"]:
        if r["apiName"] != "trade_cal":
            groups[r["params"]["ts_code"]].append(r)

    def rows():
        count = 0
        for symbol in plan["scope"]["symbols"]:
            for row in symbol_rows(
                groups[symbol], read_receipt, set(sessions), plan["fields"]
            ):
                count += 1
                require(
                    count <= 300000, "MARKET_ROW_BUDGET", "Observed rows exceed budget"
                )
                yield row

    writer = ChunkWriter(write_chunk)
    parts = {
        # Bounded 300k/23-column panel, sorted once for the common snapshot order.
        # Whole-pool numerical code receives every observed row; no symbol batching.
        "rows": writer.collection(
            "rows", sorted(rows(), key=lambda r: (r["trade_date"], r["ts_code"]))
        ),
        "receipts": writer.collection("receipts", receipt_meta),
    }
    provenance = {
        "source": "SYNTHETIC_MARKET_FIXTURE" if synthetic else "TUSHARE_PRO",
        "synthetic": synthetic,
        "transport": "one_attempt_authorized_endpoint",
        "originalProviderWireAvailable": False,
        "tradingDates": sessions,
        "planRoot": plan["planRoot"],
        "universeScopeRoot": plan["universeScopeRef"]["scopeRoot"],
        "membershipPolicy": "complete_filtered_set",
        "historicalMembershipVerified": False,
        "adjustment": "OHLC multiplied by adj_factor / first observed adj_factor per symbol",
        "volumeUnit": "hands",
        "amountUnit": "CNY_thousands",
        "missingSessions": "preserved_no_price_fill",
        "observedColumns": ["raw_close", "vol", "amount", "adj_factor"]
        + sorted(set(plan["fields"]) & set(BASIC_FIELDS)),
        "derivedColumns": {
            c: {
                "formula": "raw_" + c + " * adj_factor / first_adj_factor",
                "classification": "CORPORATE_ACTION_ADJUSTED",
            }
            for c in ["open", "high", "low", "close"]
        },
    }
    parts["provenance"] = writer.collection("provenance", [provenance])
    manifest = {
        "format": "atlas.quant.market_dataset",
        "version": 1,
        "profile": PROFILE,
        "planRoot": plan["planRoot"],
        "universeScopeRef": plan["universeScopeRef"],
        "scope": plan["scope"],
        "calendar": sessions,
        "fields": plan["fields"],
        "rowCount": parts["rows"]["rowCount"],
        "sourceKind": source_kind,
        "collections": parts,
        "rawArchive": {
            "chunks": raw_chunks,
            "byteLength": raw_total,
            "receiptCount": len(receipt_meta),
        },
    }
    require(
        len(encode(manifest)) <= MANIFEST_BYTES,
        "MARKET_OUTPUT_BUDGET",
        "Manifest exceeds budget",
    )
    return manifest
