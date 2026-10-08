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


@contextmanager
def compute_slot(path, *, deadline, check=None):
    if path is None:
        yield
        return
    if (
        not isinstance(path, str)
        or not path
        or not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(deadline)
    ):
        raise ComputeSlotError("COMPUTE_SLOT_CONFIG")
    target = Path(path)
    parent = target.parent
    if (
        not target.is_absolute()
        or parent.resolve() != parent
        or not parent.is_dir()
        or stat.S_IMODE(parent.stat().st_mode) & 0o077
    ):
        raise ComputeSlotError("COMPUTE_SLOT_PATH")
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
                or stat.S_IMODE(info.st_mode) & 0o077
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
