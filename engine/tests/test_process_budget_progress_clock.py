"""No-fit resource-monitor races: progress may change during blocking parent IO."""

import multiprocessing
import subprocess
import time
from types import SimpleNamespace

import pytest

from atlas_quant import runner
from atlas_quant.bundle_spool import BundleSpool
from atlas_quant.dataset_runner.protocol import encode
from atlas_quant.financial_graph_bundle_spool import FinancialGraphBundleSpool
from atlas_quant.graph_research_runner import limits as graph_limits
from atlas_quant.graph_research_runner.spool import GraphResearchSpool
from atlas_quant.market_research_runner import limits as market_limits
from atlas_quant.market_research_runner.spool import MarketResearchSpool
from dataset_runner_support import JOB, LEASE, config


def setup_budget(kind, tmp_path):
    completion = runner.CompletionSpool(config(tmp_path))
    cls = FinancialGraphBundleSpool if kind == "graph" else BundleSpool
    context = cls.context_for(completion, {"id": JOB, "leaseToken": LEASE})
    module = graph_limits if kind == "graph" else market_limits
    budget_cls = module.GraphProcessBudget if kind == "graph" else module.MarketProcessBudget
    return module, context, budget_cls(context)


@pytest.mark.parametrize("kind", ["graph", "market"])
@pytest.mark.parametrize("phase", ["ps", "read"])
def test_new_fit_progress_during_parent_io_is_not_from_the_future(tmp_path, monkeypatch, kind, phase):
    module, _, monitor = setup_budget(kind, tmp_path)
    clock = [1000.0]
    monitor.store.write("progress", encode({"phase": "idle"}))
    real_read = monitor.store.read

    def publish():
        clock[0] = 1000.1
        monitor.store.write("progress", encode({"phase": "fit_started", "startedMonotonic": clock[0]}))
        clock[0] = 1000.2

    def ps(*args, **kwargs):
        if phase == "ps":
            publish()
        return SimpleNamespace(returncode=0, stdout="1024")

    def read(name):
        if phase == "read":
            publish()
        return real_read(name)

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=ps))
    if kind == "graph":
        monkeypatch.setattr(module, "shutil", SimpleNamespace(disk_usage=lambda _: SimpleNamespace(free=1024**3)))
    monkeypatch.setattr(monitor.store, "read", read)
    monitor.check(123)
    assert clock[0] == 1000.2


@pytest.mark.parametrize("kind", ["graph", "market"])
@pytest.mark.parametrize("started,observed,expected", [
    (700.0, 1000.0, None),
    (700.0, 1000.001, "CAPACITY_FIT_TIMEOUT"),
    (-1.0, 1000.1, "CAPACITY_MONITOR"),
    (1000.2, 1000.1, "CAPACITY_MONITOR"),
    (True, 1000.1, "CAPACITY_MONITOR"),
    ("1000.0", 1000.1, "CAPACITY_MONITOR"),
    (None, 1000.1, "CAPACITY_MONITOR"),
])
def test_fit_age_uses_observation_after_read_without_weakening_bounds(tmp_path, monkeypatch, kind, started, observed, expected):
    module, _, monitor = setup_budget(kind, tmp_path)
    monitor.store.write("progress", encode({"phase": "fit_started", "startedMonotonic": started}))
    clock = [1000.0]
    real_read = monitor.store.read

    def read(name):
        result = real_read(name)
        clock[0] = observed
        return result

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="1024")))
    if kind == "graph":
        monkeypatch.setattr(module, "shutil", SimpleNamespace(disk_usage=lambda _: SimpleNamespace(free=1024**3)))
    monkeypatch.setattr(monitor.store, "read", read)
    if expected:
        with pytest.raises(runner.RunnerError) as error:
            monitor.check(123)
        assert error.value.code == expected
    else:
        monitor.check(123)


def _progress_child(context, kind, pipe):
    """Only writes an encrypted timing event; never loads sources or fits a model."""
    try:
        store = GraphResearchSpool(context) if kind == "graph" else MarketResearchSpool(context)
        pipe.send("ready")
        assert pipe.poll(10) and pipe.recv() == "write"
        started = time.monotonic()
        store.write("progress", encode({"phase": "fit_started", "startedMonotonic": started}))
        pipe.send(started)
        assert pipe.poll(10) and pipe.recv() == "stop"
    finally:
        pipe.close()


@pytest.mark.parametrize("kind", ["graph", "market"])
def test_real_child_progress_advances_after_parent_sample_during_ps(tmp_path, monkeypatch, kind):
    module, context, monitor = setup_budget(kind, tmp_path)
    mp = multiprocessing.get_context("spawn")
    parent, child = mp.Pipe()
    process = mp.Process(target=_progress_child, args=(context, kind, child))
    process.start()
    child.close()
    samples, written = [], []
    real_ps = subprocess.run

    def monotonic():
        value = time.monotonic()
        samples.append(value)
        return value

    def ps(*args, **kwargs):
        parent.send("write")
        assert parent.poll(10)
        written.append(parent.recv())
        return real_ps(*args, **kwargs)

    try:
        assert parent.poll(10) and parent.recv() == "ready"
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=monotonic))
        monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=ps))
        monitor.check(process.pid)
        assert samples[0] < written[0] <= samples[-1]
    finally:
        if process.is_alive():
            parent.send("stop")
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
    assert process.exitcode == 0
