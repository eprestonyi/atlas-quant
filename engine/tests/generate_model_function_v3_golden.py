"""Generate actual fitted basis artifacts for cross-runtime numerical tests."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from atlas_quant.statistical_quant.models import candidates, fit
from atlas_quant.statistical_quant.basis_models import NONLINEAR_GRIDS
from atlas_quant.statistical_quant.model_function import export_function, predict_function
from atlas_quant.statistical_quant.preprocessing import automatic_metadata
from atlas_quant.statistical_quant.schema import validate


def generate():
    rng = np.random.default_rng(561)
    X = pd.DataFrame(rng.normal(size=(160, 3)), columns=["state_deviation20", "factor:price", "change1"])
    y = pd.DataFrame({"entry": X.change1*.002,
                      "exit": .015*X["factor:price"]**2-.02*X.state_deviation20+.007*X["factor:price"]*X.change1})
    dates = pd.bdate_range("20230102", periods=160).strftime("%Y%m%d").tolist()
    output = []
    for family in NONLINEAR_GRIDS:
        for automatic in (False, True):
            config = {"schemaVersion": 2, "name": "Basis parity fixture", "research": {"mode": "statistical_quant"},
                      "universe": {"symbols": ["000001.SZ"], "start": "20230101", "end": "20250930"},
                      "target": {"kind": "asset_price", "horizonSessions": 5},
                      "model": {"family": "mean_reversion", "estimator": family},
                      "factors": [{"id": "price", "expression": "close", "direction": 1, "role": "predictor"}],
                      "preprocess": {"automatic": {"schema": "auto-factor-preprocess/1"}} if automatic else {},
                      "execution": {"enabled": False}}
            s = validate(config)
            kwargs = {"automatic_metadata": automatic_metadata(s), "training_dates": dates[:130]} if automatic else {}
            with threadpool_limits(limits=1):
                model = fit(candidates(family)[0], X.iloc[:130], y.iloc[:130], s["preprocess"], **kwargs)
            audit = {**model.audit, "trainStart": dates[0], "trainEnd": dates[129], "labelEndMax": dates[133],
                     "informationCutoff": dates[134], "trainDates": 130}
            artifact = export_function(model, audit, s)
            test = X.iloc[135:].copy()
            test.iloc[0, :] = np.nan
            test.iloc[1, :] = [1e6, -1e6, 0]
            rows = [{key: None if pd.isna(value) else float(value) for key, value in row.items()} for row in test.to_dict("records")]
            expected = predict_function(artifact, rows, current_state=[100.]*len(rows), scale=[100.]*len(rows))
            np.testing.assert_allclose(expected["normalizedChanges"], model.predict(test), rtol=1e-12, atol=1e-14)
            output.append({"name": family+("-automatic" if automatic else "-legacy"), "artifact": artifact,
                           "fitAudit": audit, "strategy": s, "rows": rows, "expected": expected})
    return {"schema": "model-function-v3-golden/1", "synthetic": True, "cases": output}


if __name__ == "__main__":
    path = Path(__file__).with_name("fixtures") / "model-function-v3-golden.json"
    path.write_text(json.dumps(generate(), allow_nan=False, ensure_ascii=False, separators=(",", ":"))+"\n")
    print(path)
