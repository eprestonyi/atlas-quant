#!/usr/bin/env python3
"""Audit a private manifest.json + chunks/<collection>/<ordinal>.json export."""
import argparse
import json
import sqlite3
from bundle_audit import audit_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--source-bundle", help="Independently validate the unchanged forecast and origin plan")
    args = parser.parse_args()
    try:
        result = audit_bundle(args.directory, args.source_bundle)
    except (KeyError, TypeError, ValueError, OSError, sqlite3.DatabaseError, RecursionError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
