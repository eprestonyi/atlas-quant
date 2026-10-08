"""Exact source reconstruction without network, fitting, or raw-response retries."""

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from atlas_quant.market_acquisition.reader import (
    MarketSourceReader,
    DirectoryMarketSourceReader,
)
from atlas_quant.market_acquisition.protocol import encode, sha
from atlas_quant.runner import RunnerError
from test_market_acquisition import PLAN, JOB, sources, build_publication


def fixture():
    original = sources()
    parts = {}
    manifest = build_publication(
        JOB,
        PLAN,
        lambda r: original[r["requestKey"]],
        lambda n, i, raw: parts.__setitem__((n, i), raw),
    )
    scope = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "contracts/fixtures/market-scope-v1.json"
        ).read_text()
    )
    return deepcopy(manifest), parts, scope


def reader(m, parts, scope, **kwargs):
    return MarketSourceReader(
        encode(m), encode(PLAN), encode(scope), lambda n, i: parts[n, i], **kwargs
    )


def replace_records(m, parts, collection, rows):
    raw = encode(rows)
    parts[collection, 0] = raw
    c = m["collections"][collection]
    c["byteLength"] = len(raw)
    c["chunks"][0].update(sha256=sha(raw), byteLength=len(raw))


def test_full_source_recomposes_values_and_keeps_missing_sessions():
    m, parts, scope = fixture()
    r = reader(m, parts, scope, expected_root=sha(encode(m)))
    result = r.verify_integrity()
    assert result["normalizationRecomputed"] is True
    assert result["rowCount"] == 15 and result["symbolCount"] == 2
    assert result["rawReceipts"] == 8 and result["providerCalls"] == 0
    assert result["sourceAuthorityVerified"] is False
    frame, provenance = r.research_input()
    assert len(frame) == 15  # 16 grid cells, one truly missing observation.
    assert frame.pb.isna().sum() == 1
    assert len(provenance["tradingDates"]) == 8
    assert provenance["synthetic"] is True


def test_public_descriptor_edits_cannot_rebind_source_bytes():
    m, parts, scope = fixture()
    r = reader(m, parts, scope)
    altered = r.manifest
    altered["collections"]["rows"]["chunks"][0]["sha256"] = "0" * 64
    del r.plan["requests"]
    r.scope["symbols"].clear()
    assert r.verify_integrity()["normalizationRecomputed"]
    raw = bytearray(parts["rows", 0])
    raw[2] ^= 1
    parts["rows", 0] = bytes(raw)
    with pytest.raises(RunnerError, match="bytes differ"):
        r.verify_integrity()


def test_rehashed_adjusted_value_and_null_mask_must_recompute():
    for field, value in [("close", 99.0), ("pb", None)]:
        m, parts, scope = fixture()
        rows = json.loads(parts["rows", 0])
        rows[0][field] = value
        replace_records(m, parts, "rows", rows)
        with pytest.raises(RunnerError, match="values or missingness differ"):
            reader(m, parts, scope).verify_integrity()


@pytest.mark.parametrize("change", ["offset", "overlap", "tail", "missing", "raw_hash"])
def test_rehashed_transport_cannot_omit_or_reinterpret_raw_bodies(change):
    m, parts, scope = fixture()
    receipts = json.loads(parts["receipts", 0])
    if change == "offset":
        receipts[0]["rawLocation"]["offset"] = 1
    elif change == "overlap":
        receipts[1]["rawLocation"]["offset"] = 0
    elif change == "tail":
        raw = parts["raw", 0] + b" "
        parts["raw", 0] = raw
        m["rawArchive"]["byteLength"] = len(raw)
        m["rawArchive"]["chunks"][0].update(byteLength=len(raw), sha256=sha(raw))
    elif change == "missing":
        del parts["raw", 0]
    elif change == "raw_hash":
        raw = parts["raw", 0].replace(b'"code":0', b'"code":1', 1)
        parts["raw", 0] = raw
        m["rawArchive"]["chunks"][0]["sha256"] = sha(raw)
    replace_records(m, parts, "receipts", receipts)
    with pytest.raises((RunnerError, KeyError)):
        reader(m, parts, scope).verify_integrity()


def test_root_and_scope_fail_before_part_access():
    m, parts, scope = fixture()
    called = []
    with pytest.raises(RunnerError):
        MarketSourceReader(
            encode(m),
            encode(PLAN),
            encode(scope),
            lambda *x: called.append(x),
            expected_root="0" * 64,
        )
    assert called == []
    scope["symbols"].pop()
    with pytest.raises(RunnerError, match="scope root"):
        reader(m, parts, scope)


@pytest.mark.parametrize("field", ["scopeRoot", "snapshotHash", "resolutionHash"])
def test_conflicting_internal_roots_cannot_be_self_resigned(field):
    m, parts, scope = fixture()
    plan = deepcopy(PLAN)
    if field == "scopeRoot":
        plan["scope"][field] = "0" * 64
        m["scope"][field] = "0" * 64
    else:
        plan["catalog"][field] = "0" * 64
    plan["planRoot"] = sha(encode({k: v for k, v in plan.items() if k != "planRoot"}))
    m["planRoot"] = plan["planRoot"]
    with pytest.raises(RunnerError):
        MarketSourceReader(
            encode(m), encode(plan), encode(scope), lambda n, i: parts[n, i]
        )


def test_fifo_rejected_without_waiting_for_a_writer(tmp_path):
    os.mkfifo(tmp_path / "manifest.json", 0o600)
    with pytest.raises(RunnerError, match="file exceeds"):
        DirectoryMarketSourceReader(tmp_path)


def test_descriptor_parent_budget_rejected_before_read():
    m, parts, scope = fixture()
    m["rawArchive"]["chunks"][0]["byteLength"] = 4 * 1024 * 1024 + 1
    with pytest.raises(RunnerError, match="byte budget"):
        reader(m, parts, scope)


def test_directory_reader_links_and_existing_parts_are_checked(tmp_path):
    m, parts, scope = fixture()
    for name, value in [("manifest", m), ("plan", PLAN), ("scope", scope)]:
        (tmp_path / (name + ".json")).write_bytes(encode(value))
    for (collection, ordinal), raw in parts.items():
        d = tmp_path / "parts" / collection
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{ordinal}.bin").write_bytes(raw)
    r = DirectoryMarketSourceReader(tmp_path, expected_root=sha(encode(m)))
    assert r.verify_integrity()["rowCount"] == 15
    target = tmp_path / "parts" / "raw" / "0.bin"
    original = tmp_path / "original.bin"
    target.rename(original)
    target.symlink_to(original)
    with pytest.raises(RunnerError, match="links"):
        r.verify_integrity()
