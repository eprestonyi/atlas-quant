"""Explicit provider-enabled process; never launched by the preparation runner."""

import argparse
import signal
import sys
import threading
from ..runner import RunnerError, load_config
from .protocol import encode, require, safe_error
from .service import serve


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Isolated financial-acquire/v1 consumer"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    stopped = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopped.set())
    try:
        config = load_config(args.config)
        require(
            config.get("acquisition_enabled") is True
            and isinstance(config.get("authorization_scope"), str),
            "ACQUISITION_DISABLED",
            "Explicit acquisition enablement and authorization scope required",
        )
        require(
            config.get("allow_acquisition_fixtures", False) is False,
            "ACQUISITION_SOURCE_KIND",
            "The production CLI never enables fixture provider responses",
        )
        serve(config, once=args.once, stop_requested=stopped.is_set)
        return 0
    except (RunnerError, OSError) as error:
        print(encode(safe_error(error)).decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
