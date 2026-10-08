#!/usr/bin/env python3
"""Generate a small explicit SYNTHETIC dataset/2 transport fixture, with no fit.

Requires the locked development Python dependencies. Never reads runtime config
or calls the network. Numerical financial states use the public hand fixtures.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "engine"), str(ROOT / "engine/tests")]

from atlas_quant import bundle
from atlas_quant.research_dataset.codec import encode, sha
from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source, derive, publication


def generate(destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Existing fixture directory is preserved")
    destination.mkdir(mode=0o700, parents=False)
    source = sources.__wrapped__()
    original = legacy_source.__wrapped__(source)
    view = derive(original, source["scope"])
    result, parts, _ = publication(view, source)

    def write(relative, raw):
        path = destination / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            output.write(raw)

    write("source/manifest.json", original["manifest"])
    write("source/snapshot.json", original["raw"])
    manifest = json.loads(original["manifest"])
    rows = json.loads(original["raw"])["rows"]
    collection = next(c for c in manifest["collections"] if c["id"] == "snapshotRows")
    for part in collection["chunks"]:
        raw = bundle.encode(rows[part["start"] : part["start"] + part["count"]])
        assert sha(raw) == part["sha256"]
        write(f'source/snapshotRows/{part["ordinal"]}.json', raw)
    write("financial/package.json", source["source"].package_bytes)
    for ref, raw in source["registry"].items():
        write(f"registry/{ref}.json", raw)
    write("dataset/manifest.json", result.manifest_bytes)
    for (component, ordinal), raw in parts.items():
        write(f"dataset/parts/{component}/{ordinal}.bin", raw)
    summary = {
        "synthetic": True,
        "sourceKind": "fixture",
        "modelFits": 0,
        "providerCalls": 0,
        "fixtureStatementSnapshots": len(source["acquired"].snapshots),
        "sourceBundleId": sha(original["manifest"]),
        "sourceSnapshotSha256": sha(original["raw"]),
        "sourceStrategy": original["strategy"],
        "scope": source["scope"],
        "transform": json.loads(view.origin_bytes)["transform"],
        "financialSource": {
            "preparedRoot": source["source"].prepared_root,
            "calendarRef": source["source"].calendar_ref,
            "proofRefs": list(source["source"].proof_refs),
        },
        "datasetRoot": result.dataset_root,
        "manifestSha256": sha(result.manifest_bytes),
        "partCount": len(parts),
        "totalPartBytes": sum(map(len, parts.values())),
        "registryRefs": sorted(source["registry"]),
    }
    write("summary.json", encode(summary))
    return {
        key: summary[key]
        for key in (
            "synthetic",
            "modelFits",
            "providerCalls",
            "datasetRoot",
            "partCount",
            "totalPartBytes",
        )
    }


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        help="New directory under an existing private/temp parent",
    )
    args = parser.parse_args()
    print(json.dumps(generate(args.output), sort_keys=True))
