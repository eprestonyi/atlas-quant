"""Closed Stage 2A sampling contract; no model controls or research admission."""
from __future__ import annotations

from copy import deepcopy
import re

from ..factors import DSL_CONTRACT, FIELDS, FactorError, validate_expression
from .contract import PairContractError, _date, digest, keys, require, validate_declaration
from .source import MarketPairSource, MAX_SYMBOLS, validate_source_binding

FORMAT = "atlas.quant.pair_samples"
VERSION = 1
FEATURE_VERSION = "explicit_pair_causal_features/1"
MAX_FACTORS = 16
MAX_SAMPLES = 110000
STATE_COLUMNS = ("volatility20", "state_deviation20", "state_deviation60", "change1")
DSL_ROOT = digest(DSL_CONTRACT)


def _factors(value, available_fields):
    require(type(value) is list and len(value) <= MAX_FACTORS,
            "PAIR_RESEARCH_BUDGET", "At most 16 explicit predictors in this local envelope")
    seen, factors = set(), []
    for factor in value:
        keys(factor, {"id", "expression", "direction", "role"}, "predictor")
        require(type(factor["id"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", factor["id"])
                and factor["id"] not in seen, "PAIR_FACTOR", "Unique bounded predictor id required")
        require(type(factor["direction"]) is int and factor["direction"] in (-1, 1)
                and factor["role"] == "predictor", "PAIR_FACTOR", "Only explicit predictor +/-1 directions are supported")
        try:
            parsed = validate_expression(factor["expression"])
        except FactorError as exc:
            raise PairContractError("PAIR_FACTOR", str(exc)) from exc
        require(set(parsed["fields"]) <= set(FIELDS) & set(available_fields),
                "PAIR_FACTOR_SOURCE", "Predictor fields must be native fields present in this exact source")
        seen.add(factor["id"])
        factors.append(deepcopy(factor))
    return factors


def research_origins(source, *, quantity_cutoff, factors, observation_days):
    """Plan before declaration; no target, availability or future-label filtering."""
    validate_source_binding(source)
    domain = source.domain
    require(len(domain["symbols"]) <= MAX_SYMBOLS,
            "PAIR_RESEARCH_BUDGET", "Complete U exceeds the local envelope")
    _date(quantity_cutoff)
    dates = domain["calendar"]
    require(quantity_cutoff in dates, "PAIR_CUTOFF", "Quantity cutoff must be a source session")
    require(type(observation_days) is int and 1 <= observation_days <= 60,
            "PAIR_OBSERVATIONS", "Explicit observationDays integer in 1..60 required")
    normalized = _factors(factors, source.fields)
    lookback = max((validate_expression(f["expression"])["lookback"] for f in normalized), default=0)
    start = max(61, lookback + 1, dates.index(quantity_cutoff) + 1)
    origins = dates[start::observation_days]
    require(bool(origins), "PAIR_ORIGINS", "No origins after required warmup and quantity cutoff")
    return origins


def declare_research(source, declaration, *, factors, observation_days):
    """Bind complete target domain and market feature values to a no-fit plan."""
    spec = validate_declaration(declaration)
    require(isinstance(source, MarketPairSource), "PAIR_SOURCE_READER", "Prepared local source required")
    price = source.price_input
    require(spec["sourceDomainRoot"] == price["sourceDomainRoot"]
            and spec["priceProjectionRoot"] == price["priceProjectionRoot"],
            "PAIR_SOURCE_BINDING", "Target declaration and feature source differ")
    normalized = _factors(factors, source.fields)
    origins = research_origins(source, quantity_cutoff=spec["targetScope"]["quantityCutoff"],
                               factors=normalized, observation_days=observation_days)
    require(spec["origins"] == origins, "PAIR_ORIGIN_GRID",
            "Declaration must exactly equal the planned full origin grid; never trim or compress")
    require(len(spec["targetScope"]["pairMap"]) * len(origins) <= MAX_SAMPLES,
            "PAIR_RESEARCH_BUDGET", "Complete T x origins exceeds local sample envelope; never truncate")
    body = {"format": FORMAT, "version": VERSION, "stage": "2A_no_fit",
            "family": "pair_reversion", "targetDeclarationRoot": spec["declarationRoot"],
            "sourceDomainRoot": spec["sourceDomainRoot"], "priceProjectionRoot": spec["priceProjectionRoot"],
            "featureSourceRoot": source.feature_source_root,
            "featureConstructionVersion": FEATURE_VERSION, "dslContractRoot": DSL_ROOT,
            "factorUnitPolicy": "expression_native_units_signed_dollar_weighted",
            "factors": normalized, "observationDays": observation_days,
            "targetTiming": spec["targetTiming"], "execution": {"enabled": False},
            "quantityProvenanceVerified": False, "pairSelectionLeakageVerified": False,
            "evaluationScope": "conditional_on_declared_pair_map_and_quantities"}
    return {**body, "researchRoot": digest(body)}


def validate_research_contract(value, source, declaration):
    keys(value, {"format", "version", "stage", "family", "targetDeclarationRoot", "sourceDomainRoot",
                 "priceProjectionRoot", "featureSourceRoot", "featureConstructionVersion", "dslContractRoot",
                 "factorUnitPolicy", "factors", "observationDays", "targetTiming", "execution",
                 "quantityProvenanceVerified", "pairSelectionLeakageVerified", "evaluationScope", "researchRoot"},
         "pair sampling contract")
    keys(value["execution"], {"enabled"}, "execution")
    require(type(value["version"]) is int and value["version"] == VERSION
            and value["execution"]["enabled"] is False
            and value["quantityProvenanceVerified"] is False
            and value["pairSelectionLeakageVerified"] is False,
            "PAIR_RESEARCH_CONTRACT", "Only explicit Stage 2A/no-execution assertions are supported")
    expected = declare_research(source, declaration, factors=value["factors"],
                                observation_days=value["observationDays"])
    require(value == expected, "PAIR_RESEARCH_BINDING", "Sampling identity or source/target/DSL binding differs")
    return expected
