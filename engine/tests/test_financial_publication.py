"""Offline publication using explicitly synthetic declared-unit inputs only.

Scoped-proof cases use named synthetic operator registries, not real documents.
No provider calls, filesystem writes, model fits or HTTP mocks are needed here.
"""

from copy import deepcopy
from dataclasses import asdict
import json
from uuid import UUID

import pytest

from atlas_quant.runner import RunnerError
from atlas_quant.financial_statements import RECIPES, UnitEvidence
from atlas_quant.financial_statements.package import _encoded, prepare_package
from atlas_quant.financial_statements.results import canonical_hash
from atlas_quant.financial_statements.unit_bindings import (
    DocumentUnitBinding,
    GlobalUnitBinding,
    normalized_row_hash,
)
from atlas_quant.financial_runner import publication as module
from atlas_quant.financial_runner.publication import (
    compute_publication,
    PublicationWriter,
)
from atlas_quant.financial_runner.protocol import encode, sha
from atlas_quant.financial_runner.trust import calendar_scope, proof_scope
from test_financial_adapter import run
from test_financial_package import freeze, declarations
from test_financial_statements import calendar


@pytest.fixture(scope="module")
def source():
    acquired = run()
    return acquired, freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )


def task_inputs(package, kind="financial_prepare", proof_entries=None):
    """Reusable exact wire fixture for the financial consumer's spawned tests."""
    job = {"id": str(UUID(int=101)), "inputId": str(UUID(int=102)), "kind": kind}
    source = _encoded(package)
    calendar_record = {
        "kind": "calendar",
        "registryVersion": 1,
        "evidenceLevel": "EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE",
        "payload": package["raw"]["calendar"],
        "scope": calendar_scope(package["raw"]["calendar"]),
    }
    registry = {}

    def descriptor(value, number):
        ref = str(UUID(int=number))
        raw = encode(value)
        registry[ref] = raw
        return {"ref": ref, "sha256": sha(raw), "byteLength": len(raw)}

    cal = descriptor(calendar_record, 110)
    if proof_entries is None:
        proof_entries = [
            entry
            for entries in package["bindings"].values()
            for entry in entries
            if entry["type"] != "DeclaredUnitBinding"
        ]
    proofs = [
        descriptor(
            {
                "kind": "unit_proof",
                "registryVersion": 1,
                "evidenceLevel": entry["value"]["evidence"]["kind"],
                "payload": entry,
                "scope": proof_scope(entry),
            },
            120 + n,
        )
        for n, entry in enumerate(proof_entries)
    ]
    operation = (
        {"expectedUploadSha256": sha(source)}
        if kind == "financial_validate"
        else {"expectedPackRoot": package["packRoot"]}
    )
    meta = {
        "job": dict(job),
        "source": {"sha256": sha(source), "byteLength": len(source)},
        "calendar": cal,
        "proofs": proofs,
        "operation": operation,
    }
    return job, meta, source, registry


def publish(inputs):
    chunks = {}

    def write(collection, ordinal, raw):
        assert (collection, ordinal) not in chunks and 0 < len(raw) <= 512 * 1024
        chunks[(collection, ordinal)] = raw

    result = compute_publication(*inputs, write)
    for name, collection in result["collections"].items():
        for part in collection["chunks"]:
            raw = chunks[(name, part["ordinal"])]
            assert len(raw) == part["byteLength"] and sha(raw) == part["sha256"]
        assert (
            sum(part["byteLength"] for part in collection["chunks"])
            == collection["byteLength"]
        )
    return result, chunks


def records(chunks, name):
    return [
        row
        for (collection, _), raw in chunks.items()
        if collection == name
        for row in json.loads(raw)
    ]


def test_validate_authenticates_but_does_not_prepare(source, monkeypatch):
    monkeypatch.setattr(
        module,
        "prepare_package",
        lambda *a, **k: pytest.fail("validation must not prepare"),
    )
    manifest, chunks = publish(task_inputs(source[1], "financial_validate"))
    assert manifest["kind"] == "validated" and set(manifest["collections"]) == {
        "package"
    }
    assert manifest["roots"]["preparedRoot"] is None
    assert manifest["summary"]["input"]["source"]["kind"] == "fixture"
    assert manifest["summary"]["input"]["evidence"]["unitAssumptions"] is True
    raw = b"".join(raw for (name, _), raw in chunks.items() if name == "package")
    assert (
        raw == _encoded(source[1])
        and sha(raw) == manifest["collections"]["package"]["sha256"]
    )


def test_prepared_output_preserves_exact_lineage_and_real_missing_coverage(source):
    manifest, chunks = publish(task_inputs(source[1]))
    prepared = prepare_package(source[1])
    assert manifest["roots"]["preparedRoot"] == prepared.provenance["preparedRoot"]
    assert manifest["summary"]["hasUsableStates"] is True
    assert manifest["summary"]["preparation"]["qualityFlags"] == [
        "USER_DECLARED_UNIT_ASSUMPTION"
    ]
    dependencies = records(chunks, "dependencies")
    reconstructed = []
    for projected in records(chunks, "events"):
        offset, count = projected.pop("dependencyStart"), projected.pop(
            "dependencyCount"
        )
        group = dependencies[offset : offset + count] if count else []
        assert [row["index"] for row in group] == list(range(count))
        assert all(row["eventId"] == projected["id"] for row in group)
        projected["result"]["dependencies"] = [row["dependency"] for row in group]
        result = projected["result"]
        assert (
            canonical_hash({k: v for k, v in result.items() if k != "lineageHash"})
            == result["lineageHash"]
        )
        assert (
            canonical_hash({k: v for k, v in projected.items() if k != "id"})
            == projected["id"]
        )
        assert result["unitVerified"] is False
        reconstructed.append(projected)
    # JSON represents the core's immutable tuples as arrays; canonical bytes and
    # the original lineage/event hashes must remain exactly identical.
    assert encode(reconstructed) == encode(list(prepared.state_events))
    coverage = records(chunks, "coverage")
    assert len(coverage) == 16
    cash = next(
        row for row in coverage if row["stateId"] == "model_fin_cash_asset_share"
    )
    assert cash["firstAvailable"] == "20240429" and cash["latestAgeCalendarDays"] == 124
    assert cash["missingRows"] == 1
    assert records(chunks, "panel")[0]["model_fin_cash_asset_share"] is None
    assert records(chunks, "assignments") == list(prepared.assignments)


def test_all_missing_is_a_complete_diagnostic_result(source):
    package = freeze(source[0], declarations(source[0]), trusted_unit_proofs=False)
    manifest, chunks = publish(task_inputs(package))
    assert manifest["summary"]["hasUsableStates"] is False
    assert all(
        row["okRows"] == 0 and row["status"] == "missing"
        for row in records(chunks, "coverage")
    )
    assert manifest["collections"]["panel"]["rowCount"] > 0


@pytest.mark.parametrize(
    "mutation",
    [
        "calendar_payload",
        "calendar_scope",
        "source_sha",
        "extra_registry",
        "duplicate_json",
    ],
)
def test_trust_failures_write_no_chunks(source, mutation):
    job, meta, raw, registry = task_inputs(source[1])
    ref = meta["calendar"]["ref"]
    if mutation.startswith("calendar"):
        record = json.loads(registry[ref])
        if mutation == "calendar_payload":
            record["payload"]["evidence_reference"] = "self-certified replacement"
            record["scope"] = calendar_scope(record["payload"])
        else:
            record["scope"]["calendarRoot"] = "0" * 64
        registry[ref] = encode(record)
        meta["calendar"].update(
            sha256=sha(registry[ref]), byteLength=len(registry[ref])
        )
    elif mutation == "source_sha":
        meta["source"]["sha256"] = "0" * 64
    elif mutation == "extra_registry":
        registry[str(UUID(int=999))] = b"{}"
    else:
        raw = b'{"bindings":{},' + raw[1:]
        meta["source"].update(sha256=sha(raw), byteLength=len(raw))
    with pytest.raises(RunnerError):
        compute_publication(
            job,
            meta,
            raw,
            registry,
            lambda *args: pytest.fail("untrusted source wrote a chunk"),
        )


def document_package(acquired):
    units = declarations(acquired)
    field = "balancesheet.total_assets"
    matches = []
    for snapshot in acquired.snapshots:
        if snapshot["endpoint"] != "balancesheet":
            continue
        for row in snapshot["rows"]:
            evidence = UnitEvidence(
                "CNY",
                "CNY",
                True,
                "source_document",
                "SYNTHETIC_OPERATOR_SCOPE_TEST_NO_REAL_PDF",
            )
            matches.append(
                DocumentUnitBinding(
                    evidence,
                    field,
                    "HAND_FAKE_PROVIDER",
                    row["ts_code"],
                    row["end_date"],
                    row["ann_date"],
                    row["f_ann_date"],
                    row["report_type"],
                    row["comp_type"],
                    snapshot["id"],
                    normalized_row_hash("balancesheet", row),
                    "d" * 64,
                )
            )
    units[field] = matches
    return freeze(
        acquired, units, unit_policy="allow_declared", trusted_unit_proofs=True
    )


def test_document_proof_requires_exact_authorized_payload_and_actual_row(source):
    package = document_package(source[0])
    manifest, _ = publish(task_inputs(package, "financial_validate"))
    assert manifest["roots"]["packRoot"] == package["packRoot"]
    for mutate in ("unregistered", "wrong_row"):
        candidate = deepcopy(package)
        if mutate == "wrong_row":
            candidate["bindings"]["balancesheet.total_assets"][0]["value"][
                "source_snapshot"
            ] = ("0" * 64)
            candidate["packRoot"] = canonical_hash(
                {k: v for k, v in candidate.items() if k != "packRoot"}
            )
        inputs = task_inputs(
            candidate, "financial_validate", [] if mutate == "unregistered" else None
        )
        with pytest.raises(RunnerError) as error:
            compute_publication(
                *inputs, lambda *args: pytest.fail("unmatched proof published")
            )
        assert error.value.code in {
            "UNIT_PROOF_REGISTRY_MISMATCH",
            "UNIT_PROOF_SCOPE_MISMATCH",
        }


def test_fixture_binding_never_crosses_public_trust_boundary(source):
    package = freeze(source[0])
    inputs = task_inputs(package, proof_entries=[])
    with pytest.raises(RunnerError) as error:
        compute_publication(
            *inputs, lambda *args: pytest.fail("fixture proof published")
        )
    assert error.value.code == "FINANCIAL_PUBLIC_FIXTURE"


def test_global_proof_cannot_self_authorize_without_registry(source):
    units = declarations(source[0])
    units["balancesheet.total_assets"] = GlobalUnitBinding(
        UnitEvidence(
            "CNY", "CNY", True, "source_contract", "SYNTHETIC_GLOBAL_SCOPE_TEST"
        ),
        "balancesheet.total_assets",
        "HAND_FAKE_PROVIDER",
        "a" * 64,
        "global",
    )
    package = freeze(
        source[0], units, unit_policy="allow_declared", trusted_unit_proofs=True
    )
    with pytest.raises(RunnerError) as error:
        compute_publication(
            *task_inputs(package, proof_entries=[]),
            lambda *args: pytest.fail("unregistered global proof wrote data")
        )
    assert error.value.code == "UNIT_PROOF_REGISTRY_MISMATCH"
    manifest, _ = publish(task_inputs(package, "financial_validate"))
    assert manifest["roots"]["packRoot"] == package["packRoot"]


def test_assumption_cannot_self_upgrade_verified_or_kind(source):
    for mutation in ("verified", "kind"):
        package = deepcopy(source[1])
        entry = next(iter(package["bindings"].values()))[0]
        if mutation == "verified":
            entry["value"]["evidence"]["verified"] = True
        else:
            entry["value"]["evidence"]["kind"] = "source_contract"
        package["packRoot"] = canonical_hash(
            {k: v for k, v in package.items() if k != "packRoot"}
        )
        with pytest.raises(RunnerError):
            compute_publication(
                *task_inputs(package),
                lambda *args: pytest.fail("upgraded assumption published")
            )


@pytest.mark.parametrize(
    "mutation",
    ["job", "operation", "null_calendar", "version_bool", "unknown_trust_flag"],
)
def test_malformed_and_unexpected_authority_fields_fail_closed(source, mutation):
    package = deepcopy(source[1])
    if mutation == "unknown_trust_flag":
        package["trusted_unit_proofs"] = True
        package["packRoot"] = canonical_hash(
            {k: v for k, v in package.items() if k != "packRoot"}
        )
    job, meta, raw, registry = task_inputs(package)
    if mutation == "job":
        meta["job"]["inputId"] = str(UUID(int=999))
    elif mutation == "operation":
        meta["operation"]["trusted"] = True
    elif mutation in {"null_calendar", "version_bool"}:
        ref = meta["calendar"]["ref"]
        record = json.loads(registry[ref])
        if mutation == "null_calendar":
            record["payload"] = None
        else:
            record["registryVersion"] = True
        registry[ref] = encode(record)
        meta["calendar"].update(
            sha256=sha(registry[ref]), byteLength=len(registry[ref])
        )
    with pytest.raises(RunnerError):
        compute_publication(
            job,
            meta,
            raw,
            registry,
            lambda *args: pytest.fail("invalid authority published"),
        )


def test_successful_revision_creates_new_unverified_scoped_author_declaration(source):
    job, meta, raw, registry = task_inputs(source[1], "financial_revise")
    meta["operation"].update(
        parentId=str(UUID(int=500)),
        selection=module._input_summary(source[1])["selection"],
        unitPolicy="allow_declared",
        declaredBy="authenticated-test-owner",
        declaredAt="2026-10-08T00:00:00Z",
        declarations=[
            {
                "fieldId": "balancesheet.total_assets",
                "inputRoot": source[1]["inputRoot"],
                "nativeUnit": "CNY",
                "currency": "CNY",
                "positiveOutflow": None,
                "statement": "Explicit synthetic assumption for this frozen input",
            }
        ],
    )
    manifest, chunks = publish((job, meta, raw, registry))
    revised = json.loads(
        b"".join(raw for (name, _), raw in chunks.items() if name == "package")
    )
    assert set(revised["bindings"]) == {"balancesheet.total_assets"}
    binding = revised["bindings"]["balancesheet.total_assets"][0]["value"]
    assert binding["declared_by"] == "authenticated-test-owner"
    assert binding["evidence"]["verified"] is False
    assert binding["input_root"] == source[1]["inputRoot"]
    assert manifest["summary"]["input"]["evidence"]["unitAssumptions"] is True


def test_revision_replaces_declarations_and_keeps_raw_identity(source):
    job, meta, raw, registry = task_inputs(source[1], "financial_revise")
    summary = module._input_summary(source[1])
    meta["operation"].update(
        parentId=str(UUID(int=500)),
        selection=summary["selection"],
        unitPolicy="verified_only",
        declarations=[],
        declaredBy="authenticated-test-owner",
        declaredAt="2026-10-08T00:00:00+00:00",
    )
    manifest, chunks = publish((job, meta, raw, registry))
    revised = json.loads(
        b"".join(raw for (name, _), raw in chunks.items() if name == "package")
    )
    assert manifest["kind"] == "revised" and revised["bindings"] == {}
    assert revised["inputRoot"] == source[1]["inputRoot"]
    assert revised["packRoot"] != source[1]["packRoot"]
    assert manifest["summary"]["validation"]["missingPrerequisites"]


def test_revision_cannot_rebind_declaration_to_different_input(source):
    job, meta, raw, registry = task_inputs(source[1], "financial_revise")
    meta["operation"].update(
        parentId=str(UUID(int=500)),
        selection=module._input_summary(source[1])["selection"],
        unitPolicy="allow_declared",
        declaredBy="authenticated-test-owner",
        declaredAt="2026-10-08T00:00:00Z",
        declarations=[
            {
                "fieldId": "balancesheet.total_assets",
                "inputRoot": "0" * 64,
                "nativeUnit": "CNY",
                "currency": "CNY",
                "positiveOutflow": None,
                "statement": "Explicit synthetic assumption",
            }
        ],
    )
    with pytest.raises(RunnerError) as error:
        compute_publication(
            job,
            meta,
            raw,
            registry,
            lambda *args: pytest.fail("bad revision published"),
        )
    assert error.value.code == "UNIT_DECLARATION_SCOPE"


def test_chunk_and_cumulative_budgets_are_checked_before_callbacks(monkeypatch):
    monkeypatch.setattr(module, "CHUNK_BYTES", 100)
    monkeypatch.setattr(module, "RESULT_BYTES", 120)
    written = []
    writer = PublicationWriter(lambda *args: written.append(args))
    writer.records("coverage", [{"x": "a" * 60}])
    with pytest.raises(RunnerError) as error:
        writer.records("events", [{"x": "b" * 60}])
    assert error.value.code == "PREPARED_BYTE_BUDGET" and len(written) == 1
    with pytest.raises(RunnerError) as error:
        writer.records("events", [{"x": "b" * 100}])
    assert error.value.code == "FINANCIAL_RECORD_BYTE_BUDGET" and len(written) == 1


def test_empty_collection_and_500_record_chunk_bound():
    written = []
    writer = PublicationWriter(lambda *args: written.append(args))
    writer.records("coverage", [])
    assert writer.collections["coverage"] == {
        "encoding": "json_records",
        "rowCount": 0,
        "byteLength": 0,
        "chunks": [],
    }
    writer.records("panel", ({"value": n} for n in range(501)))
    assert [len(json.loads(raw)) for _, _, raw in written] == [500, 1]
    assert [part["startRow"] for part in writer.collections["panel"]["chunks"]] == [
        0,
        500,
    ]


def test_chunk_limit_known_package_budget_and_callback_failures_do_not_return_manifest(
    source, monkeypatch
):
    written = []
    monkeypatch.setattr(module, "CHUNK_COUNT", 0)
    with pytest.raises(RunnerError):
        compute_publication(*task_inputs(source[1]), lambda *args: written.append(args))
    assert written == []
    monkeypatch.setattr(module, "CHUNK_COUNT", 512)

    def interrupted(*args):
        raise OSError("explicit test staging failure")

    with pytest.raises(OSError):
        compute_publication(*task_inputs(source[1]), interrupted)
