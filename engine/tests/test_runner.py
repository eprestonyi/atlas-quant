import json
import os
from pathlib import Path
import time
import fcntl

import pytest

from atlas_quant.provider import ProviderError
from atlas_quant.runner import (CompletionSpool, QueueClient, RunnerError, _safe_error, execute_bounded,
                                flush_completions, load_config, prepare_job, run_job)


def config(path, **changes):
    value = {"api_base": "https://atlas-aletheia.com/quant/api", "runner_secret": "x"*48, **changes}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_private_config_permissions_and_https(tmp_path):
    p = config(tmp_path / "config.json")
    assert load_config(p)["poll_seconds"] == 10
    p.chmod(0o644)
    with pytest.raises(RunnerError) as e:
        load_config(p)
    assert e.value.code == "CONFIG_PERMISSIONS"
    config(p, api_base="http://example.com/quant/api")
    with pytest.raises(RunnerError) as e:
        load_config(p)
    assert e.value.code == "CONFIG_URL"


def test_private_provider_config_is_only_injected_for_tushare(tmp_path):
    access = {"proxyUrl": "https://atlas-aletheia.com/api/internal/atlas-quant/tushare", "serviceToken": "private-service-token"*3}
    p = config(tmp_path / "config.json", provider_access=access)
    cfg = load_config(p)
    job = {"id": "test", "strategy": {}, "dataSource": "tushare"}
    injected = prepare_job(job, cfg)
    assert injected["providerAccess"] == access
    assert "providerAccess" not in job
    assert "providerAccess" not in prepare_job(dict(job, dataSource="demo"), cfg)
    config(p, provider_access=dict(access, proxyUrl="https://evil.test/data"))
    with pytest.raises(RunnerError) as e:
        load_config(p)
    assert e.value.code == "CONFIG_PROVIDER"


def test_proxy_job_cannot_send_credentials_to_arbitrary_host():
    job = {"strategy": {}, "dataSource": "tushare", "providerAccess": {"proxyUrl": "https://evil.test/data", "serviceToken": "secret"}}
    with pytest.raises(RunnerError) as e:
        run_job(job)
    assert e.value.code == "PROVIDER_PROXY_FORBIDDEN"


def test_tushare_missing_credentials_never_selects_demo():
    with pytest.raises(ProviderError) as e:
        run_job({"strategy": {}, "dataSource": "tushare"})
    assert e.value.code == "TUSHARE_TOKEN_MISSING"


def test_raw_exception_does_not_leak_secrets():
    error = _safe_error(RuntimeError("secret-access-token"))
    assert "secret-access-token" not in json.dumps(error)
    assert error["code"] == "JOB_FAILED"


def test_real_child_process_returns_validated_error():
    result = execute_bounded({"strategy": {}, "dataSource": "unsupported"}, timeout=20)
    assert result["error"]["code"] == "DATA_SOURCE"


def test_hard_timeout_terminates_spawned_child():
    before = time.monotonic()
    result = execute_bounded({"strategy": {}, "dataSource": "unsupported"}, timeout=0.0001)
    assert result["error"]["code"] == "JOB_TIMEOUT"
    assert time.monotonic()-before < 5


def test_queue_contract_auth_and_no_redirects():
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, n): yield b'{"job":null}'
    class Session:
        def post(self, url, **kwargs):
            assert url == "https://atlas-aletheia.com/quant/api/runner/claim"
            assert kwargs["headers"]["Authorization"] == "Bearer " + "x"*48
            assert kwargs["allow_redirects"] is False
            return Response()
    client = QueueClient({"api_base": "https://atlas-aletheia.com/quant/api", "runner_secret": "x"*48}, Session())
    assert client.post("claim", {}) == {"job": None}


def test_worker_cancellation_reaps_child():
    result = execute_bounded({"strategy": {}, "dataSource": "unsupported"}, timeout=20, stop_requested=lambda: True)
    assert result["error"]["code"] == "RUNNER_STOPPED"


def test_durable_completion_is_encrypted_and_retried_exactly(tmp_path):
    cfg = load_config(config(tmp_path / "config.json"))
    spool = CompletionSpool(cfg)
    payload = {"id": "job-1", "leaseToken": "secret-lease-never-plaintext", "result": {"equity": [{"equity": 123.45}]}}
    saved = spool.write(payload)
    assert b"secret-lease-never-plaintext" not in saved.read_bytes()
    assert b"123.45" not in saved.read_bytes()
    assert saved.stat().st_mode & 0o077 == 0
    # Fresh spool object simulates restart. Only completion delivery occurs.
    resumed = CompletionSpool(cfg)
    class Client:
        calls = []
        def post(self, route, value):
            assert route == "complete"
            self.calls.append(value)
            return {"ok": True}
    client = Client()
    flush_completions(client, resumed)
    assert client.calls == [payload]
    assert list(resumed.pending()) == [] and not saved.exists()


def test_corrupt_completion_is_preserved_and_fails_closed(tmp_path):
    cfg = load_config(config(tmp_path / "config.json"))
    spool = CompletionSpool(cfg)
    saved = spool.write({"id": "job1", "leaseToken": "secret", "error": {"code": "JOB_FAILED"}})
    content = bytearray(saved.read_bytes())
    content[-1] ^= 1
    saved.write_bytes(content)
    with pytest.raises(RunnerError) as e:
        list(CompletionSpool(cfg).pending())
    assert e.value.code == "DELIVERY_INTEGRITY" and saved.exists()


def test_spool_is_bound_to_runner_and_queue(tmp_path):
    cfg = load_config(config(tmp_path / "config.json"))
    spool = CompletionSpool(cfg)
    saved = spool.write({"id": "job1", "leaseToken": "secret", "result": {"ok": True}})
    changed = dict(cfg, api_base="https://other.example/quant/api")
    with pytest.raises(RunnerError) as e:
        list(CompletionSpool(changed).pending())
    assert e.value.code == "DELIVERY_INTEGRITY" and saved.exists()


def test_failed_completion_remains_durable_without_recomputation(tmp_path, monkeypatch):
    cfg = load_config(config(tmp_path / "config.json"))
    spool = CompletionSpool(cfg)
    payload = {"id": "job1", "leaseToken": "lease1", "result": {"ok": True}}
    saved = spool.write(payload)
    monkeypatch.setattr("atlas_quant.runner._wait", lambda seconds: None)
    class Offline:
        calls = []
        def post(self, route, value):
            self.calls.append((route, value))
            raise RunnerError("QUEUE_NETWORK", "offline")
    offline = Offline()
    with pytest.raises(RunnerError) as e:
        flush_completions(offline, spool)
    assert e.value.code == "COMPLETION_UNCONFIRMED" and saved.exists()
    assert offline.calls == [("complete", payload)]*5


@pytest.mark.parametrize("status", [400, 413])
def test_permanent_result_rejection_converts_to_durable_sanitized_error(tmp_path, monkeypatch, status):
    cfg = load_config(config(tmp_path / "config.json"))
    spool = CompletionSpool(cfg)
    packet = {"id": "job", "leaseToken": "lease", "result": {"strategy": {"token": "non-secret-user-marker"}}}
    spool.write(packet)
    monkeypatch.setattr("atlas_quant.runner._wait", lambda seconds: None)
    class Client:
        def __init__(self): self.calls = []
        def post(self, route, payload):
            assert route == "complete"
            self.calls.append(payload)
            if "result" in payload:
                raise RunnerError("QUEUE_HTTP", "rejected", http_status=status)
            # Confirm sanitized failure was persisted before trying delivery.
            pending = list(CompletionSpool(cfg).pending())
            assert pending[0][1] == payload
            if len(self.calls) == 2:
                raise RunnerError("QUEUE_NETWORK", "transient")
            return {"ok": True, "status": "failed"}
    client = Client()
    flush_completions(client, spool)
    assert len(client.calls) == 3
    assert client.calls[1] == client.calls[2]
    assert client.calls[1]["error"]["code"] == "RESULT_REJECTED"
    assert "non-secret-user-marker" not in json.dumps(client.calls[1])
    assert not list(spool.pending())


def test_rejected_result_then_offline_preserves_only_safe_error(tmp_path, monkeypatch):
    cfg = load_config(config(tmp_path / "config.json"))
    spool = CompletionSpool(cfg)
    spool.write({"id": "job", "leaseToken": "lease", "result": {"oversized": "sensitive-marker"}})
    monkeypatch.setattr("atlas_quant.runner._wait", lambda seconds: None)
    class OfflineAfterReject:
        def post(self, route, payload):
            raise RunnerError("QUEUE_HTTP", "not acknowledged", http_status=413 if "result" in payload else 503)
    with pytest.raises(RunnerError) as error:
        flush_completions(OfflineAfterReject(), spool)
    assert error.value.code == "COMPLETION_UNCONFIRMED"
    pending = list(CompletionSpool(cfg).pending())
    assert pending[0][1]["error"]["code"] == "RESULT_REJECTED"
    assert "result" not in pending[0][1]


def test_second_runner_cannot_enter_claim_loop(tmp_path):
    from atlas_quant.runner import serve
    cfg = load_config(config(tmp_path / "config.json"))
    spool = CompletionSpool(cfg)
    fd = os.open(spool.root / "runner.lock", os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RunnerError) as e:
            serve(cfg, once=True)
        assert e.value.code == "RUNNER_ALREADY_ACTIVE"
    finally:
        os.close(fd)
