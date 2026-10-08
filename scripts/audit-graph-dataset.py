#!/usr/bin/env python3
"""Read-only stdlib dataset/3 graph closure auditor. Does not extract or fit a model."""

import argparse
import json
import os
from pathlib import Path
import sys

from dataset_audit import AuditError, load_registry_pins
from graph_dataset_audit import audit_graph_dataset, load_source_pins


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", help="Dataset directory or strict atlas-dataset-ustar-v1 archive"
    )
    parser.add_argument(
        "--expected-root", help="Independently pinned lowercase dataset SHA-256"
    )
    parser.add_argument(
        "--registry-pins",
        help="Separately authorized JSON object mapping UUID to local registry file",
    )
    parser.add_argument(
        "--output",
        help="Optional NEW result JSON; existing files are never overwritten",
    )
    parser.add_argument('--source-pins', help='Separately authorized local-file map: sourceManifest, sourceSnapshot, financialInputN')
    args = parser.parse_args()
    try:
        pins = load_registry_pins(args.registry_pins) if args.registry_pins else None
        report = audit_graph_dataset(
            args.input, expected_root=args.expected_root, registry_pins=pins,
            source_pins=load_source_pins(args.source_pins) if args.source_pins else None
        )
        code = 0
    except (AuditError, OSError, ValueError) as error:
        report = {
            "status": "FAIL",
            "auditor": "atlas.graph_dataset.stdlib_audit/1",
            "code": getattr(error, "code", "INPUT"),
            "message": (
                str(error)
                if isinstance(error, AuditError)
                else "Input file or JSON could not be read"
            ),
            "integrityVerified": False,
            "trustStatus": "unverified",
            "sourceAuthorityVerified": False,
            "providerCalls": 0,
            "modelFitted": False,
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
            fd = os.open(Path(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            print(
                "Audit output could not be created; existing files are preserved.",
                file=sys.stderr,
            )
            return 2
    sys.stdout.buffer.write(raw)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
