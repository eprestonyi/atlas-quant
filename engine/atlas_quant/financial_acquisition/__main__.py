"""Explicit provider-enabled process; never launched by the preparation runner."""

import argparse
from ..runner import load_config
from .protocol import require
from .service import serve


def main():
    parser = argparse.ArgumentParser(
        description="Isolated financial-acquire/v1 consumer"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
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
    serve(config, once=args.once)


if __name__ == "__main__":
    main()
