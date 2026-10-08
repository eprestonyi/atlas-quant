"""Scope and lineage counterexamples using fake tables only; no source is certified."""

from dataclasses import replace

import pandas as pd
import pytest

from atlas_quant.financial_statements import ContractError, UnitEvidence
from atlas_quant.financial_statements.adapter import load_statement_states
from atlas_quant.financial_statements.unit_bindings import (
    DocumentUnitBinding,
    FixtureUnitBinding,
    GlobalUnitBinding,
    normalized_row_hash,
)
from test_financial_adapter import FakeProvider, run, STRATEGY
from test_financial_statements import calendar, UNIT, SYMBOL

FIELDS = ("balancesheet.money_cap", "balancesheet.total_assets")
DOC = UnitEvidence("CNY", "CNY", True, "source_document", "TEST_ONLY_document_page", True)


def bindings_for(output):
    snapshot = next(
        s for s in output.snapshots if any(r["end_date"] == "20231231" for r in s["rows"])
    )
    row = next(r for r in snapshot["rows"] if r["end_date"] == "20231231")
    return {
        field: DocumentUnitBinding(
            DOC,
            field,
            "HAND_FAKE_PROVIDER",
            SYMBOL,
            "20231231",
            row["ann_date"],
            row["f_ann_date"],
            row["report_type"],
            row["comp_type"],
            snapshot["id"],
            normalized_row_hash("balancesheet", row),
            "a" * 64,
        )
        for field in FIELDS
    }


def provider_run(units):
    # A test declaration exercises provider contract branching. It is NOT real
    # exchange-calendar evidence, and the fake values do not certify a provider.
    return load_statement_states(
        FakeProvider(),
        STRATEGY,
        ["model_fin_cash_asset_share"],
        replace(calendar(), kind="official", evidence_reference="TEST_ONLY_official_contract"),
        units,
        announcement_start="20210101",
        source_kind="provider",
        source_provider="HAND_FAKE_PROVIDER",
        retrieved_at="2025-01-01T00:00:00Z",
    )


def test_document_binding_matches_only_frozen_normalized_record_and_retains_scope():
    unverified = run(selected_ids=["model_fin_cash_asset_share"], unit_contract={})
    units = bindings_for(unverified)
    output = run(selected_ids=["model_fin_cash_asset_share"], unit_contract=units)
    assert output.panel.iloc[-1].model_fin_cash_asset_share == pytest.approx(0.1)
    for dependency in output.state_events[-1]["result"]["dependencies"]:
        scope = dependency["unit_scope"]
        assert scope["kind"] == "document" and scope["status"] == "matched"
        assert scope["field_id"] == dependency["field_id"]
        assert scope["symbol"] == SYMBOL and scope["period_end"] == "20231231"
        assert scope["source_snapshot"] == dependency["source_snapshot"]
        assert scope["normalized_row_hash"] == units[dependency["field_id"]].normalized_row_hash
        assert scope["document_hashes"] == ("a" * 64,)
        assert scope["binding_hashes"]


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "000001.SZ"},
        {"period_end": "20221231"},
        {"ann_date": "20240425"},
        {"f_ann_date": "20240425"},
        {"report_type": "4"},
        {"company_type": "2"},
        {"source_snapshot": "b" * 64},
        {"normalized_row_hash": "b" * 64},
        {"provider": "ANOTHER_PROVIDER"},
    ],
)
def test_narrow_document_proof_never_certifies_other_observation(change):
    source = run(selected_ids=["model_fin_cash_asset_share"], unit_contract={})
    units = bindings_for(source)
    units[FIELDS[0]] = replace(units[FIELDS[0]], **change)
    output = run(selected_ids=["model_fin_cash_asset_share"], unit_contract=units)
    assert output.panel.model_fin_cash_asset_share.isna().all()
    assert output.coverage["model_fin_cash_asset_share"]["reasons"]["UNIT_SCOPE_MISMATCH"] > 0
    scope = next(
        d["unit_scope"]
        for d in output.state_events[-1]["result"]["dependencies"]
        if d["field_id"] == FIELDS[0]
    )
    assert scope["status"] == "mismatch" and scope["binding_hashes"]


def test_bare_evidence_and_wrong_field_binding_are_rejected_before_any_read():
    for units in [{FIELDS[0]: DOC}, {FIELDS[0]: UNIT}]:
        client = FakeProvider()
        with pytest.raises(ContractError, match="scoped bindings"):
            run(client, selected_ids=["model_fin_cash_asset_share"], unit_contract=units)
        assert client.calls == []
    with pytest.raises(ContractError, match="field ID"):
        run(unit_contract={FIELDS[0]: FixtureUnitBinding(UNIT, FIELDS[1], "HAND_FAKE_PROVIDER")})


def test_global_contract_requires_explicit_scope_and_document_hash():
    evidence = replace(DOC, kind="source_contract", reference="TEST_ONLY_global_source_contract")
    with pytest.raises(ContractError, match="global scope"):
        GlobalUnitBinding(evidence, FIELDS[0], "HAND_FAKE_PROVIDER", "a" * 64, "")
    with pytest.raises(ContractError, match="SHA256"):
        GlobalUnitBinding(evidence, FIELDS[0], "HAND_FAKE_PROVIDER", "", "global")
    units = {
        field: GlobalUnitBinding(evidence, field, "HAND_FAKE_PROVIDER", "a" * 64, "global")
        for field in FIELDS
    }
    output = provider_run(units)
    assert output.panel.iloc[-1].model_fin_cash_asset_share == pytest.approx(0.1)
    assert all(
        d["unit_scope"]["kind"] == "global"
        for d in output.state_events[-1]["result"]["dependencies"]
    )
    assert output.provenance["availabilityEvidenceLevel"] == "vendor_reported_disclosure_dates"
    assert not output.provenance["originalAsPublishedVerified"]
    assert not output.provenance["revisionTimeVerified"]
    metadata = output.provenance["externalFields"]["model_fin_cash_asset_share"]
    assert metadata["availabilityEvidenceLevel"] == "vendor_reported_disclosure_dates"
    assert metadata["originalAsPublishedVerified"] is False


def test_document_hash_is_mandatory_and_changes_lineage_without_changing_value():
    source = run(selected_ids=["model_fin_cash_asset_share"], unit_contract={})
    units = bindings_for(source)
    with pytest.raises(ContractError, match="SHA256"):
        replace(units[FIELDS[0]], document_hash="")
    before = run(selected_ids=["model_fin_cash_asset_share"], unit_contract=units)
    units[FIELDS[0]] = replace(units[FIELDS[0]], document_hash="b" * 64)
    after = run(selected_ids=["model_fin_cash_asset_share"], unit_contract=units)
    assert before.panel.equals(after.panel)
    assert (
        before.state_events[-1]["result"]["lineageHash"]
        != after.state_events[-1]["result"]["lineageHash"]
    )


def test_conflicting_matching_unit_proofs_produce_missing_instead_of_first_wins():
    source = run(selected_ids=["model_fin_cash_asset_share"], unit_contract={})
    units = bindings_for(source)
    original = units[FIELDS[0]]
    units[FIELDS[0]] = [original, replace(original, evidence=replace(DOC, native_unit="CNY_1000"))]
    output = run(selected_ids=["model_fin_cash_asset_share"], unit_contract=units)
    assert output.panel.model_fin_cash_asset_share.isna().all()
    assert output.coverage["model_fin_cash_asset_share"]["reasons"]["UNIT_SCOPE_CONFLICT"] > 0


def test_normalized_values_changed_with_same_metadata_cannot_reuse_document_proof():
    source = run(selected_ids=["model_fin_cash_asset_share"], unit_contract={})
    units = bindings_for(source)

    def mutate(endpoint, params, frame):
        frame = frame.copy()
        if endpoint == "balancesheet":
            frame.loc[frame.end_date == "20231231", "money_cap"] = 220
        return frame

    output = run(
        FakeProvider(transform=mutate),
        selected_ids=["model_fin_cash_asset_share"],
        unit_contract=units,
    )
    assert output.panel.model_fin_cash_asset_share.isna().all()
    assert output.coverage["model_fin_cash_asset_share"]["reasons"]["UNIT_SCOPE_MISMATCH"] > 0


def test_document_unit_matching_does_not_upgrade_historical_publication_claims():
    unverified = provider_run({})
    output = provider_run(bindings_for(unverified))
    assert output.panel.iloc[-1].model_fin_cash_asset_share == pytest.approx(0.1)
    assert output.provenance["availabilityEvidenceLevel"] == "vendor_reported_disclosure_dates"
    assert output.provenance["originalAsPublishedVerified"] is False
    assert output.provenance["revisionTimeVerified"] is False
