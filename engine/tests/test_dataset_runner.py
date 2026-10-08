"""Durable dataset consumer acceptance; no provider or prediction fit."""

from copy import deepcopy
import json
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from atlas_quant.dataset_runner import service
from atlas_quant.dataset_runner.client import DatasetClient
from atlas_quant.dataset_runner.compute import execute_bounded
from atlas_quant.dataset_runner.lease import LeaseMonitor
from atlas_quant.dataset_runner.protocol import LIMITS, encode, sha
from atlas_quant.dataset_runner.publication import compute_publication
from atlas_quant.dataset_runner.spool import DatasetSpool
from atlas_quant.financial_runner.spool import FinancialSpool
from atlas_quant.runner import RunnerError
from dataset_runner_support import Queue, config, inputs, job, iso
from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source


@pytest.fixture(scope="module")
def prepared(sources, legacy_source):
    task = job()
    data = inputs(sources, legacy_source, task)
    parts = {}
    manifest = compute_publication(
        task, data, lambda c, n, b: parts.__setitem__((c, n), b)
    )
    return task, data, manifest, parts


def writer(manifest, parts, calls):
    def compute(spool, task, data, monitor, **kwargs):
        calls.append(1)
        publication = spool.publication(task)
        for (name, ordinal), raw in parts.items():
            publication.write_chunk(name, ordinal, raw)
        publication.finalize(manifest)

    return compute


def test_contract_matches_and_actual_composition_is_a_valid_v2(prepared):
    _, _, manifest, parts = prepared
    contract = json.loads(
        (Path(__file__).parents[2] / "contracts/hosted-datasets-v1.json").read_text()
    )
    assert LIMITS == contract["limits"]
    assert manifest["version"] == 2 and manifest["profile"] == contract["profile"]
    assert len(parts) == 8 and sum(map(len, parts.values())) < 1024 * 1024


@pytest.mark.parametrize("lost", ["claim", "begin", "part", "complete"])
def test_lost_ack_restores_identity_and_does_not_recompute(prepared, tmp_path, lost):
    task, data, manifest, parts = prepared
    cfg = config(tmp_path)
    spool = DatasetSpool(cfg)
    queue = Queue(data, task)
    queue.lost.add(lost)
    calls = []
    with pytest.raises(RunnerError, match="Lost fixture response"):
        service.run_once(
            cfg, spool, queue, queue, bounded_compute=writer(manifest, parts, calls)
        )
    pending = spool.read()
    assert pending is not None
    service.run_once(
        cfg,
        DatasetSpool(cfg),
        queue,
        queue,
        bounded_compute=writer(manifest, parts, calls),
    )
    assert calls == [1] and queue.input_calls == 1
    assert len(set(queue.claims)) == 1 and queue.status == "completed"
    assert DatasetSpool(cfg).read() is None
    assert not list(spool.root.glob("input-*.enc"))
    assert queue.parts == parts


def test_computing_without_commit_marker_fails_without_reading_sources(
    prepared, tmp_path
):
    task, data, _, _ = prepared
    cfg, queue = config(tmp_path), Queue(data, task)
    spool = DatasetSpool(cfg)
    state = spool.current_or_create()
    spool.save(dict(state, phase="computing", job=task))
    spool.publication(task).write_chunk(
        "marketOrigin", 0, b"partial SYNTHETIC evidence"
    )
    service.run_once(cfg, spool, queue, queue)
    assert queue.status == "failed" and queue.error["code"] == "RUNNER_INTERRUPTED"
    assert queue.input_calls == 0 and spool.read() is None


def test_complete_manifest_recovers_child_ack_loss_without_computation(
    prepared, tmp_path
):
    task, data, manifest, parts = prepared
    cfg, queue = config(tmp_path), Queue(data, task)
    spool = DatasetSpool(cfg)
    spool.save(dict(spool.current_or_create(), phase="computing", job=task))
    writer(manifest, parts, [])(spool, task, data, None)
    service.run_once(
        cfg,
        spool,
        queue,
        queue,
        bounded_compute=lambda *a, **k: pytest.fail("recomputed"),
    )
    assert queue.status == "completed" and queue.input_calls == 0


def test_real_spawn_matches_every_pure_core_component(prepared, tmp_path):
    task, data, manifest, parts = prepared
    cfg = config(tmp_path)
    spool, queue = DatasetSpool(cfg), Queue(data, task)
    os.chmod(tmp_path, 0o700)
    with LeaseMonitor(queue, task) as monitor:
        execute_bounded(
            spool, task, data, monitor, slot_path=str(tmp_path / "compute.lock")
        )
    publication = spool.publication(task)
    assert publication.manifest() == manifest
    for component in manifest["components"]:
        for part in component["parts"]:
            assert (
                publication.read_chunk(component["componentId"], part)
                == parts[component["componentId"], part["ordinal"]]
            )
    assert not multiprocessing.active_children()


def test_spool_is_private_and_has_independent_authentication_domain(prepared, tmp_path):
    task, data, _, _ = prepared
    cfg = config(tmp_path)
    spool = DatasetSpool(cfg)
    state = spool.current_or_create()
    spool.remember_input(task, data[0])
    assert (spool.root.stat().st_mode & 0o777) == 0o700
    for file in spool.root.glob("*.enc"):
        assert (file.stat().st_mode & 0o777) == 0o600
        assert b"600000.SH" not in file.read_bytes()
    old = FinancialSpool({**cfg, "financial_delivery_dir": str(spool.root)})
    with pytest.raises(RunnerError):
        old.read()
    assert spool.read() == state


def test_source_plan_is_frozen_across_read_retries(prepared, tmp_path):
    task, data, _, _ = prepared
    spool = DatasetSpool(config(tmp_path))
    spool.remember_input(task, data[0])
    changed = deepcopy(data[0])
    changed["planRoot"] = "b" * 64
    with pytest.raises(RunnerError) as error:
        spool.remember_input(task, changed)
    assert error.value.code == "DATASET_INPUT_CHANGED"


def test_idle_gate_never_creates_a_claim(tmp_path):
    class Idle:
        calls = 0

        def post(self, route, payload, **kwargs):
            assert route == "heartbeat"
            self.calls += 1
            return {"ok": True, "canClaim": False}

    queue, cfg = Idle(), config(tmp_path)
    assert (
        service.serve(
            cfg,
            client_factory=lambda _: queue,
            stop_requested=lambda: queue.calls == 30,
        )
        == 0
    )
    assert DatasetSpool(cfg).read() is None


def test_existing_unknown_claim_resumes_without_idle_gate(prepared, tmp_path):
    task, data, manifest, parts = prepared
    cfg, queue = config(tmp_path), Queue(data, task)
    spool = DatasetSpool(cfg)
    state = spool.current_or_create()
    queue.status = "cancelled"
    assert service.serve(cfg, once=True, client_factory=lambda _: queue) == 0
    assert queue.claims == [state["requestId"]] and spool.read() is None


def test_transient_heartbeat_does_not_extend_confirmed_lease(prepared):
    task = dict(prepared[0], leaseUntil=iso(80), deadline=iso(200))

    class Client:
        def post(self, *a, **k):
            raise RunnerError("DATASET_NETWORK", "temporary")

    monitor = LeaseMonitor(Client(), task)
    old = monitor.lease_until
    monitor.pulse()
    assert monitor.lease_until == old
    monitor.lease_until = time.monotonic() - 1
    with pytest.raises(RunnerError) as error:
        monitor.pulse()
    assert error.value.code == "LEASE_EXPIRED"


@pytest.mark.parametrize("changed", ["cancel", "lease", "deadline", "stop"])
def test_confirmed_stop_conditions_stop_before_child(prepared, tmp_path, changed):
    task, data, _, _ = prepared
    queue = Queue(data, task)
    monitor = LeaseMonitor(queue, task, stop_requested=lambda: changed == "stop")
    if changed == "cancel":
        queue.status = "cancel_requested"
        with pytest.raises(RunnerError):
            monitor.pulse()
        return
    if changed == "lease":
        monitor.lease_until = time.monotonic() - 1
    if changed == "deadline":
        monitor.deadline = time.monotonic() - 1
    with pytest.raises(RunnerError):
        execute_bounded(DatasetSpool(config(tmp_path)), task, data, monitor)
    assert not multiprocessing.active_children()


def test_invalid_json_and_provider_credentials_cannot_enter_cli(tmp_path):
    from atlas_quant.dataset_runner.__main__ import load_config

    cfg = {**config(tmp_path), "poll_seconds": 3}
    target = tmp_path / "config.json"
    target.write_bytes(encode(cfg))
    target.chmod(0o600)
    assert load_config(str(target))["dataset_enabled"] is True
    target.write_bytes(
        encode({**cfg, "provider_access": {"serviceToken": "NEVER_READ"}})
    )
    with pytest.raises(RunnerError) as error:
        load_config(str(target))
    assert error.value.code == "DATASET_CONFIG"


def sleeping_compute(task, data, write):
    time.sleep(10)
    raise AssertionError("deadline should terminate this child")


def test_bounded_compute_terminates_child_at_original_deadline(prepared, tmp_path):
    task, data, _, _ = prepared
    monitor = LeaseMonitor(Queue(data, task), task)
    monitor.deadline = time.monotonic() + 0.25
    started = time.monotonic()
    with pytest.raises(RunnerError) as error:
        execute_bounded(
            DatasetSpool(config(tmp_path)),
            task,
            data,
            monitor,
            computer=sleeping_compute,
        )
    assert error.value.code == "DATASET_DEADLINE"
    assert time.monotonic() - started < 5 and not multiprocessing.active_children()


def test_waiting_for_shared_slot_still_consumes_deadline(prepared, tmp_path):
    import fcntl

    task, data, _, _ = prepared
    tmp_path.chmod(0o700)
    slot = tmp_path / "shared.lock"
    descriptor = os.open(slot, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        monitor = LeaseMonitor(Queue(data, task), task)
        monitor.deadline = time.monotonic() + 0.3
        spool = DatasetSpool(config(tmp_path))
        with pytest.raises(RunnerError) as error:
            execute_bounded(spool, task, data, monitor, slot_path=str(slot))
        assert error.value.code in {"DATASET_DEADLINE", "COMPUTE_SLOT_TIMEOUT"}
        assert spool.publication(task).manifest() is None
        assert not multiprocessing.active_children()
    finally:
        os.close(descriptor)


def test_cleanup_refuses_input_symlink_before_deleting_durable_claim(
    prepared, tmp_path
):
    task, data, _, _ = prepared
    spool = DatasetSpool(config(tmp_path))
    state = spool.save(
        dict(
            spool.current_or_create(),
            job=task,
            phase="terminal",
            terminalStatus="failed",
        )
    )
    path, _ = spool._input_path(task)
    other = tmp_path / "unrelated"
    other.write_text("must remain")
    path.symlink_to(other)
    with pytest.raises(RunnerError):
        spool.cleanup(state)
    assert spool.read() == state and other.read_text() == "must remain"


@pytest.mark.parametrize("change", ["leaseToken", "deadline", "planId", "inputUrl"])
def test_claim_retry_cannot_change_identity(prepared, tmp_path, change):
    task, data, _, _ = prepared
    spool = DatasetSpool(config(tmp_path))
    state = spool.save(dict(spool.current_or_create(), phase="claimed", job=task))
    response = Queue(data, task).post("claim", {"requestId": state["requestId"]})
    response["job"][change] = (
        iso(300)
        if change == "deadline"
        else (
            "/wrong" if change == "inputUrl" else "99999999-9999-4999-8999-999999999999"
        )
    )
    with pytest.raises(RunnerError):
        service.validate_claim(response, state, config(tmp_path)["api_base"])


def test_completed_claim_with_changed_dataset_root_preserves_spool(prepared, tmp_path):
    task, data, manifest, parts = prepared
    cfg, queue = config(tmp_path), Queue(data, task)
    spool = DatasetSpool(cfg)
    queue.lost.add("complete")
    with pytest.raises(RunnerError):
        service.run_once(
            cfg, spool, queue, queue, bounded_compute=writer(manifest, parts, [])
        )
    state = spool.read()
    queue.root = "f" * 64
    with pytest.raises(RunnerError):
        service.run_once(cfg, spool, queue, queue)
    assert spool.read() == state
    assert spool.publication(task).manifest() == manifest


def test_permanent_publication_rejection_settles_error_before_next_claim(
    prepared, tmp_path
):
    task, data, manifest, parts = prepared
    cfg, queue = config(tmp_path), Queue(data, task)
    original_post = queue.post

    def post(route, payload, **kwargs):
        if route.endswith("/publication"):
            raise RunnerError("DATASET_HTTP", "rejected", http_status=413)
        return original_post(route, payload, **kwargs)

    queue.post = post
    spool = DatasetSpool(cfg)
    service.run_once(
        cfg, spool, queue, queue, bounded_compute=writer(manifest, parts, [])
    )
    assert queue.error["code"] == "DATASET_RESULT_REJECTED"
    assert queue.status == "failed" and spool.read() is None


def test_missing_parts_cannot_select_unlisted_or_duplicate_payloads(prepared):
    from atlas_quant.dataset_runner.delivery import missing_parts

    manifest = prepared[2]
    for value in (
        [{"componentId": "notAComponent", "ordinals": [0]}],
        [{"componentId": "marketOrigin", "ordinals": [0, 0]}],
        [{"componentId": "marketOrigin", "ordinals": [True]}],
        [
            {"componentId": "marketOrigin", "ordinals": [0]},
            {"componentId": "marketOrigin", "ordinals": [0]},
        ],
    ):
        with pytest.raises(RunnerError):
            missing_parts({"missing": value}, manifest)


def test_spawn_import_failure_cannot_block_sending_large_input(prepared, tmp_path):
    import subprocess
    import sys

    script = tmp_path / "bad_spawn_main.py"
    script.write_text(
        """
if __name__ == '__mp_main__':
    raise RuntimeError('Explicit startup-failure fixture')
if __name__ == '__main__':
    import time
    from pathlib import Path
    from test_research_dataset_components import sources
    from test_snapshot_market_view import legacy_source
    from dataset_runner_support import inputs,job,config,Queue
    from atlas_quant.dataset_runner.compute import execute_bounded
    from atlas_quant.dataset_runner.spool import DatasetSpool
    from atlas_quant.dataset_runner.lease import LeaseMonitor
    from atlas_quant.runner import RunnerError
    source=sources.__wrapped__(); original=legacy_source.__wrapped__(source)
    task=job(); values=inputs(source,original,task)
    spool=DatasetSpool(config(Path(__file__).parent)); monitor=LeaseMonitor(Queue(values,task),task)
    monitor.deadline=time.monotonic()+3
    try:
        execute_bounded(spool,task,values,monitor)
    except RunnerError as error:
        assert error.code=='DATASET_CHILD_EXIT', error.code
    else:
        raise AssertionError('failed startup was accepted')
"""
    )
    root = Path(__file__).parents[2]
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(root / "engine"), str(root / "engine/tests")]),
    )
    result = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()


def test_encrypted_source_cache_tampering_cannot_reach_composition(prepared, tmp_path):
    from atlas_quant.dataset_runner.source_spool import (
        store_sources,
        read_sources,
        cache_identity,
    )

    task, data, _, _ = prepared
    spool = DatasetSpool(config(tmp_path))
    store_sources(spool, task, data, lambda: None)
    assert read_sources(spool, task, lambda: None) == data
    root, _ = cache_identity(spool, task)
    path = root / "market-snapshot.enc"
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(RunnerError) as error:
        read_sources(spool, task, lambda: None)
    assert error.value.code == "FINANCIAL_SPOOL_INTEGRITY"


def test_terminal_cleanup_handles_interrupted_encrypted_temp_write(prepared, tmp_path):
    from atlas_quant.dataset_runner.source_spool import store_sources, cache_identity

    task, data, _, _ = prepared
    spool = DatasetSpool(config(tmp_path))
    store_sources(spool, task, data, lambda: None)
    root, _ = cache_identity(spool, task)
    (root / ("write-" + "a" * 32 + ".tmp")).write_bytes(
        b"AQF1unfinished encrypted write"
    )
    state = spool.save(
        dict(
            spool.current_or_create(),
            job=task,
            phase="terminal",
            terminalStatus="failed",
        )
    )
    spool.cleanup(state)
    assert spool.read() is None and not root.exists()


def test_public_long_fixture_has_declared_scope_and_retains_missingness(tmp_path):
    import runpy
    from atlas_quant.research_dataset import DirectoryDatasetReader

    module = runpy.run_path(
        str(Path(__file__).parents[2] / "scripts/make-snapshot-view-fixture.py")
    )
    output = tmp_path / "long-fixture"
    summary = module["generate"](output, long_scope=True)
    assert (
        summary["providerCalls"] == summary["modelFits"] == 0
        and summary["synthetic"] is True
    )
    reader = DirectoryDatasetReader(
        output / "dataset", expected_root=summary["datasetRoot"]
    )
    coverage = json.loads(reader.payload("coverage"))["financial"][0]
    security = coverage["securities"][0]
    states = {s["stateId"]: s for s in security["states"]}
    assert security["rows"] == 262
    assert states["model_fin_operating_margin"]["okRows"] == 262
    assert states["model_fin_revenue_ttm_yoy"]["missingRows"] == 85
    assert states["model_fin_revenue_ttm_yoy"]["firstObserved"] == "20240429"
    assert reader.manifest["scope"] == {
        "symbols": ["600000.SH"],
        "start": "20240101",
        "end": "20241231",
    }
