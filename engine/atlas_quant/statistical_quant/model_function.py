"""Portable, bounded JSON F(X), with no pickle, imports or executable expressions.

F emits two normalized level changes. Restoring V requires the observed origin
state and gross scale, never a predicted or future price. Editing creates a new,
explicitly unvalidated artifact; it does not inherit the parent forecast evidence.
"""
from __future__ import annotations
import copy
import json
import math
import hashlib
import struct
import re
from datetime import datetime
from pathlib import Path
import numpy as np

SCHEMA = "atlas-model-function/1"
HASH_ALGORITHM = "sha256-canonical-f64-json/1"
MAX_BYTES = 2 * 1024 * 1024
MAX_FEATURES = 128
MAX_TREES = 256
MAX_NODES = 255
OUTPUTS = ["entry_level_change_over_known_gross", "exit_level_change_over_known_gross"]


def _bad(message):
    raise ValueError("INVALID_MODEL_FUNCTION: " + message)


def _numeric(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _vector(value, length):
    if not isinstance(value, list) or len(value) != length or not all(_numeric(v) for v in value):
        _bad("invalid numeric vector")


def function_digest(value):
    """Cross-runtime identity: all finite numbers become canonical binary64 hex."""
    def canonical(item, depth=0):
        if depth > 32:
            _bad("JSON nesting budget exceeded")
        if isinstance(item, bool) or item is None or isinstance(item, str):
            return item
        if isinstance(item, (float, int)):
            if not _numeric(item):
                _bad("unsupported hash number")
            number = float(item)
            # JS may serialize an integral binary64 such as 1e20 without an
            # exponent. Accept its exact Python-int parse, never silently round.
            if isinstance(item, int) and int(number) != item:
                _bad("unsupported hash number")
            return {"$f64": struct.pack("!d", 0.0 if number == 0 else number).hex()}
        if isinstance(item, list):
            return [canonical(x, depth+1) for x in item]
        if isinstance(item, dict):
            if any(not isinstance(k, str) or not k.isascii() or k == "$f64" for k in item):
                _bad("canonical object keys must be ASCII")
            return {key: canonical(v, depth+1) for key, v in item.items()}
        _bad("non-JSON identity value")
    raw = json.dumps(canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _seal(value):
    value.pop("artifactId", None)
    value["artifactId"] = function_digest(value)
    return value


def export_function(model, training, strategy):
    """Export learned numerical parameters only; unsupported tree shapes fail closed."""
    import sklearn
    if isinstance(model.predictor, np.ndarray):
        estimator = {"kind": "constant", "value": model.predictor.tolist()}
        transforms = {"imputeMedian": None, "winsorLower": None, "winsorUpper": None,
                      "scaleMean": None, "scaleScale": None}
    else:
        pipe = model.predictor
        fitted = pipe.named_steps["model"]
        transforms = {"imputeMedian": pipe.named_steps["impute"].statistics_.tolist(),
                      "winsorLower": model.audit.get("winsorLower"), "winsorUpper": model.audit.get("winsorUpper"),
                      "scaleMean": model.audit.get("scalerMean"), "scaleScale": model.audit.get("scalerScale")}
        if hasattr(fitted, "coef_"):
            estimator = {"kind": "linear", "coefficients": np.asarray(fitted.coef_).tolist(),
                         "intercepts": np.asarray(fitted.intercept_).tolist()}
        else:
            outputs = []
            for output in fitted.estimators_:
                if output.is_categorical_ is not None and np.any(output.is_categorical_):
                    _bad("categorical histogram trees are unsupported")
                trees = []
                for iteration in output._predictors:
                    if len(iteration) != 1:
                        _bad("unexpected histogram tree output shape")
                    nodes = iteration[0].nodes
                    if np.any(nodes["is_categorical"]):
                        _bad("categorical tree nodes are unsupported")
                    # Leaf values already include learning_rate in sklearn.
                    trees.append([[float(n["value"]), int(n["feature_idx"]), float(n["num_threshold"]),
                                   int(n["left"]), int(n["right"]), int(n["is_leaf"]), int(n["missing_go_to_left"])] for n in nodes])
                outputs.append({"baseline": float(output._baseline_prediction[0, 0]), "trees": trees})
            estimator = {"kind": "histogram_trees", "nodeFields": ["value", "feature", "threshold", "left", "right", "leaf", "missingLeft"],
                         "thresholdRule": "left_if_less_equal", "leafValuesIncludeLearningRate": True, "outputs": outputs}
    value = {"schema": SCHEMA, "hashAlgorithm": HASH_ALGORITHM, "inputSchema": [{"name": c, "type": "finite_number_or_null"} for c in model.columns],
             "transforms": transforms, "estimator": estimator,
             "featureConstruction": {"schema": "origin-state-features/1", "family": strategy["model"]["family"],
                 "factors": copy.deepcopy(strategy["factors"]), "preprocess": copy.deepcopy(strategy["preprocess"]),
                 "targetSpecification": copy.deepcopy(strategy["target"]), "quantityPolicy": "origin_specific_frozen_quantities"},
             "training": {k: training.get(k) for k in ("trainStart", "trainEnd", "informationCutoff", "labelEndMax", "trainRows", "trainDates")},
             "scope": {"family": strategy["model"]["family"], "targetKind": strategy["target"]["kind"],
                       "horizonSessions": strategy["target"]["horizonSessions"], "symbols": list(strategy["universe"]["symbols"]),
                       "observationDays": strategy["research"]["observationDays"],
                       "researchStart": strategy["universe"]["start"], "researchEnd": strategy["universe"]["end"],
                       "generalizationOutsideScopeValidated": False},
             "outputs": OUTPUTS, "identity": {"entry": "currentState + scale * output[0]", "future": "currentState + scale * output[1]",
                 "e": "currentState - expectedFuture", "expectedChange": "-e", "scale": "origin_known_gross_absolute_leg_value"},
             "provenance": {"estimator": model.spec["estimator"], "parameters": model.spec["params"], "sklearnVersion": sklearn.__version__},
             "editPolicy": {"allowed": ["estimator_numeric_parameters"], "arbitraryCode": False, "editedEvidenceStatus": "UNVALIDATED_USER_EDIT"},
             "lineage": {"parentArtifactId": None, "status": "fitted"}}
    result = _seal(value)
    validate_function(result)
    return result


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value)-set(required)-set(optional) or set(required)-set(value):
        _bad("invalid metadata fields")


def _integer(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        _bad("invalid metadata date")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        _bad("invalid calendar date")


def _metadata(a):
    training, scope, construction = a["training"], a["scope"], a["featureConstruction"]
    _keys(training, ("trainStart", "trainEnd", "informationCutoff", "labelEndMax", "trainRows", "trainDates"))
    for key in ("trainStart", "trainEnd", "informationCutoff", "labelEndMax"):
        _date(training[key])
    if not training["trainStart"] <= training["trainEnd"] <= training["labelEndMax"] < training["informationCutoff"] or not _integer(training["trainRows"], 1, 10_000_000) or not _integer(training["trainDates"], 1, training["trainRows"]):
        _bad("invalid training chronology or counts")
    _keys(scope, ("family", "targetKind", "horizonSessions", "symbols", "observationDays", "researchStart", "researchEnd", "generalizationOutsideScopeValidated"))
    families = ("mean_reversion", "pair_reversion", "trend", "fundamental", "event")
    symbols = scope["symbols"]
    if scope["family"] not in families or scope["targetKind"] not in ("asset_price", "frozen_basket") or not _integer(scope["horizonSessions"], 1, 60) or not _integer(scope["observationDays"], 1, 60) or scope["generalizationOutsideScopeValidated"] is not False:
        _bad("invalid model scope")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 10000 or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", v) for v in symbols) or len(set(symbols)) != len(symbols):
        _bad("invalid symbol scope")
    _date(scope["researchStart"]); _date(scope["researchEnd"])
    if scope["researchStart"] >= scope["researchEnd"]:
        _bad("invalid research interval")
    _keys(construction, ("schema", "family", "factors", "preprocess", "targetSpecification", "quantityPolicy"))
    if construction["schema"] != "origin-state-features/1" or construction["family"] != scope["family"] or construction["quantityPolicy"] != "origin_specific_frozen_quantities":
        _bad("invalid feature construction")
    factors = construction["factors"]
    if not isinstance(factors, list) or len(factors) > 32:
        _bad("invalid factor definitions")
    factor_ids = set()
    for factor in factors:
        _keys(factor, ("id", "expression", "direction", "role"), ("version",))
        if not isinstance(factor["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", factor["id"]) or factor["id"] in factor_ids or not isinstance(factor["expression"], str) or not 1 <= len(factor["expression"]) <= 500 or factor["role"] not in ("predictor", "event", "hedge") or not _numeric(factor["direction"]) or factor["direction"] not in (-1, 1) or ("version" in factor and not _integer(factor["version"], 1, 1000000)):
            _bad("invalid factor metadata")
        factor_ids.add(factor["id"])
    pre = construction["preprocess"]
    _keys(pre, ("winsorize", "standardize", "decorrelation", "correlationThreshold"))
    if type(pre["winsorize"]) is not bool or type(pre["standardize"]) is not bool or pre["decorrelation"] not in ("none", "drop_correlated") or not _numeric(pre["correlationThreshold"]) or not .5 <= pre["correlationThreshold"] <= 1:
        _bad("invalid preprocessing metadata")
    target = construction["targetSpecification"]
    _keys(target, ("kind", "horizonSessions"), ("basket",))
    if target["kind"] != scope["targetKind"] or target["horizonSessions"] != scope["horizonSessions"]:
        _bad("target scope mismatch")
    if target["kind"] == "asset_price":
        if "basket" in target:
            _bad("asset price does not take a basket")
    else:
        basket = target.get("basket")
        _keys(basket, ("method", "symbols", "formationDays"), ("components", "quantities"))
        members = basket["symbols"]
        if not isinstance(members, list) or not 1 <= len(members) <= 20 or any(not isinstance(v, str) for v in members) or len(set(members)) != len(members) or set(members)-set(symbols) or not _integer(basket["formationDays"], 60, 504) or basket["method"] not in ("pair_ols", "pca_residual", "fixed"):
            _bad("invalid frozen basket metadata")
        if basket["method"] == "pair_ols" and (len(members) != 2 or set(basket) != {"method", "symbols", "formationDays"}):
            _bad("invalid pair metadata")
        if basket["method"] == "pca_residual" and (len(members) < 3 or set(basket) != {"method", "symbols", "formationDays", "components"} or not _integer(basket["components"], 1, min(10, len(members)-2))):
            _bad("invalid PCA metadata")
        if basket["method"] == "fixed":
            quantities = basket.get("quantities")
            if set(basket) != {"method", "symbols", "formationDays", "quantities"} or not isinstance(quantities, dict) or set(quantities) != set(members) or any(not _numeric(v) or abs(v)>1e6 for v in quantities.values()) or not any(quantities.values()):
                _bad("invalid frozen quantities")
    provenance = a["provenance"]
    _keys(provenance, ("estimator", "parameters", "sklearnVersion"))
    from .models import GRIDS
    if not isinstance(provenance["estimator"], str) or provenance["estimator"] not in GRIDS or not isinstance(provenance["parameters"], dict) or any(not _numeric(v) for v in provenance["parameters"].values()) or provenance["parameters"] not in GRIDS[provenance["estimator"]] or not isinstance(provenance["sklearnVersion"], str) or not re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", provenance["sklearnVersion"]):
        _bad("invalid estimator provenance")
    edit_policy = {"allowed": ["estimator_numeric_parameters"], "arbitraryCode": False, "editedEvidenceStatus": "UNVALIDATED_USER_EDIT"}
    if a["editPolicy"] != edit_policy:
        _bad("invalid edit policy")
    lineage = a["lineage"]
    _keys(lineage, ("parentArtifactId", "status"), ("edits",))
    if lineage["status"] == "fitted":
        if lineage["parentArtifactId"] is not None or "edits" in lineage:
            _bad("fitted artifacts cannot claim edit ancestry")
    elif lineage["status"] == "UNVALIDATED_USER_EDIT":
        if not isinstance(lineage["parentArtifactId"], str) or not re.fullmatch(r"[a-f0-9]{64}", lineage["parentArtifactId"]) or not isinstance(lineage.get("edits"), list) or not 1 <= len(lineage["edits"]) <= 256:
            _bad("invalid edit ancestry")
        for edit in lineage["edits"]:
            _keys(edit, ("path", "value"))
            if not isinstance(edit["path"], str) or not 1 <= len(edit["path"]) <= 160 or not _numeric(edit["value"]):
                _bad("invalid edit receipt")
    else:
        _bad("unknown evidence status")


def validate_function(artifact):
    if not isinstance(artifact, dict):
        _bad("expected JSON object")
    try:
        raw = json.dumps(artifact, allow_nan=False, separators=(",", ":")).encode()
        valid_hash = artifact.get("artifactId") == function_digest({k: v for k, v in artifact.items() if k != "artifactId"})
    except (TypeError, ValueError, OverflowError, RecursionError):
        _bad("non-finite, recursive or non-JSON object")
    if len(raw) > MAX_BYTES or artifact.get("schema") != SCHEMA or artifact.get("hashAlgorithm") != HASH_ALGORITHM or not valid_hash:
        _bad("schema, content hash or size mismatch")
    expected = {"schema", "hashAlgorithm", "inputSchema", "featureConstruction", "transforms", "estimator", "training", "scope", "outputs", "identity", "provenance", "editPolicy", "lineage", "artifactId"}
    if set(artifact) != expected or artifact["outputs"] != OUTPUTS:
        _bad("unexpected artifact fields or outputs")
    identity = {"entry": "currentState + scale * output[0]", "future": "currentState + scale * output[1]",
                "e": "currentState - expectedFuture", "expectedChange": "-e", "scale": "origin_known_gross_absolute_leg_value"}
    if artifact["identity"] != identity:
        _bad("unexpected context-to-value identity")
    if not isinstance(artifact["lineage"], dict) or artifact["lineage"].get("status") not in ("fitted", "UNVALIDATED_USER_EDIT"):
        _bad("invalid lineage")
    _metadata(artifact)
    inputs = artifact["inputSchema"]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_FEATURES:
        _bad("invalid input schema")
    names = []
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"name", "type"} or item["type"] != "finite_number_or_null" or not isinstance(item["name"], str) or not 1 <= len(item["name"]) <= 160:
            _bad("invalid feature declaration")
        names.append(item["name"])
    if len(set(names)) != len(names):
        _bad("duplicate feature names")
    n = len(names)
    t = artifact["transforms"]
    if not isinstance(t, dict) or set(t) != {"imputeMedian", "winsorLower", "winsorUpper", "scaleMean", "scaleScale"}:
        _bad("invalid transforms")
    for value in t.values():
        if value is not None:
            _vector(value, n)
    for lo, hi in (("winsorLower", "winsorUpper"), ("scaleMean", "scaleScale")):
        if (t[lo] is None) != (t[hi] is None):
            _bad("incomplete transform")
    if t["winsorLower"] is not None and any(a > b for a, b in zip(t["winsorLower"], t["winsorUpper"])):
        _bad("inverted winsor boundaries")
    if t["scaleScale"] is not None and any(x <= 0 for x in t["scaleScale"]):
        _bad("nonpositive scale")
    e = artifact["estimator"]
    if not isinstance(e, dict):
        _bad("invalid estimator")
    if e.get("kind") == "constant" and set(e) == {"kind", "value"}:
        _vector(e["value"], 2)
    elif e.get("kind") == "linear" and set(e) == {"kind", "coefficients", "intercepts"}:
        _vector(e["intercepts"], 2)
        if not isinstance(e["coefficients"], list) or len(e["coefficients"]) != 2:
            _bad("invalid coefficient shape")
        for output in e["coefficients"]:
            _vector(output, n)
    elif e.get("kind") == "histogram_trees" and set(e) == {"kind", "nodeFields", "thresholdRule", "leafValuesIncludeLearningRate", "outputs"}:
        if e["nodeFields"] != ["value", "feature", "threshold", "left", "right", "leaf", "missingLeft"] or e["thresholdRule"] != "left_if_less_equal" or e["leafValuesIncludeLearningRate"] is not True or not isinstance(e["outputs"], list) or len(e["outputs"]) != 2:
            _bad("invalid histogram encoding")
        for output in e["outputs"]:
            if not isinstance(output, dict) or set(output) != {"baseline", "trees"} or not _numeric(output["baseline"]) or not isinstance(output["trees"], list) or not 1 <= len(output["trees"]) <= MAX_TREES:
                _bad("invalid forest")
            for tree in output["trees"]:
                if not isinstance(tree, list) or not 1 <= len(tree) <= MAX_NODES:
                    _bad("invalid tree size")
                seen = set()
                pending = [0]
                while pending:
                    i = pending.pop()
                    if i in seen:
                        _bad("cyclic or multiply-parented tree")
                    seen.add(i)
                    node = tree[i]
                    _vector(node, 7)
                    _, feature, _, left, right, leaf, missing = node
                    if any(type(v) is not int for v in (feature, left, right, leaf, missing)) or not 0 <= feature < n or leaf not in (0, 1) or missing not in (0, 1):
                        _bad("invalid tree index")
                    if not leaf:
                        if not (i < left < len(tree) and i < right < len(tree)) or left == right:
                            _bad("tree children must point strictly forward")
                        pending.extend((left, right))
                if len(seen) != len(tree):
                    _bad("unreachable tree nodes")
    else:
        _bad("unsupported estimator or extra executable fields")
    expected_kind = {"no_change": "constant", "historical_drift": "constant", "ridge": "linear", "elastic_net": "linear", "hist_gradient_boosting": "histogram_trees"}[artifact["provenance"]["estimator"]]
    if e["kind"] != expected_kind:
        _bad("estimator provenance and encoding disagree")
    if e["kind"] == "constant" and any(v is not None for v in t.values()):
        _bad("constant function cannot carry ignored transforms")
    if e["kind"] != "constant" and t["imputeMedian"] is None:
        _bad("trained median required")
    return artifact


def predict_function(artifact, rows, *, current_state=None, scale=None):
    """Evaluate numeric JSON only. Source expressions/metadata are never executed."""
    a = validate_function(artifact)
    if not isinstance(rows, list) or len(rows) > 25000:
        _bad("rows must be a bounded list")
    names = [x["name"] for x in a["inputSchema"]]
    values = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(names) or any(v is not None and not _numeric(v) for v in row.values()):
            _bad("input row must exactly match the feature schema")
        values.append([np.nan if row[name] is None else row[name] for name in names])
    X = np.asarray(values, dtype=float).reshape(len(rows), len(names))
    t, e = a["transforms"], a["estimator"]
    with np.errstate(over="ignore", invalid="ignore"):
        if t["winsorLower"] is not None:
            X = np.clip(X, t["winsorLower"], t["winsorUpper"])
        if t["imputeMedian"] is not None:
            X = np.where(np.isnan(X), t["imputeMedian"], X)
        if t["scaleMean"] is not None:
            X = (X - t["scaleMean"]) / t["scaleScale"]
        if e["kind"] != "constant" and not np.isfinite(X).all():
            _bad("non-finite transformed feature")
        if e["kind"] == "constant":
            result = np.tile(e["value"], (len(rows), 1))
        elif e["kind"] == "linear":
            result = X @ np.asarray(e["coefficients"]).T + e["intercepts"]
        else:
            result = np.zeros((len(rows), 2))
            for output_i, output in enumerate(e["outputs"]):
                result[:, output_i] = output["baseline"]
                for tree in output["trees"]:
                    for row_i, row in enumerate(X):
                        node_i = 0
                        while not tree[node_i][5]:
                            node = tree[node_i]
                            value = row[node[1]]
                            left = bool(node[6]) if np.isnan(value) else value <= node[2]
                            node_i = node[3] if left else node[4]
                        result[row_i, output_i] += tree[node_i][0]
    if not np.isfinite(result).all():
        _bad("non-finite prediction")
    response = {"artifactId": a["artifactId"], "normalizedChanges": result.tolist(), "evidenceStatus": a["lineage"]["status"]}
    if (current_state is None) != (scale is None):
        _bad("current_state and scale must be supplied together")
    if current_state is not None:
        _vector(current_state, len(rows)); _vector(scale, len(rows))
        if any(x <= 0 for x in scale):
            _bad("origin gross scale must be positive")
        levels = np.asarray(current_state)[:, None] + np.asarray(scale)[:, None] * result
        if not np.isfinite(levels).all() or not np.isfinite(np.asarray(current_state)-levels[:, 1]).all():
            _bad("non-finite restored level")
        response["levels"] = [{"expectedEntry": float(v[0]), "expectedFuture": float(v[1]),
            "e": float(p-v[1]), "expectedChange": float(v[1]-p)} for p, v in zip(current_state, levels)]
    return response


def edit_function(artifact, edits):
    """Apply explicit finite numeric leaves under estimator; topology is immutable."""
    original = validate_function(artifact)
    if not isinstance(edits, list) or not 1 <= len(edits) <= 256:
        _bad("edits must be a bounded list")
    value = copy.deepcopy(original)
    kind = value["estimator"]["kind"]
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "value"} or not isinstance(edit["path"], str) or not _numeric(edit["value"]):
            _bad("edit requires path and finite value")
        keys = edit["path"].split("/")
        permitted = False
        if kind == "constant":
            permitted = len(keys) == 4 and keys[:3] == ["", "estimator", "value"] and keys[3] in ("0", "1")
        elif kind == "linear":
            permitted = (len(keys) == 4 and keys[:3] == ["", "estimator", "intercepts"] and keys[3] in ("0", "1")) or (len(keys) == 5 and keys[:3] == ["", "estimator", "coefficients"] and keys[3] in ("0", "1") and re.fullmatch(r"0|[1-9][0-9]*", keys[4]))
        else:
            permitted = (len(keys) == 5 and keys[:3] == ["", "estimator", "outputs"] and keys[3] in ("0", "1") and keys[4] == "baseline") or (len(keys) == 8 and keys[:3] == ["", "estimator", "outputs"] and keys[3] in ("0", "1") and keys[4] == "trees" and all(re.fullmatch(r"0|[1-9][0-9]*", x) for x in keys[5:]) and keys[7] == "0")
        if not permitted:
            _bad("only coefficients, intercepts, constants, or tree leaf values are editable")
        try:
            target = value
            for key in keys[1:-1]:
                target = target[int(key)] if isinstance(target, list) else target[key]
            last = int(keys[-1]) if isinstance(target, list) else keys[-1]
            if kind == "histogram_trees" and len(keys) == 8 and not target[5]:
                _bad("only tree leaves are editable")
            target[last] = float(edit["value"])
        except (IndexError, KeyError, TypeError):
            _bad("edit path outside artifact")
    value["lineage"] = {"parentArtifactId": original["artifactId"], "status": "UNVALIDATED_USER_EDIT", "edits": copy.deepcopy(edits)}
    return validate_function(_seal(value))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Evaluate or derive a non-executable JSON F(X).")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("input", type=Path, help="JSON {rows, currentState?, scale?}")
    parser.add_argument("--edits", type=Path, help="optional restricted numeric JSON edits")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    for path in (args.artifact, args.input, args.edits):
        if path is not None and path.stat().st_size > MAX_BYTES:
            parser.error("input exceeds bounded JSON size")
    if args.output.resolve() in {p.resolve() for p in (args.artifact, args.input, args.edits) if p is not None}:
        parser.error("output must not overwrite an input artifact")
    artifact = json.loads(args.artifact.read_text())
    inputs = json.loads(args.input.read_text())
    if args.edits:
        artifact = edit_function(artifact, json.loads(args.edits.read_text()))
    result = predict_function(artifact, inputs["rows"], current_state=inputs.get("currentState"), scale=inputs.get("scale"))
    if args.edits:
        result["derivedArtifact"] = artifact
    args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
