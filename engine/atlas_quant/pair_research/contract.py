"""Closed local contract for U and explicit frozen-quantity targets T.

These shape limits grant no product/profile admission. Source authenticity and
the historical provenance of supplied quantities must be established upstream.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from uuid import UUID

FORMAT = "atlas.quant.pair_research"
INPUT_FORMAT = "atlas.quant.pair_price_input"
VERSION = 1
TIMING = "legacy_next_open_plus_h"
ADJUSTMENT = "adj_factor_divided_by_first_observed_factor_per_symbol"
LIMITS = {
    "maxSymbols": 1000,
    "maxPairs": 1000,
    "maxCalendarDaysInclusive": 366,
    "maxPriceRows": 300000,
    "maxTargetRows": 300000,
    "maxHorizonSessions": 60,
    "maxAbsoluteQuantity": 1000000,
}


class PairContractError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise PairContractError(code, message)


def keys(value, expected, where):
    require(type(value) is dict and set(value) == set(expected),
            "PAIR_KEYS", f"{where}: exact fields required")


def digest(value):
    # Object key order is immaterial. Integral floats have the JSON integer
    # spelling, including -0. Lists retain their explicitly declared order.
    def canonical(item):
        if type(item) is dict:
            return {k: canonical(v) for k, v in item.items()}
        if type(item) is list:
            return [canonical(v) for v in item]
        if type(item) is float and math.isfinite(item) and item.is_integer():
            return int(item)
        return item
    raw = json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hash(value, where):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value),
            "PAIR_IDENTITY", f"{where}: lowercase SHA256 required")
    return value


def _date(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{8}", value),
            "PAIR_DATE", "Explicit YYYYMMDD date required")
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise PairContractError("PAIR_DATE", "Invalid calendar date") from exc


def _ref(value, kind, fmt):
    keys(value, {kind + "Id", kind + "Root", "format", "version"}, kind + "Ref")
    identity = value[kind + "Id"]
    try:
        valid_id = type(identity) is str and str(UUID(identity)) == identity
    except (ValueError, AttributeError):
        valid_id = False
    require(valid_id and value["format"] == fmt and type(value["version"]) is int
            and value["version"] == VERSION, "PAIR_IDENTITY", "Unsupported source reference")
    _hash(value[kind + "Root"], kind + "Root")
    return dict(value)


def _domain(value):
    keys(value, {"ownerKey", "marketDatasetRef", "universeScopeRef", "symbols", "start", "end",
                 "calendar", "membershipPolicy", "currency", "priceUnit", "quantityUnit", "adjustment"},
         "sourceDomain")
    require(type(value["ownerKey"]) is str and
            re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value["ownerKey"]),
            "PAIR_IDENTITY", "Explicit opaque owner key required")
    dataset = _ref(value["marketDatasetRef"], "dataset", "atlas.quant.market_dataset")
    scope = _ref(value["universeScopeRef"], "scope", "atlas.quant.universe_scope")
    symbols = value["symbols"]
    require(type(symbols) is list and 1 <= len(symbols) <= LIMITS["maxSymbols"] and
            all(type(s) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", s) for s in symbols),
            "PAIR_UNIVERSE", "U must contain bounded explicit security identifiers")
    require(len(set(symbols)) == len(symbols), "PAIR_UNIVERSE", "Duplicate U member")
    start, end = _date(value["start"]), _date(value["end"])
    require(1 <= (end - start).days + 1 <= LIMITS["maxCalendarDaysInclusive"],
            "PAIR_BUDGET", "Source date range exceeds local shape bound")
    calendar = value["calendar"]
    require(type(calendar) is list and 1 <= len(calendar) <= LIMITS["maxCalendarDaysInclusive"],
            "PAIR_CALENDAR", "Explicit ordered source calendar required")
    for date in calendar:
        _date(date)
    require(calendar == sorted(set(calendar)) and
            all(value["start"] <= d <= value["end"] for d in calendar),
            "PAIR_CALENDAR", "Calendar must be unique, increasing and within source dates")
    require(value["membershipPolicy"] == "complete_filtered_set" and
            value["currency"] == "CNY" and value["priceUnit"] == "CNY_per_adjusted_share" and
            value["quantityUnit"] == "adjusted_share" and value["adjustment"] == ADJUSTMENT,
            "PAIR_UNITS", "Only the declared adjusted-share source basis is supported")
    return {**value, "marketDatasetRef": dataset, "universeScopeRef": scope,
            "symbols": list(symbols), "calendar": list(calendar)}


def _finite(value, code):
    require(type(value) in (int, float), code, "Finite JSON number required; booleans are invalid")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise PairContractError(code, "Number outside finite float range") from exc
    require(math.isfinite(result), code, "Finite JSON number required")
    return result


def _rows(value, domain):
    require(type(value) is list and len(value) <= LIMITS["maxPriceRows"],
            "PAIR_BUDGET", "Price projection must be a bounded list")
    symbols, dates, seen, rows = set(domain["symbols"]), set(domain["calendar"]), set(), []
    for raw in value:
        keys(raw, {"symbol", "date", "open", "close"}, "price row")
        require(type(raw["symbol"]) is str and raw["symbol"] in symbols and
                type(raw["date"]) is str and raw["date"] in dates,
                "PAIR_SOURCE_DOMAIN", "Price row is outside the declared U/calendar")
        key = (raw["date"], raw["symbol"])
        require(key not in seen, "PAIR_DUPLICATE_ROW", "Duplicate price row")
        seen.add(key)
        row = {"symbol": raw["symbol"], "date": raw["date"]}
        for field in ("open", "close"):
            val = raw[field]
            if val is not None:
                val = _finite(val, "PAIR_PRICE")
                require(val > 0, "PAIR_PRICE", "Prices must be positive or explicit null")
            row[field] = val
        rows.append(row)
    # Transport row order is not part of the projection identity.
    return sorted(rows, key=lambda r: (r["date"], r["symbol"]))


def prepare_price_input(source_domain, rows):
    """Bind a local open/close projection; performs no fetch or archive verification."""
    domain = _domain(source_domain)
    normalized = _rows(rows, domain)
    domain_root = digest(domain)
    root = digest({"sourceDomainRoot": domain_root, "rows": normalized})
    return {"format": INPUT_FORMAT, "version": VERSION, "sourceDomain": domain,
            "sourceDomainRoot": domain_root, "priceProjectionRoot": root, "rows": normalized}


def validate_price_input(value):
    keys(value, {"format", "version", "sourceDomain", "sourceDomainRoot", "priceProjectionRoot", "rows"},
         "price input")
    require(value["format"] == INPUT_FORMAT and type(value["version"]) is int and
            value["version"] == VERSION, "PAIR_VERSION", "Unsupported price input version")
    expected = prepare_price_input(value["sourceDomain"], value["rows"])
    require(value["sourceDomainRoot"] == expected["sourceDomainRoot"] and
            value["priceProjectionRoot"] == expected["priceProjectionRoot"],
            "PAIR_IDENTITY", "Price projection/domain root mismatch")
    return expected


def _pairs(value, symbols):
    require(type(value) is list and len(value) <= LIMITS["maxPairs"],
            "PAIR_BUDGET", "Explicit pairMap exceeds local shape bound")
    seen_ids, seen_pairs, pairs = set(), set(), []
    for raw in value:
        keys(raw, {"pairId", "legs"}, "pairMap entry")
        pair_id, legs = raw["pairId"], raw["legs"]
        require(type(pair_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pair_id) and
                pair_id not in seen_ids, "PAIR_DUPLICATE", "Unique explicit pairId required")
        seen_ids.add(pair_id)
        require(type(legs) is list and len(legs) == 2, "PAIR_LEGS", "Exactly two explicit legs required")
        normalized = []
        for leg in legs:
            keys(leg, {"symbol", "quantity"}, "pair leg")
            require(type(leg["symbol"]) is str and leg["symbol"] in symbols,
                    "PAIR_LEGS", "Pair leg is outside U")
            quantity = _finite(leg["quantity"], "PAIR_QUANTITY")
            require(0 < abs(quantity) <= LIMITS["maxAbsoluteQuantity"],
                    "PAIR_QUANTITY", "Frozen quantity must be nonzero and bounded")
            normalized.append({"symbol": leg["symbol"], "quantity": quantity})
        members = frozenset(leg["symbol"] for leg in normalized)
        require(len(members) == 2, "PAIR_LEGS", "Self pair is invalid")
        require(members not in seen_pairs, "PAIR_DUPLICATE", "Duplicate unordered pair, including reversal")
        require((normalized[0]["quantity"] > 0) != (normalized[1]["quantity"] > 0),
                "PAIR_QUANTITY", "Relative-value targets require opposite quantity signs")
        seen_pairs.add(members)
        pairs.append({"pairId": pair_id, "legs": normalized})
    return pairs


def _assemble(domain, projection_root, pair_map, quantity_cutoff, origins, horizon):
    domain = _domain(domain)
    _hash(projection_root, "priceProjectionRoot")
    pairs = _pairs(pair_map, set(domain["symbols"]))
    _date(quantity_cutoff)
    require(quantity_cutoff in domain["calendar"], "PAIR_CUTOFF", "Quantity cutoff must be in the source calendar")
    require(type(origins) is list and 1 <= len(origins) <= len(domain["calendar"]),
            "PAIR_ORIGINS", "Explicit nonempty origin list required")
    for origin in origins:
        _date(origin)
    require(origins == sorted(set(origins)) and all(d in domain["calendar"] and d > quantity_cutoff for d in origins),
            "PAIR_CUTOFF", "Origins must be unique increasing source sessions strictly after quantity cutoff")
    require(type(horizon) is int and 1 <= horizon <= LIMITS["maxHorizonSessions"],
            "PAIR_HORIZON", "Explicit bounded integer horizon required")
    require(len(pairs) * len(origins) <= LIMITS["maxTargetRows"],
            "PAIR_BUDGET", "Complete T x origins exceeds local shape bound; never truncate")
    incidence = {symbol: [] for symbol in domain["symbols"]}
    for pair in pairs:
        for leg in pair["legs"]:
            incidence[leg["symbol"]].append(pair["pairId"])
    members = [{"symbol": symbol, "status": "targeted" if ids else "unmatched",
                "pairIds": ids, "reason": None if ids else "not_in_explicit_map"}
               for symbol, ids in incidence.items()]
    source_root = digest(domain)
    target_scope = {"mode": "explicit_frozen_quantities", "sourceDomainRoot": source_root,
                    "quantityCutoff": quantity_cutoff, "pairMap": pairs}
    body = {"format": FORMAT, "version": VERSION, "sourceDomain": domain,
            "sourceDomainRoot": source_root, "priceProjectionRoot": projection_root,
            "targetScope": target_scope, "targetScopeRoot": digest(target_scope),
            "memberStates": members, "origins": list(origins),
            "horizonSessions": horizon, "targetTiming": TIMING}
    return {**body, "declarationRoot": digest(body)}


def declare_targets(price_input, pair_map, *, quantity_cutoff, origins, horizon_sessions):
    """Freeze explicit T and enumerate every U member; never estimate q or select pairs."""
    source = validate_price_input(price_input)
    return _assemble(source["sourceDomain"], source["priceProjectionRoot"], pair_map,
                     quantity_cutoff, origins, horizon_sessions)


def validate_declaration(value):
    keys(value, {"format", "version", "sourceDomain", "sourceDomainRoot", "priceProjectionRoot",
                 "targetScope", "targetScopeRoot", "memberStates", "origins", "horizonSessions",
                 "targetTiming", "declarationRoot"}, "pair declaration")
    scope = value["targetScope"]
    keys(scope, {"mode", "sourceDomainRoot", "quantityCutoff", "pairMap"}, "targetScope")
    require(value["format"] == FORMAT and type(value["version"]) is int and value["version"] == VERSION
            and scope["mode"] == "explicit_frozen_quantities" and value["targetTiming"] == TIMING,
            "PAIR_VERSION", "Unsupported pair contract or timing")
    expected = _assemble(value["sourceDomain"], value["priceProjectionRoot"], scope["pairMap"],
                         scope["quantityCutoff"], value["origins"], value["horizonSessions"])
    # Verify the ledger even if a caller recomputed the declaration hash. It must
    # account for every member of U and every incident target of shared legs.
    require(type(value["memberStates"]) is list, "PAIR_MEMBER_STATES", "Complete U ledger required")
    for row in value["memberStates"]:
        keys(row, {"symbol", "status", "pairIds", "reason"}, "member state")
    require(value["memberStates"] == expected["memberStates"],
            "PAIR_MEMBER_STATES", "Member states must exactly cover U and all incidences in T")
    for key in ("sourceDomainRoot", "priceProjectionRoot", "targetScopeRoot", "declarationRoot"):
        _hash(value[key], key)
        require(value[key] == expected[key], "PAIR_IDENTITY", f"{key} mismatch")
    require(scope["sourceDomainRoot"] == expected["sourceDomainRoot"],
            "PAIR_IDENTITY", "T must bind the complete source domain U")
    return expected
