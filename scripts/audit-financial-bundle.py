#!/usr/bin/env python3
"""Audit private financial forecast directory/TAR and its separately saved dataset."""

import argparse
import json
import os
from pathlib import Path
import sys
import sqlite3

from dataset_audit import load_registry_pins
from financial_bundle_audit import audit_financial_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--source-dataset")
    parser.add_argument("--registry-pins")
    parser.add_argument(
        "--output", help="Optional new private JSON file; never overwritten"
    )
    args = parser.parse_args()
    try:
        report = audit_financial_bundle(
            args.input,
            source_dataset=args.source_dataset,
            registry_pins=(
                load_registry_pins(args.registry_pins) if args.registry_pins else None
            ),
        )
        code = 0 if report["sourceEvidenceClosed"] else 2
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        sqlite3.DatabaseError,
        RecursionError,
        OverflowError,
    ) as error:
        report = {
            "status": "FAIL",
            "transportVerified": False,
            "sourceEvidenceClosed": False,
            "registryTrustStatus": "unverified",
            "modelFitted": False,
            "providerCalls": 0,
            "message": "Financial transport, source closure or input file failed validation",
            "code": getattr(error, "code", "INTEGRITY"),
        }
        code = 1
    raw = (
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode()
    if args.output:
        try:
            descriptor = os.open(
                Path(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            print(
                "Output could not be created; existing files are preserved.",
                file=sys.stderr,
            )
            return 1
    sys.stdout.buffer.write(raw)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
