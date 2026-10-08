import copy
import json
from pathlib import Path

import pytest

from atlas_quant import bundle
from atlas_quant.bundle_spool import BundleSpool
from atlas_quant.runner import CompletionSpool, RunnerError, run_job
from atlas_quant.statistical_quant.schema import digest


@pytest.fixture(scope='module')
def research():
    strategy = json.loads((Path(__file__).parents[1] / 'examples/statistical-quant.json').read_text())
    strategy['factors'] = [{'id': 'mom5', 'expression': 'returns(close,5)', 'direction': 1, 'role': 'predictor'}]
    strategy['execution']['enabled'] = True
    snapshots, plans = [], []
    result = run_job({'strategy': strategy, 'dataSource': 'demo'}, snapshot_sink=snapshots.append, forecast_plan_sink=plans.append)
    return result, snapshots[0], plans[0]


def packed(research, target=bundle.CHUNK_TARGET):
    chunks = {}
    raw = bundle.build_bundle(*research, lambda c, n, raw: chunks.__setitem__((c, n), raw), lambda c, n: chunks[c, n], chunk_target=target)
    return raw, chunks, bundle.BundleReader(raw, lambda c, n: chunks[c, n])


def test_real_forecast_execution_and_snapshot_keep_v1_identity(research):
    raw, chunks, reader = packed(research)
    assert reader.verify_integrity()['verified'] is True
    assert reader.document('report') == research[0]
    assert reader.document('snapshot') == research[1]
    assert reader.document('coverage') == research[2]
    assert reader.manifest['documents']['forecast']['sha256'] == research[0]['forecasts']['artifactId']
    assert set(reader.collections) == set(bundle.COLLECTIONS)
    assert max(map(len, chunks.values())) <= bundle.CHUNK_LIMIT


def test_chunk_layout_changes_transport_not_forecast_or_records(research):
    first = packed(research, 128*1024)[2]
    second = packed(research, 64*1024)[2]
    assert first.bundle_id != second.bundle_id
    assert first.manifest['forecastArtifactId'] == second.manifest['forecastArtifactId']
    assert first.document('report') == second.document('report')
    second.verify_integrity()


def test_pre_fit_plan_emitted_before_any_forecast_and_does_not_change_artifact(monkeypatch):
    from atlas_quant.statistical_quant import core
    strategy = json.loads((Path(__file__).parents[1] / 'examples/statistical-quant.json').read_text())
    plans = []
    original = core.forecast
    def forecast(samples, strategy):
        assert len(plans) == 1 and plans[0]['source'] == 'samples_before_model_fitting'
        return original(samples, strategy)
    reference = run_job({'strategy': strategy, 'dataSource': 'demo'})
    monkeypatch.setattr(core, 'forecast', forecast)
    actual = run_job({'strategy': strategy, 'dataSource': 'demo'}, forecast_plan_sink=plans.append)
    assert actual['forecasts'] == reference['forecasts']
    assert [(r['date'],r['targetId']) for r in plans[0]['origins']] == [(r['date'],r['targetId']) for r in actual['forecasts']['rows']]


def test_missing_prediction_cannot_be_hidden_by_rehashing_result(research):
    result, snapshot, plan = copy.deepcopy(research)
    result['forecasts']['rows'].pop()
    result['forecasts']['totalRows'] -= 1
    artifact = result['forecasts']
    artifact['artifactId'] = digest({k:v for k,v in artifact.items() if k != 'artifactId'})
    reader = packed((result, snapshot, plan))[2]
    with pytest.raises(RunnerError) as exc:
        reader.verify_integrity()
    assert exc.value.code == 'BUNDLE_COVERAGE'


def test_corrupt_chunk_rejected_without_a_whole_document_parse(research):
    raw, chunks, reader = packed(research)
    key = next(iter(chunks))
    chunks[key] = chunks[key].replace(b'{', b'[', 1)
    with pytest.raises(RunnerError) as exc:
        reader.verify_hashes()
    assert exc.value.code == 'BUNDLE_INTEGRITY'


def test_rehashed_noncanonical_literal_cannot_change_v1_hash_contract(research):
    raw, chunks, _ = packed(research)
    manifest = json.loads(raw)
    # Whitespace is legal JSON but not the existing artifact's canonical bytes.
    # Rehashing the transport and document cannot quietly redefine its identity.
    part = manifest['documents']['coverage']['parts'][0]
    part['literal'] = part['literal'].replace('{', '{ ', 1)
    content = b''.join(bundle.iter_document_bytes(manifest, 'coverage', lambda c, n: chunks[c, n]))
    manifest['documents']['coverage'].update(sha256=bundle.sha(content), byteLength=len(content))
    reader = bundle.BundleReader(bundle.encode(manifest), lambda c, n: chunks[c, n])
    with pytest.raises(RunnerError) as exc:
        reader.verify_hashes()
    assert exc.value.code == 'BUNDLE_INTEGRITY'


@pytest.mark.parametrize('mutation', [
    lambda m: m['collections'][0].update(path='/fake'),
    lambda m: m['collections'][0]['chunks'][0].update(start=1),
    lambda m: m['collections'][0]['chunks'][0].update(count=10001),
    lambda m: m['collections'][0]['chunks'][0].update(ordinal=False),
    lambda m: m['documents']['report']['parts'].append({'document':'report','wrapArtifactId':m['forecastArtifactId']}),
    lambda m: m.update(version=True),
])
def test_bad_manifest_rejected(research, mutation):
    raw, _, _ = packed(research)
    manifest = json.loads(raw)
    mutation(manifest)
    with pytest.raises(RunnerError):
        bundle.validate_manifest(bundle.encode(manifest))


def test_limits_never_return_a_truncated_manifest(research, monkeypatch):
    monkeypatch.setattr(bundle, 'CHUNK_COUNT_LIMIT', 1)
    with pytest.raises(RunnerError) as exc:
        packed(research)
    assert exc.value.code == 'BUNDLE_SIZE'


def test_duplicate_json_keys_and_nonfinite_are_invalid(research):
    raw, _, _ = packed(research)
    with pytest.raises(RunnerError):
        bundle.validate_manifest(raw[:-1] + b',"version":1}')
    altered = copy.deepcopy(research)
    altered[0]['metrics']['totalReturn'] = float('inf')
    with pytest.raises(RunnerError):
        packed(altered)


def test_encrypted_spool_bound_to_job_lease_queue_and_content(tmp_path, research):
    config = {'delivery_dir': str(tmp_path/'delivery'), 'runner_secret':'x'*48, 'api_base':'https://queue.test'}
    completion = CompletionSpool(config)
    identity = {'id':'job','leaseToken':'never-plaintext'}
    store = BundleSpool.from_spool(completion, identity)
    result = store.build(*research)
    assert result['_bundleKey'] == store.key
    for file in store.root.iterdir():
        assert file.stat().st_mode & 0o077 == 0
        assert b'never-plaintext' not in file.read_bytes() and b'forecasts' not in file.read_bytes()
    resumed = BundleSpool.from_spool(CompletionSpool(config), identity)
    assert resumed.reader(result['bundleId']).verify_integrity()['verified']
    other = CompletionSpool(dict(config, api_base='https://other.test'))
    with pytest.raises(RunnerError):
        BundleSpool.from_spool(other, identity).reader()
    assert not list(completion.pending())
    resumed.cleanup()
    assert not store.root.exists()


def test_export_paths_are_private_and_manifest_published_last(tmp_path, research):
    reader = packed(research)[2]
    target = tmp_path/'bundle'
    bundle.export_bundle(reader, target)
    restored = bundle.directory_reader(target)
    assert restored.verify_integrity()['verified']
    assert restored.bundle_id == reader.bundle_id
    assert all(p.stat().st_mode & 0o077 == 0 for p in target.rglob('*'))
