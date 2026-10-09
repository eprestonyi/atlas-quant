"""Generate the small local cross-runtime fixture; never overwrite a golden."""
import json
from pathlib import Path
from atlas_quant.statistical_quant.models import candidates
from atlas_quant.statistical_quant.model_function import predict_function, edit_function
from test_automatic_preprocessing import fitted_fixture, rows


def main():
    cases = []
    for name in ("no_change", "historical_drift", "ridge", "hist_gradient_boosting"):
        _, artifact, frame, audit, strategy = fitted_fixture(candidates(name)[0],include_source=True)
        given = rows(frame)
        kind = artifact["estimator"]["kind"]
        path = "/estimator/value/1" if kind == "constant" else "/estimator/intercepts/1" if kind == "linear" else "/estimator/outputs/1/baseline"
        edits = [{"path": path, "value": .0123456789}]
        edited = edit_function(artifact, edits)
        cases.append({"name": name, "artifact": artifact, "rows": given,
            "sourceFit":{**audit,"fitDate":audit["informationCutoff"],"status":"valid"},"sourceStrategy":strategy,
            "expected": predict_function(artifact,given), "edits": edits,
            "editedArtifact": edited, "editedExpected": predict_function(edited,given)})
    output = Path(__file__).resolve().parents[2]/"tests/fixtures/model-function-v2-golden.json"
    with output.open("x") as stream:
        json.dump({"schema":"atlas-model-function-golden/2", "source":"local unit fixture, no provider or research run", "cases":cases},stream,ensure_ascii=False,allow_nan=False,indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
