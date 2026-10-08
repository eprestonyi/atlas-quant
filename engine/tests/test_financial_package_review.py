"""Independent package counterexamples. Synthetic only; zero provider calls.

This file is reviewer-owned. Do not alter implementation to make an assertion
pass without reviewing the evidence and intended resource/trust contract.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from atlas_quant.financial_statements import ContractError, RECIPES, UnitEvidence
from atlas_quant.financial_statements.package import (
    decode_package, freeze_package, prepare_package, raw_input, validate_package,
)
from atlas_quant.financial_statements.prepare import AdapterBudget, prepare_statement_states
from atlas_quant.financial_statements.results import canonical_hash
from atlas_quant.financial_statements.unit_bindings import DeclaredUnitBinding
from test_financial_adapter import STRATEGY, UNITS, run
from test_financial_statements import calendar


@pytest.fixture(scope='module')
def source():
    return run()


def declared(snapshots, field='balancesheet.total_assets', statement='Independent synthetic assumption'):
    _, root = raw_input(snapshots, calendar(), source_kind='fixture', source_provider='HAND_FAKE_PROVIDER')
    return DeclaredUnitBinding(
        UnitEvidence('CNY', 'CNY', False, 'user_declared_assumption', 'REVIEW_FIXTURE', True),
        field, 'HAND_FAKE_PROVIDER', root, 'independent-review',
        '2026-10-08T00:00:00Z', statement,
    )


def freeze(snapshots, units, *, trusted=False, policy='allow_declared', selected=None):
    return freeze_package(
        snapshots, calendar(), STRATEGY, selected or tuple(RECIPES), units,
        announcement_start='20210101', source_kind='fixture', source_provider='HAND_FAKE_PROVIDER',
        trusted_unit_proofs=trusted, unit_policy=policy,
    )


def resign(package):
    package['packRoot'] = canonical_hash({k: v for k, v in package.items() if k != 'packRoot'})
    return package


def test_one_declared_dependency_taints_only_dependent_derivations_even_with_trusted_other_proofs(source):
    units = {**UNITS, 'balancesheet.total_assets': declared(source.snapshots)}
    result = prepare_package(freeze(source.snapshots, units, trusted=True), trusted_unit_proofs=True)
    affected = unaffected = 0
    for event in result.state_events:
        value = event['result']
        if value['status'] != 'ok':
            continue
        assumption = any(d['unit_evidence_kind'] == 'user_declared_assumption' for d in value['dependencies'])
        assert ('USER_DECLARED_UNIT_ASSUMPTION' in value['qualityFlags']) is assumption
        assert bool(value['declarationHashes']) is assumption
        assert value['unitVerified'] is (not assumption)
        affected += assumption
        unaffected += not assumption
    assert affected and unaffected


def test_missing_declared_denominator_still_retains_assumption_lineage(source):
    assumed = declared(source.snapshots)
    units = {**UNITS, 'balancesheet.total_assets': replace(assumed, evidence=replace(assumed.evidence, currency='USD'))}
    result = prepare_package(freeze(source.snapshots, units, trusted=True), trusted_unit_proofs=True)
    events = [e for e in result.state_events if e['stateId'] == 'model_fin_cash_asset_share']
    latest = events[-1]['result']
    assert latest['status'] == 'missing'
    assert 'UNSUPPORTED_CURRENCY' in latest['reasonCodes']
    assert latest['unitVerified'] is False
    assert latest['qualityFlags'] == ['USER_DECLARED_UNIT_ASSUMPTION']
    assert latest['declarationHashes'] == [units['balancesheet.total_assets'].declaration_hash]


@pytest.mark.parametrize('change', ['verified', 'kind', 'type', 'public-trust-switch'])
def test_resigned_declaration_cannot_self_upgrade_public_trust(source, change):
    units = {field: declared(source.snapshots, field) for field in UNITS}
    package = freeze(source.snapshots, units)
    first = next(iter(package['bindings'].values()))[0]
    if change == 'verified':
        first['value']['evidence']['verified'] = True
    elif change == 'kind':
        first['value']['evidence']['kind'] = 'source_document'
    elif change == 'type':
        first['type'] = 'GlobalUnitBinding'
    else:
        package['trusted_unit_proofs'] = True
    with pytest.raises(ContractError):
        prepare_package(resign(package))


def test_rehashing_raw_and_outer_root_still_requires_new_explicit_declarations(source):
    units = {field: declared(source.snapshots, field) for field in UNITS}
    package = freeze(source.snapshots, units)
    package['raw']['calendar']['evidence_reference'] += '-different-calendar-evidence'
    package['inputRoot'] = canonical_hash(package['raw'])
    with pytest.raises(ContractError, match='declaration scope'):
        validate_package(resign(package))


def test_decode_rejects_duplicate_json_keys_even_when_the_last_value_matches_root(source):
    package = freeze(source.snapshots, {field: declared(source.snapshots, field) for field in UNITS})
    raw = json.dumps(package)
    duplicate = raw.replace('"unitPolicy":', '"unitPolicy":"verified_only","unitPolicy":', 1)
    with pytest.raises(ContractError):
        decode_package(duplicate.encode())


def test_known_source_size_over_common_budget_fails_before_resolving_all_units(source, monkeypatch):
    """Known raw bytes already exceed this bound; pre-expansion work is wasteful."""
    import atlas_quant.financial_statements.prepare as module
    calls = []
    original = module.resolve_unit
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'resolve_unit', counted)
    with pytest.raises(ContractError):
        prepare_statement_states(
            source.snapshots, STRATEGY, tuple(RECIPES), calendar(), UNITS,
            announcement_start='20210101', source_kind='fixture', source_provider='HAND_FAKE_PROVIDER',
            budget=AdapterBudget(max_prepared_bytes=1),
        )
    assert not calls, f'{len(calls)} unit resolutions occurred after the known source already exceeded the total budget'


def test_repeated_binding_hash_work_is_bounded_before_row_fanout(source, monkeypatch):
    """Small exact operation-count witness; no timing/RSS guess or large run."""
    import atlas_quant.financial_statements.unit_bindings as module
    snapshots = list(deepcopy(source.snapshots))
    index = next(i for i, s in enumerate(snapshots) if s['endpoint'] == 'balancesheet' and s['rows'])
    body = {k: v for k, v in snapshots[index].items() if k not in {'id', 'rowCount', 'byteLength'}}
    body['rows'] = [deepcopy(body['rows'][0]) for _ in range(32)]
    snapshots[index] = {
        **body, 'id': canonical_hash(body), 'rowCount': 32,
        'byteLength': len(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()),
    }
    choices = tuple(declared(snapshots, statement=f'Independent equivalent source assumption {i}') for i in range(32))
    units = {'balancesheet.total_assets': choices,
             'balancesheet.money_cap': declared(snapshots, 'balancesheet.money_cap')}
    package = freeze(snapshots, units, selected=['model_fin_cash_asset_share'])
    hashes = []
    original = module.canonical_hash
    def counted(value):
        if isinstance(value, dict) and value.get('scope') == 'declared' and 'evidence' in value:
            hashes.append(value['field_id'])
        return original(value)
    monkeypatch.setattr(module, 'canonical_hash', counted)
    prepare_package(package)
    # An immutable declaration's digest is independent of the current row.
    # A generous 4 passes allows validation and evidence serialization, but
    # must not grow as the product of candidate declarations and source rows.
    assert len(hashes) <= 4 * 33, f'{len(hashes)} repeated binding hashes for only 33 immutable declarations'


def test_expanded_evidence_budget_stops_before_building_every_source_record(source, monkeypatch):
    """A bounded 64-KiB reference must not multiply through every source row."""
    import atlas_quant.financial_statements.prepare as module
    snapshots = list(deepcopy(source.snapshots))
    index = next(i for i, s in enumerate(snapshots) if s['endpoint'] == 'balancesheet' and s['rows'])
    body = {k: v for k, v in snapshots[index].items() if k not in {'id', 'rowCount', 'byteLength'}}
    body['rows'] = [deepcopy(body['rows'][0]) for _ in range(32)]
    snapshots[index] = {
        **body, 'id': canonical_hash(body), 'rowCount': 32,
        'byteLength': len(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()),
    }
    binding = declared(snapshots)
    units = {
        'balancesheet.total_assets': replace(binding, evidence=replace(binding.evidence, reference='R' * (64 * 1024))),
        'balancesheet.money_cap': declared(snapshots, 'balancesheet.money_cap'),
    }
    package = freeze(snapshots, units, selected=['model_fin_cash_asset_share'])
    calls = []
    original = module.resolve_unit
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'resolve_unit', counted)
    expected_all = 2 * sum(len(s['rows']) for s in snapshots if s['endpoint'] == 'balancesheet')
    with pytest.raises(ContractError):
        prepare_package(package, budget=AdapterBudget(max_prepared_bytes=512 * 1024))
    assert len(calls) < expected_all, f'All {len(calls)} unit resolutions were expanded before enforcing the common evidence budget'
