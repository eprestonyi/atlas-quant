"""Actual synthetic financial F, typed dataset/2 and independent source audit.

The source legacy bundle is explicitly a transport fixture, not a historical
validated study. No real-market data, provider or execution is used.
"""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from atlas_quant.engine import run_research
from atlas_quant.financial_bundle import (
    FinancialBundleReader,
    build_financial_bundle,
    export_financial_bundle,
    financial_directory_reader,
)
from atlas_quant.research_dataset import freeze_financial_input, export_dataset_archive
from atlas_quant.research_dataset.codec import encode
from test_research_dataset_components import sources, long_sources, strategy
from test_snapshot_market_view import legacy_source, derive, publication

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from financial_bundle_audit import audit_financial_bundle


@pytest.fixture(scope="module")
def version_two(long_sources, legacy_source):
    view = derive(legacy_source, long_sources["scope"])
    dataset, parts, reader = publication(view, long_sources)
    config = strategy(long_sources)
    config["model"].update(trainWindow=120, refitDays=60)
    config["validation"] = {"minTrainDates": 40, "innerFolds": 2, "outerFolds": 2}
    ref = {
        "datasetId": "00000000-0000-0000-0000-000000000082",
        "datasetRoot": dataset.dataset_root,
        "format": "atlas.quant.research_dataset",
        "version": 2,
    }
    plans = []
    report = run_research(
        config,
        dataset.result.data,
        dataset.result.provenance,
        forecast_plan_sink=plans.append,
    )
    snapshot = encode(
        freeze_financial_input(
            config, dataset.result, ref, manifest_bytes=dataset.manifest_bytes
        )
    )
    chunks = {}
    raw = build_financial_bundle(
        report,
        snapshot,
        plans[0],
        {"datasetRef": ref, "admissionProfile": "financial_snapshot_view_50_v1"},
        lambda c, n, r: chunks.__setitem__((c, n), r),
        lambda c, n: chunks[c, n],
    )
    return (
        FinancialBundleReader(raw, lambda c, n: chunks[c, n]),
        reader,
        long_sources["registry"],
        report,
    )


def test_registered_v2_snapshot_uses_exact_version_and_recomposes_same_source(
    version_two,
):
    bundle, dataset, pins, report = version_two
    assert bundle.verify_integrity()["transportVerified"]
    snapshot = json.loads(bundle.snapshot_bytes())
    assert snapshot["sourceEvidenceClosure"] == "separate_research_dataset_v2"
    assert snapshot["datasetRef"]["version"] == 2
    assert bundle.manifest["forecastArtifactId"] == report["forecasts"]["artifactId"]
    restored = bundle.restore_sources(dataset, pins)
    assert encode(restored.to_dataset()) == dataset.payload("researchRows")


def test_full_v2_independent_audit_needs_retained_market_origin(version_two, tmp_path):
    bundle, dataset, pins, report = version_two
    folder = tmp_path / "bundle"
    export_financial_bundle(bundle, folder)
    source = tmp_path / "dataset.tar"
    export_dataset_archive(dataset, source)
    result = audit_financial_bundle(folder, source_dataset=source, registry_pins=pins)
    assert result["status"] == "PASS"
    assert result["sourceEvidenceClosed"]
    assert result["sourceViewProjectionVerified"] is True
    assert result["sourceResearchFingerprintRecomputed"] is False
    assert result["registryTrustStatus"] == "external_registry_bytes_matched"
    assert result["forecastRows"] == len(report["forecasts"]["rows"])
    assert (
        financial_directory_reader(folder).snapshot_bytes() == bundle.snapshot_bytes()
    )
