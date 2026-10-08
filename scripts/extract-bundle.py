#!/usr/bin/env python3
"""Strictly extract and independently audit a private Atlas reproduction tar."""

import argparse
import json
import sqlite3

from bundle_archive import extract_bundle_archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Downloaded Atlas bundle.tar file")
    parser.add_argument(
        "directory", help="New output directory; parent must already exist"
    )
    parser.add_argument(
        "--source-bundle",
        help="Original forecast bundle with frozen inputs, required for execution archives",
    )
    args = parser.parse_args()
    try:
        result = extract_bundle_archive(
            args.archive, args.directory, source_bundle=args.source_bundle
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        sqlite3.DatabaseError,
        RecursionError,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": getattr(error, "code", "ARCHIVE_AUDIT"),
                    "reason": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
