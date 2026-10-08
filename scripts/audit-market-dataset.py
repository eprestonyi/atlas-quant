#!/usr/bin/env python3
"""Read-only stdlib market source auditor. No provider request or model fit."""
import argparse
import json
import os
from pathlib import Path
import sys
from market_dataset_audit import AuditError, audit_market_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Market source directory or strict USTAR archive")
    parser.add_argument(
        "--expected-root", help="Independently pinned market dataset SHA-256"
    )
    parser.add_argument(
        "--output", help="Optional new JSON result; never overwrites a file"
    )
    parser.add_argument(
        "--result-bundle",
        help="Pair with a full forecast bundle directory or strict USTAR",
    )
    args = parser.parse_args()
    try:
        report = audit_market_dataset(
            args.input,
            expected_root=args.expected_root,
            result_bundle=args.result_bundle,
        )
        code = 0
    except (AuditError, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        report = dict(
            status="FAIL",
            auditor="atlas.market_dataset.stdlib_audit/1",
            code=getattr(exc, "code", "INPUT"),
            message=(
                str(exc) if isinstance(exc, AuditError) else "Invalid bounded input"
            ),
            integrityVerified=False,
            normalizationVerified=False,
            trustStatus="unverified_source_authority",
            sourceAuthorityVerified=False,
            providerCalls=0,
            modelFitted=False,
        )
        code = 1
    raw = (
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode()
    if args.output:
        try:
            fd = os.open(Path(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            print(
                "Audit result could not be created; existing files are preserved.",
                file=sys.stderr,
            )
            return 2
    sys.stdout.buffer.write(raw)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
