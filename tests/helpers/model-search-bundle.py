"""Pack supplied, already-fitted documents. Never calls a provider or estimator."""
import json
import sys
from unittest.mock import patch

from atlas_quant import bundle

request = json.load(sys.stdin)
report, snapshot, coverage = (request[name] for name in ("report", "snapshot", "coverage"))
if request.get("syntheticEnvelope"):
    assert report["provenance"]["synthetic"] is True
    forecast = {k: v for k, v in report["forecasts"].items() if k != "artifactId"}
    report["forecasts"]["artifactId"] = bundle.sha(bundle.encode(forecast))
    report["execution"]["forecastArtifactId"] = report["forecasts"]["artifactId"]


def pack():
    chunks = {}
    write = lambda c, n, raw: chunks.__setitem__(f"{c}:{n}", raw)
    read = lambda c, n: chunks[f"{c}:{n}"]
    raw = bundle.build_bundle(report, snapshot, coverage, write, read, chunk_target=65536)
    reader = bundle.BundleReader(raw, read)
    verified = reader.verify_integrity()
    for name, original in (("report", report), ("snapshot", snapshot), ("coverage", coverage)):
        assert reader.document(name) == original, name
    return {"manifestText": raw.decode(), "bundleId": reader.bundle_id,
            "chunks": {key: value.decode() for key, value in chunks.items()},
            "verification": verified, "providerCalls": 0, "estimatorCalls": 0}


result = pack()
if request.get("verifyLegacyBytes"):
    assert "modelSearch" not in report["forecasts"].get("diagnostics", {})
    with patch.dict(bundle.OPTIONAL_COLLECTIONS, {}, clear=True):
        legacy = pack()
    assert result["manifestText"] == legacy["manifestText"]
    assert result["chunks"] == legacy["chunks"]
    result["legacyBytesUnchanged"] = True
json.dump(result, sys.stdout, separators=(",", ":"), allow_nan=False)
