"""Independent control-stop races. No provider calls, sockets, or model fits."""

import time

import pytest

from atlas_quant.financial_acquisition.service import execute_one
from atlas_quant.runner import RunnerError
from test_financial_acquisition import consumer, planned, config


def test_stop_arriving_during_begin_ack_cannot_start_provider_executor(tmp_path):
    instance, client, _ = consumer(tmp_path)
    stopped = [False]
    instance.stop_requested = lambda: stopped[0]
    original = client.post
    executions = []

    def control(route, body, **kwargs):
        response = original(route, body, **kwargs)
        if route.endswith("/begin"):
            assert response["maySend"] is True
            stopped[0] = True  # SIGTERM while awaiting the admitted intent ACK.
        return response

    client.post = control
    instance.executor = lambda *args: executions.append(args[3]["requestKey"])
    with instance.spool.locked():
        instance.once()
    assert executions == [], "A stopped process must not start a fresh provider child"
    assert client.status == "failed"
    assert len(client.intents) == 1  # Admitted intent remains; never undo/retry it.
    assert list(instance.spool.root.glob("*-review.enc"))


def test_execute_one_checks_control_before_allocating_provider_child(
    tmp_path, monkeypatch
):
    job, metadata = planned()
    request = metadata["executionPlan"]["requests"][0]
    import atlas_quant.financial_acquisition.service as service
    from atlas_quant.financial_acquisition.spool import AcquisitionSpool

    allocated = []

    class Process:
        def __init__(self, **kwargs):
            allocated.append("process")
            self.alive = False

        def start(self):
            allocated.append("started")

        def is_alive(self):
            return False

        def close(self):
            pass

    from types import SimpleNamespace

    context = SimpleNamespace(Process=Process)

    monkeypatch.setattr(service.mp, "get_context", lambda _: context)

    def cancelled():
        raise RunnerError("ACQUISITION_STOPPED", "Explicit local test stop")

    with pytest.raises(RunnerError) as result:
        execute_one(
            config(tmp_path),
            AcquisitionSpool(config(tmp_path)),
            job,
            request,
            1024,
            time.monotonic() + 10,
            cancelled,
        )
    assert result.value.code == "ACQUISITION_STOPPED"
    assert "started" not in allocated
