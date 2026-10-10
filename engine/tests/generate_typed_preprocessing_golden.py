"""Small actual fitted v2 preprocessing functions for Python/JS parity."""
import json
from pathlib import Path
import numpy as np
from atlas_quant.statistical_quant.models import candidates
from atlas_quant.statistical_quant.model_function import predict_function
from test_typed_preprocessing import v2_fitted_fixture
from test_automatic_preprocessing import rows


def generate():
    cases = []
    for estimator in ("ridge", "transformed_ridge"):
        model, artifact, samples, strategy, training = v2_fitted_fixture(candidates(estimator)[0])
        test = samples.X.iloc[200:205][model.columns].copy()
        test.iloc[1, :] = np.nan
        test.iloc[2, :] = -1e8
        test.iloc[3, :] = 1e8
        inputs = rows(test)
        expected = predict_function(artifact, inputs, current_state=[100.]*len(inputs), scale=[100.]*len(inputs))
        np.testing.assert_allclose(expected["normalizedChanges"], model.predict(test), rtol=1e-12, atol=1e-14)
        cases.append({"name": estimator, "strategy": strategy, "fitAudit": training,
                      "artifact": artifact, "rows": inputs, "expected": expected})
    return {"schema": "typed-preprocessing-function-golden/1", "synthetic": True, "cases": cases}


if __name__ == "__main__":
    target = Path(__file__).with_name("fixtures") / "typed-preprocessing-function-golden.json"
    target.write_text(json.dumps(generate(), allow_nan=False, ensure_ascii=False, separators=(",", ":"))+"\n")
    print(target)
