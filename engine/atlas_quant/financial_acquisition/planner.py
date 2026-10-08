"""Python mirror of the shared fixed JS request planner; never fetches data."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re

from ..financial_statements.contracts import parse_date
from ..financial_statements.recipes import RECIPES
from .protocol import PROFILE, digest, encode, identifier, keys, require, sha

LIMITATIONS = [
    "GENERAL_COMPANY_CONSOLIDATED_ANNUAL_ONLY",
    "UNITS_UNVERIFIED",
    "ORIGINAL_AS_PUBLISHED_UNVERIFIED",
    "SINGLE_PERIOD_MAY_NOT_SUPPORT_TTM",
]


def create_plan(value, authorization_scope, *, today=None):
    keys(
        value,
        {
            "requestId",
            "profile",
            "name",
            "symbols",
            "period",
            "start",
            "end",
            "announcementStart",
            "selectedStateIds",
        },
    )
    identifier(value["requestId"])
    require(
        isinstance(authorization_scope, str)
        and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,79}", authorization_scope),
        "ACQUISITION_SCOPE",
        "A fixed nonsecret authorization scope is required",
    )
    require(
        value["profile"] == PROFILE["id"]
        and isinstance(value["name"], str)
        and 1 <= len(value["name"]) <= 80
        and value["name"].strip(),
        "ACQUISITION_PROFILE",
        "Unsupported profile or name",
    )
    symbols = value["symbols"]
    require(
        isinstance(symbols, list)
        and 1 <= len(symbols) <= PROFILE["maxSymbols"]
        and all(
            isinstance(s, str) and re.fullmatch(r"\d{6}\.(SH|SZ)", s) for s in symbols
        )
        and len(set(symbols)) == len(symbols),
        "ACQUISITION_SYMBOLS",
        "Select one or two exact unique securities",
    )
    venues = {s[-2:] for s in symbols}
    require(
        len(venues) == 1,
        "MULTI_EXCHANGE_PROFILE_UNAVAILABLE",
        "Mixed-exchange calendars are not inferred",
    )
    today = today or datetime.now(timezone.utc).astimezone(
        ZoneInfo("Asia/Hong_Kong")
    ).strftime("%Y%m%d")
    for key in ("period", "start", "end", "announcementStart"):
        parse_date(value[key])
    parse_date(today)
    require(
        value["period"].endswith("1231") and "20001231" <= value["period"] < today,
        "ACQUISITION_PERIOD",
        "An ended December annual period is required",
    )
    days = (parse_date(value["end"]) - parse_date(value["announcementStart"])).days + 1
    require(
        value["period"]
        <= value["announcementStart"]
        <= value["start"]
        <= value["end"]
        <= today
        and days <= PROFILE["maxCalendarDays"],
        "ACQUISITION_DATES",
        "Calendar/history range exceeds the fixed profile",
    )
    selected = value["selectedStateIds"]
    require(
        isinstance(selected, list)
        and 1 <= len(selected) <= 16
        and all(isinstance(s, str) and s in RECIPES for s in selected)
        and len(set(selected)) == len(selected),
        "ACQUISITION_STATES",
        "Select unique registered states",
    )
    selection = {
        "symbols": sorted(symbols),
        "period": value["period"],
        "start": value["start"],
        "end": value["end"],
        "announcementStart": value["announcementStart"],
        "selectedStateIds": sorted(selected),
        "scope": PROFILE["scope"],
        "flowBasis": PROFILE["flowBasis"],
        "exchange": PROFILE["exchanges"][next(iter(venues))],
    }
    requests = []

    def add(endpoint, params):
        definition = {
            "requestVersion": PROFILE["requestVersion"],
            "profileVersion": f"{PROFILE['id']}@{PROFILE['version']}",
            "provider": PROFILE["provider"],
            "authorizationScope": authorization_scope,
            "normalizerVersion": PROFILE["normalizerVersion"],
            "endpoint": endpoint,
            "params": params,
            "fields": list(PROFILE["fields"][endpoint]),
        }
        requests.append({"requestKey": sha(encode(definition)), **definition})

    add(
        "trade_cal",
        {
            "exchange": selection["exchange"],
            "start_date": selection["announcementStart"],
            "end_date": selection["end"],
        },
    )
    for symbol in selection["symbols"]:
        for endpoint in PROFILE["statementEndpoints"]:
            add(
                endpoint,
                {
                    "ts_code": symbol,
                    "period": selection["period"],
                    "report_type": PROFILE["reportType"],
                    "comp_type": PROFILE["companyType"],
                },
            )
    spec = {
        "version": 1,
        "profile": PROFILE["id"],
        "name": value["name"].strip(),
        "selection": selection,
        "requests": requests,
        "budget": {
            "maximumProviderCalls": len(requests),
            "maximumResponseBytes": PROFILE["maxResponseBytes"],
            "maximumTotalBytes": PROFILE["maxTotalBytes"],
        },
        "limitations": list(LIMITATIONS),
        "researchBinding": False,
    }
    return {**spec, "planRoot": sha(encode(spec))}


def validate_request(request, authorization_scope):
    keys(
        request,
        {
            "requestKey",
            "requestVersion",
            "profileVersion",
            "provider",
            "authorizationScope",
            "normalizerVersion",
            "endpoint",
            "params",
            "fields",
        },
    )
    digest(request["requestKey"])
    require(
        request["authorizationScope"] == authorization_scope
        and request["requestVersion"] == PROFILE["requestVersion"]
        and request["profileVersion"] == f"{PROFILE['id']}@{PROFILE['version']}"
        and request["provider"] == PROFILE["provider"]
        and request["normalizerVersion"] == PROFILE["normalizerVersion"]
        and isinstance(request["endpoint"], str)
        and request["endpoint"] in PROFILE["fields"]
        and request["fields"] == PROFILE["fields"][request["endpoint"]],
        "ACQUISITION_REQUEST",
        "Request differs from the authorization or fixed field contract",
    )
    params = request["params"]
    if request["endpoint"] == "trade_cal":
        keys(params, {"exchange", "start_date", "end_date"})
        require(
            params["exchange"] in {"SSE", "SZSE"},
            "ACQUISITION_REQUEST",
            "Unknown calendar exchange",
        )
        start, end = parse_date(params["start_date"]), parse_date(params["end_date"])
        require(
            1 <= (end - start).days + 1 <= PROFILE["maxCalendarDays"],
            "ACQUISITION_REQUEST",
            "Calendar request exceeds fixed range",
        )
    else:
        keys(params, {"ts_code", "period", "report_type", "comp_type"})
        parse_date(params["period"])
        require(
            isinstance(params["ts_code"], str)
            and re.fullmatch(r"\d{6}\.(SH|SZ)", params["ts_code"])
            and params["period"].endswith("1231")
            and params["period"] >= "20001231"
            and params["report_type"] == PROFILE["reportType"]
            and params["comp_type"] == PROFILE["companyType"],
            "ACQUISITION_REQUEST",
            "Only the fixed annual general-company consolidated request is allowed",
        )
    definition = {k: v for k, v in request.items() if k != "requestKey"}
    require(
        sha(encode(definition)) == request["requestKey"],
        "ACQUISITION_REQUEST",
        "Request identity differs",
    )
    return request


def validate_cache(cache, *, allow_fixtures=False):
    require(isinstance(cache, dict), "ACQUISITION_CACHE", "Missing pinned cache state")
    if cache.get("status") == "missing":
        keys(cache, {"status"})
        return
    keys(
        cache,
        {
            "status",
            "receiptId",
            "sha256",
            "byteLength",
            "retrievedAt",
            "httpStatus",
            "sourceKind",
        },
    )
    require(
        cache["status"] == "frozen"
        and type(cache["byteLength"]) is int
        and 0 <= cache["byteLength"] <= PROFILE["maxResponseBytes"]
        and type(cache["httpStatus"]) is int
        and 100 <= cache["httpStatus"] <= 599,
        "ACQUISITION_CACHE",
        "Unresolved or malformed cache cannot trigger another read",
    )
    identifier(cache["receiptId"])
    digest(cache["sha256"])
    from .protocol import timestamp

    timestamp(cache["retrievedAt"])
    require(
        cache["sourceKind"] == "provider"
        or allow_fixtures
        and cache["sourceKind"] == "fixture",
        "ACQUISITION_SOURCE_KIND",
        "Synthetic cache cannot be promoted into production evidence",
    )


def validate_execution(
    meta, job, authorization_scope, *, allow_fixtures=False, today=None
):
    keys(meta, {"job", "reviewedPlan", "executionPlan", "limits"})
    require(
        meta["job"] == {k: job[k] for k in ("id", "kind", "planId")},
        "ACQUISITION_IDENTITY",
        "Claim and immutable input identities disagree",
    )
    require(
        encode(meta["limits"])
        == encode(
            {
                "responseBytes": 4194304,
                "totalBytes": 16777216,
                "packageBytes": 25165824,
                "calendarBytes": 262144,
                "chunkBytes": 524288,
                "manifestBytes": 131072,
            }
        ),
        "ACQUISITION_LIMITS",
        "Server resource profile differs",
    )
    reviewed, execution = meta["reviewedPlan"], meta["executionPlan"]
    common = {
        "version",
        "profile",
        "name",
        "selection",
        "requests",
        "budget",
        "limitations",
        "researchBinding",
        "blockedReasons",
        "planRoot",
    }
    keys(reviewed, common)
    keys(execution, common | {"executionPlanRoot"})
    digest(reviewed["planRoot"])
    require(
        sha(encode({k: v for k, v in reviewed.items() if k != "planRoot"}))
        == reviewed["planRoot"]
        and reviewed["blockedReasons"] == [],
        "ACQUISITION_PLAN",
        "Reviewed plan identity or unresolved state differs",
    )
    selection = reviewed["selection"]
    keys(
        selection,
        {
            "symbols",
            "period",
            "start",
            "end",
            "announcementStart",
            "selectedStateIds",
            "scope",
            "flowBasis",
            "exchange",
        },
    )
    planned = create_plan(
        {
            "requestId": "00000000-0000-0000-0000-000000000001",
            "profile": reviewed["profile"],
            "name": reviewed["name"],
            **{
                k: selection[k]
                for k in (
                    "symbols",
                    "period",
                    "start",
                    "end",
                    "announcementStart",
                    "selectedStateIds",
                )
            },
        },
        authorization_scope,
        today=today,
    )
    require(
        type(reviewed["version"]) is int
        and reviewed["version"] == 1
        and reviewed["researchBinding"] is False,
        "ACQUISITION_PLAN",
        "Unknown plan version or research capability",
    )
    for key in ("profile", "name", "selection", "limitations", "researchBinding"):
        require(
            encode(reviewed[key]) == encode(planned[key]),
            "ACQUISITION_PLAN",
            "Plan differs from fixed profile",
        )
    keys(
        reviewed["budget"],
        {
            "maximumProviderCalls",
            "maximumResponseBytes",
            "maximumTotalBytes",
            "cachedRequests",
            "newRequests",
        },
    )
    require(
        isinstance(reviewed["requests"], list)
        and len(reviewed["requests"]) == len(planned["requests"])
        and isinstance(execution["requests"], list)
        and len(execution["requests"]) == len(planned["requests"]),
        "ACQUISITION_PLAN",
        "Request count differs from fixed plan",
    )
    for actual, fixed in zip(reviewed["requests"], planned["requests"]):
        require(
            isinstance(actual, dict)
            and set(actual) == set(fixed) | {"cache"}
            and encode({k: v for k, v in actual.items() if k != "cache"})
            == encode(fixed),
            "ACQUISITION_PLAN",
            "Request parameters or fields were changed",
        )
        validate_cache(actual["cache"], allow_fixtures=allow_fixtures)
    expected_budget = {
        **planned["budget"],
        "cachedRequests": sum(
            r["cache"]["status"] == "frozen" for r in reviewed["requests"]
        ),
        "newRequests": sum(
            r["cache"]["status"] == "missing" for r in reviewed["requests"]
        ),
    }
    require(
        encode(reviewed["budget"]) == encode(expected_budget),
        "ACQUISITION_PLAN",
        "Reviewed request budget differs",
    )
    for key in common - {"requests"}:
        require(
            encode(execution[key]) == encode(reviewed[key]),
            "ACQUISITION_PLAN",
            "Execution changed the reviewed selection",
        )
    for old, new in zip(reviewed["requests"], execution["requests"]):
        require(
            isinstance(new, dict)
            and set(new) == set(old)
            and encode({k: v for k, v in new.items() if k != "cache"})
            == encode({k: v for k, v in old.items() if k != "cache"}),
            "ACQUISITION_PLAN",
            "Execution expanded or replaced a provider request",
        )
        validate_cache(new["cache"], allow_fixtures=allow_fixtures)
        if old["cache"]["status"] == "frozen":
            require(
                encode(old["cache"]) == encode(new["cache"]),
                "ACQUISITION_CACHE",
                "A frozen hit cannot change at start",
            )
    root = sha(
        encode({"planRoot": reviewed["planRoot"], "requests": execution["requests"]})
    )
    require(
        execution["executionPlanRoot"] == root,
        "ACQUISITION_PLAN",
        "Execution plan root differs",
    )
    require(
        len(encode(meta)) <= 256 * 1024,
        "ACQUISITION_PLAN",
        "Input metadata is oversized",
    )
    return execution
