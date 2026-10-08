"""Explicit opt-in CLI; no provider enablement through the research runner."""

import argparse
import signal
import threading
from ..runner import load_config, RunnerError
from .protocol import require, encode
from .service import serve, safe_error


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    stop = threading.Event()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: stop.set())
    try:
        c = load_config(a.config)
        require(
            c.get("allow_market_fixtures", False) is False,
            "MARKET_SOURCE_KIND",
            "Production CLI refuses fixture sources",
        )
        serve(c, once=a.once, stop_requested=stop.is_set)
        return 0
    except (RunnerError, OSError) as e:
        print(encode(safe_error(e)).decode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
