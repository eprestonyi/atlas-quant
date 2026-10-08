"""Completion races and export failures; no child, provider or model is run."""
from contextlib import nullcontext
from types import SimpleNamespace

import pandas as pd
import pytest

from atlas_quant import runner
from atlas_quant.bundle_spool import BundleSpool
from atlas_quant.capacity.profiles import FULL_FILTER_PROFILE_ID
from atlas_quant.graph_research_runner import limits as graph_limits
from atlas_quant.market_research_runner import compute as market_compute
from atlas_quant.market_research_runner import limits as market_limits


def ready_reply(monkeypatch, *, source="ready_market", failure=None, disappears=False,
                change_at=None, action=None):
    state = {"now": 1000.0, "stop": False, "alive": True, "samples": 0, "received": 0}

    def change(point):
        if change_at == point:
            if action == "stop": state["stop"] = True
            elif action == "deadline": state["now"] = 2000.0
            elif action == "cancel": state["now"] = 1016.0

    class Budget:
        def __init__(self, *args): self.next_check = 100000.0
        def check(self, pid):
            assert self.next_check == 0.0  # Final sampling bypasses the timer.
            state["samples"] += 1
            change("sample")
            if disappears: state["alive"] = False
            if failure: raise runner.RunnerError(failure, "test resource failure")

    class Pipe:
        def poll(self, *args): change("poll"); return True
        def recv(self):
            state["received"] += 1
            change("recv")
            return {"result": "ready"}
        def close(self): pass

    class Process:
        pid = 123
        def start(self): pass
        def is_alive(self): return state["alive"]
        def terminate(self): state["alive"] = False
        def kill(self): state["alive"] = False
        def join(self, **kwargs): pass

    class Context:
        def Pipe(self, **kwargs): return Pipe(), Pipe()
        def Process(self, **kwargs): return Process()

    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: state["now"]))
    monkeypatch.setattr(runner.multiprocessing, "get_context", lambda *_: Context())
    monkeypatch.setattr(market_limits, "MarketProcessBudget", Budget)
    monkeypatch.setattr(graph_limits, "GraphProcessBudget", Budget)
    job = {"dataSource": source}
    if source == "ready_dataset": job["datasetRef"] = {"version": 3}
    answer = runner.execute_bounded(job, bundle_context={},
        stop_requested=lambda: state["stop"],
        heartbeat=lambda: {"cancelled": action == "cancel"})
    return answer, state


@pytest.mark.parametrize("source", ["ready_market", "ready_dataset"])
@pytest.mark.parametrize("code", ["CAPACITY_MEMORY", "CAPACITY_DISK", "CAPACITY_FIT_TIMEOUT", "CAPACITY_MONITOR"])
def test_first_ready_reply_cannot_skip_live_resource_rejection(monkeypatch, source, code):
    answer, state = ready_reply(monkeypatch, source=source, failure=code)
    assert answer["error"]["code"] == code
    assert state["samples"] == 1 and state["received"] == 0


@pytest.mark.parametrize("code", ["CAPACITY_MEMORY", "CAPACITY_DISK", "CAPACITY_FIT_TIMEOUT", "CAPACITY_MONITOR"])
def test_only_process_disappearance_can_release_a_ready_reply(monkeypatch, code):
    answer, state = ready_reply(monkeypatch, failure=code, disappears=True)
    if code == "CAPACITY_MONITOR":
        assert answer == {"result": "ready"} and state["received"] == 1
    else:
        assert answer["error"]["code"] == code and state["received"] == 0


@pytest.mark.parametrize("point", ["poll", "sample", "recv"])
@pytest.mark.parametrize("action,code", [("stop", "RUNNER_STOPPED"), ("deadline", "JOB_TIMEOUT"), ("cancel", "JOB_CANCELLED")])
def test_stop_deadline_and_lease_take_priority_at_every_reply_boundary(monkeypatch, point, action, code):
    answer, _ = ready_reply(monkeypatch, change_at=point, action=action)
    assert answer["error"]["code"] == code


def test_valid_ready_reply_still_completes_after_final_sample(monkeypatch):
    answer, state = ready_reply(monkeypatch)
    assert answer == {"result": "ready"}
    assert state["samples"] == state["received"] == 1


@pytest.mark.parametrize("exhausted,code", [("memory", "CAPACITY_MEMORY"), ("disk", "CAPACITY_DISK"),
    ("manifest_reserve", "CAPACITY_DISK"), ("deadline", "MARKET_DEADLINE"), (None, None)])
def test_market_export_checks_final_serialization_before_recovery_marker(tmp_path, monkeypatch, exhausted, code):
    completion = runner.CompletionSpool({"delivery_dir": str(tmp_path / "delivery"),
        "runner_secret": "r" * 48, "api_base": "https://queue.test"})
    context = BundleSpool.context_for(completion, {"id": "job", "leaseToken": "lease"})
    root = completion.root / "market-source"
    root.mkdir(mode=0o700)
    (root / "source.enc").write_bytes(b"original source retained")
    scope = {"symbols": ["600000.SH"], "start": "20240101", "end": "20241231"}
    strategy = {"universe": scope}
    frame = pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20240101", "close": 1.0}])
    reader = SimpleNamespace(manifest={"scope": scope}, research_input=lambda: (frame, {}))
    monkeypatch.setattr(market_compute, "MarketResearchSpool", lambda _: SimpleNamespace(root=root, inputs=lambda _: reader))
    monkeypatch.setattr(market_compute, "validate", lambda s, **kw: s)
    monkeypatch.setattr(market_compute, "compute_slot", lambda *a, **kw: nullcontext())
    state = {"now": 1000.0, "peak": 1, "free": 10 * 1024**3, "fits": 0}
    monkeypatch.setattr(market_compute, "time", SimpleNamespace(monotonic=lambda: state["now"]))
    monkeypatch.setattr(market_compute, "peak_rss_bytes", lambda: state["peak"])
    monkeypatch.setattr(market_compute.shutil, "disk_usage", lambda _: SimpleNamespace(free=state["free"]))

    def no_fit(*args, **kwargs):
        kwargs["plan_sink"]({"origins": []})
        return {"capacity": {}, "forecasts": {"dataFingerprint": "f" * 64}}

    def finish_export(report, snapshot, coverage, write_chunk, read_chunk):
        write_chunk("forecasts", 0, b"[]")
        assert read_chunk("forecasts", 0) == b"[]"
        # Resource exhaustion occurs during final export validation, after every
        # capacity/F check and after the last serialized chunk has been written.
        if exhausted == "memory": state["peak"] = 3 * 1024**3 + 1
        elif exhausted == "disk": state["free"] = 500 * 1024**2 - 1
        elif exhausted == "manifest_reserve": state["free"] = 500 * 1024**2
        elif exhausted == "deadline": state["now"] = 1100.0
        return b"{}"

    monkeypatch.setattr(market_compute, "run_capacity_research", no_fit)
    monkeypatch.setattr(market_compute, "build_bundle", finish_export)
    job = {"jobKind": "forecast", "dataSource": "ready_market", "strategy": strategy,
        "admissionProfile": FULL_FILTER_PROFILE_ID, "sourceEvidence": {}}
    if code:
        with pytest.raises(runner.RunnerError) as caught:
            market_compute.compute(job, context, slot_path="unused-test-lock", deadline=1100.0)
        assert caught.value.code == code
    else:
        result = market_compute.compute(job, context, slot_path="unused-test-lock", deadline=1100.0)
        assert result["_bundleKey"] == BundleSpool(context).key
    output = BundleSpool(context)
    assert (output.root / "manifest.enc").exists() == (code is None)
    assert output.read_chunk("forecasts", 0) == b"[]"
    assert (root / "source.enc").read_bytes() == b"original source retained"
    assert not (root / "cache").exists()
