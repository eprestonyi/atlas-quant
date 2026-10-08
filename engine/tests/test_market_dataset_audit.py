"""Independent auditor acceptance and adversarial source-closure tests; no fit/network."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from market_dataset_audit import (
    AuditError,
    audit_market_dataset,
    encode,
    sha,
    tar_header,
)


@pytest.fixture(scope="module")
def source():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "engine")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/helpers/market-fixture.py")],
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )
    value = json.loads(result.stdout)
    value["scope"] = json.loads(
        (ROOT / "contracts/fixtures/market-scope-v1.json").read_text()
    )
    return value


def payloads(value):
    return {
        "manifest.json": encode(value["manifest"]),
        "plan.json": encode(value["plan"]),
        "scope.json": encode(value["scope"]),
        **{
            f"parts/{name}/{ordinal}.bin": raw.encode()
            for name, parts in value["chunks"].items()
            for ordinal, raw in parts.items()
        },
    }


def write_dir(tmp_path, value):
    path = tmp_path / "source"
    path.mkdir()
    for name, raw in payloads(value).items():
        p = path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    return path


def archive_bytes(value):
    parts = payloads(value)
    names = ["manifest.json", "plan.json", "scope.json"]
    for collection in [*sorted(value["manifest"]["collections"]), "raw"]:
        names.extend(
            f"parts/{collection}/{p['ordinal']}.bin"
            for p in (
                value["manifest"]["rawArchive"]
                if collection == "raw"
                else value["manifest"]["collections"][collection]
            )["chunks"]
        )
    return b"".join(
        tar_header(name, len(parts[name]))
        + parts[name]
        + bytes((-len(parts[name])) % 512)
        for name in names
    ) + bytes(1024)


def replace_collection(value, name, rows):
    raw = encode(rows)
    value["chunks"][name] = {"0": raw.decode()}
    value["manifest"]["collections"][name] = {
        "chunks": [
            {
                "ordinal": 0,
                "sha256": sha(raw),
                "byteLength": len(raw),
                "rowCount": len(rows),
            }
        ],
        "rowCount": len(rows),
        "byteLength": len(raw),
    }
    if name == "rows":
        value["manifest"]["rowCount"] = len(rows)


def receipt_bodies(value):
    receipts = json.loads(value["chunks"]["receipts"]["0"])
    bodies = []
    for r in receipts:
        loc = r["rawLocation"]
        raw = value["chunks"]["raw"][str(loc["ordinal"])].encode()
        bodies.append(raw[loc["offset"] : loc["offset"] + loc["byteLength"]])
    return receipts, bodies


def repack_raw(value, bodies, split=None):
    receipts = json.loads(value["chunks"]["receipts"]["0"])
    groups, pending = [], bytearray()
    for i, (r, body) in enumerate(zip(receipts, bodies)):
        if split == i:
            groups.append(bytes(pending))
            pending.clear()
        r.update(
            sha256=sha(body),
            byteLength=len(body),
            rawLocation={
                "ordinal": len(groups),
                "offset": len(pending),
                "byteLength": len(body),
            },
        )
        pending.extend(body)
    groups.append(bytes(pending))
    value["chunks"]["raw"] = {str(i): raw.decode() for i, raw in enumerate(groups)}
    value["manifest"]["rawArchive"] = {
        "chunks": [
            {"ordinal": i, "sha256": sha(raw), "byteLength": len(raw)}
            for i, raw in enumerate(groups)
        ],
        "byteLength": sum(map(len, groups)),
        "receiptCount": len(receipts),
    }
    replace_collection(value, "receipts", receipts)


def test_real_normalizer_directory_and_exact_tar(source, tmp_path):
    directory = write_dir(tmp_path, source)
    expected = sha(encode(source["manifest"]))
    report = audit_market_dataset(directory, expected_root=expected)
    assert report["status"] == "PASS" and report["rootPinned"] is True
    assert (report["symbolCount"], report["rowCount"], report["receiptCount"]) == (
        2,
        15,
        8,
    )
    assert (
        report["normalizationVerified"] is True
        and report["missingMaskVerified"] is True
    )
    assert (
        report["originalProviderWireAvailable"] is False
        and report["sourceAuthorityVerified"] is False
    )
    path = tmp_path / "source.tar"
    path.write_bytes(archive_bytes(source))
    second = audit_market_dataset(path, expected_root=expected)
    assert second["status"] == "PASS" and second["marketDatasetRoot"] == expected


def test_exact_noncanonical_wire_and_multichunk_raw_supported(source, tmp_path):
    value = copy.deepcopy(source)
    _, bodies = receipt_bodies(value)
    # Reordering/whitespace of provider JSON must preserve the delivered byte identity.
    bodies[2] = json.dumps(json.loads(bodies[2]), indent=1).encode()
    repack_raw(value, bodies, split=4)
    report = audit_market_dataset(write_dir(tmp_path, value))
    assert report["status"] == "PASS" and report["rootPinned"] is False


@pytest.mark.parametrize(
    "case",
    [
        "normalized_price",
        "missing_session",
        "extra_session",
        "raw_price",
        "adjustment",
        "calendar",
        "duplicate_provider_key",
        "raw_location",
        "raw_tail",
        "provenance",
        "whole_scope",
        "budget",
        "bool_number",
        "source_kind",
        "receipt_count",
    ],
)
def test_rehashed_semantic_and_shape_forgery_rejected(source, tmp_path, case):
    value = copy.deepcopy(source)
    rows = json.loads(value["chunks"]["rows"]["0"])
    receipts, bodies = receipt_bodies(value)
    if case == "normalized_price":
        rows[0]["close"] += 0.25
        replace_collection(value, "rows", rows)
    elif case == "missing_session":
        rows.pop(0)
        replace_collection(value, "rows", rows)
    elif case == "extra_session":
        row = next(r.copy() for r in rows if r["ts_code"] == "600000.SH")
        row["trade_date"] = "20240103"
        rows.append(row)
        replace_collection(
            value, "rows", sorted(rows, key=lambda r: (r["trade_date"], r["ts_code"]))
        )
    elif case in {"raw_price", "adjustment", "calendar"}:
        idx = {"raw_price": 2, "adjustment": 3, "calendar": 0}[case]
        raw = json.loads(bodies[idx])
        field = {
            "raw_price": "close",
            "adjustment": "adj_factor",
            "calendar": "is_open",
        }[case]
        raw["data"]["items"][0][raw["data"]["fields"].index(field)] = (
            11.25 if case == "raw_price" else 3 if case == "adjustment" else 0
        )
        bodies[idx] = encode(raw)
        repack_raw(value, bodies)
    elif case == "duplicate_provider_key":
        bodies[2] = bodies[2].replace(b'"code":0', b'"code":0,"code":0')
        repack_raw(value, bodies)
    elif case == "raw_location":
        receipts[0]["rawLocation"]["offset"] = 1
        replace_collection(value, "receipts", receipts)
    elif case == "raw_tail":
        raw = value["chunks"]["raw"]["0"].encode() + b" "
        value["chunks"]["raw"]["0"] = raw.decode()
        value["manifest"]["rawArchive"]["chunks"][0].update(
            sha256=sha(raw), byteLength=len(raw)
        )
        value["manifest"]["rawArchive"]["byteLength"] = len(raw)
    elif case == "provenance":
        p = json.loads(value["chunks"]["provenance"]["0"])
        p[0]["originalProviderWireAvailable"] = True
        replace_collection(value, "provenance", p)
    elif case == "whole_scope":
        value["manifest"]["scope"]["symbols"].pop()
    elif case == "budget":
        value["manifest"]["rawArchive"]["chunks"][0]["byteLength"] = 4 * 1024 * 1024 + 1
    elif case == "bool_number":
        rows[0]["close"] = True
        replace_collection(value, "rows", rows)
    elif case == "source_kind":
        value["manifest"]["sourceKind"] = "unknown"
    elif case == "receipt_count":
        value["manifest"]["rawArchive"]["receiptCount"] -= 1
    with pytest.raises(AuditError):
        audit_market_dataset(write_dir(tmp_path, value))


@pytest.mark.parametrize(
    "case",
    [
        "truncated",
        "trailer",
        "padding",
        "link",
        "traversal",
        "duplicate",
        "huge_member",
    ],
)
def test_strict_tar_rejects_nonprofile_bytes(source, tmp_path, case):
    raw = archive_bytes(source)
    if case == "truncated":
        raw = raw[:-1]
    elif case == "trailer":
        raw += b"\0"
    elif case == "padding":
        offset = 512 + len(encode(source["manifest"]))
        assert offset % 512
        raw = raw[:offset] + b"x" + raw[offset + 1 :]
    elif case == "link":
        header = bytearray(raw[:512])
        header[156] = ord("2")
        header[148:156] = b" " * 8
        header[148:156] = f"{sum(header):06o}\0 ".encode()
        raw = bytes(header) + raw[512:]
    elif case == "traversal":
        raw = (
            tar_header("../manifest.json", len(encode(source["manifest"]))) + raw[512:]
        )
    elif case == "duplicate":
        first_size = (
            512
            + len(encode(source["manifest"]))
            + (-len(encode(source["manifest"]))) % 512
        )
        raw = raw[:first_size] + raw
    elif case == "huge_member":
        raw = tar_header("manifest.json", 257 * 1024) + raw[512:]
    path = tmp_path / "bad.tar"
    path.write_bytes(raw)
    with pytest.raises(AuditError):
        audit_market_dataset(path)


@pytest.mark.parametrize(
    "case",
    [
        "extra",
        "leaf_symlink",
        "directory_symlink",
        "root_symlink",
        "wrong_pin",
        "duplicate_json",
    ],
)
def test_directory_and_pin_boundaries(source, tmp_path, case):
    path = write_dir(tmp_path, source)
    if case == "extra":
        (path / "secret.txt").write_text("not part of closure")
    elif case == "leaf_symlink":
        part = path / "parts/raw/0.bin"
        target = tmp_path / "raw"
        part.rename(target)
        part.symlink_to(target)
    elif case == "directory_symlink":
        part = path / "parts/raw"
        target = tmp_path / "raw"
        part.rename(target)
        part.symlink_to(target, target_is_directory=True)
    elif case == "root_symlink":
        link = tmp_path / "alias"
        link.symlink_to(path, target_is_directory=True)
        path = link
    elif case == "duplicate_json":
        f = path / "manifest.json"
        f.write_bytes(
            f.read_bytes().replace(b'"version":1', b'"version":1,"version":1', 1)
        )
    with pytest.raises((AuditError, OSError)):
        audit_market_dataset(
            path, expected_root="0" * 64 if case == "wrong_pin" else None
        )


def test_cli_zero_provider_and_no_overwrite(source, tmp_path):
    path = write_dir(tmp_path, source)
    output = tmp_path / "report.json"
    args = [
        sys.executable,
        str(ROOT / "scripts/audit-market-dataset.py"),
        str(path),
        "--expected-root",
        sha(encode(source["manifest"])),
        "--output",
        str(output),
    ]
    result = subprocess.run(args, check=True, capture_output=True, timeout=60)
    report = json.loads(result.stdout)
    assert report["providerCalls"] == 0 and report["modelFitted"] is False
    original = output.read_bytes()
    result = subprocess.run(args, capture_output=True, timeout=60)
    assert result.returncode == 2 and output.read_bytes() == original


def test_receipt_explicit_timezone_offset_is_preserved(source, tmp_path):
    value = copy.deepcopy(source)
    receipts = json.loads(value["chunks"]["receipts"]["0"])
    receipts[0]["retrievedAt"] = "2026-10-08T08:00:00+08:00"
    replace_collection(value, "receipts", receipts)
    assert audit_market_dataset(write_dir(tmp_path, value))["status"] == "PASS"


@pytest.mark.parametrize(
    "case",
    ["scope_alias_root", "catalog_root", "malformed_source_kind", "malformed_row_date"],
)
def test_internal_evidence_aliases_and_malformed_shapes(source, tmp_path, case):
    value = copy.deepcopy(source)
    if case in {"scope_alias_root", "catalog_root"}:
        if case == "scope_alias_root":
            value["plan"]["scope"]["scopeRoot"] = "0" * 64
            value["manifest"]["scope"]["scopeRoot"] = "0" * 64
        else:
            value["plan"]["catalog"]["snapshotHash"] = "0" * 64
        value["plan"]["planRoot"] = sha(
            encode({k: v for k, v in value["plan"].items() if k != "planRoot"})
        )
        value["manifest"]["planRoot"] = value["plan"]["planRoot"]
        provenance = json.loads(value["chunks"]["provenance"]["0"])
        provenance[0]["planRoot"] = value["plan"]["planRoot"]
        replace_collection(value, "provenance", provenance)
    elif case == "malformed_source_kind":
        value["manifest"]["sourceKind"] = {}
    else:
        rows = json.loads(value["chunks"]["rows"]["0"])
        rows[0]["trade_date"] = []
        replace_collection(value, "rows", rows)
    with pytest.raises(AuditError):
        audit_market_dataset(write_dir(tmp_path, value))
