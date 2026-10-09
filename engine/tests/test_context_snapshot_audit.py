"""Context snapshot version and independent source audit; no provider or model fit."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from atlas_quant.context_sources import summarize_context_provenance
from atlas_quant.provider import _exact_records, validate_upload
from atlas_quant.runner import RunnerError
from atlas_quant.runner_artifacts import freeze_input, restore_input
from test_context_restore import frozen

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('context_independent_audit', ROOT / 'scripts/bundle_audit.py')
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def snapshot_fixture(frozen):
    frame, provenance, strategy = frozen
    frame, provenance = validate_upload(strategy, {'rows': _exact_records(frame), 'provenance': provenance})
    return strategy, freeze_input(strategy, frame, provenance)


def test_context_snapshot_uses_distinct_fingerprint_contract(frozen):
    strategy, snapshot = snapshot_fixture(frozen)
    assert snapshot['fingerprintVersion'] == 'research_input_context_v1'
    restored, provenance = restore_input(strategy, snapshot, snapshot['dataFingerprint'])
    assert provenance['contextSourceRoot'] == snapshot['provenance']['contextSourceRoot']
    assert len(restored) == len(snapshot['rows'])
    snapshot['fingerprintVersion'] = 'research_input_v1'
    with pytest.raises(RunnerError, match='数据指纹'):
        restore_input(strategy, snapshot)


def make_auditor(snapshot):
    obj = audit.BundleAudit.__new__(audit.BundleAudit)
    obj.db = sqlite3.connect(':memory:')
    obj.db.execute('CREATE TABLE records(collection TEXT, position INT, payload TEXT)')
    obj.checks = 0
    context = snapshot['provenance']['contextSources']
    for name, rows in [('snapshotContextSources', context), ('snapshotRows', snapshot['rows'])]:
        obj.db.executemany('INSERT INTO records VALUES(?,?,?)', [(name, i, json.dumps(row)) for i, row in enumerate(rows)])
    obj.collections = {'snapshotContextSources': {'rowCount': len(context)}}
    meta = copy.deepcopy(snapshot)
    meta['provenance']['contextSources'] = audit.Collection('snapshotContextSources')
    obj.documents = {'snapshot': meta, 'report': {'provenance': summarize_context_provenance(snapshot['provenance'])}}
    obj.manifest = {'dataFingerprint': snapshot['dataFingerprint']}
    return obj


def test_independent_audit_checks_source_root_and_stock_broadcast(frozen):
    _, snapshot = snapshot_fixture(frozen)
    obj = make_auditor(snapshot)
    try:
        obj.validate_snapshot()
        assert obj.checks > len(snapshot['rows'])
    finally:
        obj.db.close()


@pytest.mark.parametrize('damage,reason', [
    ('version', 'Snapshot input metadata'), ('root', 'Context source root'),
    ('value', 'Context snapshot broadcast'), ('available', 'Context snapshot broadcast'),
    ('summary', 'summary differs'), ('missing', 'archive/version mismatch'),
])
def test_independent_context_audit_rejects_rehashed_damage(frozen, damage, reason):
    _, snapshot = snapshot_fixture(frozen)
    if damage == 'version':
        snapshot['fingerprintVersion'] = 'research_input_v1'
    elif damage == 'root':
        snapshot['provenance']['contextSourceRoot'] = '0' * 64
    elif damage == 'value':
        snapshot['rows'][0]['ext_ctx_000300_sh_close'] += 1
    elif damage == 'available':
        snapshot['rows'][0]['ext_ctx_000300_sh_close__available_date'] = '20220101'
    obj = make_auditor(snapshot)
    if damage == 'summary':
        obj.documents['report']['provenance']['contextSources'][0]['rowCount'] += 1
    elif damage == 'missing':
        obj.collections.clear()
    try:
        with pytest.raises(ValueError, match=reason):
            obj.validate_snapshot()
    finally:
        obj.db.close()
