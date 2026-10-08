"""Actual server USTAR bytes, strict hostile imports, and independent audit."""

import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile

import pytest

from atlas_quant.bundle import build_bundle
from atlas_quant.fixtures import make_demo_data
from atlas_quant.runner_artifacts import freeze_input
from atlas_quant.statistical_quant import execute_forecasts, validate_statistical_quant
from atlas_quant.statistical_quant.core import run_statistical_quant
from atlas_quant.statistical_quant.schema import digest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bundle_archive as archive


def export(directory, report, snapshot, plan):
    directory.mkdir()

    def write(collection, ordinal, raw):
        path = directory / "chunks" / collection / f"{ordinal}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def read(collection, ordinal):
        return (directory / "chunks" / collection / f"{ordinal}.json").read_bytes()

    manifest = build_bundle(report, snapshot, plan, write, read, chunk_target=32768)
    (directory / "manifest.json").write_bytes(manifest)
    return directory


def server_archive(directory, destination):
    # Exercise the actual JS exporter, not a Python duplicate of its encoding.
    script = """
      import fs from 'node:fs/promises';
      import path from 'node:path';
      import {Readable} from 'node:stream';
      import {pipeline} from 'node:stream/promises';
      import {archiveEntries, archiveStream} from './edge/bundles/archive.mjs';
      const root = process.argv[1];
      const manifest_text = await fs.readFile(path.join(root, 'manifest.json'), 'utf8');
      const manifest = JSON.parse(manifest_text);
      const parsed = {manifest, collections: new Map(manifest.collections.map(c => [c.id,c]))};
      const entries = archiveEntries({manifest_text}, parsed);
      const stream = archiveStream(entries, (collection,descriptor) =>
        fs.readFile(path.join(root,'chunks',collection,`${descriptor.ordinal}.json`)));
      await pipeline(Readable.fromWeb(stream), process.stdout);
    """
    with destination.open("wb") as output:
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script, str(directory)],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    assert completed.returncode == 0, completed.stderr.decode()
    return destination


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    root = tmp_path_factory.mktemp("real-bundle-archives")
    strategy = json.loads((ROOT / "engine/examples/statistical-quant.json").read_text())
    strategy["execution"]["enabled"] = False
    strategy = validate_statistical_quant(strategy)
    data, provenance = make_demo_data(strategy)
    plans = []
    forecast = run_statistical_quant(strategy, data, provenance, plan_sink=plans.append)
    snapshot = freeze_input(strategy, data, provenance)
    execution_strategy = copy.deepcopy(strategy)
    execution_strategy["execution"]["enabled"] = True
    execution = execute_forecasts(
        execution_strategy, data, forecast["forecasts"], provenance
    )
    original = export(root / "forecast", forecast, snapshot, plans[0])
    replay = export(root / "execution", execution, None, plans[0])
    return {
        "forecast": original,
        "execution": replay,
        "forecastTar": server_archive(original, root / "forecast.tar"),
        "executionTar": server_archive(replay, root / "execution.tar"),
        "report": forecast,
        "snapshot": snapshot,
        "plan": plans[0],
    }


def members(raw):
    result = []
    offset = 0
    while raw[offset : offset + 512] != bytes(512):
        info = tarfile.TarInfo.frombuf(raw[offset : offset + 512], "ascii", "strict")
        result.append((offset, info))
        offset += 512 + info.size + (-info.size) % 512
    return result


def fix_checksum(header):
    header[148:156] = b" " * 8
    header[148:156] = format(sum(header), "o").zfill(6).encode() + b"\0 "
    return bytes(header)


def rejected(tmp_path, raw, expected_code=None):
    source = tmp_path / "damaged.tar"
    source.write_bytes(raw)
    destination = tmp_path / "output"
    with pytest.raises((ValueError, OSError)) as caught:
        archive.extract_bundle_archive(source, destination)
    if expected_code:
        assert getattr(caught.value, "code", None) == expected_code
    assert not destination.exists()
    assert not list(tmp_path.glob(".atlas-bundle-extract-*"))
    return caught.value


def test_actual_server_archive_imports_as_private_verified_files(tmp_path, bundles):
    target = tmp_path / "imported"
    result = archive.extract_bundle_archive(bundles["forecastTar"], target)
    assert result["status"] == "passed" and result["published"] is True
    assert result["engineImports"] is False and result["forecastRows"] > 0
    assert (
        result["bundleId"]
        == archive.hashlib.sha256((target / "manifest.json").read_bytes()).hexdigest()
    )
    assert result["archive"]["bytes"] == bundles["forecastTar"].stat().st_size
    expected = {
        p.relative_to(bundles["forecast"]): p.read_bytes()
        for p in bundles["forecast"].rglob("*")
        if p.is_file()
    }
    actual = {
        p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
    assert actual == expected
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    for path in target.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    manifest = json.loads((target / "manifest.json").read_bytes())
    planned_names = ["manifest.json"] + [
        f"chunks/{collection['id']}/{chunk['ordinal']}.json"
        for collection in manifest["collections"]
        for chunk in collection["chunks"]
    ]
    with tarfile.open(bundles["forecastTar"], mode="r:") as ordinary:
        assert ordinary.getnames() == planned_names
        assert set(ordinary.getnames()) == {str(p) for p in actual}


def test_execution_requires_original_snapshot_and_cli_passes_source(tmp_path, bundles):
    with pytest.raises(archive.ArchiveError, match="original forecast"):
        archive.extract_bundle_archive(
            bundles["executionTar"], tmp_path / "without-source"
        )
    with pytest.raises(archive.ArchiveError, match="source must be a forecast"):
        archive.extract_bundle_archive(
            bundles["executionTar"],
            tmp_path / "wrong-source",
            source_bundle=bundles["execution"],
        )
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/extract-bundle.py"),
            str(bundles["executionTar"]),
            str(tmp_path / "with-source"),
            "--source-bundle",
            str(bundles["forecast"]),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["sourceForecastAndPlanIdentical"] is True and result["trades"] > 0


@pytest.mark.parametrize("missing", [1, 511, 512, 1023, 1024])
def test_exact_eof_is_required_even_when_tarfile_accepts_missing_eof(
    tmp_path, bundles, missing
):
    raw = bundles["forecastTar"].read_bytes()[:-missing]
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as ordinary:
        assert len(ordinary.getnames()) > 1
    rejected(tmp_path, raw, "ARCHIVE_TRUNCATED")


def test_truncated_chunk_body_is_rejected(tmp_path, bundles):
    raw = bundles["forecastTar"].read_bytes()
    offset, _ = members(raw)[1]
    rejected(tmp_path, raw[: offset + 515], "ARCHIVE_TRUNCATED")


@pytest.mark.parametrize("suffix", [b"x", bytes(512), bytes(1024)])
def test_trailing_data_and_tarfile_style_extra_padding_are_rejected(
    tmp_path, bundles, suffix
):
    rejected(tmp_path, bundles["forecastTar"].read_bytes() + suffix, "ARCHIVE_END")


@pytest.mark.parametrize(
    "name",
    [
        "../escape.json",
        "/tmp/escape.json",
        "chunks/unknown/0.json",
        "chunks/forecasts/00.json",
        "manifest.json",
    ],
)
def test_unsafe_unknown_noncanonical_or_duplicate_member_names(tmp_path, bundles, name):
    raw = bundles["forecastTar"].read_bytes()
    offset, info = members(raw)[1]
    damaged = (
        raw[:offset] + archive._expected_header(name, info.size) + raw[offset + 512 :]
    )
    rejected(tmp_path, damaged, "ARCHIVE_PATH")
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.parametrize(
    "typeflag", [b"1", b"2", b"5", b"x", b"g", b"L", b"K", b"S", b"\0"]
)
def test_no_link_directory_pax_gnu_or_alternate_regular_type(tmp_path, typeflag):
    header = bytearray(archive._expected_header("manifest.json", 400_000_000))
    header[156:157] = typeflag
    # No payload exists: type is rejected before any extension-sized read.
    rejected(tmp_path, fix_checksum(header), "ARCHIVE_TYPE")


@pytest.mark.parametrize(
    "offset,value",
    [
        (100, b"0000644\0"),
        (108, b"0000001\0"),
        (116, b"0000001\0"),
        (136, b"00000000001\0"),
        (157, b"target"),
        (265, b"user"),
        (297, b"group"),
        (257, b"ustarX"),
    ],
)
def test_metadata_and_magic_must_match_deterministic_header(
    tmp_path, bundles, offset, value
):
    raw = bundles["forecastTar"].read_bytes()
    header = bytearray(raw[:512])
    header[offset : offset + len(value)] = value
    rejected(tmp_path, fix_checksum(header) + raw[512:], "ARCHIVE_HEADER")


def test_bad_checksum_and_oversized_header_are_rejected_before_body(tmp_path, bundles):
    raw = bytearray(bundles["forecastTar"].read_bytes())
    raw[30] ^= 1
    rejected(tmp_path, bytes(raw), "ARCHIVE_HEADER")
    rejected(
        tmp_path,
        archive._expected_header("manifest.json", archive.LIMIT_MANIFEST + 1),
        "ARCHIVE_SIZE",
    )


def test_nonzero_body_padding_and_changed_chunk_hash(tmp_path, bundles):
    raw = bundles["forecastTar"].read_bytes()
    offset, info = next((o, i) for o, i in members(raw) if i.size % 512)
    damaged = bytearray(raw)
    damaged[offset + 512 + info.size] = 1
    rejected(tmp_path, bytes(damaged), "ARCHIVE_PADDING")
    offset, _ = members(raw)[1]
    damaged = bytearray(raw)
    damaged[offset + 513] ^= 1
    rejected(tmp_path, bytes(damaged), "ARCHIVE_HASH")


def test_declared_hash_damage_and_registered_member_omission(tmp_path, bundles):
    raw = bundles["forecastTar"].read_bytes()
    first = members(raw)[0][1]
    manifest = json.loads(raw[512 : 512 + first.size])
    manifest["collections"][0]["chunks"][0]["sha256"] = "f" * 64
    body = archive.canonical(manifest)
    assert len(body) == first.size
    rejected(tmp_path, raw[:512] + body + raw[512 + first.size :], "ARCHIVE_HASH")
    offset, info = members(raw)[1]
    end = offset + 512 + info.size + (-info.size) % 512
    rejected(tmp_path, raw[:offset] + raw[end:], "ARCHIVE_PATH")


@pytest.mark.parametrize("existing", ["file", "directory", "empty", "symlink"])
def test_existing_user_paths_are_preserved(tmp_path, bundles, existing):
    destination = tmp_path / "existing"
    if existing == "file":
        destination.write_bytes(b"keep me")
    elif existing in ("directory", "empty"):
        destination.mkdir()
        if existing == "directory":
            (destination / "sentinel").write_bytes(b"keep me")
    else:
        destination.symlink_to(tmp_path / "absent")
    before = destination.lstat()
    with pytest.raises(archive.ArchiveError) as error:
        archive.extract_bundle_archive(bundles["forecastTar"], destination)
    assert error.value.code == "ARCHIVE_OUTPUT_EXISTS"
    assert destination.lstat().st_ino == before.st_ino
    if existing == "file":
        assert destination.read_bytes() == b"keep me"
    if existing == "directory":
        assert (destination / "sentinel").read_bytes() == b"keep me"


def test_output_created_during_audit_is_not_replaced(tmp_path, bundles, monkeypatch):
    original = archive._publish
    created = []

    def race(source, destination):
        destination.mkdir()
        created.append(destination.stat().st_ino)
        return original(source, destination)

    monkeypatch.setattr(archive, "_publish", race)
    destination = tmp_path / "racing-output"
    with pytest.raises((archive.ArchiveError, FileExistsError)):
        archive.extract_bundle_archive(bundles["forecastTar"], destination)
    assert destination.stat().st_ino == created[0] and list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".atlas-bundle-extract-*"))


def test_sparse_oversized_input_is_rejected_without_reading_payload(tmp_path):
    source = tmp_path / "oversized.tar"
    with source.open("wb") as stream:
        stream.truncate(archive.MAX_ARCHIVE_BYTES + 1)
    with pytest.raises(archive.ArchiveError) as error:
        archive.extract_bundle_archive(source, tmp_path / "output")
    assert error.value.code == "ARCHIVE_SIZE"
    assert not (tmp_path / "output").exists()


def test_excess_chunk_plan_is_rejected_before_any_chunk_body(tmp_path, bundles):
    manifest = json.loads((bundles["forecast"] / "manifest.json").read_bytes())
    collection = manifest["collections"][0]
    collection["chunks"] = [
        {"ordinal": i, "start": i, "count": 1, "byteLength": 2, "sha256": "0" * 64}
        for i in range(257)
    ]
    collection["rowCount"] = 257
    body = archive.canonical(manifest)
    raw = (
        archive._expected_header("manifest.json", len(body))
        + body
        + bytes((-len(body)) % 512)
        + bytes(1024)
    )
    error = rejected(tmp_path, raw)
    assert "resource budget" in str(error)


def test_semantically_invalid_but_rehashed_bundle_is_not_published(tmp_path, bundles):
    report = copy.deepcopy(bundles["report"])
    artifact = report["forecasts"]
    row = next(row for row in artifact["rows"] if row["status"] == "valid")
    row["forecastError"] += 1
    artifact["artifactId"] = digest(
        {k: v for k, v in artifact.items() if k != "artifactId"}
    )
    report["execution"]["forecastArtifactId"] = artifact["artifactId"]
    bad = export(tmp_path / "rehashed", report, bundles["snapshot"], bundles["plan"])
    source = server_archive(bad, tmp_path / "rehashed.tar")
    with pytest.raises(ValueError, match="forecast error"):
        archive.extract_bundle_archive(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".atlas-bundle-extract-*"))
