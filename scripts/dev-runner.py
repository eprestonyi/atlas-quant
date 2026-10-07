"""Compute tutorial/upload jobs from the loopback-only development server."""
import os
import argparse
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from atlas_quant.runner import serve, _stop, load_config

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Optional existing private provider configuration; never copied to the dev server")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    port = int(os.environ.get("PORT", "8895"))
    if not 1024 <= port <= 65535:
        raise SystemExit("PORT must be between 1024 and 65535")
    private = load_config(args.config) if args.config else {}
    provider = {key: private[key] for key in ("provider_access", "pcd_access", "allowed_proxy_hosts") if key in private}
    raise SystemExit(serve({
        "api_base": f"http://127.0.0.1:{port}/quant/api",
        "runner_secret": "local-development-runner-secret-not-for-deployment",
        "delivery_dir": str(ROOT / "private" / "dev-delivery" / str(port)),
        "poll_seconds": 3,
        "job_timeout": 600,
        "allowed_proxy_hosts": [],
        **provider,
    }))
