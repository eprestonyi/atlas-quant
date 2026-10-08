"""Pure frozen-share two-leg valuations and entry/exit labels, without a model."""
from __future__ import annotations

import math

from .contract import (
    FORMAT, VERSION, _date, digest, require, validate_declaration, validate_price_input,
)


def _valuation(prices, date, field, legs, *, require_gross=False):
    if date is None:
        return {"date": None, "field": field, "state": None,
                "status": "outside_calendar", "missingLegs": []}, None
    missing = [leg["symbol"] for leg in legs if prices.get((date, leg["symbol"]), {}).get(field) is None]
    if missing:
        return {"date": date, "field": field, "state": None,
                "status": "missing_legs", "missingLegs": missing}, None
    terms = [leg["quantity"] * prices[(date, leg["symbol"])][field] for leg in legs]
    try:
        state = math.fsum(terms)
        gross = math.fsum(abs(term) for term in terms) if require_gross else None
    except (OverflowError, ValueError):
        state, gross = math.nan, math.nan
    valid = (all(math.isfinite(term) and term != 0 for term in terms) and math.isfinite(state)
             and (not require_gross or (math.isfinite(gross) and gross > 0)))
    return {"date": date, "field": field, "state": state if valid else None,
            "status": "complete" if valid else "arithmetic_unavailable", "missingLegs": []}, gross if valid else None


def _label(valuation, current, scale):
    if current["status"] != "complete":
        return {**valuation, "normalizedChange": None, "labelStatus": "current_unavailable"}
    if valuation["status"] != "complete":
        return {**valuation, "normalizedChange": None, "labelStatus": "valuation_unavailable"}
    difference = valuation["state"] - current["state"]
    # Preserve subtraction precision normally, but avoid a needless overflow
    # before division when the normalized difference is still representable.
    if math.isfinite(difference):
        change = difference / scale
    else:
        try:
            change = math.fsum((valuation["state"] / scale, -current["state"] / scale))
        except (ValueError, OverflowError):
            change = math.nan
    finite = math.isfinite(change)
    return {**valuation, "normalizedChange": change if finite else None,
            "labelStatus": "complete" if finite else "arithmetic_unavailable"}


def build_targets(declaration, price_input):
    """Return all T x origin rows, including missing-leg and out-of-calendar labels.

    A label is a normalized realized change, not F or a trading return. The same
    signed shares are used at origin close, next open, and next+h open.
    """
    spec, source = validate_declaration(declaration), validate_price_input(price_input)
    require(spec["sourceDomainRoot"] == source["sourceDomainRoot"] and
            spec["priceProjectionRoot"] == source["priceProjectionRoot"],
            "PAIR_SOURCE_BINDING", "Declaration and price input identity differ")
    prices = {(row["date"], row["symbol"]): row for row in source["rows"]}
    calendar = spec["sourceDomain"]["calendar"]
    indices = {date: i for i, date in enumerate(calendar)}
    definitions = []
    for pair in spec["targetScope"]["pairMap"]:
        content = {**pair, "sourceDomainRoot": spec["sourceDomainRoot"],
                   "quantityCutoff": spec["targetScope"]["quantityCutoff"],
                   "construction": "explicit_frozen_quantities",
                   "unit": "CNY_adjusted_research_basket", "quantityUnit": "adjusted_share"}
        definitions.append({**content, "targetVersionId": "pair_" + digest(content)})
    rows = []
    for date in spec["origins"]:
        t = indices[date]
        entry_i, exit_i = t + 1, t + 1 + spec["horizonSessions"]
        entry_date = calendar[entry_i] if entry_i < len(calendar) else None
        exit_date = calendar[exit_i] if exit_i < len(calendar) else None
        for target in definitions:
            current, scale = _valuation(prices, date, "close", target["legs"], require_gross=True)
            entry, _ = _valuation(prices, entry_date, "open", target["legs"])
            exit_, _ = _valuation(prices, exit_date, "open", target["legs"])
            rows.append({"pairId": target["pairId"], "targetVersionId": target["targetVersionId"],
                         "origin": date, "current": {**current, "grossScale": scale},
                         "entry": _label(entry, current, scale), "exit": _label(exit_, current, scale)})
    body = {"format": FORMAT + ".targets", "version": VERSION,
            "declarationRoot": spec["declarationRoot"], "sourceDomainRoot": spec["sourceDomainRoot"],
            "priceProjectionRoot": spec["priceProjectionRoot"], "targetScopeRoot": spec["targetScopeRoot"],
            "memberStates": spec["memberStates"], "targets": definitions, "rows": rows,
            "coverage": {"universeMembers": len(spec["sourceDomain"]["symbols"]),
                         "targetPairs": len(definitions), "origins": len(spec["origins"]),
                         "expectedTargetRows": len(definitions) * len(spec["origins"]),
                         "actualTargetRows": len(rows)},
            "status": "complete_target_grid" if definitions else "empty_target_scope"}
    return {**body, "resultRoot": digest(body)}


def label_is_mature(row, which, cutoff):
    """Strict date cutoff for this kernel's labels; no training is performed."""
    _date(cutoff)
    require(which in ("entry", "exit"), "PAIR_LABEL", "Choose entry or exit label")
    label = row[which]
    return (row["origin"] < cutoff and label["labelStatus"] == "complete" and
            label["date"] is not None and label["date"] < cutoff)
