"""Local typed-dataset commands. No provider, network or estimator entry points."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from atlas_quant.research_dataset import (
    DatasetError,
    DirectoryDatasetReader,
    FinancialSource,
    compose_dataset_components,
    export_dataset_archive,
    extract_dataset_archive,
    restore_dataset,
)
from atlas_quant.research_dataset.codec import keys, require, uuid
from atlas_quant.research_dataset.profile import DEFAULT_PROFILE
from atlas_quant.research_dataset.reader import _read_file


def local_path(value, base):
    require(
        isinstance(value, str) and bool(value) and "://" not in value,
        "DATASET_PATH",
        "A local file path is required; URLs are unsupported",
    )
    path = Path(value)
    return path if path.is_absolute() else base / path


def configuration(path):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "DATASET_CONFIG", "Duplicate configuration key")
            out[key] = value
        return out

    def invalid(_):
        raise DatasetError("DATASET_CONFIG", "Nonfinite configuration number")

    try:
        value = json.loads(
            _read_file(path, DEFAULT_PROFILE.manifest_bytes),
            object_pairs_hook=pairs,
            parse_constant=invalid,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DatasetError(
            "DATASET_CONFIG", "Invalid bounded JSON configuration"
        ) from exc
    require(
        isinstance(value, dict), "DATASET_CONFIG", "Configuration must be an object"
    )
    return value


def registry_pins(path):
    path = Path(path).absolute()
    mapping = configuration(path)
    require(
        1 <= len(mapping) <= 1 + 257 * DEFAULT_PROFILE.max_inputs,
        "DATASET_REGISTRY",
        "Registry reference count exceeds profile",
    )
    registry, total = {}, 0
    for ref, filename in mapping.items():
        uuid(ref)
        raw = _read_file(
            local_path(filename, path.parent), DEFAULT_PROFILE.registry_bytes
        )
        total += len(raw)
        require(
            total <= DEFAULT_PROFILE.registries_bytes,
            "DATASET_BUDGET",
            "Registry bytes exceed profile",
        )
        registry[ref] = raw
    return registry


def pack(args):
    recipe_path = Path(args.recipe).absolute()
    recipe = configuration(recipe_path)
    keys(recipe, {"scope", "market", "marketCalendarRef", "financialInputs"})
    inputs = recipe["financialInputs"]
    require(
        isinstance(inputs, list) and 1 <= len(inputs) <= DEFAULT_PROFILE.max_inputs,
        "DATASET_SOURCE",
        "One to eight frozen financial inputs are required",
    )
    sources, total = [], 0
    for source in inputs:
        keys(source, {"package", "preparedRoot", "calendarRef", "proofRefs"})
        require(
            isinstance(source["proofRefs"], list),
            "DATASET_SOURCE",
            "Proof refs must be a list",
        )
        raw = _read_file(
            local_path(source["package"], recipe_path.parent),
            DEFAULT_PROFILE.package_bytes,
        )
        total += len(raw)
        require(
            total <= DEFAULT_PROFILE.package_bytes,
            "DATASET_BUDGET",
            "Package bytes exceed profile",
        )
        sources.append(
            FinancialSource(
                raw,
                source["preparedRoot"],
                source["calendarRef"],
                tuple(source["proofRefs"]),
            )
        )
    market = _read_file(
        local_path(recipe["market"], recipe_path.parent), DEFAULT_PROFILE.market_bytes
    )
    registry = registry_pins(args.registry_pins)
    destination = Path(args.output).absolute()
    require(
        not os.path.lexists(destination),
        "DATASET_OUTPUT_EXISTS",
        "Existing output is preserved",
    )
    with tempfile.TemporaryDirectory(
        prefix=".atlas-dataset-compose-", dir=destination.parent
    ) as directory:
        staging = Path(directory)

        def write(component, ordinal, raw):
            path = staging / "parts" / component / f"{ordinal}.bin"
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)

        publication = compose_dataset_components(
            recipe["scope"],
            market,
            sources,
            registry,
            write,
            market_calendar_ref=recipe["marketCalendarRef"],
        )
        (staging / "manifest.json").write_bytes(publication.manifest_bytes)
        result = export_dataset_archive(DirectoryDatasetReader(staging), destination)
        return {
            **result,
            "financialRecomputed": True,
            "registryPinsMatched": True,
            "rows": len(publication.result.data),
            "providerCalls": 0,
            "modelFits": 0,
            "hostedAdmission": False,
            "originalDocumentAuthorityVerified": False,
        }


def extract(args):
    return extract_dataset_archive(
        args.archive, args.output, expected_root=args.dataset_root
    )


def recompose(args):
    reader = DirectoryDatasetReader(args.dataset, expected_root=args.dataset_root)
    restored = restore_dataset(reader, registry_pins(args.registry_pins))
    return {
        "datasetRoot": reader.dataset_root,
        "transportVerified": True,
        "registryPinsMatched": True,
        "financialRecomputed": True,
        "financialDatasetRoot": restored.provenance["financialDatasetRoot"],
        "rows": len(restored.data),
        "providerCalls": 0,
        "modelFits": 0,
        "hostedAdmission": False,
        "originalDocumentAuthorityVerified": False,
    }


def main(command):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    if command == "pack":
        parser.add_argument("--recipe", required=True)
        parser.add_argument("--registry-pins", required=True)
        parser.add_argument("--output", required=True, help="New private USTAR file")
    elif command == "extract":
        parser.add_argument("archive")
        parser.add_argument("--output", required=True, help="New private directory")
        parser.add_argument("--dataset-root", required=True)
    elif command == "recompose":
        parser.add_argument("dataset", help="Previously extracted private directory")
        parser.add_argument("--dataset-root", required=True)
        parser.add_argument("--registry-pins", required=True)
    else:
        raise ValueError("Unknown local command")
    args = parser.parse_args()
    try:
        result = {"pack": pack, "extract": extract, "recompose": recompose}[command](
            args
        )
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (DatasetError, ValueError, OSError) as error:
        print(
            json.dumps(
                {
                    "error": {
                        "code": getattr(error, "code", "DATASET_LOCAL_IO"),
                        "message": (
                            str(error)
                            if isinstance(error, ValueError)
                            else "Unable to read or publish a local file"
                        ),
                    }
                }
            ),
            file=sys.stderr,
        )
        return 2
