import multiprocessing
import json
import os
import time

import pytest

from atlas_quant.compute_slot import ComputeSlotError, compute_slot, validate_slot_path


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


def config_loaders():
    from atlas_quant.runner import load_config
    from atlas_quant.financial_runner.service import load_config as financial_config
    from atlas_quant.dataset_runner.__main__ import load_config as dataset_config

    return {
        "research": load_config,
        "financial": financial_config,
        "dataset": dataset_config,
    }


def configuration(tmp_path, **extras):
    path = tmp_path / "service.json"
    path.write_text(
        json.dumps(
            {
                "api_base": "https://example.test/quant/api",
                "runner_secret": "test-only-" + "x" * 32,
                "delivery_dir": str(tmp_path / "research-delivery"),
                "dataset_delivery_dir": str(tmp_path / "dataset-delivery"),
                "dataset_enabled": True,
                **extras,
            }
        )
    )
    path.chmod(0o600)
    return str(path)


@pytest.mark.parametrize("loader", ["research", "financial", "dataset"])
@pytest.mark.parametrize("value", [None, "", False, 0, [], {}, "relative/compute.lock"])
def test_all_service_configs_reject_explicit_invalid_lock(tmp_path, loader, value):
    from atlas_quant.runner import RunnerError

    path = configuration(tmp_path, compute_lock_path=value)
    with pytest.raises(RunnerError) as error:
        config_loaders()[loader](path)
    assert error.value.code in {"CONFIG_COMPUTE_SLOT", "DATASET_CONFIG"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["service.json"]


@pytest.mark.parametrize("loader", ["research", "financial", "dataset"])
def test_configs_preserve_same_lock_without_creating_it_and_allow_legacy_omission(
    tmp_path, loader
):
    load = config_loaders()[loader]
    assert "compute_lock_path" not in load(configuration(tmp_path))
    lock = tmp_path / "compute.lock"
    assert load(configuration(tmp_path, compute_lock_path=str(lock)))[
        "compute_lock_path"
    ] == str(lock)
    assert not lock.exists()


def test_validation_does_not_create_open_or_acquire_any_file(tmp_path, monkeypatch):
    import atlas_quant.compute_slot as module

    existing = tmp_path / "existing.lock"
    existing.write_bytes(b"retain")
    existing.chmod(0o600)
    before = existing.stat()

    def forbidden(*args, **kwargs):
        pytest.fail("pure preflight must not open or acquire a lock")

    monkeypatch.setattr(module.os, "open", forbidden)
    monkeypatch.setattr(module.fcntl, "flock", forbidden)
    assert validate_slot_path(str(existing)) == str(existing)
    missing = tmp_path / "absent.lock"
    assert validate_slot_path(str(missing)) == str(missing)
    assert not missing.exists()
    after = existing.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


@pytest.mark.parametrize(
    "kind",
    [
        "missing_parent",
        "shared_parent",
        "parent_symlink",
        "file_symlink",
        "directory",
        "shared_file",
        "executable_file",
        "dotdot",
    ],
)
def test_preflight_refuses_unsafe_or_noncanonical_paths(tmp_path, kind):
    folder = tmp_path / "private"
    folder.mkdir(mode=0o700)
    target = folder / "compute.lock"
    if kind == "missing_parent":
        target = tmp_path / "missing" / "compute.lock"
    elif kind == "shared_parent":
        folder.chmod(0o755)
    elif kind == "parent_symlink":
        link = tmp_path / "link"
        link.symlink_to(folder, target_is_directory=True)
        target = link / "compute.lock"
    elif kind == "file_symlink":
        target.symlink_to(folder / "absent-target")
    elif kind == "directory":
        target.mkdir(mode=0o700)
    elif kind in {"shared_file", "executable_file"}:
        target.write_bytes(b"preserve")
        target.chmod(0o644 if kind == "shared_file" else 0o700)
    elif kind == "dotdot":
        target = folder / ".." / "compute.lock"
    with pytest.raises(ComputeSlotError):
        validate_slot_path(str(target))


def test_runtime_rechecks_fd_permissions_after_successful_preflight(
    tmp_path, monkeypatch
):
    import atlas_quant.compute_slot as module

    path = tmp_path / "compute.lock"
    path.write_bytes(b"retain")
    path.chmod(0o600)
    assert validate_slot_path(str(path)) == str(path)
    original_open = module.os.open

    def changed_before_open(name, flags, mode=0o777):
        path.chmod(0o644)
        return original_open(name, flags, mode)

    monkeypatch.setattr(module.os, "open", changed_before_open)
    with pytest.raises(ComputeSlotError, match="COMPUTE_SLOT_PERMISSIONS"):
        with compute_slot(str(path), deadline=time.monotonic() + 1):
            pytest.fail("changed permissions admitted by stale preflight")
    assert path.read_bytes() == b"retain"


def test_other_owner_file_is_rejected_without_open(tmp_path, monkeypatch):
    import atlas_quant.compute_slot as module

    path = tmp_path / "compute.lock"
    path.write_bytes(b"retain")
    path.chmod(0o600)
    original_lstat = type(path).lstat

    def other_owner(self):
        info = original_lstat(self)
        if self == path:
            values = list(info)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(type(path), "lstat", other_owner)
    with pytest.raises(ComputeSlotError, match="COMPUTE_SLOT_PERMISSIONS"):
        module.validate_slot_path(str(path))
