"""Engine supplies synthetic fixtures only; independent audit imports stdlib."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from atlas_quant.financial_bundle import export_financial_bundle
from atlas_quant.research_dataset import export_dataset_archive
from atlas_quant.research_dataset.codec import encode
from test_financial_bundle import financial_research, long_sources, sources, pack
from test_research_dataset_components import reader as dataset_reader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from financial_bundle_audit import audit_financial_bundle
from bundle_audit import audit_bundle
from bundle_archive import _expected_header


@pytest.fixture
def exports(financial_research, tmp_path):
    directory = tmp_path / "financial"
    export_financial_bundle(pack(financial_research)[2], directory)
    dataset = tmp_path / "dataset.tar"
    export_dataset_archive(
        dataset_reader(financial_research["publication"], financial_research["parts"]),
        dataset,
    )
    return directory, dataset


def archive(directory, path):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    names = ["manifest.json"] + [
        f'chunks/{c["id"]}/{d["ordinal"]}.json'
        for c in manifest["collections"]
        for d in c["chunks"]
    ]
    with path.open("wb") as output:
        for name in names:
            raw = (directory / name).read_bytes()
            output.write(_expected_header(name, len(raw)))
            output.write(raw)
            output.write(bytes((-len(raw)) % 512))
        output.write(bytes(1024))
    return path


def test_independent_transport_is_explicitly_incomplete_without_sidecar(exports):
    result = audit_financial_bundle(exports[0])
    assert result["status"] == "INCOMPLETE_SOURCE"
    assert result["transportVerified"] is True
    assert result["sourceEvidenceClosed"] is False
    assert result["registryTrustStatus"] == "unverified"
    assert result["engineImports"] is False
    assert result["recomposition"] == "not_performed"
    with pytest.raises(ValueError, match="Unsupported bundle"):
        audit_bundle(exports[0])


def test_sidecar_closes_exact_typed_rows_and_keeps_authority_separate(
    exports, financial_research
):
    result = audit_financial_bundle(exports[0], source_dataset=exports[1])
    assert result["status"] == "PASS"
    assert result["sourceEvidenceClosed"] is True
    assert result["registryTrustStatus"] == "unverified"
    trusted = audit_financial_bundle(
        exports[0],
        source_dataset=exports[1],
        registry_pins=financial_research["registry"],
    )
    assert trusted["registryTrustStatus"] == "external_registry_bytes_matched"
    assert trusted["pdfAuthenticityVerified"] is False
    assert trusted["financialFormulasRecomputed"] is False
    assert trusted["modelFitted"] is False
    with pytest.raises(ValueError):
        audit_financial_bundle(exports[0], source_dataset=exports[1], registry_pins={})


def test_raw_tar_audits_and_missing_exact_eof_rejects(exports, tmp_path):
    path = archive(exports[0], tmp_path / "financial.tar")
    result = audit_financial_bundle(path, source_dataset=exports[1])
    assert result["status"] == "PASS"
    path.write_bytes(path.read_bytes()[:-512])
    with pytest.raises(ValueError):
        audit_financial_bundle(path, source_dataset=exports[1])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.replace(b'"positiveZero":0.0', b'"positiveZero":0'),
        lambda raw: raw.replace(b'"negativeZero":-0.0', b'"negativeZero":0.0'),
        lambda raw: raw.replace(b'"floating":1.0', b'"floating":1'),
    ],
)
def test_self_consistent_rehash_cannot_rewrite_source_types(
    financial_research, tmp_path, mutation
):
    altered = deepcopy(financial_research)
    altered["snapshot"] = mutation(altered["snapshot"])
    assert altered["snapshot"] != financial_research["snapshot"]
    directory = tmp_path / "changed"
    export_financial_bundle(pack(altered)[2], directory)
    dataset = tmp_path / "dataset.tar"
    export_dataset_archive(
        dataset_reader(financial_research["publication"], financial_research["parts"]),
        dataset,
    )
    with pytest.raises(ValueError, match="Snapshot provenance differs"):
        audit_financial_bundle(directory, source_dataset=dataset)


def test_cli_is_standalone_stdlib_and_does_not_overwrite(exports, tmp_path):
    output = tmp_path / "audit.json"
    args = [
        sys.executable,
        "-I",
        str(ROOT / "scripts/audit-financial-bundle.py"),
        str(exports[0]),
        "--output",
        str(output),
    ]
    # -I removes sibling module lookup, so run the script using runpy with only
    # its scripts directory added; no engine/PYTHONPATH is available.
    program = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name='__main__')"
    args = [sys.executable, "-I", "-c", program, str(ROOT / "scripts"), *args[2:]]
    first = subprocess.run(args, capture_output=True, text=True)
    assert first.returncode == 2, first.stderr
    assert json.loads(first.stdout)["status"] == "INCOMPLETE_SOURCE"
    original = output.read_bytes()
    second = subprocess.run(args, capture_output=True, text=True)
    assert second.returncode == 1
    assert output.read_bytes() == original
