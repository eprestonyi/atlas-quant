"""Lease confirmation never extends the server's original absolute deadline."""

from datetime import datetime
import threading
import time

from .. import __version__
from ..runner import RunnerError
from .protocol import CAPABILITY, LIMITS, fail, require


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        fail("DATASET_LEASE", "租约需要带时区的固定截止时间。")


class LeaseMonitor:
    def __init__(self, client, job, *, stop_requested=None, interval=20):
        self.client, self.job = client, dict(job)
        self.stop_requested = stop_requested or (lambda: False)
        self.interval = interval
        now = time.monotonic()
        self.deadline = now + min(
            LIMITS["deadlineSeconds"], timestamp(job["deadline"]) - time.time()
        )
        self.lease_until = now + min(
            LIMITS["leaseSeconds"], timestamp(job["leaseUntil"]) - time.time()
        )
        self.phase, self.error = "checking_sources", None
        self.closed, self.thread = threading.Event(), None

    def check(self):
        if self.stop_requested():
            fail("RUNNER_STOPPED")
        if self.error is not None:
            raise self.error
        if time.monotonic() >= self.deadline:
            fail("DATASET_DEADLINE")
        if time.monotonic() >= self.lease_until:
            fail("LEASE_EXPIRED")

    def pulse(self):
        self.check()
        try:
            response = self.client.post(
                "heartbeat",
                {
                    "capability": CAPABILITY,
                    "engineVersion": __version__,
                    "state": "busy",
                    "jobId": self.job["id"],
                    "leaseToken": self.job["leaseToken"],
                    "phase": self.phase,
                },
                deadline=min(self.deadline, time.monotonic() + 20),
            )
        except RunnerError as error:
            if error.code == "DATASET_NETWORK" or (
                error.code == "DATASET_HTTP"
                and isinstance(error.http_status, int)
                and 500 <= error.http_status <= 599
            ):
                self.check()  # Lost renewal does not add time to a confirmed lease.
                return
            raise
        require(isinstance(response, dict) and response.get("ok") is True)
        if response.get("cancelRequested") is True:
            fail("CANCELLED")
        require(response.get("leaseValid") is True, "LEASE_EXPIRED")
        until = timestamp(response.get("leaseUntil"))
        require(until <= timestamp(self.job["deadline"]), "DATASET_LEASE")
        self.lease_until = time.monotonic() + min(
            LIMITS["leaseSeconds"], until - time.time()
        )
        self.check()

    def _loop(self):
        while not self.closed.wait(self.interval):
            try:
                self.pulse()
            except RunnerError as error:
                self.error = error
                return
            except Exception:
                self.error = RunnerError("DATASET_HEARTBEAT", "数据集心跳中断。")
                return

    def __enter__(self):
        self.pulse()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=1)
