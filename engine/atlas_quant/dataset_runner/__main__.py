"""Provider-free entrypoint with a separate explicit service configuration."""

import argparse
from pathlib import Path
import signal
import stat
import sys
import threading
from urllib.parse import urlsplit

from ..runner import RunnerError
from .compute import safe_error
from .protocol import decode, encode, require
from .service import serve


def load_config(path):
    target = Path(path).expanduser()
    require(
        target.is_absolute() and not target.is_symlink() and target.is_file(),
        "DATASET_CONFIG",
    )
    require(
        not stat.S_IMODE(target.stat().st_mode) & 0o077
        and target.stat().st_size <= 32768,
        "DATASET_CONFIG",
    )
    value = decode(target.read_bytes(), limit=32768)
    allowed = {
        "api_base",
        "runner_secret",
        "delivery_dir",
        "financial_delivery_dir",
        "acquisition_delivery_dir",
        "dataset_delivery_dir",
        "compute_lock_path",
        "dataset_enabled",
        "poll_seconds",
    }
    require(isinstance(value, dict) and set(value) <= allowed, "DATASET_CONFIG")
    require(value.get("dataset_enabled") is True, "DATASET_DISABLED")
    base, secret = value.get("api_base"), value.get("runner_secret")
    require(isinstance(base, str), "DATASET_CONFIG")
    parsed = urlsplit(base)
    require(
        parsed.scheme == "https"
        and parsed.hostname
        and not any((parsed.username, parsed.password, parsed.query, parsed.fragment)),
        "DATASET_CONFIG",
    )
    require(
        isinstance(secret, str)
        and 32 <= len(secret) <= 512
        and not any(c.isspace() for c in secret),
        "DATASET_CONFIG",
    )
    for name in ("delivery_dir", "dataset_delivery_dir"):
        require(
            isinstance(value.get(name), str) and Path(value[name]).is_absolute(),
            "DATASET_CONFIG",
        )
    if "compute_lock_path" in value:
        from ..compute_slot import ComputeSlotError, validate_slot_path

        try:
            validate_slot_path(value["compute_lock_path"])
        except ComputeSlotError:
            require(False, "DATASET_CONFIG")
    poll = value.get("poll_seconds", 10)
    require(type(poll) is int and 3 <= poll <= 60, "DATASET_CONFIG")
    return {**value, "api_base": base.rstrip("/"), "poll_seconds": poll}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compose frozen research datasets without provider acquisition"
    )
    parser.add_argument(
        "--config", required=True, help="Dedicated private 0600 configuration"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    stopped = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopped.set())
    try:
        return serve(
            load_config(args.config), once=args.once, stop_requested=stopped.is_set
        )
    except (RunnerError, OSError, ValueError) as error:
        print(encode({"error": safe_error(error)}).decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
