"""Actual subprocess commands over synthetic frozen evidence, never provider/F."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from atlas_quant.research_dataset.codec import encode
from test_research_dataset_components import sources

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def recipe_files(tmp_path, sources):
    (tmp_path / "market.json").write_bytes(encode(sources["market"]))
    (tmp_path / "financial.json").write_bytes(sources["source"].package_bytes)
    pins = {}
    for ref, raw in sources["registry"].items():
        name = ref + ".json"
        (tmp_path / name).write_bytes(raw)
        pins[ref] = name
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    recipe = {
        "scope": sources["scope"],
        "market": "market.json",
        "marketCalendarRef": sources["calendar"],
        "financialInputs": [
            {
                "package": "financial.json",
                "preparedRoot": sources["source"].prepared_root,
                "calendarRef": sources["calendar"],
                "proofRefs": [],
            }
        ],
    }
    (tmp_path / "recipe.json").write_text(json.dumps(recipe, indent=2))
    return tmp_path, recipe


def command(name, *args, success=True):
    # Run from another cwd without PYTHONPATH to exercise an actual checkout CLI.
    env = {
        k: v for k, v in os.environ.items() if k != "PYTHONPATH" and "TOKEN" not in k
    }
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), *map(str, args)],
        cwd=ROOT.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == (0 if success else 2), process.stderr
    return json.loads(process.stdout if success else process.stderr)


def pack(path, success=True):
    return command(
        "pack-dataset.py",
        "--recipe",
        path / "recipe.json",
        "--registry-pins",
        path / "pins.json",
        "--output",
        path / "dataset.tar",
        success=success,
    )


def test_cli_pack_extract_recompose_and_existing_outputs_are_preserved(recipe_files):
    path, _ = recipe_files
    result = pack(path)
    root = result["datasetRoot"]
    archive = (path / "dataset.tar").read_bytes()
    assert result["financialRecomputed"] and result["registryPinsMatched"]
    assert result["modelFits"] == result["providerCalls"] == 0
    assert result["originalDocumentAuthorityVerified"] is False
    assert pack(path, success=False)["error"]["code"] == "DATASET_OUTPUT_EXISTS"
    assert (path / "dataset.tar").read_bytes() == archive
    extracted = command(
        "extract-dataset.py",
        path / "dataset.tar",
        "--output",
        path / "dataset",
        "--dataset-root",
        root,
    )
    assert extracted["sourceAuthorityVerified"] is False
    assert extracted["financialRecomputed"] is False
    recomposed = command(
        "recompose-dataset.py",
        path / "dataset",
        "--dataset-root",
        root,
        "--registry-pins",
        path / "pins.json",
    )
    assert recomposed["datasetRoot"] == root and recomposed["financialRecomputed"]
    again = command(
        "extract-dataset.py",
        path / "dataset.tar",
        "--output",
        path / "dataset",
        "--dataset-root",
        root,
        success=False,
    )
    assert again["error"]["code"] == "DATASET_OUTPUT_EXISTS"
    assert (path / "dataset.tar").stat().st_mode & 0o077 == 0
    assert (path / "dataset").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("case", ["unknown", "url", "proof_array", "duplicate"])
def test_cli_invalid_recipe_never_publishes_output(recipe_files, case):
    path, recipe = recipe_files
    if case == "unknown":
        recipe["providerToken"] = "explicit-test-only"
    elif case == "url":
        recipe["market"] = "https://example.invalid/market.json"
    elif case == "proof_array":
        recipe["financialInputs"][0]["proofRefs"] = "not-an-array"
    else:
        (path / "recipe.json").write_text('{"scope":{},"scope":{}}')
    if case != "duplicate":
        (path / "recipe.json").write_text(json.dumps(recipe))
    pack(path, success=False)
    assert not (path / "dataset.tar").exists()
    assert not list(path.glob(".atlas-dataset-*"))


def test_cli_wrong_root_and_changed_external_pin_fail(recipe_files):
    path, _ = recipe_files
    root = pack(path)["datasetRoot"]
    command(
        "extract-dataset.py",
        path / "dataset.tar",
        "--output",
        path / "bad",
        "--dataset-root",
        "0" * 64,
        success=False,
    )
    assert not (path / "bad").exists()
    command(
        "extract-dataset.py",
        path / "dataset.tar",
        "--output",
        path / "dataset",
        "--dataset-root",
        root,
    )
    ref, filename = next(iter(json.loads((path / "pins.json").read_text()).items()))
    (path / filename).write_bytes(b"{}")
    result = command(
        "recompose-dataset.py",
        path / "dataset",
        "--dataset-root",
        root,
        "--registry-pins",
        path / "pins.json",
        success=False,
    )
    assert result["error"]["code"] == "DATASET_AUTHORITY"
