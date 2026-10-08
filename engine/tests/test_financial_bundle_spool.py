"""Encrypted byte recovery tests; no network, provider or real-market model."""

from pathlib import Path
import shutil
import time

import pytest

from atlas_quant import runner
from atlas_quant.bundle import sha
from atlas_quant.bundle_spool import BundleSpool
from atlas_quant.financial_bundle import FinancialBundleReader
from atlas_quant.financial_bundle_spool import (
    FinancialBundleSpool,
    deliver_financial_bundle,
)
from test_financial_bundle import financial_research, sources, long_sources


def store(tmp_path, fixture):
    completion = runner.CompletionSpool(
        {
            "delivery_dir": str(tmp_path / "delivery"),
            "runner_secret": "x" * 48,
            "api_base": "https://queue.test",
        }
    )
    identity = {"id": "financial-job", "leaseToken": "financial-lease"}
    encrypted = FinancialBundleSpool.from_spool(completion, identity)
    receipt = encrypted.build(
        fixture["report"],
        fixture["snapshot"],
        fixture["coverage"],
        fixture["sourceEvidence"],
    )
    return completion, encrypted, {**identity, **receipt}


class Client:
    def __init__(self):
        self.manifest = None
        self.chunks = {}
        self.lose = False
        self.cancelled = False
        self.calls = []
        self.uploads = []

    def post(self, route, body, **kwargs):
        self.calls.append(route)
        if route == "heartbeat":
            return {"leaseValid": not self.cancelled, "cancelled": self.cancelled}
        if route == "financial-bundles/begin":
            raw = body["manifestText"].encode()
            assert self.manifest in (None, raw)
            self.manifest = raw
            reader = FinancialBundleReader(raw, lambda c, n: self.chunks[c, n])
            missing = [
                {"collection": c["id"], "ordinal": d["ordinal"]}
                for c in reader.manifest["collections"]
                for d in c["chunks"]
                if (c["id"], d["ordinal"]) not in self.chunks
            ]
            return {
                "stageId": "financial-stage",
                "bundleId": sha(raw),
                "status": "staging",
                "missing": missing,
            }
        if route == "financial-bundles/finalize":
            reader = FinancialBundleReader(
                self.manifest, lambda c, n: self.chunks[c, n]
            )
            reader.verify_integrity()
            return {"bundleId": reader.bundle_id, "status": "verified"}
        pytest.fail("Unexpected route " + route)

    def bundle_chunk(self, method, bundle_id, collection, ordinal, identity, **kwargs):
        assert method == "PUT" and kwargs["namespace"] == "financial-bundles"
        raw = kwargs["raw"]
        self.chunks[collection, ordinal] = raw
        self.uploads.append((collection, ordinal))
        if self.lose:
            self.lose = False
            raise runner.RunnerError("QUEUE_NETWORK", "Lost chunk ACK")
        return {
            "bundleId": bundle_id,
            "collection": collection,
            "ordinal": ordinal,
            "sha256": sha(raw),
        }


def test_unknown_ack_resumes_exact_financial_bytes_not_recompute(
    tmp_path, financial_research, monkeypatch
):
    completion, encrypted, payload = store(tmp_path, financial_research)
    client = Client()
    client.lose = True
    monkeypatch.setattr(runner, "STOP", False)
    with pytest.raises(runner.RunnerError, match="Lost chunk"):
        deliver_financial_bundle(
            client, completion, payload, deadline=time.monotonic() + 30
        )
    first = client.uploads[0]
    again = FinancialBundleSpool.from_spool(completion, payload)
    assert again.reader().snapshot_bytes() == financial_research["snapshot"]
    assert (
        deliver_financial_bundle(
            client, completion, payload, deadline=time.monotonic() + 30
        )
        == "financial-stage"
    )
    assert client.uploads.count(first) == 1
    assert all(
        "bundles/" not in r or r.startswith("financial-bundles/") for r in client.calls
    )
    assert (
        encrypted.root.exists()
    ), "No cleanup before independently confirmed terminal state"


def test_domains_are_cryptographically_separate_even_for_same_job_lease(
    tmp_path, financial_research
):
    completion, encrypted, payload = store(tmp_path, financial_research)
    legacy = BundleSpool.from_spool(completion, payload)
    assert encrypted.root != legacy.root and encrypted.aad != legacy.aad
    shutil.copyfile(encrypted.root / "manifest.enc", legacy.root / "manifest.enc")
    with pytest.raises(runner.RunnerError):
        legacy.read("manifest")
    other = FinancialBundleSpool.from_spool(
        completion, {**payload, "leaseToken": "other"}
    )
    shutil.copyfile(encrypted.root / "manifest.enc", other.root / "manifest.enc")
    with pytest.raises(runner.RunnerError):
        other.read("manifest")
    assert encrypted.reader().verify_integrity()["transportVerified"]


def test_cancel_stop_and_deadline_keep_authenticated_bytes(
    tmp_path, financial_research, monkeypatch
):
    completion, encrypted, payload = store(tmp_path, financial_research)
    client = Client()
    client.cancelled = True
    monkeypatch.setattr(runner, "STOP", False)
    assert (
        deliver_financial_bundle(
            client, completion, payload, deadline=time.monotonic() + 30
        )
        is None
    )
    assert client.calls == ["heartbeat"]
    monkeypatch.setattr(runner, "STOP", True)
    with pytest.raises(runner.RunnerError) as stopped:
        deliver_financial_bundle(
            client, completion, payload, deadline=time.monotonic() + 30
        )
    assert stopped.value.code == "DELIVERY_INTERRUPTED"
    monkeypatch.setattr(runner, "STOP", False)
    with pytest.raises(runner.RunnerError) as expired:
        deliver_financial_bundle(
            client, completion, payload, deadline=time.monotonic() - 1
        )
    assert expired.value.code == "DELIVERY_DEADLINE"
    assert encrypted.reader().snapshot_bytes() == financial_research["snapshot"]


def test_tamper_never_gets_uploaded_and_cleanup_is_idempotent(
    tmp_path, financial_research, monkeypatch
):
    completion, encrypted, payload = store(tmp_path, financial_research)
    target = encrypted.root / "manifest.enc"
    original = target.read_bytes()
    target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    monkeypatch.setattr(runner, "STOP", False)
    client = Client()
    with pytest.raises(runner.RunnerError):
        deliver_financial_bundle(
            client, completion, payload, deadline=time.monotonic() + 30
        )
    assert not client.calls and target.exists()
    target.write_bytes(original)
    encrypted.cleanup()
    encrypted.cleanup()
    assert not encrypted.root.exists()
