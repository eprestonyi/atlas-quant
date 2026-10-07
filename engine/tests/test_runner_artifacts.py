"""Replay must keep its inputs, even across a lost completion acknowledgement."""
import copy
import json
import sys
import types
import subprocess
import shutil

import pandas as pd
import numpy as np
import pytest

from atlas_quant.fixtures import make_demo_data
from atlas_quant.provider import _records, canonical_hash
from atlas_quant.runner import CompletionSpool, RunnerError, fetch_replay, flush_completions, prepare_job, run_job
from atlas_quant.runner_artifacts import SnapshotSpool, freeze_input, restore_input


def inputs():
    strategy = {"schemaVersion": 2, "research": {"mode": "statistical_quant"},
                "universe": {"symbols": ["000001.SZ", "600000.SH"],
                             "start": "20230102", "end": "20250930"}, "factors": []}
    frame, provenance = make_demo_data(strategy)
    return strategy, frame, provenance


def spool_config(tmp_path):
    return {"api_base": "https://example.test/quant/api", "runner_secret": "private" * 8,
            "delivery_dir": str(tmp_path / "delivery")}


def test_snapshot_round_trip_retains_exact_float_cash_inputs():
    strategy, frame, provenance = inputs()
    snapshot = json.loads(json.dumps(freeze_input(strategy, frame, provenance)))
    restored, restored_provenance = restore_input(strategy, snapshot, snapshot["dataFingerprint"])
    pd.testing.assert_frame_equal(frame, restored, check_exact=True)
    assert restored_provenance == provenance
    restored_provenance["warnings"].append("changed outside snapshot")
    assert restored_provenance != snapshot["provenance"]


@pytest.mark.parametrize("change", ["price", "source_hash", "artifact_hash"])
def test_replay_rejects_modified_input_or_wrong_origin(change):
    strategy, frame, provenance = inputs()
    snapshot = freeze_input(strategy, frame, provenance)
    expected = snapshot["dataFingerprint"]
    if change == "price":
        snapshot["rows"][0]["amount"] *= 1.01
    elif change == "source_hash":
        snapshot["provenance"]["dataFingerprint"] = "a" * 64
    else:
        expected = "b" * 64
    with pytest.raises(RunnerError, match="指纹|内容") as exc:
        restore_input(strategy, snapshot, expected)
    assert exc.value.code == "SNAPSHOT_FINGERPRINT"


def test_changed_data_cannot_be_frozen_under_old_provenance():
    strategy, frame, provenance = inputs()
    frame.loc[0, "amount"] *= 1.01
    with pytest.raises(RunnerError) as exc:
        freeze_input(strategy, frame, provenance)
    assert exc.value.code == "SNAPSHOT_FINGERPRINT"


@pytest.mark.parametrize("change", ["last_float_bit", "calendar"])
def test_research_fingerprint_includes_full_precision_and_calendar(change):
    strategy, frame, provenance = inputs()
    snapshot = freeze_input(strategy, frame, provenance)
    if change == "last_float_bit":
        snapshot["rows"][0]["open"] = float(np.nextafter(snapshot["rows"][0]["open"], np.inf))
        # The old 12-decimal provider hash alone misses this change.
        assert canonical_hash(_records(pd.DataFrame(snapshot["rows"]))) == provenance["dataFingerprint"]
    else:
        snapshot["provenance"]["tradingDates"] = sorted(provenance["tradingDates"] + ["20230107"])
    with pytest.raises(RunnerError) as exc:
        restore_input(strategy, snapshot)
    assert exc.value.code == "SNAPSHOT_FINGERPRINT"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required to verify the actual Worker JSON transport")
def test_snapshot_survives_actual_javascript_number_serialization():
    strategy, frame, provenance = inputs()
    # Worker JSON cannot preserve distinctions such as integer-valued float
    # literals or negative zero; these must not invalidate the same observation.
    strategy["factors"] = [{"id": "external", "expression": "ext_test", "direction": 1}]
    frame["ext_test"] = np.resize(np.array([0., -0., 1., np.nan]), len(frame))
    frame["ext_test__available_date"] = frame.trade_date.where(frame.ext_test.notna(), None)
    provenance["externalFields"] = {"ext_test": {"dataType": "number", "source": "TEST_PIT",
        "path": "test/value", "availabilityPolicy": "point_in_time_asof",
        "availableDateColumn": "ext_test__available_date"}}
    provenance["dataFingerprint"] = canonical_hash(_records(frame))
    snapshot = freeze_input(strategy, frame, provenance)
    javascript = 'let s="";process.stdin.on("data",x=>s+=x);process.stdin.on("end",()=>process.stdout.write(JSON.stringify(JSON.parse(s))));'
    transported = subprocess.run([shutil.which("node"), "-e", javascript],
        input=json.dumps(snapshot, allow_nan=False), text=True, capture_output=True, check=True)
    restored, _ = restore_input(strategy, json.loads(transported.stdout), snapshot["dataFingerprint"])
    pd.testing.assert_frame_equal(frame, restored, check_exact=True)


def test_execution_replay_has_no_provider_access_or_research_path(monkeypatch):
    strategy, frame, provenance = inputs()
    snapshot = freeze_input(strategy, frame, provenance)
    artifact = {"artifactId": "forecast-1", "dataFingerprint": snapshot["dataFingerprint"]}
    calls = []
    def execute(strategy_arg, data_arg, artifact_arg, provenance=None):
        calls.append(artifact_arg)
        pd.testing.assert_frame_equal(data_arg, frame, check_exact=True)
        return {"forecasts": artifact_arg, "research": {"executionOnly": True}}
    def forbidden(*args, **kwargs):
        pytest.fail("Execution replay entered a data provider or research fitting path")
    monkeypatch.setitem(sys.modules, "atlas_quant.statistical_quant", types.SimpleNamespace(execute_forecasts=execute))
    for name in ("load_tushare", "load_tushare_proxy", "validate_upload", "make_demo_data", "PCDReadClient"):
        monkeypatch.setattr("atlas_quant.runner." + name, forbidden)
    import atlas_quant.engine
    monkeypatch.setattr(atlas_quant.engine, "run_research", forbidden)
    job = {"id": "execution1", "jobKind": "execution", "strategy": strategy,
           "dataSource": "tushare", "forecastArtifactId": "forecast-1",
           "providerAccess": {"serviceToken": "must-be-removed"}, "pcdAccess": {"token": "removed"},
           "replay": {"artifact": artifact, "snapshot": snapshot}}
    prepared = prepare_job(job, {"provider_access": {"serviceToken": "also-removed"}})
    assert "providerAccess" not in prepared and "pcdAccess" not in prepared
    assert run_job(prepared)["research"]["executionOnly"] is True
    assert calls == [artifact]
    prepared["forecastArtifactId"] = "someone-else"
    with pytest.raises(RunnerError) as exc:
        run_job(prepared)
    assert exc.value.code == "REPLAY_IDENTITY" and len(calls) == 1


def test_snapshot_and_result_retry_in_order_without_raw_data_in_result(tmp_path, monkeypatch):
    strategy, frame, provenance = inputs()
    snapshot = freeze_input(strategy, frame, provenance)
    config = spool_config(tmp_path)
    spool = CompletionSpool(config)
    packet = {"id": "job1", "leaseToken": "secret-lease", "result": {"forecasts": {"artifactId": "f1"}},
              "snapshot": snapshot}
    saved = spool.write(packet)
    snapshot_path = next((spool.root / "snapshots").glob("*.enc"))
    assert b"secret-lease" not in saved.read_bytes() and b"SYNTHETIC" not in snapshot_path.read_bytes()
    assert snapshot_path.stat().st_mode & 0o077 == 0
    monkeypatch.setattr("atlas_quant.runner._wait", lambda seconds: None)
    class Client:
        def __init__(self, offline): self.calls, self.offline = [], offline
        def post(self, route, payload):
            self.calls.append((route, copy.deepcopy(payload)))
            if route == "complete":
                assert set(payload) == {"id", "leaseToken", "result"}
                if self.offline:
                    raise RunnerError("QUEUE_NETWORK", "Acknowledgement lost")
            return {"ok": True}
    offline = Client(True)
    with pytest.raises(RunnerError) as exc:
        flush_completions(offline, spool)
    assert exc.value.code == "COMPLETION_UNCONFIRMED"
    assert [route for route, _ in offline.calls] == ["snapshot", "complete"] * 5
    assert saved.exists() and snapshot_path.exists()
    resumed = CompletionSpool(config)
    online = Client(False)
    flush_completions(online, resumed)
    assert online.calls == offline.calls[:2]
    assert not saved.exists() and not snapshot_path.exists()
    assert "snapshot" in packet  # caller-owned payload was not modified


def test_corrupt_or_cross_lease_snapshot_blocks_all_delivery(tmp_path):
    strategy, frame, provenance = inputs()
    spool = CompletionSpool(spool_config(tmp_path))
    packet = {"id": "job1", "leaseToken": "lease1", "result": {"ok": True},
              "snapshot": freeze_input(strategy, frame, provenance)}
    saved = spool.write(packet)
    stored = list(spool.pending())[0][1]
    snapshots = SnapshotSpool(spool)
    with pytest.raises(RunnerError) as exc:
        snapshots.read(stored["_snapshotKey"], dict(packet, leaseToken="lease2"))
    assert exc.value.code == "DELIVERY_INTEGRITY"
    path = snapshots.path(stored["_snapshotKey"])
    content = bytearray(path.read_bytes())
    content[-1] ^= 1
    path.write_bytes(content)
    class Client:
        def post(self, *args): pytest.fail("Corrupt frozen input must prevent delivery")
    with pytest.raises(RunnerError) as exc:
        flush_completions(Client(), spool)
    assert exc.value.code == "DELIVERY_INTEGRITY" and saved.exists() and path.exists()


def test_snapshot_rejection_is_durable_failure_and_cleans_up_only_after_ack(tmp_path, monkeypatch):
    strategy, frame, provenance = inputs()
    config = spool_config(tmp_path)
    spool = CompletionSpool(config)
    spool.write({"id": "job", "leaseToken": "lease", "result": {"ok": True},
                 "snapshot": freeze_input(strategy, frame, provenance)})
    monkeypatch.setattr("atlas_quant.runner._wait", lambda seconds: None)
    class Reject:
        def post(self, route, payload):
            if route == "snapshot":
                raise RunnerError("QUEUE_HTTP", "Bad snapshot", http_status=400)
            assert "error" in payload and "_snapshotKey" not in payload
            raise RunnerError("QUEUE_NETWORK", "offline")
    with pytest.raises(RunnerError):
        flush_completions(Reject(), spool)
    pending = list(CompletionSpool(config).pending())[0][1]
    assert "result" not in pending and pending["error"]["code"] == "RESULT_REJECTED"
    assert SnapshotSpool(spool).path(pending["_snapshotKey"]).exists()
    class Acknowledge:
        def post(self, route, payload):
            assert route == "complete" and "error" in payload
            return {"ok": True}
    flush_completions(Acknowledge(), CompletionSpool(config))
    assert not list(spool.pending()) and not list((spool.root / "snapshots").glob("*.enc"))


def test_replay_transport_retries_only_missing_artifact(monkeypatch):
    monkeypatch.setattr("atlas_quant.runner._wait", lambda seconds: None)
    class Client:
        calls = []
        def post(self, route, value, **kwargs):
            assert route == "replay" and value["leaseToken"] == "lease"
            self.calls.append(value["kind"])
            if len(self.calls) == 2:
                raise RunnerError("QUEUE_NETWORK", "offline")
            return {"artifact": {"artifactId": "f"}} if value["kind"] == "forecast" else {"snapshot": {"rows": []}}
    client = Client()
    result = fetch_replay(client, {"id": "job", "leaseToken": "lease"})
    assert client.calls == ["forecast", "dataset", "dataset"]
    assert result == {"artifact": {"artifactId": "f"}, "snapshot": {"rows": []}}


def test_replay_missing_artifact_never_retries_or_refetches_data():
    class Client:
        calls = 0
        def post(self, route, value, **kwargs):
            self.calls += 1
            raise RunnerError("QUEUE_HTTP", "missing", http_status=404)
    client = Client()
    with pytest.raises(RunnerError) as exc:
        fetch_replay(client, {"id": "job", "leaseToken": "lease"})
    assert exc.value.code == "REPLAY_INPUT" and client.calls == 1


def test_queue_snapshot_uses_budgeted_utf8_bytes():
    from atlas_quant.runner import QueueClient
    payload = {"id": "job", "snapshot": {"rows": [{"label": "财务观察", "x": 1.2}] * 100}}
    expected = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b'{"ok":true}'
    class Session:
        def post(self, url, **kwargs):
            assert "json" not in kwargs
            assert kwargs["data"] == expected
            assert kwargs["headers"]["Content-Type"] == "application/json"
            assert json.loads(kwargs["data"]) == payload
            return Response()
    client = QueueClient({"api_base": "https://example.test", "runner_secret": "x"*32}, Session())
    assert client.post("snapshot", payload) == {"ok": True}


def test_continuous_slow_stream_cannot_extend_queue_deadline(monkeypatch):
    import atlas_quant.runner as runner
    clock = iter([0, 0, 30, 61])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size):
            yield b'{"ok":'
            yield b'true}'
    class Session:
        def post(self, *args, **kwargs): return Response()
    client = runner.QueueClient({"api_base": "https://example.test", "runner_secret": "x"*32}, Session())
    with pytest.raises(RunnerError) as exc:
        client.post("replay", {})
    assert exc.value.code == "QUEUE_DEADLINE"


def test_expired_replay_acquisition_budget_makes_no_network_call():
    class Client:
        def post(self, *args, **kwargs): pytest.fail("Deadline expired before request")
    with pytest.raises(RunnerError) as exc:
        fetch_replay(Client(), {"id": "job", "leaseToken": "lease"}, deadline=0)
    assert exc.value.code == "REPLAY_UNAVAILABLE"


@pytest.mark.parametrize("status", [503, 401, 409])
def test_claimed_execution_input_failure_is_durable_before_next_claim(tmp_path, monkeypatch, status):
    import atlas_quant.runner as runner
    config = spool_config(tmp_path)
    spool = CompletionSpool(config)
    class Client:
        calls = []
        completed = False
        def post(self, route, payload, **kwargs):
            self.calls.append(route)
            if route == "claim":
                return {"job": None if self.completed else {"id": "execution", "leaseToken": "held-lease", "jobKind": "execution"},
                        "claim": {"requestId": payload["requestId"], "jobId": "execution", "status": "failed" if self.completed else "running"}}
            if route == "replay":
                raise RunnerError("QUEUE_HTTP", "unavailable", http_status=status)
            if route == "complete":
                pending = list(spool.pending())
                assert len(pending) == 1
                assert {k: v for k, v in pending[0][1].items() if k != "_claimRequestId"} == payload
                assert payload["id"] == "execution" and payload["leaseToken"] == "held-lease"
                assert payload["error"]["code"] == ("REPLAY_UNAVAILABLE" if status == 503 else "QUEUE_HTTP")
                self.completed = True
                return {"ok": True}
            pytest.fail("unexpected runner route")
    client = Client()
    monkeypatch.setattr(runner, "STOP", False)
    monkeypatch.setattr(runner, "QueueClient", lambda _: client)
    monkeypatch.setattr(runner, "_wait", lambda _: None)
    monkeypatch.setattr(runner, "execute_bounded", lambda *a, **k: pytest.fail("No computation after missing frozen input"))
    assert runner._serve(config, spool, once=True) == 0
    assert client.calls[0] == "claim" and client.calls[-2:] == ["complete", "claim"]
    assert client.calls.count("claim") == 2 and not list(spool.pending())
