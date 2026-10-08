"""Bounded subprocess bridge for actual Python-core/Worker integration tests.
Only hand-written synthetic statements and injected FakeProvider are used.
"""
import base64
import json
import sys
from test_financial_publication import source, task_inputs
from atlas_quant.financial_runner.publication import compute_publication

request = json.load(sys.stdin)
def b64(raw):
    return base64.b64encode(raw).decode("ascii")

if request["command"] == "source":
    acquired, package = source.__wrapped__()
    if "scope" in request or "flowBasis" in request:
        from test_financial_package import freeze, declarations
        package = freeze(acquired, declarations(acquired), unit_policy="allow_declared",
                         trusted_unit_proofs=False, scope=request.get("scope", "consolidated"),
                         flow_basis=request.get("flowBasis", "ytd"))
    _, metadata, raw, registry = task_inputs(package)
    print(json.dumps({"raw": b64(raw), "metadata": metadata,
                      "registry": {key: b64(value) for key, value in registry.items()}}))
else:
    chunks = []
    manifest = compute_publication(
        request["job"], request["metadata"], base64.b64decode(request["raw"]),
        {key: base64.b64decode(value) for key, value in request["registry"].items()},
        lambda collection, ordinal, raw: chunks.append(
            {"collection": collection, "ordinal": ordinal, "raw": b64(raw)}),
    )
    print(json.dumps({"manifest": manifest, "chunks": chunks}))
