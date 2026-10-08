"""Offline typed source closure acceptance. Synthetic inputs; zero provider/F fit."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pandas as pd
import pytest

from atlas_quant.engine import _prepare_data, ResearchError
from atlas_quant.fixtures import make_demo_data
from atlas_quant.financial_statements.package import (
    _encoded,
    prepare_package,
    freeze_package,
)
from atlas_quant.financial_statements.admission import assert_composed
from test_financial_statements import calendar
from atlas_quant.financial_statements.prepare import _safe_rows
from atlas_quant.financial_statements.recipes import RECIPES
from atlas_quant.research_dataset import (
    DatasetError,
    DatasetProfile,
    FinancialSource,
    DatasetReader,
    DirectoryDatasetReader,
    compose_dataset_components,
    restore_dataset,
    restore_dataset_for_research,
    export_dataset_archive,
    extract_dataset_archive,
    freeze_financial_input,
    restore_financial_input,
)
from atlas_quant.research_dataset.codec import decode, encode, sha
from atlas_quant.research_dataset.manifest import component_root, validate_manifest
from atlas_quant.runner_artifacts import restore_input
from test_financial_adapter import run, STRATEGY
from test_financial_package import freeze, declarations
from test_financial_publication import task_inputs, document_package


@pytest.fixture(scope="module")
def sources():
    acquired = run()
    package = freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )
    _, meta, package_raw, registry = task_inputs(package)
    frame, provenance = make_demo_data(STRATEGY)
    # Preserve both declared numeric types, including a floating signed zero.
    rows = _safe_rows(frame)
    rows[0]["vol"] = 0
    rows[1]["vol"] = -0.0
    market = {"schemaVersion": 1, "rows": rows, "provenance": provenance}
    source = FinancialSource(
        package_raw,
        prepare_package(package).provenance["preparedRoot"],
        meta["calendar"]["ref"],
    )
    return {
        "scope": deepcopy(STRATEGY["universe"]),
        "market": market,
        "source": source,
        "registry": registry,
        "calendar": source.calendar_ref,
        "acquired": acquired,
    }


@pytest.fixture(scope="module")
def long_sources(sources):
    # A separate predeclared synthetic source for fingerprint-only checks. No fit
    # occurs, and the six-day example remains explicitly insufficient for F.
    scope = {**sources["scope"], "start": "20240101", "end": "20241231"}
    acquired = sources["acquired"]
    package = freeze_package(
        acquired.snapshots,
        calendar(),
        {"universe": scope},
        list(RECIPES),
        declarations(acquired),
        announcement_start="20210101",
        source_kind="fixture",
        source_provider="HAND_FAKE_PROVIDER",
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )
    _, meta, raw, registry = task_inputs(package)
    frame, provenance = make_demo_data({"universe": scope})
    return {
        "scope": scope,
        "market": {
            "schemaVersion": 1,
            "rows": _safe_rows(frame),
            "provenance": provenance,
        },
        "source": FinancialSource(
            raw,
            prepare_package(package).provenance["preparedRoot"],
            meta["calendar"]["ref"],
        ),
        "registry": registry,
        "calendar": meta["calendar"]["ref"],
        "acquired": acquired,
    }


def compose(sources, **overrides):
    parts = {}

    def write(name, ordinal, raw):
        assert (name, ordinal) not in parts
        parts[(name, ordinal)] = raw

    publication = compose_dataset_components(
        overrides.get("scope", sources["scope"]),
        encode(overrides.get("market", sources["market"])),
        [overrides.get("source", sources["source"])],
        overrides.get("registry", sources["registry"]),
        write,
        market_calendar_ref=sources["calendar"],
        profile=overrides.get("profile", DatasetProfile()),
    )
    return publication, parts


def reader(publication, parts, **kwargs):
    return DatasetReader(
        publication.manifest_bytes,
        lambda name, ordinal: parts[(name, ordinal)],
        expected_root=publication.dataset_root,
        **kwargs,
    )


def strategy(sources):
    return {
        "schemaVersion": 2,
        "name": "Explicit synthetic dataset closure",
        "universe": deepcopy(sources["scope"]),
        "research": {"mode": "statistical_quant"},
        "target": {"kind": "asset_price", "horizonSessions": 5},
        "model": {"family": "fundamental", "estimator": "ridge"},
        "execution": {"enabled": False},
        "factors": [
            {"id": key, "expression": key, "role": "predictor"} for key in RECIPES
        ],
    }


def test_all_sixteen_states_and_full_lineage_survive_fresh_recomposition(sources):
    publication, parts = compose(sources)
    typed = reader(publication, parts)
    assert typed.verify_integrity()["sourceAuthorityVerified"] is False
    result = restore_dataset_for_research(strategy(sources), typed, sources["registry"])
    pd.testing.assert_frame_equal(
        result.data, publication.result.data, check_exact=True
    )
    assert result.provenance == publication.result.provenance
    assert len([c for c in result.data if c in RECIPES]) == 16
    assert (
        result.financial_artifacts[0].prepared.state_events
        == publication.result.financial_artifacts[0].prepared.state_events
    )
    commitment = assert_composed(
        result.data,
        result.data.copy(),
        result.provenance,
        set(RECIPES),
        strategy(sources),
    )
    assert (
        commitment["financialDatasetRoot"] == result.provenance["financialDatasetRoot"]
    )
    with pytest.raises(ResearchError) as insufficient:
        _prepare_data(result.data, strategy(sources), result.provenance)
    assert insufficient.value.code == "INSUFFICIENT_DATA"
    with pytest.raises(ResearchError, match="重新 compose"):
        _prepare_data(result.data.copy(), strategy(sources), result.provenance)


def test_float_integer_types_and_negative_zero_keep_original_source_bytes(sources):
    publication, parts = compose(sources)
    typed = reader(publication, parts)
    raw = typed.payload("marketDataset")
    assert raw == encode(sources["market"])
    value = decode(raw, len(raw))
    assert type(value["rows"][0]["vol"]) is int
    assert type(value["rows"][1]["vol"]) is float
    assert b'"vol":-0.0' in raw
    assert typed.payload("financialInput0") == sources["source"].package_bytes
    changed = deepcopy(sources["market"])
    changed["rows"][0]["vol"] = 0.0
    other, _ = compose(sources, market=changed)
    assert other.dataset_root != publication.dataset_root
    assert (
        other.result.provenance["marketRoot"]
        != publication.result.provenance["marketRoot"]
    )


@pytest.mark.parametrize(
    "mutation",
    ["extra", "missing", "prefilled", "calendar", "symbols", "date", "namespace"],
)
def test_market_dimension_calendar_and_namespace_are_exact(sources, mutation):
    market = deepcopy(sources["market"])
    if mutation == "extra":
        for row in market["rows"]:
            row["unknown_observation"] = 1
    elif mutation == "missing":
        del market["rows"][0]["vol"]
    elif mutation == "prefilled":
        for row in market["rows"]:
            row["model_fin_cash_asset_share"] = 0.2
    elif mutation == "calendar":
        market["provenance"]["tradingDates"].pop()
    elif mutation == "symbols":
        market["provenance"]["symbols"] = ["000001.SZ"]
    elif mutation == "date":
        market["provenance"]["end"] = "20240502"
    else:
        market["provenance"]["financialDatasetRoot"] = "a" * 64
    with pytest.raises(ValueError):
        compose(sources, market=market)


def test_calendar_authorization_is_external_and_exact(sources):
    publication, parts = compose(sources)
    typed = reader(publication, parts)
    for registry in ({}, {sources["calendar"]: b"{}"}):
        with pytest.raises(DatasetError, match="authorize itself"):
            restore_dataset(typed, registry)
    record = json.loads(sources["registry"][sources["calendar"]])
    record["payload"]["evidence_reference"] += " CHANGED"
    with pytest.raises(ValueError):
        compose(sources, registry={sources["calendar"]: encode(record)})


def test_reviewed_proof_payload_is_closed_and_cannot_self_authorize(sources):
    package = document_package(sources["acquired"])
    _, meta, raw, registry = task_inputs(package)
    source = FinancialSource(
        raw,
        prepare_package(package, trusted_unit_proofs=True).provenance["preparedRoot"],
        meta["calendar"]["ref"],
        tuple(sorted(p["ref"] for p in meta["proofs"])),
    )
    publication, parts = compose(sources, source=source, registry=registry)
    result = restore_dataset(reader(publication, parts), registry)
    assert result.financial_artifacts[0].package["packRoot"] == package["packRoot"]
    with pytest.raises(DatasetError):
        restore_dataset(reader(publication, parts), sources["registry"])
    changed = dict(registry)
    proof = json.loads(changed[source.proof_refs[0]])
    proof["payload"]["value"]["document_hash"] = "0" * 64
    changed[source.proof_refs[0]] = encode(proof)
    with pytest.raises(ValueError):
        compose(sources, source=source, registry=changed)


def test_preparation_pin_and_exact_financial_scope_cannot_be_replaced(sources):
    with pytest.raises(DatasetError, match="preparation"):
        compose(sources, source=replace(sources["source"], prepared_root="0" * 64))
    scope = deepcopy(sources["scope"])
    scope["end"] = "20240502"
    with pytest.raises(ValueError):
        compose(sources, scope=scope)


def resign_payload(manifest, parts, name, value):
    """Attacker recomputes all transport identities; semantic checks must still fail."""
    manifest = deepcopy(manifest)
    parts = dict(parts)
    item = next(c for c in manifest["components"] if c["componentId"] == name)
    assert len(item["parts"]) == 1
    raw = encode(value)
    parts[(name, 0)] = raw
    item["payloadSha256"] = sha(raw)
    item["byteLength"] = len(raw)
    item["parts"][0].update(sha256=sha(raw), byteLength=len(raw))
    mapping = {}
    for component in manifest["components"]:
        old = component["componentRoot"]
        component["dependencies"] = sorted(
            mapping.get(d, d) for d in component["dependencies"]
        )
        component["componentRoot"] = component_root(component)
        mapping[old] = component["componentRoot"]
    return encode(manifest), parts


@pytest.mark.parametrize(
    "name", ["researchRows", "financialPrepared0", "schema", "coverage"]
)
def test_rehashed_derived_forgery_never_inherits_admission(sources, name):
    publication, parts = compose(sources)
    typed = reader(publication, parts)
    value = json.loads(typed.payload(name))
    if name == "researchRows":
        value["rows"][-1]["model_fin_cash_asset_share"] += 0.1
    elif name == "financialPrepared0":
        value["panel"][-1]["model_fin_cash_asset_share"] += 0.1
    elif name == "schema":
        value["columns"].reverse()
    else:
        value["marketRows"] += 1
    raw, poisoned = resign_payload(typed.manifest, parts, name, value)
    malicious = DatasetReader(raw, lambda n, i: poisoned[(n, i)])
    assert malicious.verify_integrity()["transportVerified"] is True
    with pytest.raises(DatasetError) as exc:
        restore_dataset(malicious, sources["registry"])
    assert exc.value.code in {"DATASET_RECOMPUTATION", "DATASET_PART"}


@pytest.mark.parametrize(
    "mutation",
    ["missing", "cycle", "unknown", "bool", "extra", "root", "zero", "scope"],
)
def test_manifest_rejects_structural_loopholes_before_parts(sources, mutation):
    publication, _ = compose(sources)
    manifest = json.loads(publication.manifest_bytes)
    if mutation == "missing":
        manifest["components"].pop()
    elif mutation == "cycle":
        manifest["components"][0]["dependencies"] = [
            manifest["components"][-1]["componentRoot"]
        ]
    elif mutation == "unknown":
        manifest["components"][0]["type"] = "pickle"
    elif mutation == "bool":
        manifest["version"] = True
    elif mutation == "extra":
        manifest["trusted"] = True
    elif mutation == "root":
        manifest["roots"]["financialDatasetRoot"] = "0" * 64
    elif mutation == "zero":
        manifest["components"][0]["parts"][0]["byteLength"] = 0
    else:
        manifest["scope"]["symbols"] *= 2
    with pytest.raises(ValueError):
        DatasetReader(
            encode(manifest), lambda *a: pytest.fail("Malformed manifest read a part")
        )


def test_known_budget_rejection_before_compute_or_writes(sources, monkeypatch):
    from atlas_quant.research_dataset import compose as module

    monkeypatch.setattr(
        module,
        "compose_financial_dataset",
        lambda *a, **k: pytest.fail("Budget should fail before composition"),
    )
    with pytest.raises(DatasetError):
        compose(sources, profile=DatasetProfile(total_bytes=1024))
    with pytest.raises(DatasetError):
        DatasetProfile(max_symbols=51)


def test_parts_and_parent_budget_do_not_return_incomplete_publication(sources):
    with pytest.raises(DatasetError):
        compose(sources, profile=DatasetProfile(part_bytes=100, max_parts=1))
    with pytest.raises(DatasetError):
        compose(sources, profile=DatasetProfile(max_components=6))


def test_new_snapshot_keeps_every_column_and_old_snapshot_reader_rejects_it(
    long_sources,
):
    sources = long_sources
    publication, parts = compose(sources)
    reference = {
        "datasetId": str(UUID(int=70)),
        "datasetRoot": publication.dataset_root,
        "format": "atlas.quant.research_dataset",
        "version": 1,
    }
    config = strategy(sources)
    snapshot = freeze_financial_input(
        config, publication.result, reference, manifest_bytes=publication.manifest_bytes
    )
    assert snapshot["schemaVersion"] == 2
    assert set(snapshot["rows"][0]) == set(publication.result.data.columns)
    restored = restore_financial_input(
        config, encode(snapshot), reader(publication, parts), sources["registry"]
    )
    pd.testing.assert_frame_equal(
        restored.data, publication.result.data, check_exact=True
    )
    with pytest.raises(ValueError):
        restore_input(config, snapshot)
    snapshot["rows"][-1]["model_fin_cash_asset_share"] += 0.1
    with pytest.raises(DatasetError):
        restore_financial_input(
            config, encode(snapshot), reader(publication, parts), sources["registry"]
        )


@pytest.mark.parametrize(
    "change", ["execution", "legacy", "family", "estimator", "subset"]
)
def test_research_profile_never_opens_execution_or_other_modes(sources, change):
    publication, parts = compose(sources)
    config = strategy(sources)
    if change == "execution":
        config["execution"]["enabled"] = True
    elif change == "legacy":
        config["schemaVersion"] = 1
    elif change == "family":
        config["model"]["family"] = "trend"
    elif change == "estimator":
        config["model"]["estimator"] = "auto"
    else:
        config["universe"]["start"] = "20240429"
    with pytest.raises(ValueError):
        restore_dataset_for_research(
            config, reader(publication, parts), sources["registry"]
        )


def test_archive_roundtrip_new_process_and_no_replace(sources, tmp_path):
    publication, parts = compose(sources)
    archive = tmp_path / "source.tar"
    export_dataset_archive(reader(publication, parts), archive)
    directory = tmp_path / "dataset"
    result = extract_dataset_archive(
        archive, directory, expected_root=publication.dataset_root
    )
    assert result["transportVerified"] and not result["sourceAuthorityVerified"]
    assert (archive.stat().st_mode & 0o777) == 0o600
    assert (directory.stat().st_mode & 0o777) == 0o700
    with pytest.raises(DatasetError):
        extract_dataset_archive(archive, directory)
    registry = tmp_path / "authorized.json"
    registry.write_bytes(
        encode({k: v.decode() for k, v in sources["registry"].items()})
    )
    config = tmp_path / "strategy.json"
    config.write_bytes(encode(strategy(sources)))
    code = """import json,sys
from atlas_quant.research_dataset import DirectoryDatasetReader,restore_dataset_for_research
from atlas_quant.engine import _prepare_data,ResearchError
from atlas_quant.financial_statements.admission import assert_composed
r=DirectoryDatasetReader(sys.argv[1]); a={k:v.encode() for k,v in json.load(open(sys.argv[2])).items()}; s=json.load(open(sys.argv[3]))
x=restore_dataset_for_research(s,r,a)
commitment=assert_composed(x.data,x.data.copy(),x.provenance,{f["expression"] for f in s["factors"]},s)
try:
    _prepare_data(x.data,s,x.provenance)
except ResearchError as e:
    assert e.code=="INSUFFICIENT_DATA"
else:
    raise AssertionError("six days must remain insufficient")
print(json.dumps({"root":x.provenance["financialDatasetRoot"],"rows":len(x.data),"commitment":commitment["financialDatasetRoot"]}))
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(directory), str(registry), str(config)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
    )
    assert child.returncode == 0, child.stderr
    value = json.loads(child.stdout)
    assert (
        value["root"]
        == value["commitment"]
        == publication.result.provenance["financialDatasetRoot"]
    )
    assert value["rows"] == len(publication.result.data)


@pytest.mark.parametrize(
    "mutation", ["truncated", "tail", "padding", "symlink", "pax", "path", "duplicate"]
)
def test_strict_archive_rejects_mutations_without_publishing(
    sources, tmp_path, mutation
):
    publication, parts = compose(sources)
    archive = tmp_path / "source.tar"
    export_dataset_archive(reader(publication, parts), archive)
    raw = bytearray(archive.read_bytes())
    if mutation == "truncated":
        del raw[-1025:]
    elif mutation == "tail":
        raw += b"x"
    elif mutation == "padding":
        raw[512 + len(publication.manifest_bytes)] = 1
    elif mutation == "symlink":
        raw[156] = ord("2")
    elif mutation == "pax":
        raw[156] = ord("x")
    elif mutation == "path":
        raw[:16] = b"../manifest.json"
    else:
        raw = raw[:-1024] + raw[:512] + raw[-1024:]
    archive.write_bytes(raw)
    output = tmp_path / "output"
    with pytest.raises(DatasetError):
        extract_dataset_archive(archive, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".atlas-dataset-extract-*"))


def test_fragmented_components_parent_budget_and_missing_parts(sources):
    profile = DatasetProfile(part_bytes=8192)
    publication, parts = compose(sources, profile=profile)
    typed = reader(publication, parts, profile=profile)
    assert len(parts) > len(typed.components)
    typed.verify_integrity()
    total = len(publication.manifest_bytes) + sum(len(raw) for raw in parts.values())
    validate_manifest(
        publication.manifest_bytes, profile=replace(profile, total_bytes=total)
    )
    with pytest.raises(DatasetError):
        validate_manifest(
            publication.manifest_bytes, profile=replace(profile, total_bytes=total - 1)
        )
    missing = dict(parts)
    del missing[next(iter(missing))]
    with pytest.raises(DatasetError) as error:
        reader(publication, missing, profile=profile).verify_integrity()
    assert error.value.code == "DATASET_PART_MISSING"
    parts[next(iter(parts))] += b"x"
    with pytest.raises(DatasetError):
        typed.verify_integrity()


def test_two_packages_have_exact_disjoint_states_and_original_roots(sources):
    from atlas_quant.financial_statements.results import canonical_hash

    packages = []
    original = json.loads(sources["source"].package_bytes)
    names = original["selection"]["selectedStates"]
    for selected in (names[:8], names[8:]):
        package = deepcopy(original)
        package["selection"]["selectedStates"] = selected
        package["packRoot"] = canonical_hash(
            {k: v for k, v in package.items() if k != "packRoot"}
        )
        prepared = prepare_package(package)
        packages.append(
            FinancialSource(
                encode(package),
                prepared.provenance["preparedRoot"],
                sources["calendar"],
            )
        )
    parts = {}
    publication = compose_dataset_components(
        sources["scope"],
        encode(sources["market"]),
        packages,
        sources["registry"],
        lambda n, i, r: parts.__setitem__((n, i), r),
        market_calendar_ref=sources["calendar"],
    )
    result = restore_dataset(reader(publication, parts), sources["registry"])
    assert len(result.financial_artifacts) == 2
    assert len([c for c in result.data if c in RECIPES]) == 16
    assert sorted(json.loads(p.package_bytes)["packRoot"] for p in packages) == [
        a.package["packRoot"] for a in result.financial_artifacts
    ]
    with pytest.raises(ValueError):
        compose_dataset_components(
            sources["scope"],
            encode(sources["market"]),
            [sources["source"], packages[0]],
            sources["registry"],
            lambda *a: pytest.fail("Overlapping sources wrote evidence"),
            market_calendar_ref=sources["calendar"],
        )


def test_strict_all_missing_is_preserved_without_training_claim(sources):
    package = freeze(
        sources["acquired"],
        declarations(sources["acquired"]),
        trusted_unit_proofs=False,
    )
    source = FinancialSource(
        encode(package),
        prepare_package(package).provenance["preparedRoot"],
        sources["calendar"],
    )
    publication, parts = compose(sources, source=source)
    result = restore_dataset(reader(publication, parts), sources["registry"])
    assert result.data[list(RECIPES)].isna().all().all()
    assert all(
        state["okRows"] == 0
        for state in result.financial_artifacts[0].summary["securities"][0]["states"]
    )
    assert len(result.financial_artifacts[0].prepared.state_events) > 0


def test_serialized_authority_cannot_expand_limits_or_upgrade_proofs(sources):
    class ForgedProfile:
        max_symbols = 999

    with pytest.raises(DatasetError):
        compose(sources, profile=ForgedProfile())
    scope = deepcopy(sources["scope"])
    scope["symbols"] = [f"{i:06}.SH" for i in range(51)]
    with pytest.raises(DatasetError):
        compose(sources, scope=scope)
    package = freeze(sources["acquired"])
    source = FinancialSource(
        encode(package), sources["source"].prepared_root, sources["calendar"]
    )
    with pytest.raises(ValueError):
        compose(sources, source=source)


def test_snapshot_rejects_reference_swap_and_copied_object(long_sources):
    publication, parts = compose(long_sources)
    reference = {
        "datasetId": str(UUID(int=71)),
        "datasetRoot": publication.dataset_root,
        "format": "atlas.quant.research_dataset",
        "version": 1,
    }
    for mutation in ("root", "copy"):
        ref, result = deepcopy(reference), publication.result
        if mutation == "root":
            ref["datasetRoot"] = "0" * 64
        else:
            result = replace(result, data=result.data.copy())
        with pytest.raises(ValueError):
            freeze_financial_input(
                strategy(long_sources),
                result,
                ref,
                manifest_bytes=publication.manifest_bytes,
            )


def test_archive_cannot_replace_a_racing_destination(sources, tmp_path, monkeypatch):
    import atlas_quant.research_dataset.archive as module

    publication, parts = compose(sources)
    archive = tmp_path / "source.tar"
    export_dataset_archive(reader(publication, parts), archive)
    destination = tmp_path / "out"
    real = module._publish

    def race(source, output):
        output.mkdir()
        (output / "retained.txt").write_text("Concurrent user artifact")
        return real(source, output)

    monkeypatch.setattr(module, "_publish", race)
    with pytest.raises(DatasetError):
        extract_dataset_archive(archive, destination)
    assert (destination / "retained.txt").read_text() == "Concurrent user artifact"
    assert not list(tmp_path.glob(".atlas-dataset-extract-*"))


def test_reconstructed_object_still_cannot_execute_financial_forecasts(sources):
    from atlas_quant.statistical_quant.core import execute_forecasts

    publication, parts = compose(sources)
    result = restore_dataset(reader(publication, parts), sources["registry"])
    with pytest.raises(ResearchError) as error:
        execute_forecasts(strategy(sources), result.data, {}, result.provenance)
    assert error.value.code == "FINANCIAL_REPLAY_NOT_AVAILABLE"


def test_interrupted_staging_returns_no_manifest_or_implicit_retry(sources):
    calls = []

    def interrupted(name, ordinal, raw):
        calls.append((name, ordinal))
        if len(calls) == 2:
            raise OSError("explicit staging interruption")

    with pytest.raises(OSError, match="staging interruption"):
        compose_dataset_components(
            sources["scope"],
            encode(sources["market"]),
            [sources["source"]],
            sources["registry"],
            interrupted,
            market_calendar_ref=sources["calendar"],
        )
    assert len(calls) == 2


def test_directory_symlink_and_unregistered_files_are_not_read(sources, tmp_path):
    publication, parts = compose(sources)
    archive = tmp_path / "source.tar"
    export_dataset_archive(reader(publication, parts), archive)
    directory = tmp_path / "dataset"
    extract_dataset_archive(archive, directory)
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(DatasetError):
        DirectoryDatasetReader(alias)
    extra = directory / "surprise.json"
    extra.write_text("{}")
    with pytest.raises(DatasetError):
        DirectoryDatasetReader(directory)
    extra.unlink()
    first = next((directory / "parts").glob("*/*.bin"))
    moved = tmp_path / "real.bin"
    first.rename(moved)
    first.symlink_to(moved)
    with pytest.raises(OSError):
        DirectoryDatasetReader(directory).verify_integrity()


def test_tiny_part_budget_rejects_before_materializing_descriptor_list(
    sources, monkeypatch
):
    from atlas_quant.research_dataset import compose as module

    original = module.sha
    hashes = []

    def counted(raw):
        hashes.append(len(raw))
        return original(raw)

    monkeypatch.setattr(module, "sha", counted)
    with pytest.raises(DatasetError):
        compose(sources, profile=DatasetProfile(part_bytes=1, max_parts=256))
    # Registry construction hashes one fixed record; no per-byte part descriptors.
    assert len(hashes) < 10


def test_public_descriptor_edits_cannot_retarget_reader_or_export(sources, tmp_path):
    publication, parts = compose(sources)
    typed = reader(publication, parts)
    root, original_manifest = typed.dataset_root, typed.manifest_bytes
    forged = parts[("marketDataset", 0)].replace(b'"vol":-0.0', b'"vol":-1.0')
    assert forged != parts[("marketDataset", 0)]
    view = typed.components
    view["marketDataset"]["parts"][0]["sha256"] = sha(forged)
    view["marketDataset"]["payloadSha256"] = sha(forged)
    manifest_view = typed.manifest
    next(c for c in manifest_view["components"] if c["componentId"] == "marketDataset")[
        "payloadSha256"
    ] = sha(forged)
    assert typed.verify_integrity()["transportVerified"] is True
    assert typed.dataset_root == root and typed.manifest_bytes == original_manifest
    parts[("marketDataset", 0)] = forged
    with pytest.raises(DatasetError):
        typed.verify_integrity()
    destination = tmp_path / "bad.tar"
    with pytest.raises(DatasetError):
        export_dataset_archive(typed, destination)
    assert not destination.exists()
    for name, value in (
        ("dataset_root", "0" * 64),
        ("manifest_bytes", b"{}"),
        ("components", {}),
        ("manifest", {}),
    ):
        with pytest.raises(AttributeError):
            setattr(typed, name, value)
    with pytest.raises(TypeError):
        typed._components["marketDataset"]["parts"][0]["sha256"] = sha(forged)


def test_public_projection_cannot_hide_a_missing_coverage_component(sources):
    publication, parts = compose(sources)
    typed = reader(publication, parts)
    projection = typed.components
    del projection["coverage"]
    assert typed.verify_integrity()["components"] == len(typed.components)
    for key in tuple(parts):
        if key[0] == "coverage":
            del parts[key]
    with pytest.raises(DatasetError) as error:
        typed.verify_integrity()
    assert error.value.code == "DATASET_PART_MISSING"
