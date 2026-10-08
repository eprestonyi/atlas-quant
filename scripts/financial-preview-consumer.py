"""Run the real financial consumer against the isolated loopback preview only.

Production's HTTPS-only configuration loader remains unchanged. This developer
entrypoint reads the preview's private bootstrap file and has no provider path.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from atlas_quant.financial_runner.service import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", type=Path,
                        default=ROOT / "private/financial-preview-session.json")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    source = args.bootstrap.resolve()
    if args.bootstrap.is_symlink() or not source.is_file():
        raise SystemExit("Bootstrap must be a private regular file.")
    if stat.S_IMODE(source.stat().st_mode) & 0o077 or source.stat().st_size > 32768:
        raise SystemExit("Bootstrap must be bounded and mode 0600 or stricter.")
    bootstrap = json.loads(source.read_bytes())
    base = bootstrap.get("baseUrl", "")
    url = urlsplit(base)
    if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"}
            or url.username or url.password or url.query or url.fragment
            or url.path not in {"", "/"}):
        raise SystemExit("Only the isolated loopback HTTP preview is accepted.")
    if bootstrap.get("synthetic") is not True or bootstrap.get("providerCalls") != 0:
        raise SystemExit("Only an explicitly synthetic preview is accepted.")
    secret = bootstrap.get("runnerSecret")
    if not isinstance(secret, str) or not 32 <= len(secret) <= 512:
        raise SystemExit("Invalid preview runner credential.")
    os.umask(0o077)
    config = {
        "api_base": base.rstrip("/") + "/quant/api",
        "runner_secret": secret,
        "delivery_dir": str(ROOT / "private/financial-preview-research-unused"),
        "financial_delivery_dir": str(ROOT / "private/financial-preview-consumer-spool"),
        "poll_seconds": 3,
    }
    stopped = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopped.set())
    return serve(config, once=args.once, stop_requested=stopped.is_set)


if __name__ == "__main__":
    raise SystemExit(main())
