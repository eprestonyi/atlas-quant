"""Independent runner boundary review: synthetic source, zero provider calls or fits."""

from copy import deepcopy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import uuid

import pytest

from atlas_quant import runner
from atlas_quant.bundle_spool import BundleSpool
from atlas_quant.capacity.profiles import (
    AUTO_FILTER_CANDIDATE_ID,
    FULL_FILTER_PROFILE_ID,
)
from atlas_quant.market_acquisition.normalize import build_publication
from atlas_quant.market_acquisition.protocol import encode, sha
from atlas_quant.market_research_runner.client import MarketResearchClient
from atlas_quant.market_research_runner.spool import MarketResearchSpool
from atlas_quant.market_research_runner import compute as computation
from atlas_quant.market_research_runner import limits
from atlas_quant.runner_claims import ClaimIntent, claim_request
from dataset_runner_support import DATASET, JOB, LEASE, config
from test_dataset_client import Response, Session


@pytest.fixture(scope="module")
def market_source():
    path = Path(__file__).resolve().parents[2] / "scripts/fixtures/market_source.py"
    spec = importlib.util.spec_from_file_location("review_market_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    scope, plan, strategy = fixture.source_plan(2)
    provider = fixture.SyntheticMarketProvider({"allow_market_fixtures": True})
    receipts, parts = {}, {}
    for request in plan["requests"]:
        raw = provider.call_once(request, maximum_bytes=request["responseBytes"]).raw
        receipts[request["requestKey"]] = {
            "requestKey": request["requestKey"],
            "receiptId": str(uuid.uuid5(uuid.NAMESPACE_URL, request["requestKey"])),
            "raw": raw,
            "sha256": sha(raw),
            "byteLength": len(raw),
            "httpStatus": 200,
            "retrievedAt": "2026-10-08T00:00:00Z",
            "sourceKind": "fixture",
        }
    manifest = build_publication(
        {},
        plan,
        lambda request: receipts[request["requestKey"]],
        lambda name, ordinal, raw: parts.__setitem__((name, ordinal), raw),
    )
    documents = {
        "manifest": encode(manifest),
        "plan": encode(plan),
        "scope": encode(scope),
    }
    root = sha(documents["manifest"])
    ref = {
        "datasetId": DATASET,
        "datasetRoot": root,
        "format": "atlas.quant.market_dataset",
        "version": 1,
    }
    evidence = {
        "marketDatasetRef": ref,
        "universeScopeRef": plan["universeScopeRef"],
        "admissionProfile": FULL_FILTER_PROFILE_ID,
        # Server admission supplies this; client must preserve it, not invent one.
        "rowValueRoot": "f" * 64,
    }
    prefix = "/quant/api/runner/research-markets/" + JOB + "/"
    query = "?datasetRoot=" + root
    job = {
        "id": JOB,
        "leaseToken": LEASE,
        "jobKind": "forecast",
        "dataSource": "ready_market",
        "dataset": None,
        "strategy": strategy,
        **{
            k: evidence[k]
            for k in ("marketDatasetRef", "universeScopeRef", "admissionProfile")
        },
        "sourceEvidence": evidence,
        "marketInputUrl": prefix + "input",
        "resultTransport": {"format": "atlas.quant.bundle", "version": 1},
    }
    meta = {
        "job": {"id": JOB, "kind": "forecast"},
        **{
            k: job[k]
            for k in (
                "marketDatasetRef",
                "universeScopeRef",
                "admissionProfile",
                "sourceEvidence",
            )
        },
        "documents": {
            name: {
                "sha256": sha(raw),
                "byteLength": len(raw),
                "url": prefix + name + query,
            }
            for name, raw in documents.items()
        },
        "partUrlTemplate": prefix + "parts/{collection}/{ordinal}" + query,
    }
    responses = {prefix + name + query: raw for name, raw in documents.items()}
    responses.update(
        {
            prefix + f"parts/{name}/{ordinal}" + query: raw
            for (name, ordinal), raw in parts.items()
        }
    )
    return job, meta, responses, manifest


def prepare(tmp_path, source, mutate=None, check=lambda: None):
    job, meta, responses = deepcopy(source[:3])
    if mutate:
        mutate(job, meta, responses)
    responses[job["marketInputUrl"]] = encode(meta)
    session = Session({key: Response(raw) for key, raw in responses.items()})
    spool = runner.CompletionSpool(config(tmp_path))
    result = MarketResearchClient(config(tmp_path), session).prepare(
        job, spool, deadline=time.monotonic() + 30, check=check
    )
    return result, spool, session


def forbidden(*args, **kwargs):
    pytest.fail("Review must never contact a provider or fit a model")


def test_source_download_is_exact_lease_encrypted_and_provider_free(
    tmp_path, market_source
):
    job, spool, session = prepare(tmp_path, market_source)
    store = MarketResearchSpool.from_spool(spool, job)
    frame, provenance = store.inputs(job).research_input()
    assert len(frame) == market_source[3]["rowCount"]
    assert set(frame["ts_code"]) == set(job["strategy"]["universe"]["symbols"])
    assert provenance["synthetic"] is True
    assert session.trust_env is False
    assert len(session.calls) == 8  # input, 3 documents, 4 complete collections
    for method, url, kwargs in session.calls:
        assert method == "GET" and url.startswith(
            "https://example.test/quant/api/runner/research-markets/"
        )
        assert kwargs["headers"]["X-Dataset-Lease"] == LEASE
        assert kwargs["headers"]["Authorization"] == "Bearer " + "q" * 48
        assert kwargs["allow_redirects"] is False
    assert all(
        p.suffix == ".enc" and b"SYNTHETIC_MARKET" not in p.read_bytes()
        for p in store.root.iterdir()
    )


@pytest.mark.parametrize(
    "attack",
    [
        "provider",
        "pcd",
        "replay",
        "profile",
        "lease",
        "redirect",
        "template",
        "root",
        "part",
    ],
)
def test_hostile_source_is_rejected_without_complete_input_marker(
    tmp_path, market_source, attack
):
    def mutate(job, meta, responses):
        if attack in {"provider", "pcd", "replay"}:
            key = {
                "provider": "providerAccess",
                "pcd": "pcdAccess",
                "replay": "replayBundle",
            }[attack]
            job[key] = None  # Presence alone is forbidden, even an empty credential.
        elif attack == "profile":
            job["admissionProfile"] = "unregistered-profile"
        elif attack == "lease":
            meta["job"]["id"] = DATASET
        elif attack == "redirect":
            meta["documents"]["plan"]["url"] = "https://untrusted.test/plan"
        elif attack == "template":
            meta["partUrlTemplate"] += "&redirect=https://untrusted.test"
        elif attack == "root":
            meta["documents"]["manifest"]["sha256"] = "e" * 64
        else:
            path = next(k for k in responses if "/parts/raw/" in k)
            responses[path] += b" "

    with pytest.raises(runner.RunnerError):
        prepare(tmp_path, market_source, mutate)
    assert not list((tmp_path / "research").rglob("input.enc"))
    assert not (tmp_path / "research" / "bundles").exists()


def test_cancel_mid_download_retains_partial_source_without_ready_marker(
    tmp_path, market_source
):
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks == 12:
            raise runner.RunnerError("JOB_CANCELLED", "Synthetic cancellation")

    with pytest.raises(runner.RunnerError) as error:
        prepare(tmp_path, market_source, check=cancelled)
    assert error.value.code == "JOB_CANCELLED"
    files = list((tmp_path / "research" / "research-market-inputs").rglob("*.enc"))
    assert files and all(p.name != "input.enc" for p in files)


def test_authenticated_source_cannot_move_to_another_lease_or_result_namespace(
    tmp_path, market_source
):
    job, spool, _ = prepare(tmp_path, market_source)
    store = MarketResearchSpool.from_spool(spool, job)
    other = MarketResearchSpool.from_spool(spool, dict(job, leaseToken=DATASET))
    result = BundleSpool.from_spool(spool, job)
    for target in (other, result):
        target.write("input", b"placeholder")
        target.root.joinpath("input.enc").write_bytes(
            store.root.joinpath("input.enc").read_bytes()
        )
        with pytest.raises(runner.RunnerError) as error:
            target.read("input")
        assert error.value.code == "DELIVERY_INTEGRITY"


def test_capabilities_require_literal_opt_in_and_prepare_strips_config_credentials(
    tmp_path, market_source
):
    for value in (False, None, 1, "true"):
        assert "marketResearchProfiles" not in claim_request(JOB, market_datasets=value)
    assert claim_request(JOB, market_datasets=True)["marketResearchProfiles"] == [
        FULL_FILTER_PROFILE_ID,
        AUTO_FILTER_CANDIDATE_ID,
    ]
    job = dict(
        market_source[0],
        providerAccess={"token": "secret"},
        pcdAccess={"token": "secret"},
    )
    prepared = runner.prepare_job(
        job, dict(config(tmp_path), provider_access={"token": "config-secret"})
    )
    assert "providerAccess" not in prepared and "pcdAccess" not in prepared
    assert prepared["sourceEvidence"] == job["sourceEvidence"]


def test_market_opt_in_requires_shared_compute_slot_in_configuration_and_child(
    tmp_path, market_source, monkeypatch
):
    path = tmp_path / "runner-config.json"
    path.write_text(
        json.dumps(dict(config(tmp_path), market_dataset_research_enabled=True))
    )
    path.chmod(0o600)
    with pytest.raises(runner.RunnerError) as error:
        runner.load_config(str(path))
    assert error.value.code == "CONFIG_COMPUTE_SLOT"
    monkeypatch.setattr(computation, "run_capacity_research", forbidden)
    with pytest.raises(runner.RunnerError) as error:
        computation.compute(
            market_source[0], {}, slot_path=None, deadline=time.monotonic() + 30
        )
    assert error.value.code == "CONFIG_COMPUTE_SLOT"


@pytest.mark.parametrize("token", [None, "forbidden-provider-token"])
def test_child_scrubs_provider_environment_and_rejects_explicit_token(
    tmp_path, market_source, monkeypatch, token
):
    job, spool, _ = prepare(tmp_path, market_source)
    answers, seen = [], []

    class Pipe:
        def send(self, value):
            answers.append(value)

        def close(self):
            seen.append("closed")

    def fake_compute(*args, **kwargs):
        assert "TUSHARE_TOKEN" not in os.environ
        assert args[0] == job
        seen.append("compute")
        return {"review": "mocked-compute"}

    monkeypatch.setenv("TUSHARE_TOKEN", "must-not-reach-compute")
    monkeypatch.setattr(computation, "compute", fake_compute)
    runner._child_entry(
        Pipe(),
        job,
        token,
        None,
        None,
        bundle_context=BundleSpool.context_for(spool, job),
        deadline=time.monotonic() + 30,
    )
    if token is None:
        assert answers == [{"review": "mocked-compute"}] and seen == [
            "compute",
            "closed",
        ]
    else:
        assert answers[0]["error"]["code"] == "MARKET_SOURCE_IDENTITY" and seen == [
            "closed"
        ]


def test_compute_failure_releases_shared_slot_removes_cache_and_keeps_source(
    tmp_path, market_source, monkeypatch
):
    job, spool, _ = prepare(tmp_path, market_source)
    store = MarketResearchSpool.from_spool(spool, job)
    slot_dir = tmp_path / "shared-slot"
    slot_dir.mkdir(mode=0o700)
    slot = str(slot_dir / "compute.lock")

    def fails_after_recomposition(strategy, frame, provenance, **kwargs):
        assert len(frame) == market_source[3]["rowCount"]
        assert provenance["marketSource"] == job["sourceEvidence"]
        assert kwargs["profile_id"] == job["admissionProfile"]
        descriptor = os.open(slot, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        kwargs["cache_dir"].joinpath("partial-private-panel").write_bytes(b"partial")
        raise runner.RunnerError("REVIEW_COMPUTE_FAILURE", "No fit was invoked")

    monkeypatch.setattr(computation, "run_capacity_research", fails_after_recomposition)
    with pytest.raises(runner.RunnerError) as error:
        computation.compute(
            job, store.context, slot_path=slot, deadline=time.monotonic() + 30
        )
    assert error.value.code == "REVIEW_COMPUTE_FAILURE"
    assert not store.root.joinpath("cache").exists()
    assert store.root.joinpath("input.enc").exists()
    assert not BundleSpool(store.context).root.joinpath("manifest.enc").exists()
    descriptor = os.open(slot, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    "attack,code",
    [
        ("rss", "CAPACITY_MEMORY"),
        ("fit", "CAPACITY_FIT_TIMEOUT"),
        ("cache", "CAPACITY_DISK"),
        ("ps_failure", "CAPACITY_MONITOR"),
        ("ps_empty", "CAPACITY_MONITOR"),
    ],
)
def test_parent_monitor_fails_closed_on_resource_or_measurement_failure(
    tmp_path, monkeypatch, attack, code
):
    spool = runner.CompletionSpool(config(tmp_path))
    context = BundleSpool.context_for(spool, {"id": JOB, "leaseToken": LEASE})
    monitor = limits.MarketProcessBudget(context)
    state = SimpleNamespace(
        stdout=str(3 * 1024**2 + 1) if attack == "rss" else "1024", returncode=0
    )
    if attack in {"ps_failure", "ps_empty"}:
        state.stdout = ""
        state.returncode = 1 if attack == "ps_failure" else 0
    monkeypatch.setattr(limits.subprocess, "run", lambda *args, **kwargs: state)
    if attack == "fit":
        monitor.store.write(
            "progress",
            encode(
                {"phase": "fit_started", "startedMonotonic": time.monotonic() - 301}
            ),
        )
    if attack == "cache":
        cache = monitor.store.root / "cache"
        cache.mkdir(mode=0o700)
        with cache.joinpath("sparse-cache").open("wb") as stream:
            stream.truncate(400 * 1024**2 + 1)
    with pytest.raises(runner.RunnerError) as error:
        monitor.check(os.getpid())
    assert error.value.code == code


class CompletionQueue:
    def __init__(self, job, request_id=None):
        self.job, self.request_id = job, request_id
        self.terminal, self.unknown = False, False
        self.errors, self.claims = [], []

    def post(self, route, payload, **kwargs):
        if route == "complete":
            assert set(payload) == {"id", "leaseToken", "error"}
            self.errors.append(deepcopy(payload["error"]))
            self.terminal = True
            if self.unknown:
                raise runner.RunnerError("QUEUE_NETWORK", "Synthetic lost ACK")
            return {"ok": True}
        assert route == "claim"
        self.claims.append(deepcopy(payload))
        if self.unknown:
            raise runner.RunnerError("QUEUE_NETWORK", "Synthetic unknown receipt")
        if self.request_id is None:
            self.request_id = payload["requestId"]
        assert payload["requestId"] == self.request_id
        return {
            "job": None if self.terminal else deepcopy(self.job),
            "claim": {
                "requestId": self.request_id,
                "jobId": JOB,
                "status": "failed" if self.terminal else "running",
            },
        }


def test_unknown_completion_retains_exact_sources_and_partial_result_until_receipt(
    tmp_path, market_source, monkeypatch
):
    job, spool, _ = prepare(tmp_path, market_source)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    claims.executing(intent, job)
    inputs = MarketResearchSpool.from_spool(spool, job)
    output = BundleSpool.from_spool(spool, job)
    output.write("partial", b"synthetic incomplete output")
    spool.write(
        {
            "id": JOB,
            "leaseToken": LEASE,
            "error": {"code": "REVIEW_FAILURE", "message": "Synthetic"},
            "_claimRequestId": intent["requestId"],
            "_marketKey": inputs.key,
            "_bundleKey": output.key,
        }
    )
    queue = CompletionQueue(job, intent["requestId"])
    queue.unknown = True
    monkeypatch.setattr(runner, "_wait", lambda _: None)
    monkeypatch.setattr(runner, "execute_bounded", forbidden)
    with pytest.raises(runner.RunnerError) as error:
        runner.flush_completions(queue, spool)
    assert error.value.code == "COMPLETION_UNCONFIRMED"
    assert inputs.root.exists() and output.root.exists() and claims.read()
    assert list(spool.pending())
    queue.unknown = False
    runner.flush_completions(queue, runner.CompletionSpool(config(tmp_path)))
    assert not inputs.root.exists() and not output.root.exists()
    assert claims.read() is None and not list(spool.pending())


@pytest.mark.parametrize("remote_terminal", [False, True])
def test_restart_never_refetches_or_refits_and_cleans_only_matching_lease(
    tmp_path, market_source, monkeypatch, remote_terminal
):
    job, spool, _ = prepare(tmp_path, market_source)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    claims.executing(intent, job)
    inputs = MarketResearchSpool.from_spool(spool, job)
    output = BundleSpool.from_spool(spool, job)
    output.write("partial", b"unfinished output")
    other = MarketResearchSpool.from_spool(spool, {"id": DATASET, "leaseToken": LEASE})
    other.write("input", b"unrelated lease evidence")
    queue = CompletionQueue(market_source[0], intent["requestId"])
    queue.terminal = remote_terminal
    monkeypatch.setattr(runner, "QueueClient", lambda _: queue)
    monkeypatch.setattr(runner, "execute_bounded", forbidden)
    monkeypatch.setattr(MarketResearchClient, "prepare", forbidden)
    monkeypatch.setattr(runner, "STOP", False)
    assert (
        runner.serve(
            dict(config(tmp_path), market_dataset_research_enabled=True), once=True
        )
        == 0
    )
    assert queue.errors == (
        []
        if remote_terminal
        else [
            {
                "code": "RUNNER_INTERRUPTED",
                "message": "运行服务在计算或取数期间中断；本次实验停止，未自动重放。",
            }
        ]
    )
    assert not inputs.root.exists() and not output.root.exists()
    assert other.read("input") == b"unrelated lease evidence"
    assert not list(spool.pending()) and claims.read() is None
