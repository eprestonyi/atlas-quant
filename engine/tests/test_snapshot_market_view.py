"""Synthetic source transport + actual panel preparation; no provider or F fit."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest

from atlas_quant import bundle
from atlas_quant.fixtures import make_demo_data
from atlas_quant.runner_artifacts import freeze_input
from atlas_quant.statistical_quant.schema import validate, digest, prediction_config
from atlas_quant.research_dataset import (
    DatasetError,
    DatasetProfile,
    DatasetReader,
    DirectoryDatasetReader,
    SnapshotMarketView,
    derive_market_snapshot_view,
    validate_snapshot_scope_origin,
    compose_snapshot_dataset_components,
    restore_dataset,
    export_dataset_archive,
    extract_dataset_archive,
)
from atlas_quant.research_dataset.codec import encode, sha
from test_research_dataset_components import sources, long_sources


def pack(strategy, snapshot):
    """A declared transport-only fixture, with no invented model result."""
    artifact = {
        "schemaVersion": 1,
        "totalRows": 0,
        "truncated": False,
        "sourceStrategy": strategy,
        "dataFingerprint": snapshot["dataFingerprint"],
        "predictionConfigHash": digest(prediction_config(strategy)),
        "rows": [],
        "targetDefinitions": [],
        "modelFits": [],
        "hedgeFits": [],
        "diagnostics": {},
    }
    artifact["artifactId"] = bundle.sha(bundle.encode(artifact))
    report = {
        "schemaVersion": 2,
        "status": "completed",
        "strategy": strategy,
        "provenance": snapshot["provenance"],
        "selection": {"reason": "SYNTHETIC_TRANSPORT_FIXTURE_NO_FIT"},
        "research": {"executionOnly": False, "mode": "statistical_quant"},
        "forecasts": artifact,
        "equity": [],
        "trades": [],
        "execution": {"ledger": [], "decisions": []},
    }
    chunks = {}
    manifest = bundle.build_bundle(
        report,
        snapshot,
        {
            "schemaVersion": 1,
            "source": "legacy_artifact_derived",
            "baselineRequired": False,
            "origins": [],
        },
        lambda c, n, b: chunks.__setitem__((c, n), b),
        lambda c, n: chunks[c, n],
    )
    raw = b"".join(
        bundle.iter_document_bytes(
            json.loads(manifest), "snapshot", lambda c, n: chunks[c, n]
        )
    )
    return manifest, raw


@pytest.fixture(scope="module")
def legacy_source(sources):
    strategy = validate(
        {
            "schemaVersion": 2,
            "name": "SYNTHETIC source snapshot only; no fit",
            "universe": {**sources["scope"], "start": "20230101", "end": "20241231"},
            "research": {"mode": "statistical_quant"},
            "target": {"kind": "asset_price", "horizonSessions": 1},
            "model": {"family": "mean_reversion", "estimator": "ridge"},
            "execution": {"enabled": False},
            "factors": [
                {"id": "one", "expression": "returns(close,1)", "role": "predictor"}
            ],
        }
    )
    frame, provenance = make_demo_data(strategy)
    frame["pb"] = 2.0
    frame["pe_ttm"] = 15.0
    within = frame.index[
        frame.trade_date.between(sources["scope"]["start"], sources["scope"]["end"])
    ]
    frame.loc[within[0], "pb"] = 2.123456789012345
    frame.loc[within[0], "vol"] = 12345.125
    frame.loc[within[1], "pe_ttm"] = float("nan")
    # Keep a genuine missing security/session observation; no source fill occurs.
    frame = frame.drop(index=1).reset_index(drop=True)
    from atlas_quant.provider import canonical_hash, _records

    provenance["dataFingerprint"] = canonical_hash(_records(frame))
    provenance["rows"] = len(frame)
    snapshot = freeze_input(strategy, frame, provenance)
    manifest, raw = pack(strategy, snapshot)
    return {
        "strategy": strategy,
        "manifest": manifest,
        "raw": raw,
        "snapshot": json.loads(raw),
    }


def derive(source, scope, mode="explicit_subset", **kwargs):
    return derive_market_snapshot_view(
        source["raw"],
        source["manifest"],
        {"kind": "snapshot_scope_view", "version": 1, "mode": mode, **scope},
        expected_bundle_id=sha(source["manifest"]),
        expected_snapshot_sha256=sha(source["raw"]),
        **kwargs
    )


def publication(view, sources, **kwargs):
    parts = {}
    pub = compose_snapshot_dataset_components(
        view,
        [sources["source"]],
        sources["registry"],
        lambda c, n, b: parts.__setitem__((c, n), b),
        market_calendar_ref=sources["calendar"],
        **kwargs
    )
    reader = DatasetReader(
        pub.manifest_bytes,
        lambda c, n: parts[c, n],
        expected_root=pub.dataset_root,
        **kwargs
    )
    return pub, parts, reader


def test_exact_and_subset_preserve_raw_source_missing_rows_and_precision(
    legacy_source, sources
):
    scope = legacy_source["strategy"]["universe"]
    exact = derive(
        legacy_source, {k: scope[k] for k in ("symbols", "start", "end")}, "exact"
    )
    parsed = json.loads(exact.market_bytes)
    assert parsed["rows"] == legacy_source["snapshot"]["rows"]
    assert len(parsed["rows"]) < len(parsed["provenance"]["tradingDates"])
    view = derive(legacy_source, sources["scope"])
    original = json.loads(view.origin_bytes)
    assert original["source"]["snapshotRawText"].encode() == legacy_source["raw"]
    assert original["source"]["manifestRawText"].encode() == legacy_source["manifest"]
    expected = [
        row
        for row in legacy_source["snapshot"]["rows"]
        if sources["scope"]["start"] <= row["trade_date"] <= sources["scope"]["end"]
    ]
    assert encode(json.loads(view.market_bytes)["rows"]) == encode(expected)
    assert view.receipt["marketRoot"] == sha(view.market_bytes)
    rows = json.loads(view.market_bytes)["rows"]
    assert rows[0]["pb"] == 2.123456789012345 and type(rows[0]["vol"]) is float
    assert rows[1]["pe_ttm"] is None and type(rows[1]["vol"]) is int
    assert validate_snapshot_scope_origin(view.origin_bytes) == view


def test_version_two_closed_archive_recomposes_actual_financial_states(
    legacy_source, sources, tmp_path
):
    view = derive(legacy_source, sources["scope"])
    pub, parts, reader = publication(view, sources)
    assert reader.manifest["version"] == 2
    assert reader.manifest["profile"] == "financial_snapshot_view_50_v1"
    assert "marketOrigin" in reader.components
    reader.verify_integrity()
    result = restore_dataset(reader, sources["registry"])
    pd.testing.assert_frame_equal(result.data, pub.result.data, check_exact=True)
    assert result.provenance == pub.result.provenance
    tar = tmp_path / "v2.tar"
    export_dataset_archive(reader, tar)
    restored = extract_dataset_archive(
        tar, tmp_path / "restored", expected_root=pub.dataset_root
    )
    result2 = restore_dataset(
        DirectoryDatasetReader(restored["directory"]), sources["registry"]
    )
    pd.testing.assert_frame_equal(result2.data, result.data, check_exact=True)


@pytest.mark.parametrize("mutation", ["mode", "future", "missing_symbol", "empty"])
def test_explicit_scope_constraints(legacy_source, sources, mutation):
    scope = deepcopy(sources["scope"])
    mode = "explicit_subset"
    if mutation == "mode":
        mode = "exact"
    if mutation == "future":
        scope["end"] = "20260101"
    if mutation == "missing_symbol":
        scope["symbols"] = ["000001.SZ"]
    if mutation == "empty":
        scope["start"] = scope["end"] = "20240428"
    with pytest.raises(ValueError):
        derive(legacy_source, scope, mode)


@pytest.mark.parametrize("mutation", ["receipt", "strategy", "raw", "market"])
def test_rehashed_origin_or_market_forgery_is_recomputed(
    legacy_source, sources, mutation
):
    view = derive(legacy_source, sources["scope"])
    origin = json.loads(view.origin_bytes)
    if mutation == "receipt":
        origin["receipt"]["selectedRows"] += 1
    elif mutation == "strategy":
        origin["source"]["sourceStrategy"]["name"] = "changed"
    elif mutation == "raw":
        origin["source"]["snapshotRawText"] += " "
    else:
        market = json.loads(view.market_bytes)
        market["rows"][0]["close"] += 1
        with pytest.raises(ValueError):
            publication(SnapshotMarketView(encode(market), view.origin_bytes), sources)
        return
    with pytest.raises(ValueError):
        validate_snapshot_scope_origin(encode(origin))


def test_invalid_original_row_outside_view_cannot_be_hidden(legacy_source, sources):
    snapshot = deepcopy(legacy_source["snapshot"])
    assert snapshot["rows"][0]["trade_date"] < sources["scope"]["start"]
    snapshot["rows"][0]["close"] += 0.001
    manifest, raw = pack(legacy_source["strategy"], snapshot)
    with pytest.raises(ValueError):
        derive({"manifest": manifest, "raw": raw}, sources["scope"])


def test_original_identity_and_financial_namespace_are_rejected(legacy_source, sources):
    transform = {
        "kind": "snapshot_scope_view",
        "version": 1,
        "mode": "explicit_subset",
        **sources["scope"],
    }
    with pytest.raises(ValueError):
        derive_market_snapshot_view(
            legacy_source["raw"],
            legacy_source["manifest"],
            transform,
            expected_bundle_id="b" * 64,
            expected_snapshot_sha256=sha(legacy_source["raw"]),
        )
    snapshot = deepcopy(legacy_source["snapshot"])
    snapshot["provenance"]["financialDatasetRoot"] = "c" * 64
    manifest, raw = pack(legacy_source["strategy"], snapshot)
    with pytest.raises(DatasetError, match="typed closure"):
        derive({"manifest": manifest, "raw": raw}, sources["scope"])


def test_origin_parent_budget_reserves_before_financial_preparation(
    legacy_source, sources, monkeypatch
):
    view = derive(legacy_source, sources["scope"])
    import atlas_quant.research_dataset.compose as module

    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("financial prepare must not start")

    monkeypatch.setattr(module, "compose_financial_dataset", forbidden)
    budget = replace(
        DatasetProfile(),
        total_bytes=len(view.origin_bytes)
        + len(view.market_bytes)
        + DatasetProfile().manifest_bytes
        + 1,
    )
    with pytest.raises(DatasetError):
        publication(view, sources, profile=budget)
    assert not calls


def test_source_rows_chunk_identity_cannot_be_relabelled(legacy_source, sources):
    manifest = json.loads(legacy_source["manifest"])
    collection = next(c for c in manifest["collections"] if c["id"] == "snapshotRows")
    collection["chunks"][0]["sha256"] = "e" * 64
    changed = {**legacy_source, "manifest": bundle.encode(manifest)}
    with pytest.raises(DatasetError, match="chunk descriptors"):
        derive(changed, sources["scope"])


def test_stdlib_version_two_audit_reconstructs_projection(
    legacy_source, sources, tmp_path
):
    import subprocess
    import sys

    view = derive(legacy_source, sources["scope"])
    pub, parts, reader = publication(view, sources)
    tar = tmp_path / "source-view.tar"
    export_dataset_archive(reader, tar)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')",
            str(Path(__file__).parents[2] / "scripts"),
            str(Path(__file__).parents[2] / "scripts/audit-dataset.py"),
            str(tar),
            "--expected-root",
            pub.dataset_root,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS" and report["sourceViewProjectionVerified"] is True
    assert report["sourceResearchFingerprintRecomputed"] is False
    assert report["sourceAuthorityVerified"] is False and report["modelFitted"] is False


@pytest.mark.parametrize(
    "mutation", ["receipt", "metadata", "source_descriptor", "omit_origin"]
)
def test_stdlib_rehash_does_not_bypass_source_projection(
    legacy_source, sources, tmp_path, mutation
):
    from test_dataset_audit import audit, rebuild

    view = derive(legacy_source, sources["scope"])
    pub, parts, reader = publication(view, sources)
    values = {name: json.loads(reader.payload(name)) for name in reader.components}
    fixture = (pub, parts, values, sources)

    def alter(manifest, payloads):
        if mutation == "receipt":
            payloads["marketOrigin"]["receipt"]["removedRows"] += 1
        elif mutation == "metadata":
            payloads["marketDataset"]["provenance"]["source"] = "FORGED_SOURCE"
        elif mutation == "source_descriptor":
            origin = next(
                c for c in manifest["components"] if c["componentId"] == "marketOrigin"
            )
            origin["semanticRoots"]["sourceSnapshotSha256"] = "e" * 64
        else:
            manifest["version"] = 1
            manifest["profile"] = "financial_compose_50_v1"

    directory = rebuild(fixture, tmp_path, alter)
    with pytest.raises(ValueError):
        audit.audit_dataset(directory)
    if mutation == "source_descriptor":
        # A general transport read cannot report a different source identity.
        with pytest.raises(DatasetError):
            DirectoryDatasetReader(directory).verify_integrity()


def test_financial_snapshot_ref_and_closure_version_match_without_fit(
    legacy_source, long_sources
):
    from uuid import UUID
    from test_research_dataset_components import strategy
    from atlas_quant.research_dataset import (
        freeze_financial_input,
        restore_financial_input,
    )

    view = derive(legacy_source, long_sources["scope"])
    pub, parts, reader = publication(view, long_sources)
    ref = {
        "datasetId": str(UUID(int=300)),
        "datasetRoot": pub.dataset_root,
        "format": "atlas.quant.research_dataset",
        "version": 2,
    }
    config = strategy(long_sources)
    snapshot = freeze_financial_input(
        config, pub.result, ref, manifest_bytes=pub.manifest_bytes
    )
    assert snapshot["sourceEvidenceClosure"] == "separate_research_dataset_v2"
    restored = restore_financial_input(
        config, encode(snapshot), reader, long_sources["registry"]
    )
    pd.testing.assert_frame_equal(restored.data, pub.result.data, check_exact=True)
    for mutation in ("version", "closure"):
        bad = deepcopy(snapshot)
        if mutation == "version":
            bad["datasetRef"]["version"] = 1
        else:
            bad["sourceEvidenceClosure"] = "separate_research_dataset_v1"
        with pytest.raises(DatasetError):
            restore_financial_input(
                config, encode(bad), reader, long_sources["registry"]
            )
    with pytest.raises(DatasetError):
        freeze_financial_input(
            config, pub.result, {**ref, "version": 1}, manifest_bytes=pub.manifest_bytes
        )
