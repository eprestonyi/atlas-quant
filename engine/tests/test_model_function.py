"""Portable F parity, numerical identities and adversarial bounded JSON edits."""
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits
from atlas_quant.statistical_quant.models import fit, candidates
from atlas_quant.statistical_quant.model_function import (
    export_function, predict_function, edit_function, validate_function, function_digest,
)
from atlas_quant.statistical_quant.schema import validate
from test_statistical_quant import strategy


def training():
    rng = np.random.default_rng(17)
    X = pd.DataFrame(rng.normal(size=(180, 3)), columns=["state_deviation20", "factor:利润", "risk"])
    y = pd.DataFrame(np.c_[X.iloc[:, 0]**2*.03, -X.iloc[:, 0]*.1+X.iloc[:, 1]*.02], columns=["entry", "exit"])
    X.loc[::13, "risk"] = np.nan
    return X, y


def fitted(spec):
    X, y = training()
    s = validate(strategy(estimator=spec["estimator"]))
    with threadpool_limits(limits=1):
        model = fit(spec, X.iloc[:140], y.iloc[:140], s["preprocess"])
    audit = {**model.audit, "trainStart": "20240101", "trainEnd": "20240701", "informationCutoff": "20240710", "labelEndMax": "20240709", "trainDates": 140}
    return model, export_function(model, audit, s), X.iloc[140:]


def rows(frame):
    return [{k: None if pd.isna(v) else float(v) for k, v in row.items()} for row in frame.to_dict(orient="records")]


@pytest.mark.parametrize("spec", candidates("auto"), ids=lambda x: x["id"])
def test_every_estimator_is_reconstructed_from_json_with_missing_and_extreme_inputs(spec):
    model, artifact, X = fitted(spec)
    X = X[model.columns].copy()
    X.iloc[0, 0] = 1e20
    X.iloc[1, 0] = -1e20
    X.iloc[2, :] = np.nan
    transported = json.loads(json.dumps(artifact, allow_nan=False))
    with threadpool_limits(limits=1):
        expected = model.predict(X)
    result = predict_function(transported, rows(X), current_state=[100.]*len(X), scale=[100.]*len(X))
    np.testing.assert_allclose(result["normalizedChanges"], expected, rtol=1e-12, atol=1e-14)
    for output, change in zip(result["levels"], result["normalizedChanges"]):
        assert output["expectedFuture"] == pytest.approx(100+100*change[1])
        assert output["e"] == pytest.approx(100-output["expectedFuture"])
        assert output["expectedChange"] == -output["e"]
    assert artifact["scope"]["generalizationOutsideScopeValidated"] is False
    assert artifact["training"]["labelEndMax"] < artifact["training"]["informationCutoff"]


def test_derived_linear_edit_changes_function_but_preserves_original_artifact_and_requires_validation():
    _, artifact, X = fitted(candidates("ridge")[0])
    before = copy.deepcopy(artifact)
    edited = edit_function(artifact, [{"path": "/estimator/intercepts/1", "value": 1.5}])
    assert artifact == before
    assert edited["artifactId"] != artifact["artifactId"]
    assert edited["lineage"]["parentArtifactId"] == artifact["artifactId"]
    assert edited["lineage"]["status"] == "UNVALIDATED_USER_EDIT"
    values = rows(X[[x["name"] for x in artifact["inputSchema"]]])
    old, new = (np.asarray(predict_function(a, values)["normalizedChanges"]) for a in (artifact, edited))
    np.testing.assert_allclose(new[:, 0], old[:, 0])
    np.testing.assert_allclose(new[:, 1]-old[:, 1], 1.5-artifact["estimator"]["intercepts"][1])


def test_tree_leaf_edit_works_without_allowing_graph_or_split_edits():
    _, artifact, X = fitted(candidates("hist_gradient_boosting")[0])
    tree = artifact["estimator"]["outputs"][1]["trees"][0]
    leaf = next(i for i, node in enumerate(tree) if node[5])
    edited = edit_function(artifact, [{"path": f"/estimator/outputs/1/trees/0/{leaf}/0", "value": .7}])
    assert edited["estimator"]["outputs"][1]["trees"][0][leaf][0] == .7
    for path in ("/estimator/outputs/1/trees/0/0/2", "/estimator/outputs/1/trees/0/0/0", "/schema", "/training/informationCutoff"):
        with pytest.raises(ValueError, match="INVALID_MODEL_FUNCTION"):
            edit_function(artifact, [{"path": path, "value": 4}])


def test_hash_tamper_cycle_executable_fields_and_input_schema_are_rejected():
    _, artifact, X = fitted(candidates("hist_gradient_boosting")[0])
    bad = copy.deepcopy(artifact); bad["estimator"]["outputs"][0]["baseline"] += 1
    with pytest.raises(ValueError):
        validate_function(bad)
    for mutation in ("cycle", "code", "scale"):
        bad = copy.deepcopy(artifact)
        if mutation == "cycle":
            bad["estimator"]["outputs"][0]["trees"][0][0][3] = 0
        elif mutation == "code":
            bad["estimator"]["eval"] = "__import__('os').system('false')"
        else:
            bad["transforms"]["scaleScale"][0] = 0
        bad["artifactId"] = function_digest({k:v for k,v in bad.items() if k != "artifactId"})
        with pytest.raises(ValueError):
            validate_function(bad)
    values = rows(X[[x["name"] for x in artifact["inputSchema"]]])
    for replacement in ({}, {**values[0], "unknown": 1}, {**values[0], "risk": "__import__('os')"}):
        with pytest.raises(ValueError):
            predict_function(artifact, [replacement])


def test_cross_runtime_canonical_float_identity_and_json_roundtrip():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable")
    value = {"name": "利润🌍", "values": [-0.0, 0.0, 1.0, 1e-7, 1e-20, 1e20, 1.23456789123456, True, None]}
    script = """const c=require('crypto');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>{
      function cv(x){if(typeof x==='number'){const b=Buffer.alloc(8);b.writeDoubleBE(x===0?0:x);return {'$f64':b.toString('hex')}}
        if(Array.isArray(x))return x.map(cv);if(x&&typeof x==='object')return Object.fromEntries(Object.keys(x).sort().map(k=>[k,cv(x[k])]));return x}
      process.stdout.write(c.createHash('sha256').update(JSON.stringify(cv(JSON.parse(s)))).digest('hex'));});"""
    js = subprocess.run([node, "-e", script], input=json.dumps(value, ensure_ascii=False), text=True, capture_output=True, check=True)
    assert js.stdout == function_digest(value)
    _, artifact, _ = fitted(candidates("ridge")[0])
    body = {k:v for k,v in artifact.items() if k != "artifactId"}
    js = subprocess.run([node, "-e", script], input=json.dumps(body, ensure_ascii=False), text=True, capture_output=True, check=True)
    assert js.stdout == artifact["artifactId"]


def test_cli_derives_and_evaluates_without_executable_serialization(tmp_path):
    _, artifact, X = fitted(candidates("ridge")[0])
    a, inp, edits, out = [tmp_path/name for name in ("function.json", "input.json", "edits.json", "output.json")]
    a.write_text(json.dumps(artifact)); inp.write_text(json.dumps({"rows": rows(X[[x["name"] for x in artifact["inputSchema"]]])}))
    edits.write_text(json.dumps([{"path": "/estimator/intercepts/1", "value": .123}]))
    subprocess.run([sys.executable, "-m", "atlas_quant.statistical_quant.model_function", str(a), str(inp), "--edits", str(edits), "--output", str(out)], check=True)
    result = json.loads(out.read_text())
    assert result["derivedArtifact"]["lineage"]["status"] == "UNVALIDATED_USER_EDIT"
    assert len(result["normalizedChanges"]) == len(X)


def reseal(artifact):
    artifact["artifactId"] = function_digest({k:v for k,v in artifact.items() if k != "artifactId"})
    return artifact


@pytest.mark.parametrize("field,value", [
    ("training", {}), ("scope", {}), ("lineage", {}), ("provenance", {}), ("editPolicy", {}),
    ("identity", {"future":"random"}), ("featureConstruction", {}),
])
def test_malformed_metadata_cannot_claim_valid_fitted_function(field, value):
    _, artifact, _ = fitted(candidates("ridge")[0])
    artifact[field] = value
    with pytest.raises(ValueError, match="INVALID_MODEL_FUNCTION"):
        validate_function(reseal(artifact))


def test_reserved_hash_marker_and_excessive_nesting_are_rejected():
    with pytest.raises(ValueError, match="INVALID_MODEL_FUNCTION"):
        function_digest({"ordinary": {"$f64": "3ff0000000000000"}})
    nested = 1
    for _ in range(34): nested = [nested]
    with pytest.raises(ValueError, match="nesting budget"):
        function_digest(nested)
    with pytest.raises(ValueError, match="unsupported hash number"):
        function_digest({"integer": 2**53+1})


def test_tree_cannot_hide_standardization_overflow_by_returning_a_finite_leaf():
    _, artifact, _ = fitted(candidates("hist_gradient_boosting")[0])
    artifact["transforms"]["winsorLower"] = artifact["transforms"]["winsorUpper"] = None
    artifact["featureConstruction"]["preprocess"]["winsorize"] = False
    artifact["transforms"]["scaleScale"][0] = 1e-300
    names = [x["name"] for x in artifact["inputSchema"]]
    row = dict.fromkeys(names, 1.)
    row[names[0]] = 1e308
    with pytest.raises(ValueError, match="non-finite transformed feature"):
        predict_function(reseal(artifact), [row])


def test_no_change_is_not_allowed_to_smuggle_transforms_or_alter_training_cutoff():
    _, artifact, _ = fitted(candidates("no_change")[0])
    artifact["training"]["labelEndMax"] = artifact["training"]["informationCutoff"]
    with pytest.raises(ValueError, match="training chronology"):
        validate_function(reseal(artifact))


def test_v_restoration_rejects_overflow_instead_of_printing_null():
    _, artifact, X = fitted(candidates("historical_drift")[0])
    artifact = edit_function(artifact, [{"path":"/estimator/value/1", "value":1e308}])
    with np.errstate(over="ignore", invalid="ignore"):
        with pytest.raises(ValueError, match="restored level"):
            predict_function(artifact, rows(X[[x["name"] for x in artifact["inputSchema"]]].iloc[:1]), current_state=[1e308], scale=[1e308])


def test_integral_binary64_js_transport_and_derive_identity_golden():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable")
    _, artifact, _ = fitted(candidates("ridge")[0])
    derived = edit_function(artifact, [{"path":"/estimator/intercepts/1", "value":1e20}])
    script = "let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s))))"
    js = subprocess.run([node,"-e",script], input=json.dumps(derived), text=True, capture_output=True, check=True)
    transported = json.loads(js.stdout)
    assert type(transported["estimator"]["intercepts"][1]) is int
    assert validate_function(transported)["artifactId"] == derived["artifactId"]
    bad = copy.deepcopy(artifact); bad["provenance"]["parameters"]["alpha"] = True
    with pytest.raises(ValueError, match="estimator provenance"):
        validate_function(reseal(bad))
    with pytest.raises(ValueError, match="editable"):
        edit_function(artifact, [{"path":"/estimator/coefficients/1/00", "value":1.}])


def test_checked_in_golden_references_evaluate_and_derive_identically():
    fixture = json.loads((Path(__file__).parent/"fixtures/model-function-golden-v1.json").read_text())
    for case in fixture["cases"]:
        given = case["input"]
        actual = predict_function(case["artifact"], given["rows"], current_state=given["currentState"], scale=given["scale"])
        np.testing.assert_allclose(actual["normalizedChanges"], case["expected"]["normalizedChanges"], rtol=1e-12, atol=1e-14)
        for edit in case["derivations"]:
            derived = edit_function(case["artifact"], edit["edits"])
            assert derived["artifactId"] == edit["artifactId"]
            result = predict_function(derived, given["rows"], current_state=given["currentState"], scale=given["scale"])
            np.testing.assert_allclose(result["normalizedChanges"], edit["expected"]["normalizedChanges"], rtol=1e-12, atol=1e-14)
