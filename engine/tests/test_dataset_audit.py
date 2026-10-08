"""Independent stdlib reader; engine is used ONLY to generate synthetic fixtures."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_research_dataset_components import sources, compose, reader
from atlas_quant.research_dataset import export_dataset_archive

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "independent_dataset_audit", ROOT / "scripts/dataset_audit.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def fixture(sources):
    publication, parts = compose(sources)
    manifest = json.loads(publication.manifest_bytes)
    values = {
        c["componentId"]: json.loads(
            b"".join(parts[(c["componentId"], p["ordinal"])] for p in c["parts"])
        )
        for c in manifest["components"]
    }
    return publication, parts, values, sources


def write_closure(folder, manifest, payloads):
    folder.mkdir()
    (folder / "manifest.json").write_bytes(audit.encode(manifest))
    for component in manifest["components"]:
        name = component["componentId"]
        target = folder / "parts" / name
        target.mkdir(parents=True)
        raw = payloads[name]
        offset = 0
        for part in component["parts"]:
            (target / f"{part['ordinal']}.bin").write_bytes(
                raw[offset : offset + part["byteLength"]]
            )
            offset += part["byteLength"]
    return folder


def rebuild(fixture, tmp_path, mutate):
    """Adversary recomputes all transport hashes; semantic checks must still fail."""
    publication, _, original, _ = fixture
    m, values = json.loads(publication.manifest_bytes), deepcopy(original)
    mutate(m, values)
    mapping, payloads = {}, {}
    for component in m["components"]:
        raw = audit.encode(values[component["componentId"]])
        payloads[component["componentId"]] = raw
        old = component["componentRoot"]
        component["dependencies"] = sorted(
            mapping.get(d, d) for d in component["dependencies"]
        )
        component["byteLength"], component["payloadSha256"] = len(raw), audit.sha(raw)
        component["parts"] = [
            {
                "ordinal": i,
                "byteLength": len(raw[n : n + audit.PART]),
                "sha256": audit.sha(raw[n : n + audit.PART]),
            }
            for i, n in enumerate(range(0, len(raw), audit.PART))
        ]
        component["componentRoot"] = audit.root(
            audit.without(component, "componentRoot")
        )
        mapping[old] = component["componentRoot"]
    return write_closure(tmp_path / "closure", m, payloads)


def test_true_all_state_fixture_archive_directory_and_unverified_default(
    fixture, tmp_path
):
    publication, parts, _, source = fixture
    path = tmp_path / "dataset.tar"
    export_dataset_archive(reader(publication, parts), path)
    result = audit.audit_dataset(path, expected_root=publication.dataset_root)
    assert result["status"] == "PASS"
    assert result["states"] == 16
    assert result["trustStatus"] == "unverified"
    assert result["financialFormulasRecomputed"] is False
    assert result["sourceAuthorityVerified"] is False
    directory = rebuild(fixture, tmp_path, lambda *_: None)
    pinned = audit.audit_dataset(directory, registry_pins=source["registry"])
    assert pinned["datasetRoot"] == publication.dataset_root
    assert pinned["trustStatus"] == "external_registry_bytes_matched"
    assert pinned["pdfAuthenticityVerified"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "joined_value",
        "orphan_event",
        "missing_assignment",
        "missing_coverage",
        "calendar_scope",
        "registry_raw",
        "market_root",
        "prepared_root",
        "schema_evidence",
        "source_reorder",
    ],
)
def test_rehashed_transport_cannot_conceal_semantic_damage(fixture, tmp_path, mutation):
    def mutate(m, v):
        if mutation == "joined_value":
            v["researchRows"]["rows"][0]["close"] += 1
        elif mutation == "orphan_event":
            v["financialPrepared0"]["stateEvents"].pop()
        elif mutation == "missing_assignment":
            v["financialPrepared0"]["assignments"].pop()
        elif mutation == "missing_coverage":
            v["coverage"]["financial"][0]["securities"][0]["states"].pop()
        elif mutation == "calendar_scope":
            e = v["registryEvidence"]["entries"][0]
            raw = json.loads(e["rawText"])
            raw["scope"]["calendarRoot"] = "0" * 64
            e["rawText"] = audit.encode(raw).decode()
            e["sha256"], e["byteLength"] = audit.sha(e["rawText"].encode()), len(
                e["rawText"].encode()
            )
        elif mutation == "registry_raw":
            v["registryEvidence"]["entries"][0]["rawText"] += " "
        elif mutation == "market_root":
            m["roots"]["marketRoot"] = "0" * 64
        elif mutation == "prepared_root":
            v["financialPrepared0"]["provenance"]["preparedRoot"] = "0" * 64
        elif mutation == "schema_evidence":
            v["schema"]["externalFields"] = {}
        else:
            m["financialSources"][0]["componentId"] = "financialInput1"

    with pytest.raises(audit.AuditError):
        audit.audit_dataset(rebuild(fixture, tmp_path, mutate))


def test_external_authority_is_never_inferred_from_internal_registry(fixture, tmp_path):
    def mutation(_, v):
        entry = v["registryEvidence"]["entries"][0]
        record = json.loads(entry["rawText"])
        record["registryVersion"] += 1
        raw = audit.encode(record)
        entry.update(rawText=raw.decode(), byteLength=len(raw), sha256=audit.sha(raw))

    folder = rebuild(fixture, tmp_path, mutation)
    assert audit.audit_dataset(folder)["trustStatus"] == "unverified"
    with pytest.raises(audit.AuditError, match="separately supplied"):
        audit.audit_dataset(folder, registry_pins=fixture[3]["registry"])


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":1}',
        b'{"x":1.00}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b' {"x":1}',
        b'{"x":1e-7}',
        b"[" * 65 + b"0" + b"]" * 65,
    ],
)
def test_strict_canonical_and_preparse_nesting(raw):
    with pytest.raises(audit.AuditError):
        audit.decode(raw, 1024, audit.Checks())


def test_codec_retains_integral_float_negative_zero_null():
    encoded = [audit.encode({"x": n}) for n in (1, 1.0, 0.0, -0.0, None)]
    assert len(set(encoded)) == 5
    for raw in encoded:
        assert audit.encode(audit.decode(raw, 100, audit.Checks())) == raw


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_eof",
        "short_body",
        "traversal",
        "symlink",
        "pax",
        "duplicate",
        "oversize",
        "checksum",
        "padding",
        "trailer",
        "reorder",
    ],
)
def test_tar_fails_closed_without_extraction(fixture, tmp_path, mutation):
    publication, parts, _, _ = fixture
    path = tmp_path / "archive.tar"
    export_dataset_archive(reader(publication, parts), path)
    raw = path.read_bytes()
    manifest_len = len(publication.manifest_bytes)
    first_end = 512 + manifest_len + (-manifest_len) % 512
    if mutation == "missing_eof":
        raw = raw[:-512]
    elif mutation == "short_body":
        raw = raw[: first_end + 600]
    elif mutation in {"traversal", "oversize"}:
        raw = (
            audit.tar_header(
                "../manifest.json" if mutation == "traversal" else "manifest.json",
                manifest_len if mutation == "traversal" else audit.MANIFEST + 1,
            )
            + raw[512:]
        )
    elif mutation in {"symlink", "pax"}:
        header = bytearray(raw[:512])
        header[156] = ord("2" if mutation == "symlink" else "x")
        header[148:156] = b" " * 8
        header[148:156] = f"{sum(header):06o}\0 ".encode()
        raw = bytes(header) + raw[512:]
    elif mutation == "duplicate":
        raw = raw[:first_end] + raw
    elif mutation == "checksum":
        raw = bytes([raw[0] ^ 1]) + raw[1:]
    elif mutation == "padding":
        raw = raw[: 512 + manifest_len] + b"x" + raw[513 + manifest_len :]
    elif mutation == "trailer":
        raw += b"x"
    else:
        raw = raw[:first_end] + raw[:first_end] + raw[first_end:]
    path.write_bytes(raw)
    with pytest.raises(audit.AuditError):
        audit.audit_dataset(path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["archive.tar"]


def test_manifest_budgets_checked_before_part_read(fixture, tmp_path):
    m = json.loads(fixture[0].manifest_bytes)
    m["components"][0]["parts"][0]["byteLength"] = audit.PART + 1
    c = m["components"][0]
    c["componentRoot"] = audit.root(audit.without(c, "componentRoot"))
    with pytest.raises(audit.AuditError) as error:
        audit.validate_manifest(audit.encode(m), audit.Checks())
    assert error.value.code == "BUDGET"


def test_missing_extra_symlink_parts_and_pinned_root(fixture, tmp_path):
    folder = rebuild(fixture, tmp_path, lambda *_: None)
    with pytest.raises(audit.AuditError, match="Pinned"):
        audit.audit_dataset(folder, expected_root="f" * 64)
    part = folder / "parts/registryEvidence/0.bin"
    original = part.read_bytes()
    part.unlink()
    with pytest.raises(audit.AuditError):
        audit.audit_dataset(folder)
    target = tmp_path / "external.bin"
    target.write_bytes(original)
    part.symlink_to(target)
    with pytest.raises(OSError):
        audit.audit_dataset(folder)


def test_cli_standalone_registry_paths_and_no_replace(fixture, tmp_path):
    publication, parts, _, source = fixture
    path = tmp_path / "dataset.tar"
    export_dataset_archive(reader(publication, parts), path)
    pins = {}
    for ref, raw in source["registry"].items():
        name = ref + ".json"
        (tmp_path / name).write_bytes(raw)
        pins[ref] = name
    pin_file = tmp_path / "pins.json"
    pin_file.write_text(json.dumps(pins))
    output = tmp_path / "audit.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/audit-dataset.py"),
        str(path),
        "--registry-pins",
        str(pin_file),
        "--output",
        str(output),
    ]
    completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["externalRegistryBytesMatched"] is True
    original = output.read_bytes()
    repeated = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert repeated.returncode == 2
    assert output.read_bytes() == original
    # Independence is structural: no engine/third-party imports in either file.
    import ast

    allowed = {
        "__future__",
        "bisect",
        "collections",
        "contextlib",
        "datetime",
        "decimal",
        "hashlib",
        "json",
        "math",
        "os",
        "pathlib",
        "re",
        "stat",
        "tarfile",
        "argparse",
        "sys",
        "dataset_audit",
    }
    for file in ("audit-dataset.py", "dataset_audit.py"):
        tree = ast.parse((ROOT / "scripts" / file).read_text())
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported |= {
            name.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for name in node.names
        }
        assert imported <= allowed


def test_rehashed_event_cannot_forge_dependency_raw_value_or_trust(fixture):
    _, _, values, source = fixture
    package = values["financialInput0"]
    manifest = json.loads(fixture[0].manifest_bytes)
    c = audit.Checks()
    records = audit.registry_records(
        manifest, values["registryEvidence"], c, source["registry"]
    )
    calendar, raw_rows = audit.check_package(
        package, manifest["financialSources"][0], records, manifest["scope"], c
    )
    for mutation in ("raw", "unit", "future"):
        prepared = deepcopy(values["financialPrepared0"])
        event = next(e for e in prepared["stateEvents"] if e["result"]["dependencies"])
        old_id = event["id"]
        dep = event["result"]["dependencies"][0]
        if mutation == "raw":
            dep["raw_decimal"] = "123456789"
        elif mutation == "unit":
            dep["unit_verified"] = True  # Fixture is explicitly user-declared.
        else:
            dep["available_date"] = "20990101"
        event["result"]["lineageHash"] = audit.root(
            audit.without(event["result"], "lineageHash")
        )
        event["id"] = audit.root(audit.without(event, "id"))
        for assignment in prepared["assignments"]:
            for state, eid in assignment["states"].items():
                if eid == old_id:
                    assignment["states"][state] = event["id"]
        prepared["provenance"]["preparedRoot"] = audit.root(
            {
                "inputRoot": package["inputRoot"],
                "unitPolicy": package["unitPolicy"],
                "selection": package["selection"],
                **audit.without(prepared, "provenance"),
            }
        )
        with pytest.raises(audit.AuditError):
            audit.check_prepared(package, prepared, raw_rows, calendar, audit.Checks())


def test_pin_parser_rejects_duplicates_and_urls(tmp_path):
    path = tmp_path / "pins.json"
    ref = "12345678-1234-1234-1234-123456789abc"
    path.write_text('{"' + ref + '":"x","' + ref + '":"y"}')
    with pytest.raises(audit.AuditError, match="Duplicate"):
        audit.load_registry_pins(path)
    path.write_text(json.dumps({ref: "https://example.invalid/file"}))
    with pytest.raises(audit.AuditError, match="local file"):
        audit.load_registry_pins(path)


def test_manifest_type_graph_and_total_budget(fixture):
    original = json.loads(fixture[0].manifest_bytes)
    for mutation in ("bool", "type", "cycle", "omission", "total"):
        m = deepcopy(original)
        if mutation == "bool":
            m["version"] = True
        elif mutation == "type":
            m["components"][0]["encoding"] = "pickle"
        elif mutation == "cycle":
            m["components"][0]["dependencies"] = [m["components"][-1]["componentRoot"]]
        elif mutation == "omission":
            m["components"].pop()
        else:
            c = m["components"][0]
            c["parts"] = [
                {"ordinal": i, "byteLength": audit.PART, "sha256": "0" * 64}
                for i in range(129)
            ]
            c["byteLength"] = 129 * audit.PART
        if mutation in {"type", "cycle", "total"}:
            c = m["components"][0]
            c["componentRoot"] = audit.root(audit.without(c, "componentRoot"))
        with pytest.raises(audit.AuditError):
            audit.validate_manifest(audit.encode(m), audit.Checks())
