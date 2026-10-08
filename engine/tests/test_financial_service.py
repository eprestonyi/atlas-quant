"""Financial durability and real offline computation; no provider or model fit."""

from copy import deepcopy
from datetime import datetime, timezone
import multiprocessing
import os
from pathlib import Path
import stat
import threading
import time
import uuid

import pytest

from atlas_quant.runner import RunnerError
from atlas_quant.financial_runner import service
from atlas_quant.financial_runner.protocol import CHUNK_BYTES, encode, sha
from atlas_quant.financial_runner.spool import FinancialSpool

JOB = "11111111-1111-4111-8111-111111111111"
INPUT = "22222222-2222-4222-8222-222222222222"
LEASE = "33333333-3333-4333-8333-333333333333"
PUBLICATION = "44444444-4444-4444-8444-444444444444"


def iso(offset):
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).isoformat()


def config(tmp_path):
    return {
        "api_base": "https://example.test/quant/api",
        "runner_secret": "x" * 48,
        "delivery_dir": str(tmp_path / "research"),
        "poll_seconds": 3,
    }


class IdleQueue:
    def __init__(self, ready):
        self.ready, self.heartbeats, self.requests = ready, 0, []

    def post(self, route, payload, **kwargs):
        if route == "heartbeat":
            self.heartbeats += 1
            return self.ready
        assert route == "claim"
        self.requests.append(payload["requestId"])
        return {
            "claim": {"requestId": payload["requestId"], "status": "empty"},
            "job": None,
        }


def test_idle_service_does_not_create_durable_empty_claims(tmp_path):
    cfg = dict(config(tmp_path), poll_seconds=0)
    queue = IdleQueue({"ok": True, "canClaim": False})
    assert (
        service.serve(
            cfg,
            client_factory=lambda _: queue,
            stop_requested=lambda: queue.heartbeats >= 50,
        )
        == 0
    )
    assert queue.heartbeats == 50 and queue.requests == []
    assert FinancialSpool(cfg).read() is None


@pytest.mark.parametrize("ready", [{"ok": True, "canClaim": False}, {"ok": True}])
def test_idle_advice_cannot_block_unknown_claim_recovery(tmp_path, ready):
    cfg = config(tmp_path)
    spool = FinancialSpool(cfg)
    original = spool.current_or_create()
    queue = IdleQueue(ready)
    assert service.serve(cfg, once=True, client_factory=lambda _: queue) == 0
    assert queue.requests == [original["requestId"]]
    assert spool.read() is None


def test_ready_advice_still_uses_durable_claim_when_queue_races_empty(tmp_path):
    cfg = config(tmp_path)
    queue = IdleQueue({"ok": True, "canClaim": True})
    assert service.serve(cfg, once=True, client_factory=lambda _: queue) == 0
    assert len(queue.requests) == 1
    assert FinancialSpool(cfg).read() is None


@pytest.mark.parametrize(
    "ready", [None, {"ok": True}, {"ok": True, "canClaim": "false"}]
)
def test_missing_or_malformed_ready_advice_cannot_create_claim(tmp_path, ready):
    cfg = config(tmp_path)
    queue = IdleQueue(ready)
    assert service.serve(cfg, once=True, client_factory=lambda _: queue) == 1
    assert queue.requests == [] and FinancialSpool(cfg).read() is None


def job():
    return {
        "id": JOB,
        "inputId": INPUT,
        "kind": "financial_validate",
        "leaseToken": LEASE,
        "leaseUntil": iso(120),
        "deadline": iso(180),
        "inputUrl": "/quant/api/runner/financial/jobs/" + JOB + "/input",
    }


def publication(job, write_chunk):
    raw = b'{"fixture":"service-transport-double-only"}'
    middle = len(raw) // 2
    chunks = [raw[:middle], raw[middle:]]
    for ordinal, content in enumerate(chunks):
        write_chunk("package", ordinal, content)
    return {
        "format": "atlas.quant.financial-result",
        "version": 1,
        "kind": "validated",
        "inputId": job["inputId"],
        "roots": {
            "inputRoot": "a" * 64,
            "packRoot": "b" * 64,
            "calendarRoot": "c" * 64,
            "preparedRoot": None,
        },
        "summary": {},
        "collections": {
            "package": {
                "encoding": "bytes",
                "rowCount": None,
                "byteLength": len(raw),
                "sha256": sha(raw),
                "chunks": [
                    {
                        "ordinal": i,
                        "startRow": None,
                        "rowCount": None,
                        "byteLength": len(c),
                        "sha256": sha(c),
                    }
                    for i, c in enumerate(chunks)
                ],
            }
        },
    }


def child_compute(job, meta, source, registries, write_chunk):
    return publication(job, write_chunk)


def child_slow(job, meta, source, registries, write_chunk):
    time.sleep(60)


def child_brief(job, meta, source, registries, write_chunk):
    time.sleep(0.15)
    return publication(job, write_chunk)


def child_exit(job, meta, source, registries, write_chunk):
    write_chunk("package", 0, b'{"partial":')
    os._exit(7)


def child_commit_without_ack(connection, context, meta, source, registries, computer):
    store = FinancialSpool.from_context(context).publication(context["job"])
    store.finalize(publication(context["job"], store.write_chunk))
    os._exit(0)


class Queue:
    def __init__(self):
        self.job = job()
        self.status = "running"
        self.requests, self.calls, self.chunks = [], [], {}
        self.request_id = None
        self.manifest = None
        self.manifest_sha = None
        self.inputs_read = self.computations = 0
        self.lose_claim = self.lose_begin = self.lose_chunk = self.lose_complete = (
            self.lose_fail
        ) = False
        self.missing_override = None
        self.receipt_mutation = None
        self.status_unavailable = False
        self.heartbeat_count = 0
        self.cancel_at_heartbeat = None

    def post(self, route, payload, **kwargs):
        self.calls.append((route, deepcopy(payload)))
        if route == "heartbeat":
            if "jobId" not in payload:
                return {"ok": True}
            self.heartbeat_count += 1
            if self.cancel_at_heartbeat == self.heartbeat_count:
                self.status = "cancel_requested"
            return {
                "ok": True,
                "leaseValid": self.status in {"running", "cancel_requested"},
                "cancelRequested": self.status == "cancel_requested",
                "leaseUntil": self.job["leaseUntil"],
            }
        if route == "claim":
            self.requests.append(payload["requestId"])
            if self.request_id is None:
                self.request_id = payload["requestId"]
            assert payload["requestId"] == self.request_id
            value = {
                "claim": {
                    "requestId": self.request_id,
                    "jobId": JOB,
                    "status": self.status,
                },
                "job": (
                    deepcopy(self.job)
                    if self.status in {"running", "cancel_requested"}
                    else None
                ),
            }
            if self.lose_claim:
                self.lose_claim = False
                raise RunnerError("FINANCIAL_NETWORK", "unknown claim")
            if self.receipt_mutation:
                self.receipt_mutation(value)
            return value
        if route == "publications/begin":
            if self.manifest is None:
                self.manifest = deepcopy(payload["manifest"])
                self.manifest_sha = sha(encode(self.manifest))
            assert self.manifest == payload["manifest"]
            if self.lose_begin:
                self.lose_begin = False
                raise RunnerError("FINANCIAL_NETWORK", "unknown begin")
            return self.publication_status()
        if route == "complete":
            assert self.status == "running"
            assert payload["publicationId"] == PUBLICATION
            assert payload["manifestSha256"] == self.manifest_sha
            assert not self.publication_status()["missing"]
            self.status = "completed"
            if self.lose_complete:
                self.lose_complete = False
                raise RunnerError("FINANCIAL_NETWORK", "unknown completion")
            return {
                "ok": True,
                "status": "completed",
                "inputId": INPUT,
                "preparationId": None,
            }
        if route == "fail":
            self.status = (
                "cancelled" if payload["error"]["code"] == "CANCELLED" else "failed"
            )
            if self.lose_fail:
                self.lose_fail = False
                raise RunnerError("FINANCIAL_NETWORK", "unknown failure")
            return {"ok": True, "status": self.status}
        pytest.fail("Unexpected financial route: " + route)

    def publication_status(self):
        missing = [
            {"collection": name, "ordinal": part["ordinal"]}
            for name, collection in self.manifest["collections"].items()
            for part in collection["chunks"]
            if (name, part["ordinal"]) not in self.chunks
        ]
        return {
            "publicationId": PUBLICATION,
            "manifestSha256": self.manifest_sha,
            "status": "committed" if self.status == "completed" else "staging",
            "missing": (
                self.missing_override if self.missing_override is not None else missing
            ),
        }

    def get(self, route, lease, **kwargs):
        assert lease == LEASE
        if route == "jobs/" + JOB + "/status":
            if self.status_unavailable:
                raise RunnerError("FINANCIAL_NETWORK", "unknown terminal readback")
            return {"job": {"id": JOB, "inputId": INPUT, "status": self.status}}
        assert (
            route
            == "publications/" + PUBLICATION + "?manifestSha256=" + self.manifest_sha
        )
        return self.publication_status()

    def inputs(self, job, **kwargs):
        self.inputs_read += 1
        kwargs["heartbeat"]()
        return {}, b"source", {}

    def upload(self, pub_id, manifest_sha, name, ordinal, raw, job, **kwargs):
        assert pub_id == PUBLICATION and manifest_sha == self.manifest_sha
        self.chunks[(name, ordinal)] = raw
        if self.lose_chunk:
            self.lose_chunk = False
            raise RunnerError("FINANCIAL_NETWORK", "unknown chunk acknowledgement")
        return {"ok": True}

    def compute(self, spool, job, inputs, monitor):
        assert spool.read()["phase"] == "computing"
        monitor.check()
        self.computations += 1
        store = spool.publication(job)
        store.finalize(publication(job, store.write_chunk))


def cycle(cfg, queue, **kwargs):
    return service.run_once(
        cfg, FinancialSpool(cfg), queue, queue, bounded_compute=queue.compute, **kwargs
    )


def started(cfg, queue, phase="computing"):
    spool = FinancialSpool(cfg)
    state = spool.current_or_create()
    response = queue.post("claim", {"requestId": state["requestId"]})
    return spool, spool.save(dict(state, phase=phase, job=response["job"]))


def test_claim_intent_is_encrypted_and_durable_before_transport(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    original = queue.post

    def post(route, payload, **kwargs):
        if route == "claim":
            assert FinancialSpool(cfg).read()["requestId"] == payload["requestId"]
        return original(route, payload, **kwargs)

    queue.post = post
    cycle(cfg, queue)
    assert queue.computations == 1 and FinancialSpool(cfg).read() is None


def test_lost_claim_ack_reuses_uuid_and_computes_once(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    queue.lose_claim = True
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    original = FinancialSpool(cfg).read()
    assert original["phase"] == "claiming" and queue.computations == 0
    cycle(cfg, queue)
    assert queue.computations == 1 and set(queue.requests) == {original["requestId"]}


@pytest.mark.parametrize("lost", ["lose_begin", "lose_chunk", "lose_complete"])
def test_publication_ack_loss_only_resumes_original_delivery(tmp_path, lost):
    cfg, queue = config(tmp_path), Queue()
    setattr(queue, lost, True)
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    state = FinancialSpool(cfg).read()
    assert state["phase"] == "publishing" and queue.computations == 1
    cycle(cfg, queue)
    assert queue.status == "completed" and queue.computations == queue.inputs_read == 1
    assert set(queue.requests) == {state["requestId"]}
    assert FinancialSpool(cfg).read() is None


def test_unknown_terminal_readback_retains_complete_spool(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    queue.status_unavailable = True
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    spool = FinancialSpool(cfg)
    assert spool.read()["phase"] == "publishing"
    assert spool.publication(queue.job).manifest()
    # Matching terminal claim receipt is independently sufficient on restart.
    cycle(cfg, queue)
    assert spool.read() is None and queue.computations == 1


def test_interrupted_compute_with_partial_chunks_fails_without_recompute(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    spool, state = started(cfg, queue)
    spool.publication(queue.job).write_chunk("package", 0, b"partial")
    cycle(cfg, queue)
    failure = next(payload for route, payload in queue.calls if route == "fail")
    assert failure["error"]["code"] == "RUNNER_INTERRUPTED"
    assert not queue.inputs_read and not queue.computations
    assert spool.read() is None and not list(spool.root.glob("*/package-*.enc"))


def test_crash_after_manifest_before_parent_ack_recovers_delivery(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    spool, _ = started(cfg, queue)
    store = spool.publication(queue.job)
    store.finalize(publication(queue.job, store.write_chunk))
    cycle(cfg, queue)
    assert queue.status == "completed" and queue.computations == queue.inputs_read == 0


def test_lost_failure_ack_preserves_same_claim_then_clears_terminal(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    spool, _ = started(cfg, queue)
    queue.lose_fail = True
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    assert spool.read()["phase"] == "failing"
    cycle(cfg, queue)
    assert queue.status == "failed" and spool.read() is None and not queue.computations


@pytest.mark.parametrize("phase", ["claiming", "claimed", "computing", "publishing"])
def test_terminal_claim_never_recomputes_after_cancellation_or_expiry(tmp_path, phase):
    cfg, queue = config(tmp_path), Queue()
    spool, state = started(cfg, queue, phase=phase)
    if phase == "claiming":
        state.pop("job")
        spool.save(state)
    queue.status = "cancelled" if phase in {"claimed", "publishing"} else "failed"
    cycle(cfg, queue)
    assert spool.read() is None and queue.inputs_read == queue.computations == 0


def test_cancellation_before_compute_is_acknowledged_without_reading_input(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    queue.cancel_at_heartbeat = 1
    cycle(cfg, queue)
    assert (
        queue.status == "cancelled" and not queue.inputs_read and not queue.computations
    )


def test_stop_does_not_resume_computation_on_restart(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    cycle(cfg, queue, stop_requested=lambda: True)
    assert queue.status == "failed" and not queue.computations
    assert any(
        payload.get("error", {}).get("code") == "RUNNER_STOPPED"
        for _, payload in queue.calls
    )


@pytest.mark.parametrize(
    "missing",
    [
        [{"collection": "secrets", "ordinal": 0}],
        [
            {"collection": "package", "ordinal": 0},
            {"collection": "package", "ordinal": 0},
        ],
        [{"collection": "package", "ordinal": True}],
    ],
)
def test_server_missing_list_cannot_request_unknown_or_duplicate_chunks(
    tmp_path, missing
):
    cfg, queue = config(tmp_path), Queue()
    queue.missing_override = missing
    with pytest.raises(RunnerError, match="分片|缺片"):
        cycle(cfg, queue)
    assert not queue.chunks and FinancialSpool(cfg).read()["phase"] == "publishing"


def test_changed_lease_or_deadline_cannot_replace_frozen_claim(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    spool, old = started(cfg, queue, phase="claimed")
    queue.job["deadline"] = iso(500)
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    assert spool.read() == old and not queue.inputs_read


def test_spool_key_domain_permissions_and_tampering(tmp_path):
    cfg = config(tmp_path)
    spool = FinancialSpool(cfg)
    state = spool.current_or_create()
    path = spool.root / "current.enc"
    raw = path.read_bytes()
    assert state["requestId"].encode() not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(spool.root.stat().st_mode) == 0o700
    with pytest.raises(RunnerError):
        FinancialSpool(dict(cfg, api_base="https://other.test/quant/api")).read()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(RunnerError):
        spool.read()
    assert path.exists()


def test_chunk_aad_prevents_collection_job_and_ordinal_substitution(tmp_path):
    spool = FinancialSpool(config(tmp_path))
    store = spool.publication(job())
    manifest = publication(store.job, store.write_chunk)
    left, right = store.root / "package-0.enc", store.root / "package-1.enc"
    right.write_bytes(left.read_bytes())
    with pytest.raises(RunnerError):
        store.finalize(manifest)
    assert not (store.root / "manifest.enc").exists()


def test_manifest_cannot_commit_incomplete_or_undeclared_chunks(tmp_path):
    spool = FinancialSpool(config(tmp_path))
    store = spool.publication(job())
    manifest = publication(store.job, store.write_chunk)
    store.write_chunk("panel", 0, b"[{}]")
    with pytest.raises(RunnerError):
        store.finalize(manifest)
    assert not (store.root / "manifest.enc").exists()


def test_result_budgets_are_applied_before_chunk_write(tmp_path, monkeypatch):
    import atlas_quant.financial_runner.spool as module

    store = FinancialSpool(config(tmp_path)).publication(job())
    with pytest.raises(RunnerError):
        store.write_chunk("package", 0, b"x" * (CHUNK_BYTES + 1))
    monkeypatch.setattr(module, "RESULT_BYTES", 3)
    with pytest.raises(RunnerError):
        store.write_chunk("package", 0, b"1234")
    assert not list(store.root.iterdir())


def test_terminal_cleanup_is_restart_safe_and_keeps_old_research_spool(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    old = Path(cfg["delivery_dir"])
    old.mkdir()
    retained = old / "never-touch.enc"
    retained.write_bytes(b"old research")
    spool, state = started(cfg, queue)
    store = spool.publication(queue.job)
    store.write_chunk("package", 0, b"partial")
    terminal = spool.save(dict(state, phase="terminal", terminalStatus="cancelled"))
    store.cleanup()  # Crash after deleting payload but before removing intent.
    spool.cleanup(terminal)
    spool.cleanup(terminal)
    assert retained.read_bytes() == b"old research" and spool.read() is None


def test_spool_cannot_share_research_directory_and_uses_exclusive_lock(tmp_path):
    cfg = config(tmp_path)
    with pytest.raises(RunnerError):
        FinancialSpool(dict(cfg, financial_delivery_dir=cfg["delivery_dir"]))
    with pytest.raises(RunnerError):
        FinancialSpool(
            dict(
                cfg, financial_delivery_dir=str(tmp_path / "other" / ".." / "research")
            )
        )
    first, second = FinancialSpool(cfg), FinancialSpool(cfg)
    with first.locked():
        with pytest.raises(RunnerError):
            with second.locked():
                pytest.fail("a second consumer acquired the same spool")


def test_background_heartbeat_runs_during_blocking_input_and_delivery(tmp_path):
    queue = Queue()
    monitor = service.LeaseMonitor(queue, queue.job, interval=0.03)
    fixed_deadline = monitor.deadline
    with monitor:
        time.sleep(0.12)  # Represents a bounded blocking source/upload request.
        monitor.check()
    assert queue.heartbeat_count >= 3 and monitor.deadline == fixed_deadline


@pytest.mark.parametrize("method", [child_compute, child_slow, child_exit])
def test_actual_spawned_child_success_hard_deadline_and_crash(tmp_path, method):
    spool, queue = FinancialSpool(config(tmp_path)), Queue()
    monitor = service.LeaseMonitor(queue, queue.job)
    if method is child_slow:
        monitor.deadline = time.monotonic() + 0.3
    before = {p.pid for p in multiprocessing.active_children()}
    if method is child_compute:
        service.execute_bounded(
            spool, queue.job, ({}, b"source", {}), monitor, computer=method
        )
        assert spool.publication(queue.job).manifest()
    else:
        with pytest.raises(RunnerError) as error:
            service.execute_bounded(
                spool, queue.job, ({}, b"source", {}), monitor, computer=method
            )
        assert error.value.code == (
            "FINANCIAL_DEADLINE" if method is child_slow else "FINANCIAL_CHILD_EXIT"
        )
        assert spool.publication(queue.job).manifest() is None
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_actual_child_is_killed_after_mid_compute_cancellation(tmp_path):
    spool, queue = FinancialSpool(config(tmp_path)), Queue()
    queue.cancel_at_heartbeat = 2
    monitor = service.LeaseMonitor(queue, queue.job, interval=0.05)
    with monitor:
        with pytest.raises(RunnerError) as error:
            service.execute_bounded(
                spool, queue.job, ({}, b"source", {}), monitor, computer=child_slow
            )
    assert error.value.code == "CANCELLED"
    assert not multiprocessing.active_children()


def test_actual_child_durable_commit_survives_missing_pipe_ack(tmp_path, monkeypatch):
    spool, queue = FinancialSpool(config(tmp_path)), Queue()
    monkeypatch.setattr(service, "_child_entry", child_commit_without_ack)
    service.execute_bounded(
        spool, queue.job, ({}, b"source", {}), service.LeaseMonitor(queue, queue.job)
    )
    assert spool.publication(queue.job).manifest()


@pytest.mark.parametrize("which", ["leaseUntil", "deadline"])
def test_expired_clock_never_reads_or_computes(tmp_path, which):
    cfg, queue = config(tmp_path), Queue()
    queue.job[which] = iso(-1)
    if which == "deadline":
        queue.job["leaseUntil"] = iso(-2)
    cycle(cfg, queue)
    assert not queue.inputs_read and not queue.computations and not queue.chunks
    assert queue.status == "failed"


def test_heartbeat_cannot_extend_fixed_remote_or_local_deadline():
    queue = Queue()
    monitor = service.LeaseMonitor(queue, queue.job)
    initial = monitor.deadline
    queue.job["leaseUntil"] = iso(300)
    with pytest.raises(RunnerError) as error:
        monitor.pulse()
    assert error.value.code == "FINANCIAL_PROTOCOL" and monitor.deadline == initial


def test_lost_claim_identity_cannot_be_replaced_by_mismatched_ack(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    queue.receipt_mutation = lambda value: value["claim"].update(
        requestId=str(uuid.uuid4())
    )
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    first = FinancialSpool(cfg).read()
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    assert FinancialSpool(cfg).read() == first and not queue.inputs_read


def test_resume_verifies_chunk_tampering_before_network_publication(tmp_path):
    cfg, queue = config(tmp_path), Queue()
    queue.lose_begin = True
    with pytest.raises(RunnerError):
        cycle(cfg, queue)
    spool = FinancialSpool(cfg)
    path = spool.publication(queue.job).root / "package-0.enc"
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(RunnerError) as error:
        cycle(cfg, queue)
    assert error.value.code == "FINANCIAL_SPOOL_INTEGRITY"
    assert path.exists() and not queue.chunks and queue.computations == 1


def test_manifest_fsync_failure_never_leaves_committed_marker(tmp_path, monkeypatch):
    import atlas_quant.financial_runner.spool as module

    spool = FinancialSpool(config(tmp_path))
    store = spool.publication(job())
    manifest = publication(store.job, store.write_chunk)

    def failed_fsync(_):
        raise OSError("disk sync failed")

    monkeypatch.setattr(module.os, "fsync", failed_fsync)
    with pytest.raises(RunnerError):
        store.finalize(manifest)
    assert not (store.root / "manifest.enc").exists()
    assert len(list(store.root.iterdir())) == 2


def test_unsafe_spool_symlink_is_rejected_without_following(tmp_path):
    cfg = config(tmp_path)
    spool = FinancialSpool(cfg)
    spool.current_or_create()
    original = spool.root / "current.enc"
    retained = spool.root / "retained.enc"
    original.rename(retained)
    original.symlink_to(retained)
    with pytest.raises(RunnerError):
        spool.read()
    assert retained.exists()


def test_raw_compute_errors_never_leak_input_contents():
    assert "secret-amount" not in str(service.safe_error(ValueError("secret-amount")))


@pytest.mark.parametrize("first", [1, 2])
def test_transient_heartbeat_failure_preserves_live_lease_and_computation(
    tmp_path, first
):
    spool, queue = FinancialSpool(config(tmp_path)), Queue()
    original = queue.post
    attempts = []

    def intermittent(route, payload, **kwargs):
        if route == "heartbeat" and "jobId" in payload:
            attempts.append(payload)
            if len(attempts) == first:
                raise RunnerError("FINANCIAL_NETWORK", "transient heartbeat loss")
        return original(route, payload, **kwargs)

    queue.post = intermittent
    monitor = service.LeaseMonitor(queue, queue.job, interval=0.03)
    fixed = monitor.deadline
    with monitor:
        service.execute_bounded(
            spool, queue.job, ({}, b"source", {}), monitor, computer=child_brief
        )
    assert len(attempts) > first and monitor.deadline == fixed
    assert spool.publication(queue.job).manifest()


def test_continuous_heartbeat_failure_stops_at_last_confirmed_lease(tmp_path):
    spool, queue = FinancialSpool(config(tmp_path)), Queue()
    queue.job["leaseUntil"] = iso(0.25)

    def unavailable(*args, **kwargs):
        raise RunnerError("FINANCIAL_HTTP", "temporary outage", http_status=503)

    queue.post = unavailable
    monitor = service.LeaseMonitor(queue, queue.job, interval=0.03)
    confirmed, fixed = monitor.lease_until, monitor.deadline
    began = time.monotonic()
    with monitor:
        with pytest.raises(RunnerError) as error:
            service.execute_bounded(
                spool, queue.job, ({}, b"source", {}), monitor, computer=child_slow
            )
    assert error.value.code == "LEASE_EXPIRED"
    assert monitor.lease_until == confirmed and monitor.deadline == fixed
    assert time.monotonic() - began < 3 and not multiprocessing.active_children()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_definite_heartbeat_rejection_stops_immediately(status):
    queue = Queue()

    def rejected(*args, **kwargs):
        raise RunnerError("FINANCIAL_HTTP", "rejected", http_status=status)

    queue.post = rejected
    monitor = service.LeaseMonitor(queue, queue.job)
    with pytest.raises(RunnerError):
        monitor.pulse()
    assert monitor.last_transient is None


@pytest.mark.parametrize("status", [400, 413])
def test_definite_publication_rejection_records_failure_with_original_spool(
    tmp_path, status
):
    cfg, queue = config(tmp_path), Queue()
    original = queue.post

    def rejected(route, payload, **kwargs):
        if route == "publications/begin":
            raise RunnerError("FINANCIAL_HTTP", "rejected", http_status=status)
        if route == "fail":
            retained = FinancialSpool(cfg)
            assert retained.publication(queue.job).manifest()
            assert retained.read()["phase"] == "failing"
            assert payload["error"]["code"] == "FINANCIAL_RESULT_REJECTED"
        return original(route, payload, **kwargs)

    queue.post = rejected
    cycle(cfg, queue)
    assert queue.status == "failed" and FinancialSpool(cfg).read() is None
    assert queue.computations == 1


@pytest.mark.parametrize(
    "remote_status", ["running", "cancel_requested", "failed", "completed"]
)
def test_publication_conflict_reads_original_lease_before_deciding_terminal(
    tmp_path, remote_status
):
    cfg, queue = config(tmp_path), Queue()
    original = queue.post

    def conflict(route, payload, **kwargs):
        if route == "publications/begin":
            queue.status = remote_status
            raise RunnerError("FINANCIAL_HTTP", "conflict", http_status=409)
        return original(route, payload, **kwargs)

    queue.post = conflict
    cycle(cfg, queue)
    assert FinancialSpool(cfg).read() is None and queue.computations == 1
    if remote_status == "running":
        failure = next(payload for route, payload in queue.calls if route == "fail")
        assert failure["error"]["code"] == "FINANCIAL_PUBLICATION_CONFLICT"
    elif remote_status == "cancel_requested":
        assert queue.status == "cancelled"
    else:
        assert not any(route == "fail" for route, _ in queue.calls)


def test_entire_descriptor_budget_is_checked_before_first_payload_read(
    tmp_path, monkeypatch
):
    store = FinancialSpool(config(tmp_path)).publication(job())
    manifest = publication(store.job, store.write_chunk)
    manifest["collections"]["package"]["chunks"][-1]["byteLength"] = CHUNK_BYTES + 1

    def forbidden(*args):
        pytest.fail("The full metadata plan must be admitted before reading chunks")

    monkeypatch.setattr(store, "read_chunk", forbidden)
    with pytest.raises(RunnerError):
        store.validate_manifest(manifest)


@pytest.fixture(scope="module")
def synthetic_financial_package():
    from test_financial_adapter import run
    from test_financial_package import declarations, freeze

    acquired = run()  # Explicit injected FakeProvider; no external request.
    return freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )


@pytest.mark.parametrize("kind", ["financial_validate", "financial_prepare"])
@pytest.mark.parametrize("lost", [None, "lose_chunk", "lose_complete"])
def test_real_spawned_publication_matches_core_and_recovers_without_recompute(
    tmp_path, synthetic_financial_package, kind, lost
):
    from test_financial_publication import publish, task_inputs

    cfg, queue = config(tmp_path), Queue()
    queue.job.update(kind=kind, deadline=iso(600 if kind.endswith("prepare") else 180))
    _, meta, source, registries = task_inputs(synthetic_financial_package, kind)
    meta["job"] = {key: queue.job[key] for key in ("id", "inputId", "kind")}
    expected, expected_chunks = publish((queue.job, meta, source, registries))

    def read_inputs(claimed, **kwargs):
        assert claimed == queue.job
        kwargs["heartbeat"]()
        queue.inputs_read += 1
        return meta, source, registries

    def actual_compute(spool, claimed, inputs, monitor):
        assert spool.read()["phase"] == "computing"
        queue.computations += 1
        # No computer override: this is the production spawn/import path.
        service.execute_bounded(spool, claimed, inputs, monitor)

    queue.inputs = read_inputs
    if lost:
        setattr(queue, lost, True)
        with pytest.raises(RunnerError) as error:
            service.run_once(
                cfg,
                FinancialSpool(cfg),
                queue,
                queue,
                bounded_compute=actual_compute,
            )
        assert error.value.code == "FINANCIAL_NETWORK"
        spool = FinancialSpool(cfg)
        assert spool.read()["phase"] == "publishing"
        committed = spool.publication(queue.job)
        assert committed.manifest() == expected
        for name, collection in expected["collections"].items():
            for descriptor in collection["chunks"]:
                assert (
                    committed.read_chunk(name, descriptor)
                    == expected_chunks[(name, descriptor["ordinal"])]
                )
        assert all(
            b"USER_DECLARED_UNIT_ASSUMPTION" not in path.read_bytes()
            for path in spool.root.rglob("*.enc")
        )

    service.run_once(
        cfg,
        FinancialSpool(cfg),
        queue,
        queue,
        bounded_compute=actual_compute,
    )
    assert queue.inputs_read == queue.computations == 1
    assert queue.status == "completed"
    assert queue.manifest == expected and queue.chunks == expected_chunks
    assert expected["summary"]["input"]["evidence"]["unitAssumptions"] is True
    assert FinancialSpool(cfg).read() is None
    assert not list(FinancialSpool(cfg).root.rglob("*.enc"))
    assert not multiprocessing.active_children()
