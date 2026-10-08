"""Offline vertical acceptance; synthetic values/calendar, zero network calls."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from atlas_quant.financial_statements import (
    ContractError,
    FIELDS,
    RECIPES,
    UnitEvidence,
)
from atlas_quant.financial_statements.package import (
    freeze_package,
    prepare_package,
    validate_package,
    decode_package,
    raw_input,
)
from atlas_quant.financial_statements.prepare import (
    AdapterBudget,
    AdapterError,
    prepare_statement_states,
)
from atlas_quant.financial_statements.results import canonical_hash
from atlas_quant.financial_statements.unit_bindings import DeclaredUnitBinding
from test_financial_adapter import run, STRATEGY, UNITS
from test_financial_statements import calendar, EXPECTED


@pytest.fixture(scope="module")
def acquired():
    return run()


def test_common_budget_preflight_precedes_binding_compilation(acquired, monkeypatch):
    import atlas_quant.financial_statements.prepare as module

    def forbidden(*args, **kwargs):
        pytest.fail("proof compilation must not begin for an oversized raw input")

    monkeypatch.setattr(module, "CompiledUnitBindings", forbidden)
    with pytest.raises(AdapterError) as error:
        prepare_statement_states(
            acquired.snapshots,
            STRATEGY,
            tuple(RECIPES),
            calendar(),
            UNITS,
            announcement_start="20210101",
            source_kind="fixture",
            source_provider="HAND_FAKE_PROVIDER",
            budget=AdapterBudget(max_prepared_bytes=1),
        )
    assert error.value.code == "PREPARED_BYTE_BUDGET"


def test_compiled_scopes_hash_once_and_do_not_scan_candidates_per_row(
    acquired, monkeypatch
):
    import atlas_quant.financial_statements.unit_bindings as module

    field = "balancesheet.total_assets"
    declaration = declarations(acquired)[field]
    candidates = [
        replace(declaration, statement=f"Equivalent assumption {n}") for n in range(33)
    ]
    calls = []
    original_hash = module.canonical_hash

    def counted(value):
        calls.append(1)
        return original_hash(value)

    monkeypatch.setattr(module, "canonical_hash", counted)
    compiled = module.CompiledUnitBindings({field: candidates}, max_bytes=1024 * 1024)
    assert len(calls) == 33
    snapshot = next(
        s for s in acquired.snapshots if s["endpoint"] == "balancesheet" and s["rows"]
    )
    row = snapshot["rows"][0]
    row_hash = module.normalized_row_hash("balancesheet", row)
    expected = module.resolve_unit(
        candidates,
        field,
        "HAND_FAKE_PROVIDER",
        snapshot["id"],
        "balancesheet",
        row,
        input_root=declaration.input_root,
    )
    before = len(calls)
    # The index owns its compiled evidence. Querying never revisits this list.
    candidates.clear()
    for _ in range(33):
        selected = compiled.select(
            field,
            "HAND_FAKE_PROVIDER",
            snapshot["id"],
            "balancesheet",
            row,
            input_root=declaration.input_root,
            row_hash=row_hash,
        )
        assert compiled.resolve(selected, field, "HAND_FAKE_PROVIDER") == expected
    assert len(calls) == before


def test_audit_budget_preflight_precedes_dependency_expansion(acquired, monkeypatch):
    from atlas_quant.financial_statements.results import ValueResult

    def forbidden(self):
        pytest.fail(
            "audit limit must be checked before expanding ValueResult dependencies"
        )

    monkeypatch.setattr(ValueResult, "to_dict", forbidden)
    with pytest.raises(AdapterError) as error:
        prepare_package(
            freeze(acquired),
            trusted_unit_proofs=True,
            budget=AdapterBudget(max_audit_bytes=1),
        )
    assert error.value.code == "AUDIT_BYTE_BUDGET"


def test_duplicate_rows_are_charged_before_record_construction(acquired, monkeypatch):
    import atlas_quant.financial_statements.prepare as module
    from types import SimpleNamespace

    snapshots = list(deepcopy(acquired.snapshots))
    index = next(
        i
        for i, s in enumerate(snapshots)
        if s["endpoint"] == "balancesheet" and s["rows"]
    )
    body = {
        k: v
        for k, v in snapshots[index].items()
        if k not in {"id", "rowCount", "byteLength"}
    }
    body["rows"] = [deepcopy(body["rows"][0]) for _ in range(32)]
    snapshots[index] = {
        **body,
        "id": canonical_hash(body),
        "rowCount": 32,
        "byteLength": module._json_bytes(body),
    }
    frozen = SimpleNamespace(snapshots=snapshots)
    units = declarations(frozen)
    field = "balancesheet.total_assets"
    units[field] = replace(
        units[field],
        evidence=replace(units[field].evidence, reference="R" * (64 * 1024)),
    )
    units = {key: units[key] for key in (field, "balancesheet.money_cap")}
    package = freeze_package(
        snapshots,
        calendar(),
        STRATEGY,
        ["model_fin_cash_asset_share"],
        units,
        announcement_start="20210101",
        source_kind="fixture",
        source_provider="HAND_FAKE_PROVIDER",
        unit_policy="allow_declared",
    )
    original_record, created = module.StatementRecord, []

    def counted(*args, **kwargs):
        created.append(1)
        return original_record(*args, **kwargs)

    monkeypatch.setattr(module, "StatementRecord", counted)
    with pytest.raises(AdapterError) as error:
        prepare_package(package, budget=AdapterBudget(max_prepared_bytes=512 * 1024))
    assert error.value.code == "PREPARED_BYTE_BUDGET"
    assert 0 < len(created) < 32


def freeze(acquired, units=UNITS, **changes):
    return freeze_package(
        acquired.snapshots,
        calendar(),
        STRATEGY,
        list(RECIPES),
        units,
        announcement_start="20210101",
        source_kind="fixture",
        source_provider="HAND_FAKE_PROVIDER",
        trusted_unit_proofs=changes.pop("trusted_unit_proofs", True),
        **changes,
    )


def declarations(acquired, **changes):
    _, root = raw_input(
        acquired.snapshots,
        calendar(),
        source_kind="fixture",
        source_provider="HAND_FAKE_PROVIDER",
    )
    return {
        field: DeclaredUnitBinding(
            UnitEvidence(
                "CNY",
                "CNY",
                False,
                "user_declared_assumption",
                "SYNTHETIC_TEST_DECLARATION",
                True,
            ),
            field,
            "HAND_FAKE_PROVIDER",
            root,
            "test-researcher",
            "2026-10-08T00:00:00Z",
            "Test-only assumption that this exact input is CNY with positive capex payments.",
            **changes,
        )
        for field in FIELDS
    }


def rehash(package):
    package["packRoot"] = canonical_hash(
        {k: v for k, v in package.items() if k != "packRoot"}
    )
    return package


def test_all_16_states_prepare_from_frozen_inputs_equal_live_fake_adapter(acquired):
    package = freeze(acquired)
    result = prepare_package(package, trusted_unit_proofs=True)
    assert result.panel.equals(acquired.panel)
    assert result.state_events == acquired.state_events
    assert result.assignments == acquired.assignments
    for suffix, expected in EXPECTED.items():
        assert result.panel.iloc[-1]["model_fin_" + suffix] == pytest.approx(
            float(Decimal(expected))
        )
    assert result.provenance["packRoot"] == package["packRoot"]
    assert result.provenance["inputRoot"] == package["inputRoot"]
    assert result.provenance["requests"] == ()


def test_declarations_compute_all_16_but_every_derived_dependency_stays_unverified(
    acquired,
):
    package = freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )
    result = prepare_package(package)
    for suffix, expected in EXPECTED.items():
        assert result.panel.iloc[-1]["model_fin_" + suffix] == pytest.approx(
            float(Decimal(expected))
        )
    assert result.provenance["qualityFlags"] == ["USER_DECLARED_UNIT_ASSUMPTION"]
    assert len(result.provenance["declarationHashes"]) == 15
    for event in result.state_events:
        value = event["result"]
        if not value["dependencies"]:
            continue
        assert value["unitVerified"] is False
        assert value["qualityFlags"] == ["USER_DECLARED_UNIT_ASSUMPTION"]
        assert value["unitEvidenceLevels"] == ["user_declared_assumption"]
        assert value["declarationHashes"]
        for dep in value["dependencies"]:
            assert dep["unit_verified"] is False
            assert dep["unit_scope"]["input_root"] == package["inputRoot"]
            assert dep["unit_scope"]["status"] == "matched"
            assert dep["declaration_hashes"]


def test_default_policy_rejects_declarations_without_implicit_opt_in(acquired):
    package = freeze(acquired, declarations(acquired), trusted_unit_proofs=False)
    assert package["unitPolicy"] == "verified_only"
    result = prepare_package(package)
    for state in RECIPES:
        assert result.panel[state].isna().all()
        assert result.coverage[state]["reasons"]["UNIT_UNVERIFIED"] > 0


def test_raw_root_excludes_declarations_policy_and_selection_while_pack_root_includes_them(
    acquired,
):
    units = declarations(acquired)
    a = freeze(acquired, units, unit_policy="verified_only", trusted_unit_proofs=False)
    b = freeze(acquired, units, unit_policy="allow_declared", trusted_unit_proofs=False)
    changed = {
        key: replace(value, statement="Different explicit test declaration.")
        for key, value in units.items()
    }
    c = freeze(
        acquired, changed, unit_policy="allow_declared", trusted_unit_proofs=False
    )
    assert a["inputRoot"] == b["inputRoot"] == c["inputRoot"]
    assert len({a["packRoot"], b["packRoot"], c["packRoot"]}) == 3
    before, after = prepare_package(b), prepare_package(c)
    assert before.panel.equals(after.panel)
    assert (
        before.state_events[-1]["result"]["lineageHash"]
        != after.state_events[-1]["result"]["lineageHash"]
    )


def test_package_roundtrip_is_hash_stable_and_copies_caller_owned_containers(acquired):
    package = freeze(acquired)
    serialized = json.dumps(package, ensure_ascii=False).encode()
    assert decode_package(serialized, trusted_unit_proofs=True) == package
    original = acquired.snapshots[0]["rows"][:]
    package["raw"]["snapshots"][0]["rows"].clear()
    assert acquired.snapshots[0]["rows"] == original
    with pytest.raises(ContractError, match="root mismatch"):
        validate_package(package, trusted_unit_proofs=True)


def test_uploaded_verified_claims_are_not_accepted_as_platform_review(acquired):
    with pytest.raises(ContractError, match="trusted reviewer"):
        freeze(acquired, trusted_unit_proofs=False)
    package = freeze(acquired)
    with pytest.raises(ContractError, match="trusted reviewer"):
        prepare_package(package)
    with pytest.raises(ContractError, match="never claim verified"):
        UnitEvidence("CNY", "CNY", True, "user_declared_assumption", "self certified")


@pytest.mark.parametrize(
    "part", ["inputRoot", "packRoot", "snapshot", "calendar", "binding"]
)
def test_changed_content_never_reuses_existing_roots(acquired, part):
    package = freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )
    if part in {"inputRoot", "packRoot"}:
        package[part] = "f" * 64
    elif part == "snapshot":
        package["raw"]["snapshots"][0]["rows"].append({})
    elif part == "calendar":
        package["raw"]["calendar"]["sessions"].pop()
    else:
        package["bindings"]["income.revenue"][0]["value"]["evidence"][
            "native_unit"
        ] = "CNY_1000"
    with pytest.raises(ContractError):
        prepare_package(package)


def test_resigning_outer_hash_cannot_move_declaration_to_changed_raw_input(acquired):
    package = freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )
    package["raw"]["calendar"]["evidence_reference"] += "-different-source"
    package["inputRoot"] = canonical_hash(package["raw"])
    rehash(package)
    with pytest.raises(ContractError, match="declaration scope"):
        prepare_package(package)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"native_unit": "unknown"}, "UNSUPPORTED_UNIT"),
        ({"currency": "USD"}, "UNSUPPORTED_CURRENCY"),
        ({"positive_outflow": None}, "OUTFLOW_SIGN_UNVERIFIED"),
    ],
)
def test_assumption_policy_does_not_override_unknown_semantics(
    acquired, change, reason
):
    units = declarations(acquired)
    field = "cashflow.c_pay_acq_const_fiolta"
    units[field] = replace(
        units[field], evidence=replace(units[field].evidence, **change)
    )
    result = prepare_package(
        freeze(acquired, units, unit_policy="allow_declared", trusted_unit_proofs=False)
    )
    state = "model_fin_capex_revenue_ratio"
    assert result.panel[state].isna().all()
    assert result.coverage[state]["reasons"][reason] > 0


def test_conflicting_declarations_are_missing_not_first_or_last_wins(acquired):
    units = declarations(acquired)
    original = units["balancesheet.money_cap"]
    units["balancesheet.money_cap"] = [
        original,
        replace(original, evidence=replace(original.evidence, native_unit="CNY_1000")),
    ]
    result = prepare_package(
        freeze(acquired, units, unit_policy="allow_declared", trusted_unit_proofs=False)
    )
    assert result.panel.model_fin_cash_asset_share.isna().all()
    assert (
        result.coverage["model_fin_cash_asset_share"]["reasons"]["UNIT_SCOPE_CONFLICT"]
        > 0
    )


def test_no_calendar_does_not_create_weekday_or_assumed_official_sessions(acquired):
    with pytest.raises(AdapterError) as error:
        prepare_statement_states(
            acquired.snapshots,
            STRATEGY,
            list(RECIPES),
            None,
            UNITS,
            announcement_start="20210101",
            source_kind="fixture",
            source_provider="HAND_FAKE_PROVIDER",
        )
    assert error.value.code == "CALENDAR_EVIDENCE_REQUIRED"


def test_incomplete_calendar_is_blocked_even_under_declaration_policy(acquired):
    with pytest.raises(AdapterError) as error:
        freeze_package(
            acquired.snapshots,
            replace(calendar(), complete=False),
            STRATEGY,
            list(RECIPES),
            {},
            announcement_start="20210101",
            source_kind="fixture",
            unit_policy="allow_declared",
        )
    assert error.value.code == "CALENDAR_COVERAGE"


def test_history_gaps_and_future_disclosures_are_not_filled_by_allow_declared(acquired):
    package = freeze(
        acquired,
        declarations(acquired),
        unit_policy="allow_declared",
        trusted_unit_proofs=False,
    )
    result = prepare_package(package)
    before_disclosure = result.panel[result.panel.trade_date == "20240426"].iloc[0]
    assert (
        before_disclosure.model_fin_cash_asset_share
        != before_disclosure.model_fin_cash_asset_share
    )
    assert result.coverage["model_fin_cash_asset_share"]["missingRows"] > 0
    assert result.panel.iloc[-1].model_fin_cash_asset_share == pytest.approx(0.1)


def test_source_and_package_byte_limits_fail_without_silent_truncation(
    acquired, monkeypatch
):
    with pytest.raises(ContractError, match="budget"):
        prepare_statement_states(
            acquired.snapshots,
            STRATEGY,
            list(RECIPES),
            calendar(),
            UNITS,
            announcement_start="20210101",
            source_kind="fixture",
            source_provider="HAND_FAKE_PROVIDER",
            budget=AdapterBudget(max_source_rows=1),
        )
    import atlas_quant.financial_statements.package as module

    monkeypatch.setattr(module, "MAX_PACKAGE_BYTES", 100)
    with pytest.raises(ContractError, match="24 MiB"):
        freeze(acquired)


@pytest.mark.parametrize(
    "budget,code",
    [
        (AdapterBudget(max_panel_bytes=1), "PANEL_BYTE_BUDGET"),
        (AdapterBudget(max_prepared_bytes=1), "PREPARED_BYTE_BUDGET"),
    ],
)
def test_panel_and_common_preparation_budget_never_return_partial_success(
    acquired, budget, code
):
    with pytest.raises(AdapterError) as error:
        prepare_package(freeze(acquired), trusted_unit_proofs=True, budget=budget)
    assert error.value.code == code
    assert error.value.partial["panelPublished"] is False


def test_prepared_identity_changes_with_evidence_but_not_fetch_journal(acquired):
    package = freeze(acquired)
    offline = prepare_package(package, trusted_unit_proofs=True)
    assert offline.provenance["preparedRoot"] == acquired.provenance["preparedRoot"]
    assert (
        offline.provenance["externalFields"]["model_fin_cash_asset_share"]["formulaId"]
        == "model_fin_cash_asset_share"
    )
    declared = prepare_package(
        freeze(
            acquired,
            declarations(acquired),
            unit_policy="allow_declared",
            trusted_unit_proofs=False,
        )
    )
    assert declared.panel.equals(offline.panel)
    assert declared.provenance["preparedRoot"] != offline.provenance["preparedRoot"]
    assert declared.provenance["externalFields"]["model_fin_cash_asset_share"][
        "qualityFlags"
    ] == ["USER_DECLARED_UNIT_ASSUMPTION"]


def test_sensitive_unknown_and_duplicate_json_keys_are_rejected(acquired):
    package = freeze(acquired)
    package["authorization"] = "not-a-real-secret"
    with pytest.raises(ContractError):
        validate_package(package, trusted_unit_proofs=True)
    with pytest.raises(ContractError):
        decode_package(b'{"format":"one","format":"two"}')


def test_wrong_period_selector_is_rejected_offline_instead_of_ignored(acquired):
    snapshots = deepcopy(list(acquired.snapshots))
    snapshot = next(s for s in snapshots if s["rows"])
    snapshot["params"] = {
        "ts_code": STRATEGY["universe"]["symbols"][0],
        "period": "19991231",
    }
    body = {
        k: v for k, v in snapshot.items() if k not in {"id", "rowCount", "byteLength"}
    }
    snapshot["id"] = canonical_hash(body)
    snapshot["byteLength"] = len(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    )
    with pytest.raises(AdapterError) as error:
        prepare_statement_states(
            snapshots,
            STRATEGY,
            list(RECIPES),
            calendar(),
            UNITS,
            announcement_start="20210101",
            source_kind="fixture",
            source_provider="HAND_FAKE_PROVIDER",
        )
    assert error.value.code == "PROVIDER_SELECTOR"
