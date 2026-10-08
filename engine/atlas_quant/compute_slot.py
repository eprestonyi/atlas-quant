"""One optional, private host-wide compute slot; waiting consumes the job budget."""

from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import stat
import time


class ComputeSlotError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_slot_path(path):
    """Check an explicit configuration without creating or acquiring its lock."""
    if not isinstance(path, str) or not path:
        raise ComputeSlotError("COMPUTE_SLOT_CONFIG")
    target = Path(path)
    parent = target.parent
    try:
        if (
            not target.is_absolute()
            or str(target) != path
            or parent.resolve() != parent
        ):
            raise ComputeSlotError("COMPUTE_SLOT_PATH")
        directory = parent.stat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) & 0o077
        ):
            raise ComputeSlotError("COMPUTE_SLOT_PATH")
        try:
            info = target.lstat()
        except FileNotFoundError:
            return path
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ComputeSlotError("COMPUTE_SLOT_PERMISSIONS")
    except OSError:
        raise ComputeSlotError("COMPUTE_SLOT_IO") from None
    return path


@contextmanager
def compute_slot(path, *, deadline, check=None):
    if path is None:
        yield
        return
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(deadline)
    ):
        raise ComputeSlotError("COMPUTE_SLOT_CONFIG")
    target = Path(validate_slot_path(path))
    descriptor = None
    acquired = False
    try:
        try:
            descriptor = os.open(
                target,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                0o600,
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ComputeSlotError("COMPUTE_SLOT_PERMISSIONS")
            while True:
                if check is not None:
                    check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ComputeSlotError("COMPUTE_SLOT_TIMEOUT")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(min(0.05, remaining))
            if check is not None:
                check()
            if time.monotonic() >= deadline:
                raise ComputeSlotError("COMPUTE_SLOT_TIMEOUT")
        except OSError:
            raise ComputeSlotError("COMPUTE_SLOT_IO") from None
        yield
    finally:
        if descriptor is not None:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
