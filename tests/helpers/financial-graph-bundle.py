"""One small explicitly SYNTHETIC graph F and transport fixture; zero provider IO."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "engine/tests"))
from test_research_dataset_components import sources, long_sources, strategy
from test_snapshot_market_view import legacy_source, derive
from test_financial_graph_dataset import build
from atlas_quant.engine import run_research
from atlas_quant.research_dataset.graph_v3.snapshot import (
    restore_graph_for_research,
    freeze_graph_input,
    RESEARCH_PROFILE,
)
from atlas_quant.research_dataset.codec import encode
from atlas_quant.financial_bundle_v2 import build_financial_graph_bundle

small = sources.__wrapped__()
long = long_sources.__wrapped__(small)
view = derive(legacy_source.__wrapped__(small), long["scope"])
pub, parts, reader = build(view, long)
config = strategy(long)
config["model"].update(estimator="auto", trainWindow=120, refitDays=60)
config["validation"] = {"minTrainDates": 40, "innerFolds": 2, "outerFolds": 2}
ref = {
    "datasetId": "00000000-0000-0000-0000-000000000033",
    "datasetRoot": pub.dataset_root,
    "format": "atlas.quant.research_dataset",
    "version": 3,
}
result = restore_graph_for_research(
    config, reader, long["registry"], research_profile=RESEARCH_PROFILE
)
plans = []
report = run_research(
    config, result.data, result.provenance, forecast_plan_sink=plans.append
)
snapshot = freeze_graph_input(
    config,
    result,
    ref,
    manifest_bytes=pub.manifest_bytes,
    research_profile=RESEARCH_PROFILE,
)
chunks = {}
manifest = build_financial_graph_bundle(
    report,
    encode(snapshot),
    plans[0],
    {"datasetRef": ref, "admissionProfile": RESEARCH_PROFILE},
    lambda c, n, b: chunks.__setitem__((c, n), b),
    lambda c, n: chunks[c, n],
    chunk_target=65536,
)
print(
    json.dumps(
        {
            "manifestText": manifest.decode(),
            "chunks": {f"{c}:{n}": b.decode() for (c, n), b in chunks.items()},
            "datasetManifestText": pub.manifest_bytes.decode(),
            "datasetParts": {f"{c}:{n}": b.decode() for (c, n), b in parts.items()},
            "sourceKind": "SYNTHETIC",
            "providerCalls": 0,
            "modelFitted": True,
        }
    )
)
