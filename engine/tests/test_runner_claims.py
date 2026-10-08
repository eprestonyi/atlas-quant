"""Transport acknowledgement loss and process interruption must never claim twice."""
import copy
import json
import stat

import pytest

from atlas_quant import runner
from atlas_quant.runner import CompletionSpool, RunnerError
from atlas_quant.runner_claims import ClaimIntent, validate_receipt


def config(tmp_path):
    return {"delivery_dir": str(tmp_path / "delivery"), "api_base": "https://queue.test/api",
            "runner_secret": "x" * 48, "job_timeout": 30}


class Queue:
    """Faithful durable claim receipts: state changes can precede failed delivery."""
    def __init__(self):
        self.records = {}
        self.claims = []
        self.completions = []
        self.lose_claim = False
        self.lose_complete = False
        self.offline_receipt = False
        self.empty = False

    def post(self, route, payload, **kwargs):
        if route == "claim":
            request_id = payload["requestId"]
            self.claims.append(request_id)
            if self.empty and request_id not in self.records:
                return {"job": None, "claim": {"requestId": request_id, "status": "empty"}}
            record = self.records.setdefault(request_id, {"status": "running", "id": "job-" + str(len(self.records)), "lease": "private-lease"})
            if self.lose_claim:
                self.lose_claim = False
                raise RunnerError("QUEUE_NETWORK", "lost ack")
            terminal = record["status"] != "running"
            if terminal and self.offline_receipt:
                raise RunnerError("QUEUE_NETWORK", "receipt offline")
            return {"job": None if terminal else {"id": record["id"], "leaseToken": record["lease"], "dataSource": "demo", "strategy": {}},
                    "claim": {"requestId": request_id, "status": record["status"], "jobId": record["id"]}}
        if route == "complete":
            assert "_claimRequestId" not in payload and "_snapshotKey" not in payload
            self.completions.append(copy.deepcopy(payload))
            record = next(r for r in self.records.values() if r["id"] == payload["id"])
            record["status"] = "failed" if "error" in payload else "completed"
            if self.lose_complete:
                raise RunnerError("QUEUE_NETWORK", "completion ack lost")
            return {"ok": True}
        if route == "heartbeat":
            return {"leaseValid": True}
        pytest.fail("Unexpected network route " + route)


def install(monkeypatch, queue):
    monkeypatch.setattr(runner, "STOP", False)
    monkeypatch.setattr(runner, "QueueClient", lambda config: queue)
    monkeypatch.setattr(runner, "_wait", lambda seconds: None)
    calls = []
    def compute(job, **kwargs):
        calls.append(job["id"])
        return {"result": {"value": 123}}
    monkeypatch.setattr(runner, "execute_bounded", compute)
    return calls


def test_lost_claim_ack_survives_restart_and_computes_same_job_once(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    queue.lose_claim = True
    assert runner.serve(cfg, once=True) == 1
    first = ClaimIntent(CompletionSpool(cfg)).read()
    assert first["phase"] == "pending_claim" and not calls
    assert runner.serve(cfg, once=True) == 0
    assert len(queue.records) == 1 and calls == ["job-0"]
    assert set(queue.claims) == {first["requestId"]}
    assert ClaimIntent(CompletionSpool(cfg)).read() is None
    assert not list(CompletionSpool(cfg).pending())


def test_claim_intent_is_fsynced_before_first_request(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    original = queue.post
    def post(route, payload, **kwargs):
        if route == "claim":
            intent = ClaimIntent(CompletionSpool(cfg)).read()
            assert intent["requestId"] == payload["requestId"]
        return original(route, payload, **kwargs)
    queue.post = post
    assert runner.serve(cfg, once=True) == 0 and calls == ["job-0"]


def test_restart_after_execution_started_fails_same_lease_without_refetch(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    spool = CompletionSpool(cfg)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    response = queue.post("claim", {"requestId": intent["requestId"]})
    claims.executing(intent, response["job"])
    assert runner.serve(cfg, once=True) == 0
    assert not calls and len(queue.records) == 1
    assert queue.completions[0]["error"]["code"] == "RUNNER_INTERRUPTED"
    assert queue.completions[0]["leaseToken"] == "private-lease"
    assert claims.read() is None


def test_claim_completion_ack_loss_preserves_exact_payload_then_terminal_receipt(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    queue.lose_complete = True
    assert runner.serve(cfg, once=True) == 1
    spool = CompletionSpool(cfg)
    intent = ClaimIntent(spool).read()
    assert intent["phase"] == "executing" and len(list(spool.pending())) == 1
    assert calls == ["job-0"]
    queue.lose_complete = False
    # Flush independently: restarting starts by exactly this operation, before a claim.
    runner.flush_completions(queue, CompletionSpool(cfg))
    assert calls == ["job-0"] and len(queue.records) == 1
    assert all(payload == queue.completions[0] for payload in queue.completions)
    assert set(queue.claims) == {intent["requestId"]}
    assert not list(spool.pending()) and ClaimIntent(spool).read() is None


def test_http_success_without_terminal_receipt_does_not_clear_spool(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    queue.offline_receipt = True
    assert runner.serve(cfg, once=True) == 1
    spool = CompletionSpool(cfg)
    assert calls == ["job-0"] and ClaimIntent(spool).read() and list(spool.pending())
    queue.offline_receipt = False
    runner.flush_completions(queue, spool)
    assert not list(spool.pending()) and ClaimIntent(spool).read() is None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_recovery_does_not_recompute_or_claim_new_job(tmp_path, monkeypatch, status):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    spool = CompletionSpool(cfg)
    intent = ClaimIntent(spool).current_or_create()
    queue.post("claim", {"requestId": intent["requestId"]})
    queue.records[intent["requestId"]]["status"] = status
    assert runner.serve(cfg, once=True) == 0
    assert not calls and len(queue.records) == 1 and ClaimIntent(spool).read() is None


def test_old_server_missing_receipt_stops_and_preserves_original_uuid(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    queue.post = lambda *a, **k: {"job": None}
    assert runner.serve(cfg, once=True) == 1
    first = ClaimIntent(CompletionSpool(cfg)).read()
    assert runner.serve(cfg, once=True) == 1
    assert ClaimIntent(CompletionSpool(cfg)).read() == first and not calls


def test_empty_ack_clears_intent(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    queue.empty = True
    calls = install(monkeypatch, queue)
    assert runner.serve(cfg, once=True) == 0
    assert not calls and ClaimIntent(CompletionSpool(cfg)).read() is None


@pytest.mark.parametrize("mutate", [lambda r: r["claim"].update(requestId="wrong"),
    lambda r: r["claim"].update(jobId="other"), lambda r: r["job"].update(leaseToken="rotated"),
    lambda r: r.update(job=None), lambda r: r["claim"].update(status="queued")])
def test_wrong_identity_or_rotated_lease_rejected(mutate):
    intent = {"requestId": "123", "jobId": "job", "leaseToken": "lease"}
    response = {"job": {"id": "job", "leaseToken": "lease"}, "claim": {"requestId": "123", "jobId": "job", "status": "running"}}
    mutate(response)
    with pytest.raises(RunnerError, match="领取回执"):
        validate_receipt(response, intent)


def test_encryption_permissions_tamper_and_queue_binding(tmp_path):
    cfg = config(tmp_path)
    spool = CompletionSpool(cfg)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    claims.executing(intent, {"id": "secret-job", "leaseToken": "never-expose-lease"})
    raw = claims.path.read_bytes()
    assert b"never-expose-lease" not in raw and intent["requestId"].encode() not in raw
    assert stat.S_IMODE(claims.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(claims.root.stat().st_mode) == 0o700
    assert not list(spool.pending())  # claim envelopes cannot be sent as completion
    changed = ClaimIntent(CompletionSpool(dict(cfg, api_base="https://other.test")))
    with pytest.raises(RunnerError) as exc:
        changed.read()
    assert exc.value.code == "CLAIM_INTEGRITY"
    claims.path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(RunnerError):
        claims.read()
    assert claims.path.exists()


def test_clear_refuses_other_request_and_crash_after_clear_is_idempotent(tmp_path):
    claims = ClaimIntent(CompletionSpool(config(tmp_path)))
    intent = claims.current_or_create()
    with pytest.raises(RunnerError):
        claims.clear({"requestId": "other"})
    assert claims.read() == intent
    claims.clear(intent)
    claims.clear(intent)
    assert claims.read() is None


def test_wrong_terminal_echo_keeps_completion_and_claim_intent(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    calls = install(monkeypatch, queue)
    original = queue.post
    def post(route, payload, **kwargs):
        response = original(route, payload, **kwargs)
        if route == "claim" and response["claim"]["status"] == "completed":
            response["claim"]["requestId"] = "wrong-receipt"
        return response
    queue.post = post
    assert runner.serve(cfg, once=True) == 1
    spool = CompletionSpool(cfg)
    assert calls == ["job-0"] and ClaimIntent(spool).read() and len(list(spool.pending())) == 1
    queue.post = original
    runner.flush_completions(queue, spool)
    assert ClaimIntent(spool).read() is None and not list(spool.pending())


def test_rejection_preserves_claim_identity_and_snapshot_until_terminal_ack(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    install(monkeypatch, queue)
    spool = CompletionSpool(cfg)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    job = queue.post("claim", {"requestId": intent["requestId"]})["job"]
    claims.executing(intent, job)
    spool.write({"id": job["id"], "leaseToken": job["leaseToken"], "_claimRequestId": intent["requestId"],
                 "result": {"marker": "result-payload"}, "snapshot": {"private": "frozen-data"}})
    original = queue.post
    def reject_snapshot(route, payload, **kwargs):
        if route == "snapshot":
            raise RunnerError("QUEUE_HTTP", "rejected", http_status=400)
        if route == "complete":
            pending = list(spool.pending())[0][1]
            assert pending["_claimRequestId"] == intent["requestId"] and "_snapshotKey" in pending
            assert payload["error"]["code"] == "RESULT_REJECTED"
            assert "result-payload" not in json.dumps(pending)
        return original(route, payload, **kwargs)
    queue.post = reject_snapshot
    runner.flush_completions(queue, spool)
    assert claims.read() is None and not list(spool.pending())
    assert list((spool.root / "snapshots").glob("*.enc"))
    assert list((spool.root / "quarantine").glob("*/record.enc"))


def test_unsafe_claim_permissions_or_symlink_stop_before_network(tmp_path, monkeypatch):
    cfg, queue = config(tmp_path), Queue()
    install(monkeypatch, queue)
    claims = ClaimIntent(CompletionSpool(cfg))
    claims.current_or_create()
    claims.path.chmod(0o644)
    assert runner.serve(cfg, once=True) == 1 and not queue.claims
    claims.path.chmod(0o600)
    target = claims.path.with_name("retained.enc")
    claims.path.rename(target)
    claims.path.symlink_to(target)
    assert runner.serve(cfg, once=True) == 1 and not queue.claims and target.exists()
