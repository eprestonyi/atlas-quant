"""Synthetic financial source/forecast fixture; no provider or real-market fit."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from atlas_quant import bundle as legacy
from atlas_quant.engine import run_research
from atlas_quant.financial_bundle import (
    CODECS,
    FinancialBundleReader,
    build_financial_bundle,
    decode_exact,
    validate_manifest,
)
from atlas_quant.research_dataset.codec import encode
from atlas_quant.research_dataset import freeze_financial_input
from atlas_quant.runner import RunnerError
from atlas_quant.statistical_quant.schema import digest
from test_research_dataset_components import (
    sources,
    long_sources,
    compose,
    reader as dataset_reader,
    strategy,
)


@pytest.fixture(scope="module")
def financial_research(long_sources):
    source = deepcopy(long_sources)
    source["market"]["provenance"]["numericCodecWitness"] = {
        "integer": 1,
        "floating": 1.0,
        "positiveZero": 0.0,
        "negativeZero": -0.0,
        "null": None,
    }
    publication, parts = compose(source)
    config = strategy(source)
    config["model"].update(trainWindow=120, refitDays=60)
    config["validation"] = {"minTrainDates": 40, "innerFolds": 2, "outerFolds": 2}
    reference = {
        "datasetId": "00000000-0000-0000-0000-000000000070",
        "datasetRoot": publication.dataset_root,
        "format": "atlas.quant.research_dataset",
        "version": 1,
    }
    plans = []
    result = run_research(
        config,
        publication.result.data,
        publication.result.provenance,
        forecast_plan_sink=plans.append,
    )
    snapshot = freeze_financial_input(
        config, publication.result, reference, manifest_bytes=publication.manifest_bytes
    )
    return {
        "report": result,
        "snapshot": encode(snapshot),
        "coverage": plans[0],
        "sourceEvidence": {
            "datasetRef": reference,
            "admissionProfile": "financial_compose_50_v1",
        },
        "publication": publication,
        "parts": parts,
        "registry": source["registry"],
    }


def pack(fixture, *, target=legacy.CHUNK_TARGET):
    chunks = {}
    raw = build_financial_bundle(
        fixture["report"],
        fixture["snapshot"],
        fixture["coverage"],
        fixture["sourceEvidence"],
        lambda c, n, value: chunks.__setitem__((c, n), value),
        lambda c, n: chunks[(c, n)],
        chunk_target=target,
    )
    return raw, chunks, FinancialBundleReader(raw, lambda c, n: chunks[(c, n)])


def test_complete_synthetic_forecasts_keep_identity_and_typed_snapshot(
    financial_research,
):
    raw, chunks, result = pack(financial_research)
    verdict = result.verify_integrity()
    assert verdict["transportVerified"] is True
    assert verdict["sourceEvidenceClosed"] is False
    assert verdict["status"] == "INCOMPLETE_SOURCE"
    assert (
        result.manifest["forecastArtifactId"]
        == financial_research["report"]["forecasts"]["artifactId"]
    )
    assert result.snapshot_bytes() == financial_research["snapshot"]
    assert b'"floating":1.0' in result.snapshot_bytes()
    assert b'"negativeZero":-0.0' in result.snapshot_bytes()
    assert b'"positiveZero":0.0' in result.snapshot_bytes()
    assert result.document("report") == financial_research["report"]
    assert set(result.collections) == set(legacy.COLLECTIONS)
    assert all(len(raw) <= legacy.CHUNK_LIMIT for raw in chunks.values())
    assert {k: v["codec"] for k, v in result.manifest["documents"].items()} == CODECS


def test_source_sidecar_recomposes_exact_data_without_execution(financial_research):
    result = pack(financial_research)[2]
    dataset = dataset_reader(
        financial_research["publication"], financial_research["parts"]
    )
    restored = result.restore_sources(dataset, financial_research["registry"])
    assert encode(restored.provenance) == encode(
        financial_research["publication"].result.provenance
    )
    with pytest.raises(ValueError):
        result.restore_sources(dataset, {})


def test_old_reader_rejects_new_format_and_old_numeric_rewrite_is_detectable(
    financial_research,
):
    raw, chunks, result = pack(financial_research)
    with pytest.raises(RunnerError):
        legacy.BundleReader(raw, lambda c, n: chunks[(c, n)])
    rewritten = legacy.encode(json.loads(financial_research["snapshot"]))
    assert rewritten != financial_research["snapshot"]
    # Rewriting exact source bytes is not rescued by preserving transport IDs.
    assert legacy.sha(rewritten) != result.manifest["documents"]["snapshot"]["sha256"]


def test_chunk_size_only_changes_transport_identity(financial_research):
    first = pack(financial_research, target=32768)[2]
    second = pack(financial_research, target=65536)[2]
    assert first.bundle_id != second.bundle_id
    assert first.manifest["forecastArtifactId"] == second.manifest["forecastArtifactId"]
    assert first.snapshot_bytes() == second.snapshot_bytes()


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(kind="execution"),
        lambda m: m.update(version=True),
        lambda m: m.update(format="atlas.quant.bundle"),
        lambda m: m["documents"]["snapshot"].update(codec="forecast_json_v1"),
        lambda m: m["documents"]["forecast"].update(codec="financial_json_v1"),
        lambda m: m["sourceEvidence"].update(manifestSha256="a" * 64),
        lambda m: m["sourceEvidence"].update(admissionProfile="invented"),
        lambda m: m["sourceEvidence"]["datasetRef"].update(version=True),
        lambda m: m["sourceEvidence"]["datasetRef"].update(datasetId="foreign-url"),
        lambda m: m["collections"][0].update(path="/other"),
    ],
)
def test_registered_format_layout_and_codec_cannot_be_replaced(
    financial_research, change
):
    manifest = json.loads(pack(financial_research)[0])
    change(manifest)
    with pytest.raises(RunnerError):
        validate_manifest(legacy.encode(manifest))


def test_mutating_public_metadata_cannot_retarget_internal_hashes(financial_research):
    _, chunks, result = pack(financial_research)
    item = result.manifest["collections"][0]
    key = (item["id"], 0)
    chunks[key] = chunks[key].replace(b'"', b" ", 1)
    item["chunks"][0]["sha256"] = legacy.sha(chunks[key])
    result.collections[item["id"]]["chunks"][0]["sha256"] = legacy.sha(chunks[key])
    with pytest.raises(RunnerError):
        result.verify_hashes()
    with pytest.raises(AttributeError):
        result.manifest_raw = b"{}"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":1e999}',
        b'{"x":NaN}',
        b'{"x":"\\ud800"}',
        b"[" * 65 + b"]" * 65,
    ],
)
def test_json_canonical_finite_unicode_and_depth_are_bounded(raw):
    with pytest.raises(ValueError):
        decode_exact(raw, 10000, financial=True)


def test_deleted_forecast_cannot_be_hidden_by_rehashing(financial_research):
    fixture = deepcopy(financial_research)
    artifact = fixture["report"]["forecasts"]
    artifact["rows"].pop()
    artifact["totalRows"] -= 1
    artifact["artifactId"] = digest(
        {k: v for k, v in artifact.items() if k != "artifactId"}
    )
    fixture["report"]["execution"]["forecastArtifactId"] = artifact["artifactId"]
    with pytest.raises(RunnerError) as error:
        pack(fixture)
    assert error.value.code == "BUNDLE_COVERAGE"


def test_changed_source_reference_rejected_even_with_valid_manifest_hash(
    financial_research,
):
    raw, chunks, _ = pack(financial_research)
    value = json.loads(raw)
    value["sourceEvidence"]["datasetRef"]["datasetRoot"] = "a" * 64
    result = FinancialBundleReader(legacy.encode(value), lambda c, n: chunks[(c, n)])
    with pytest.raises(RunnerError) as error:
        result.verify_integrity()
    assert error.value.code == "FINANCIAL_BUNDLE_SOURCE"


def test_shared_codec_vectors_preserve_number_kind_and_python_exponent_spelling():
    path = Path(__file__).parents[2] / "tests/fixtures/financial-bundle-v1.json"
    fixture = json.loads(path.read_text())
    for case in fixture["valid"]:
        value = decode_exact(case["financial"].encode(), 10000, financial=True)
        assert encode(value) == case["financial"].encode()
        assert legacy.encode(value) == case["forecast"].encode()
    for raw in fixture["invalidFinancial"]:
        with pytest.raises(ValueError):
            decode_exact(raw.encode(), 10000, financial=True)


def test_snapshot_direct_read_checks_declared_bound_and_hash(financial_research):
    raw, chunks, reader = pack(financial_research)
    manifest = json.loads(raw)
    manifest["documents"]["snapshot"]["byteLength"] = 2
    changed = FinancialBundleReader(legacy.encode(manifest), lambda c, n: chunks[c, n])
    with pytest.raises(RunnerError) as error:
        changed.snapshot_bytes()
    assert error.value.code == "FINANCIAL_BUNDLE_BUDGET"
    manifest = json.loads(raw)
    manifest["documents"]["snapshot"]["sha256"] = "a" * 64
    changed = FinancialBundleReader(legacy.encode(manifest), lambda c, n: chunks[c, n])
    with pytest.raises(RunnerError) as error:
        changed.snapshot_bytes()
    assert error.value.code == "FINANCIAL_BUNDLE_INTEGRITY"
