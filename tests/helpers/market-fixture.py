"""Generate real offline-normalized SYNTHETIC transport fixture; no provider/fit."""

import importlib.util
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "market_tests", root / "engine/tests/test_market_acquisition.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
receipts = m.sources()
chunks = {}
manifest = m.build_publication(
    m.JOB,
    m.PLAN,
    lambda r: receipts[r["requestKey"]],
    lambda n, i, b: chunks.__setitem__((n, i), b),
)
print(
    json.dumps(
        {
            "plan": m.PLAN,
            "manifest": manifest,
            "receipts": {
                k: {
                    **{a: b for a, b in v.items() if a != "raw"},
                    "rawText": v["raw"].decode(),
                }
                for k, v in receipts.items()
            },
            "chunks": {
                n: {str(i): raw.decode() for (c, i), raw in chunks.items() if c == n}
                for n in manifest["collections"]
            },
        }
    )
)
