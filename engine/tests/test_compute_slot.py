import multiprocessing
import time

import pytest

from atlas_quant.compute_slot import ComputeSlotError, compute_slot


def hold(path, channel):
    with compute_slot(path, deadline=time.monotonic() + 20):
        channel.send("acquired")
        channel.recv()


def test_separate_processes_cannot_compute_together_and_dead_child_releases(tmp_path):
    path = str(tmp_path / "compute.lock")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=hold, args=(path, child))
    process.start()
    try:
        assert parent.poll(5) and parent.recv() == "acquired"
        start = time.monotonic()
        with pytest.raises(ComputeSlotError) as error:
            with compute_slot(path, deadline=start + 0.15):
                pytest.fail("concurrent computation entered the slot")
        assert error.value.code == "COMPUTE_SLOT_TIMEOUT"
        assert time.monotonic() - start < 1
        process.terminate()
        process.join(5)
        assert not process.is_alive()
        with compute_slot(path, deadline=time.monotonic() + 1):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        child.close()


def test_cancellation_during_wait_preserves_holder(tmp_path):
    path = str(tmp_path / "compute.lock")
    checks = []

    def cancel():
        checks.append(True)
        if len(checks) == 2:
            raise RuntimeError("cancelled")

    with compute_slot(path, deadline=time.monotonic() + 5):
        with pytest.raises(RuntimeError, match="cancelled"):
            with compute_slot(path, deadline=time.monotonic() + 1, check=cancel):
                pytest.fail("second holder entered")
        with pytest.raises(ComputeSlotError, match="COMPUTE_SLOT_TIMEOUT"):
            with compute_slot(path, deadline=time.monotonic() + 0.05):
                pytest.fail("cancellation released another holder")


def test_symlink_or_shared_file_is_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("preserve")
    target.chmod(0o644)
    link = tmp_path / "link"
    link.symlink_to(target)
    for path in (target, link):
        with pytest.raises(ComputeSlotError):
            with compute_slot(str(path), deadline=time.monotonic() + 1):
                pytest.fail("unsafe lock file accepted")
    assert target.read_text() == "preserve"


def test_exception_after_acquire_releases_slot(tmp_path):
    path = str(tmp_path / "compute.lock")
    with pytest.raises(OSError, match="computation failed"):
        with compute_slot(path, deadline=time.monotonic() + 1):
            raise OSError("computation failed")
    with compute_slot(path, deadline=time.monotonic() + 1):
        pass
