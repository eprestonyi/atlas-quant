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


def paired_fixture(source, damage=None):
    # Structural SYNTHETIC result only: no model is run or imported here.
    script = r"""
import fs from 'node:fs';
import {bundleFixture,canonical} from './tests/fixtures/bundle-fixture.mjs';
const source=JSON.parse(fs.readFileSync(0,'utf8')), m=source.manifest, scope=source.scope;
const damage=process.argv[1];
const evidence={admissionProfile:'pooled_asset_1000_v1',marketDatasetRef:{datasetId:'22222222-2222-2222-2222-222222222222',datasetRoot:source.root,format:'atlas.quant.market_dataset',version:1},universeScopeRef:m.universeScopeRef,rowValueRoot:'7'.repeat(64)};
if(damage==='different_source')evidence.marketDatasetRef.datasetRoot='8'.repeat(64);
if(damage==='phantom_profile')evidence.admissionProfile='pooled_asset_999999_v9';
if(damage==='wrong_auto')evidence.admissionProfile='pooled_asset_1000_auto_candidate_v1';
if(damage==='unverified_id')evidence.marketDatasetRef.datasetId='33333333-3333-3333-3333-333333333333';
const fixture=bundleFixture({mutate:({forecast,report,snapshot,coverage})=>{
 const u=forecast.sourceStrategy.universe;
 for(const key of ['symbols','start','end','selection','snapshotHash','resolutionHash'])u[key]=scope[key];
 u.subsetPolicy='all';
 report.strategy=forecast.sourceStrategy;
 for(const row of forecast.rows){row.date=row.date.replace('202501','202401');row.entryDate=row.entryDate.replace('202501','202401');row.targetDate='20240110';row.labelMaturedAt='20240110';row.informationCutoff=row.date;}
 forecast.targetDefinitions[0].formationEnd=null;
 forecast.modelFits[0].fitDate='20240102';forecast.modelFits[0].labelEndMax='20231229';
 forecast.modelFits[0].trainStart='20230101';forecast.modelFits[0].trainEnd='20231229';
 coverage.holdoutStart='20240102';coverage.origins=forecast.rows.map(r=>({date:r.date,targetId:r.targetId,entryDate:r.entryDate,targetDate:r.targetDate,inputValid:true}));
 const provenance={marketSource:evidence,synthetic:true,source:'SYNTHETIC_MARKET_FIXTURE',tradingDates:m.calendar};
 report.provenance={...provenance,dataSha256:forecast.dataFingerprint};report.execution.enabled=false;
 snapshot.rows=Object.keys(source.chunks.rows).sort((a,b)=>+a-+b).flatMap(i=>JSON.parse(source.chunks.rows[i]));
 snapshot.provenance={...provenance,dataFingerprint:'d'.repeat(64)};snapshot.sourceDataFingerprint='d'.repeat(64);snapshot.fingerprintVersion='research_input_v1';
 if(damage==='missing_row')snapshot.rows.shift();
 if(damage==='changed_row')snapshot.rows[0].close+=0.125;
 if(damage==='null_to_zero')snapshot.rows.find(r=>r.pb===null).pb=0;
 if(damage==='reordered_rows')snapshot.rows.reverse();
 if(damage==='wrong_kind')report.provenance.synthetic=false;
 if(damage==='mismatched_assertion')snapshot.provenance.marketSource={...evidence,rowValueRoot:'9'.repeat(64)};
 if(damage==='prediction_arithmetic')forecast.rows[0].expectedChange=999;
}});
process.stdout.write(JSON.stringify({manifest:fixture.manifest,manifestText:fixture.manifestText,chunks:Object.fromEntries(fixture.chunks)}));
"""
    value = {**source, "root": sha(encode(source["manifest"]))}
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, damage or "valid"],
        cwd=ROOT,
        input=json.dumps(value),
        text=True,
        check=True,
        capture_output=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def write_result(tmp_path, value, tar=False):
    path = tmp_path / ("result.tar" if tar else "result")
    entries = [("manifest.json", value["manifestText"].encode())]
    for c in value["manifest"]["collections"]:
        entries.extend(
            (
                f"chunks/{c['id']}/{d['ordinal']}.json",
                value["chunks"][f"{c['id']}:{d['ordinal']}"].encode(),
            )
            for d in c["chunks"]
        )
    if tar:
        path.write_bytes(
            b"".join(
                tar_header(name, len(raw)) + raw + bytes((-len(raw)) % 512)
                for name, raw in entries
            )
            + bytes(1024)
        )
    else:
        path.mkdir()
        for name, raw in entries:
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return path


@pytest.mark.parametrize("tar", [False, True])
def test_paired_complete_result_audit_and_full_source_comparison(source, tmp_path, tar):
    dataset = write_dir(tmp_path, source)
    result = write_result(tmp_path, paired_fixture(source), tar)
    report = audit_market_dataset(
        dataset, expected_root=sha(encode(source["manifest"])), result_bundle=result
    )
    assert report["status"] == "PASS"
    paired = report["pairedResult"]
    assert (
        paired["bundleAudit"]["status"] == "passed"
        and paired["bundleAudit"]["forecastRows"] == 4
    )
    assert paired["fullSnapshotRowsVerified"] is True and paired["snapshotRows"] == 15
    assert paired["sourceContentRootVerified"] is True
    assert (
        paired["rowValueRootInternallyConsistent"] is True
        and paired["rowValueRootRecomputed"] is False
    )
    assert (
        paired["ownershipVerified"] is False
        and paired["datasetIdAuthenticated"] is False
    )
    assert paired["serverAdmissionAuthenticated"] is False


@pytest.mark.parametrize(
    "damage",
    [
        "different_source",
        "missing_row",
        "changed_row",
        "null_to_zero",
        "reordered_rows",
        "phantom_profile",
        "wrong_auto",
        "mismatched_assertion",
        "wrong_kind",
        "prediction_arithmetic",
    ],
)
def test_pair_rejects_individually_rehashed_but_unbound_or_invalid_result(
    source, tmp_path, damage
):
    dataset = write_dir(tmp_path, source)
    result = write_result(tmp_path, paired_fixture(source, damage))
    with pytest.raises(AuditError):
        audit_market_dataset(dataset, result_bundle=result)


def test_pair_cannot_authenticate_dataset_id_and_cli_accepts_result_tar(
    source, tmp_path
):
    dataset = write_dir(tmp_path, source)
    result = write_result(tmp_path, paired_fixture(source, "unverified_id"), tar=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/audit-market-dataset.py"),
            str(dataset),
            "--expected-root",
            sha(encode(source["manifest"])),
            "--result-bundle",
            str(result),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert (
        report["pairedResult"]["marketDatasetId"]
        == "33333333-3333-3333-3333-333333333333"
    )
    assert report["pairedResult"]["datasetIdAuthenticated"] is False


@pytest.mark.parametrize(
    "damage", ["tar_trailer", "tar_traversal", "directory_link", "directory_extra"]
)
def test_pair_result_input_strict_paths_and_archive_eof(source, tmp_path, damage):
    dataset = write_dir(tmp_path, source)
    result = write_result(
        tmp_path, paired_fixture(source), tar=damage.startswith("tar")
    )
    if damage == "tar_trailer":
        result.write_bytes(result.read_bytes() + b"\0")
    elif damage == "tar_traversal":
        raw = result.read_bytes()
        size = int(raw[124:135], 8)
        result.write_bytes(tar_header("../manifest.json", size) + raw[512:])
    elif damage == "directory_extra":
        (result / "extra").write_text("unexpected")
    else:
        file = result / "chunks/snapshotRows/0.json"
        external = tmp_path / "outside.json"
        file.rename(external)
        file.symlink_to(external)
    with pytest.raises((AuditError, OSError)):
        audit_market_dataset(dataset, result_bundle=result)


def test_paired_numeric_comparison_does_not_round_changed_integers():
    from market_dataset_audit import Checks, number

    with pytest.raises(AuditError, match="Integer loses precision"):
        number(2**53 + 1, Checks())
    assert number(2**53, Checks()) == 2**53
    assert encode(number(-0.0, Checks())) == encode(number(0, Checks()))
