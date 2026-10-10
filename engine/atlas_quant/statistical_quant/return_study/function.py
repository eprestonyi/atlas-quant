"""Portable scalar return equations; economic inputs and response units are explicit."""
from __future__ import annotations
import copy
import math
import re
import numpy as np

from .contract import FUNCTION_SCHEMA, input_descriptors, outputs, target_contract
from ..model_function import _bad, _date, _export_components, _integer, _keys, _numeric, _seal, _vector, HASH_ALGORITHM

IDENTITY = {"response": "output[0]", "simpleReturn": "response * responseScale",
            "conditionalPrice": "originPrice * (1 + simpleReturn)",
            "responseScale": "one_or_origin_known_daily_volatility_times_sqrt_h"}


def export_function(model, training, strategy):
    import sklearn
    from ..model_function import validate_function
    descriptors = input_descriptors(strategy)
    if model.audit.get("returnInputDescriptors") != descriptors:
        _bad("fitted return input construction differs from the declared protocol")
    if not isinstance(model.predictor, np.ndarray) and strategy["preprocess"]["standardize"] and model.audit.get("scalerMethod") != "median_iqr":
        _bad("return function requires the fitted median/IQR scaler")
    symbols = list(strategy["universe"]["symbols"])
    symbol = training.get("targetSymbol")
    if symbol is not None:
        if symbol not in symbols:
            _bad("fitted asset is outside the research collection")
        symbols = [symbol]
    if len(symbols) != 1:
        _bad("a return equation must identify its single fitted asset")
    estimator, transforms = _export_components(model)
    value = {"schema": FUNCTION_SCHEMA, "hashAlgorithm": HASH_ALGORITHM,
        "inputSchema": [{"name": c, "type": "finite_number_or_null"} for c in model.columns],
        "transforms": transforms, "estimator": estimator,
        "training": {k: training.get(k) for k in ("trainStart", "trainEnd", "informationCutoff", "labelEndMax", "trainRows", "trainDates")},
        "scope": {"family": strategy["model"]["family"], "targetKind": "asset_return",
            "horizonSessions": strategy["target"]["horizonSessions"], "symbols": symbols,
            "observationDays": strategy["research"]["observationDays"],
            "researchStart": strategy["universe"]["start"], "researchEnd": strategy["universe"]["end"],
            "generalizationOutsideScopeValidated": False, "studyMode": strategy["research"]["returnStudy"]["mode"]},
        "outputs": outputs(strategy), "identity": copy.deepcopy(IDENTITY),
        "featureConstruction": {"schema": "asset-return-features/1", "factors": copy.deepcopy(strategy["factors"]),
            "preprocess": copy.deepcopy(strategy["preprocess"]), "targetSpecification": copy.deepcopy(strategy["target"]),
            "inputs": copy.deepcopy(descriptors)},
        "provenance": {"estimator": model.spec["estimator"], "parameters": copy.deepcopy(model.spec["params"]), "sklearnVersion": sklearn.__version__},
        "editPolicy": {"allowed": ["estimator_numeric_parameters"], "arbitraryCode": False, "editedEvidenceStatus": "UNVALIDATED_USER_EDIT"},
        "lineage": {"parentArtifactId": None, "status": "fitted"}}
    return validate_function(_seal(value))


def validate_metadata(a):
    """Reconstruct effective semantics rather than trusting a self-hashed claim."""
    training, scope, construction = a["training"], a["scope"], a["featureConstruction"]
    _keys(training, ("trainStart", "trainEnd", "informationCutoff", "labelEndMax", "trainRows", "trainDates"))
    for key in ("trainStart", "trainEnd", "informationCutoff", "labelEndMax"):
        _date(training[key])
    if not training["trainStart"] <= training["trainEnd"] <= training["labelEndMax"] < training["informationCutoff"] or not _integer(training["trainRows"], 1, 10_000_000) or not _integer(training["trainDates"], 1, training["trainRows"]):
        _bad("invalid training chronology or counts")
    _keys(scope, ("family", "targetKind", "horizonSessions", "symbols", "observationDays", "researchStart", "researchEnd", "generalizationOutsideScopeValidated", "studyMode"))
    if scope["family"] not in ("mean_reversion", "trend", "fundamental", "event") or scope["targetKind"] != "asset_return" or scope["studyMode"] not in ("forecast", "association") or not _integer(scope["horizonSessions"], 1, 252) or not _integer(scope["observationDays"], 1, 60) or scope["generalizationOutsideScopeValidated"] is not False:
        _bad("invalid return model scope")
    symbols = scope["symbols"]
    if not isinstance(symbols, list) or len(symbols) != 1 or not isinstance(symbols[0], str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbols[0]):
        _bad("return equation requires one declared security")
    _date(scope["researchStart"]); _date(scope["researchEnd"])
    if scope["researchStart"] >= scope["researchEnd"]:
        _bad("invalid research interval")
    _keys(construction, ("schema", "factors", "preprocess", "targetSpecification", "inputs"))
    if construction["schema"] != "asset-return-features/1":
        _bad("invalid return feature construction")
    factors = construction["factors"]
    if not isinstance(factors, list) or not 1 <= len(factors) <= 32:
        _bad("invalid factor definitions")
    ids = set()
    for factor in factors:
        _keys(factor, ("id", "expression", "direction", "role"), ("version",))
        if not isinstance(factor["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", factor["id"]) or factor["id"] in ids or not isinstance(factor["expression"], str) or not 1 <= len(factor["expression"]) <= 500 or factor["role"] not in ("predictor", "event") or not _numeric(factor["direction"]) or factor["direction"] not in (-1, 1) or ("version" in factor and not _integer(factor["version"], 1, 1_000_000)):
            _bad("invalid return factor metadata")
        ids.add(factor["id"])
    pre = construction["preprocess"]
    _keys(pre, ("winsorize", "standardize", "decorrelation", "correlationThreshold", "automatic"))
    if type(pre["winsorize"]) is not bool or type(pre["standardize"]) is not bool or pre["decorrelation"] not in ("none", "drop_correlated") or not _numeric(pre["correlationThreshold"]) or not .5 <= pre["correlationThreshold"] <= 1:
        _bad("invalid preprocessing metadata")
    from ..preprocessing import validate_automatic
    try:
        validate_automatic(pre["automatic"])
        if pre["automatic"]["schema"] != "auto-factor-preprocess/2" or set(pre["automatic"].get("overrides", {})) - ids:
            _bad("return study requires selected typed factors")
        target = target_contract(construction["targetSpecification"])
        if target["horizonSessions"] != scope["horizonSessions"]:
            _bad("target horizon differs from scope")
        strategy = {"research": {"returnStudy": {"mode": scope["studyMode"]}}, "target": target,
                    "universe": {"symbols": symbols}, "preprocess": pre, "factors": factors}
        expected = input_descriptors(strategy)
        # bool must not compare equal to an integer in source descriptors.
        from ..model_function import function_digest
        if function_digest(construction["inputs"]) != function_digest(expected):
            _bad("effective return factor construction differs from its declaration")
        if a["outputs"] != outputs(strategy):
            _bad("return response unit mismatch")
    except (ValueError, TypeError, KeyError) as exc:
        _bad(str(exc))
    if a["identity"] != IDENTITY:
        _bad("invalid return conversion identity")


def response_document(a, values, mode, origin_price, origin_volatility):
    if (a["scope"]["studyMode"] == "forecast" and mode != "forecast") or (a["scope"]["studyMode"] == "association" and mode not in ("association", "future_scenario")):
        _bad("evaluation mode differs from fitted research mode")
    norm = a["featureConstruction"]["targetSpecification"]["normalization"]
    normalized = norm["kind"] == "trailing_volatility"
    if not normalized and origin_volatility is not None:
        _bad("simple returns do not accept volatility conversion")
    if mode == "future_scenario" and origin_price is None:
        _bad("future scenario requires an origin price")
    if normalized and origin_price is not None and origin_volatility is None:
        _bad("restoring a conditional price requires origin-known volatility")
    if origin_price is not None:
        _vector(origin_price, len(values))
        if any(x <= 0 for x in origin_price):
            _bad("origin prices must be positive")
    if origin_volatility is not None:
        _vector(origin_volatility, len(values))
        if any(x <= norm["minimum"] for x in origin_volatility):
            _bad("origin volatility is insufficient")
    result = {"artifactId": a["artifactId"], "predictedResponse": values, "outputUnit": a["outputs"][0],
              "mode": mode, "evidenceStatus": a["lineage"]["status"], "scenarioOnly": mode == "future_scenario"}
    if not normalized or origin_volatility is not None:
        result["simpleReturns"] = [value*(origin_volatility[i]*math.sqrt(a["scope"]["horizonSessions"]) if normalized else 1) for i, value in enumerate(values)]
        _vector(result["simpleReturns"], len(values))
        if origin_price is not None:
            result["conditionalPrices"] = [p*(1+r) for p, r in zip(origin_price, result["simpleReturns"])]
            _vector(result["conditionalPrices"], len(values))
    return result
