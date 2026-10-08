"""Closed market-source contract; no model or provider request lives here."""

from datetime import datetime
import re
from ..financial_acquisition.protocol import (
    encode,
    decode,
    require,
    sha,
    identifier,
    digest,
    timestamp,
    utc_now,
)

CAPABILITY = "market-acquire/1"
PROFILE = "pooled_asset_1000_v1"
META_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 512 * 1024
MANIFEST_BYTES = 256 * 1024
RAW_BYTES = 512 * 1024 * 1024
RAW_CHUNK_BYTES = 4 * 1024 * 1024
RAW_CHUNKS = 256
DATA_BYTES = 128 * 1024 * 1024
MAX_CHUNKS = 320
BASE_FIELDS = "open high low close raw_close vol amount adj_factor".split()
BASIC_FIELDS = "turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv".split()
LIMITS = {
    "maxSymbols": 1000,
    "maxCalendarDays": 366,
    "maxRows": 300000,
    "maxRequests": 3002,
    "maxRawBytes": RAW_BYTES,
    "maxNormalizedBytes": DATA_BYTES,
    "maxRequestSeconds": 30,
    "maxWallSeconds": 7200,
    "leaseSeconds": 120,
    "heartbeatSeconds": 20,
    "requestsPerMinute": 60,
    "maxActualAttemptsPerRequest": 1,
}


def date(value):
    require(
        isinstance(value, str) and re.fullmatch(r"\d{8}", value),
        "MARKET_DATE",
        "Explicit YYYYMMDD date required",
    )
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        require(False, "MARKET_DATE", "Invalid date")


def validate_request(r, scope):
    require(
        isinstance(r, dict)
        and set(r)
        == {
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
        "MARKET_REQUEST",
        "Unexpected request fields",
    )
    require(
        type(r["ordinal"]) is int
        and 0 <= r["ordinal"] < 3002
        and r["authorizationScope"] == scope
        and r["provider"] == "TUSHARE_PRO"
        and r["maxAttempts"] == 1,
        "MARKET_REQUEST",
        "Request identity/authorization differs",
    )
    p = r["params"]
    require(isinstance(p, dict), "MARKET_REQUEST", "Explicit parameters required")
    start, end = date(p.get("start_date")), date(p.get("end_date"))
    require(
        0 < (end - start).days + 1 <= 366,
        "MARKET_REQUEST",
        "Request date budget exceeded",
    )
    api = r["apiName"]
    if api == "trade_cal":
        require(
            set(p) == {"exchange", "start_date", "end_date"}
            and p["exchange"] in {"SSE", "SZSE"},
            "MARKET_REQUEST",
            "Unsupported exchange",
        )
        fields = "exchange,cal_date,is_open,pretrade_date"
        budget = 64 * 1024
    else:
        require(
            set(p) == {"ts_code", "start_date", "end_date"}
            and isinstance(p["ts_code"], str)
            and re.fullmatch(r"\d{6}\.(SH|SZ)", p["ts_code"]),
            "MARKET_REQUEST",
            "Unsupported security",
        )
        require(
            api in {"daily", "adj_factor", "daily_basic"},
            "MARKET_REQUEST",
            "Unknown market endpoint",
        )
        fields = {
            "daily": "ts_code,trade_date,open,high,low,close,vol,amount",
            "adj_factor": "ts_code,trade_date,adj_factor",
        }.get(api)
        budget = {
            "daily": 128 * 1024,
            "adj_factor": 64 * 1024,
            "daily_basic": 256 * 1024,
        }[api]
        if api == "daily_basic":
            tokens = r["fields"].split(",") if isinstance(r["fields"], str) else []
            require(
                tokens[:2] == ["ts_code", "trade_date"]
                and tokens[2:]
                and tokens[2:] == sorted(set(tokens[2:]))
                and set(tokens[2:]) <= set(BASIC_FIELDS),
                "MARKET_REQUEST",
                "Unregistered basic fields",
            )
            fields = r["fields"]
    require(
        r["fields"] == fields and r["responseBytes"] == budget,
        "MARKET_REQUEST",
        "Request field/byte budget changed",
    )
    definition = {k: v for k, v in r.items() if k not in {"ordinal", "requestKey"}}
    require(
        sha(encode(definition)) == digest(r["requestKey"]),
        "MARKET_REQUEST",
        "Request hash differs",
    )
    return r


def validate_plan(meta, job, scope):
    require(
        isinstance(meta, dict) and set(meta) == {"job", "plan", "publicationLimits"},
        "MARKET_PLAN",
        "Unexpected input envelope",
    )
    require(
        meta["job"]
        == {"id": job["id"], "kind": "market_acquire", "planId": job["planId"]},
        "MARKET_PLAN",
        "Input belongs to another claim",
    )
    p = meta["plan"]
    require(
        isinstance(p, dict)
        and set(p)
        == {
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
        "MARKET_PLAN",
        "Closed plan required",
    )
    require(
        sha(encode({k: v for k, v in p.items() if k != "planRoot"}))
        == digest(p.get("planRoot"))
        == job["planRoot"],
        "MARKET_PLAN",
        "Immutable plan root differs",
    )
    require(
        p.get("format") == "atlas.quant.market_acquisition_plan"
        and type(p.get("version")) is int
        and p.get("version") == 1
        and p.get("profile") == PROFILE
        and p.get("authorizationScope") == scope
        and p.get("membershipPolicy") == "complete_filtered_set"
        and p.get("blockedReasons") == [],
        "MARKET_PLAN",
        "Unsupported or blocked acquisition plan",
    )
    s = p["scope"]
    require(
        isinstance(s, dict)
        and set(s) == {"symbols", "start", "end", "symbolCount", "scopeRoot"}
        and s["scopeRoot"] == p["universeScopeRef"]["scopeRoot"],
        "MARKET_PLAN",
        "Scope identity differs",
    )
    symbols = s["symbols"]
    require(
        isinstance(symbols, list)
        and 1 <= len(symbols) <= 1000
        and symbols == sorted(set(symbols))
        and s["symbolCount"] == len(symbols),
        "MARKET_PLAN",
        "Complete sorted universe required",
    )
    for x in symbols:
        require(
            isinstance(x, str) and re.fullmatch(r"\d{6}\.(SH|SZ)", x),
            "MARKET_PLAN",
            "Unsupported security",
        )
    days = (date(s["end"]) - date(s["start"])).days + 1
    require(1 <= days <= 366, "MARKET_PLAN", "Date budget exceeded")
    require(
        p["fields"] == sorted(set(p["fields"]))
        and set(BASE_FIELDS) <= set(p["fields"]) <= set(BASE_FIELDS + BASIC_FIELDS),
        "MARKET_PLAN",
        "Unregistered source fields",
    )
    requests = p["requests"]
    require(
        isinstance(requests, list) and len(requests) <= 3002,
        "MARKET_PLAN",
        "Request budget exceeded",
    )
    expected = []
    venues = sorted({"SSE" if x.endswith(".SH") else "SZSE" for x in symbols})
    for v in venues:
        expected.append(
            (
                "trade_cal",
                {"exchange": v, "start_date": s["start"], "end_date": s["end"]},
            )
        )
    basic = bool(set(p["fields"]) & set(BASIC_FIELDS))
    for symbol in symbols:
        for api in ["daily", "adj_factor"] + (["daily_basic"] if basic else []):
            expected.append(
                (
                    api,
                    {"ts_code": symbol, "start_date": s["start"], "end_date": s["end"]},
                )
            )
    require(
        len(requests) == len(expected),
        "MARKET_PLAN",
        "Missing complete-universe request",
    )
    for i, (r, (api, params)) in enumerate(zip(requests, expected)):
        validate_request(r, scope)
        require(
            r["ordinal"] == i and r["apiName"] == api and r["params"] == params,
            "MARKET_PLAN",
            "Request ordering or scope differs",
        )
        if api == "daily_basic":
            require(
                r["fields"]
                == ",".join(
                    ["ts_code", "trade_date"]
                    + sorted(set(p["fields"]) & set(BASIC_FIELDS))
                ),
                "MARKET_PLAN",
                "Basic field request differs",
            )
    require(
        len({r["requestKey"] for r in requests}) == len(requests),
        "MARKET_PLAN",
        "Duplicate source request",
    )
    budget = {
        **LIMITS,
        "calendarDays": days,
        "declaredRequests": len(requests),
        "materializedRequests": len(requests),
        "rawResponseCeilingBytes": sum(r["responseBytes"] for r in requests),
    }
    require(
        p["budget"] == budget and budget["rawResponseCeilingBytes"] <= RAW_BYTES,
        "MARKET_PLAN",
        "Declared parent budget differs",
    )
    require(
        p["sourcePolicy"]
        == {
            "responseBytes": "exact_delivered_endpoint_bytes",
            "originalProviderWireAvailable": False,
            "adjustment": "adj_factor_divided_by_first_observed_factor_per_symbol",
            "volumeUnit": "hands",
            "amountUnit": "CNY_thousands",
        }
        and p["completeness"]
        == {
            "zeroRowsForAnySymbol": "reject_entire_scope",
            "missingSessions": "preserve_missing_mask",
            "crossExchangeCalendars": "require_exact_session_equality",
            "unknownRequest": "sticky_manual_review_no_retry",
        },
        "MARKET_PLAN",
        "Unrecognized normalization policy",
    )
    require(
        meta["publicationLimits"]
        == {
            "manifestBytes": MANIFEST_BYTES,
            "chunkBytes": CHUNK_BYTES,
            "chunks": MAX_CHUNKS,
            "totalBytes": DATA_BYTES,
            "rawChunkBytes": RAW_CHUNK_BYTES,
            "rawChunks": RAW_CHUNKS,
            "rawBytes": RAW_BYTES,
        },
        "MARKET_PLAN",
        "Publication limits differ",
    )
    return p


def output_collections(manifest):
    return {**manifest["collections"], "raw": manifest["rawArchive"]}
