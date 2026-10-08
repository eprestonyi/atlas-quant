"""Hard in-flight profile checks in the parent, including a stuck model fit."""

import subprocess
import time
from ..market_acquisition.protocol import decode, require
from .spool import MarketResearchSpool


class MarketProcessBudget:
    def __init__(self, context):
        self.store = MarketResearchSpool(context)
        self.next_check = 0.0

    def check(self, pid):
        now = time.monotonic()
        if now < self.next_check:
            return
        self.next_check = now + 1
        try:
            state = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
            )
            require(
                state.returncode == 0 and bool(state.stdout.strip()),
                "CAPACITY_MONITOR",
                "Cannot verify process memory usage",
            )
            rss = int(state.stdout.strip()) * 1024
            require(rss >= 0, "CAPACITY_MONITOR", "Invalid process memory sample")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            require(False, "CAPACITY_MONITOR", "Cannot enforce declared memory limit")
        require(
            rss <= 3 * 1024**3,
            "CAPACITY_MEMORY",
            "Whole-pool process exceeds 3 GiB RSS",
        )
        progress_path = self.store.root / "progress.enc"
        if progress_path.exists():
            progress = decode(self.store.read("progress"), limit=256 * 1024)
            require(
                not (
                    progress.get("phase") == "fit_started"
                    and now - progress["startedMonotonic"] > 300
                ),
                "CAPACITY_FIT_TIMEOUT",
                "One pooled model fit exceeds 300 seconds",
            )
        cache = self.store.root / "cache"
        try:
            size = (
                sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
                if cache.exists()
                else 0
            )
        except FileNotFoundError:
            return  # Child completed atomic cache cleanup between directory reads.
        require(
            size <= 400 * 1024 * 1024,
            "CAPACITY_DISK",
            "Whole-pool cache exceeds 400 MiB",
        )
