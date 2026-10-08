import copy
import json
from pathlib import Path

import pytest

from atlas_quant import runner
from atlas_quant.bundle import BundleReader, sha
from atlas_quant.bundle_spool import BundleSpool
from atlas_quant.runner_claims import ClaimIntent


def config(tmp_path):
    return {'delivery_dir':str(tmp_path/'delivery'),'runner_secret':'x'*48,'api_base':'https://queue.test','job_timeout':30}


@pytest.fixture(scope='module')
def research():
    strategy = json.loads((Path(__file__).parents[1]/'examples/statistical-quant.json').read_text())
    snapshots, plans = [], []
    result = runner.run_job({'strategy':strategy,'dataSource':'demo'}, snapshot_sink=snapshots.append,forecast_plan_sink=plans.append)
    return result, snapshots[0], plans[0]


class Transport:
    def __init__(self, request_id, job_id='job'):
        self.request_id, self.job_id = request_id, job_id
        self.manifest_raw = None
        self.chunks = {}
        self.puts, self.completes, self.calls = [], [], []
        self.lose_put_ack = False
        self.lose_complete_ack = False
        self.terminal = False
        self.stage = 'stage-1'

    def post(self, route, payload, **kwargs):
        self.calls.append(route)
        if route == 'heartbeat':
            return {'leaseValid':not self.terminal,'cancelled':False}
        if route == 'bundles/begin':
            raw = payload['manifestText'].encode()
            assert sha(raw) == payload['bundleId']
            assert self.manifest_raw in (None, raw)
            self.manifest_raw = raw
            manifest = json.loads(raw)
            missing = [{'collection':c['id'],'ordinal':d['ordinal']} for c in manifest['collections'] for d in c['chunks'] if (c['id'],d['ordinal']) not in self.chunks]
            return {'ok':True,'bundleId':sha(raw),'stageId':self.stage,'status':'committed' if self.terminal else 'staging','missing':missing}
        if route == 'bundles/finalize':
            assert payload['stageId'] == self.stage
            reader = BundleReader(self.manifest_raw, lambda c,n:self.chunks[c,n])
            reader.verify_integrity()
            return {'ok':True,'bundleId':reader.bundle_id,'status':'verified'}
        if route == 'complete':
            assert set(payload) == {'id','leaseToken','bundleId','stageId'} or set(payload) == {'id','leaseToken','error'}
            self.completes.append(copy.deepcopy(payload))
            self.terminal = True
            if self.lose_complete_ack:
                raise runner.RunnerError('QUEUE_NETWORK','lost completion acknowledgement')
            return {'ok':True}
        if route == 'claim':
            assert payload['requestId'] == self.request_id and self.terminal
            if self.lose_complete_ack:
                raise runner.RunnerError('QUEUE_NETWORK','terminal receipt also unavailable')
            return {'job':None,'claim':{'requestId':self.request_id,'jobId':self.job_id,'status':'completed'}}
        if route == 'replay':
            assert payload['kind'] == 'bundle'
            return {'bundleId':sha(self.manifest_raw),'manifestText':self.manifest_raw.decode()}
        pytest.fail('unexpected route '+route)

    def bundle_chunk(self, method, bundle_id, collection, ordinal, identity, **kwargs):
        if method == 'GET':
            return self.chunks[collection,ordinal]
        assert kwargs['stage_id'] == self.stage
        raw = kwargs['raw']
        self.puts.append((collection,ordinal))
        self.chunks[collection,ordinal] = raw
        if self.lose_put_ack:
            self.lose_put_ack = False
            raise runner.RunnerError('QUEUE_NETWORK','lost uploaded chunk acknowledgement')
        return {'ok':True,'bundleId':bundle_id,'collection':collection,'ordinal':ordinal,'sha256':sha(raw),'idempotent':False}


def ready(tmp_path, research):
    cfg = config(tmp_path)
    spool = runner.CompletionSpool(cfg)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    identity = {'id':'job','leaseToken':'lease'}
    claims.executing(intent, identity)
    store = BundleSpool.from_spool(spool, identity)
    result = store.build(*research)
    spool.write(dict(identity, **result, _claimRequestId=intent['requestId']))
    return cfg, spool, store, intent, Transport(intent['requestId'])


def test_lost_chunk_ack_resumes_only_missing_bytes_and_preserves_v1(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    client.lose_put_ack = True
    monkeypatch.setattr(runner,'_wait',lambda seconds:None)
    runner.flush_completions(client,spool)
    assert len(client.puts) == len(set(client.puts))
    assert client.calls.count('bundles/begin') == 2
    assert len(client.completes) == 1 and not list(spool.pending())
    assert not store.root.exists() and ClaimIntent(spool).read() is None
    reader = BundleReader(client.manifest_raw,lambda c,n:client.chunks[c,n])
    assert reader.document('report') == research[0]


def test_lost_terminal_ack_keeps_encrypted_bundle_for_restart(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    client.lose_complete_ack = True
    monkeypatch.setattr(runner,'_wait',lambda seconds:None)
    with pytest.raises(runner.RunnerError) as exc:
        runner.flush_completions(client,spool)
    assert exc.value.code == 'COMPLETION_UNCONFIRMED'
    assert store.root.exists() and len(list(spool.pending())) == 1
    assert len(client.puts) == len(client.chunks)
    client.lose_complete_ack = False
    runner.flush_completions(client,runner.CompletionSpool(cfg))
    assert len(client.puts) == len(client.chunks)  # no refetch, fit or reupload
    assert all(p == client.completes[0] for p in client.completes)
    assert not store.root.exists() and not list(spool.pending())


def test_unknown_missing_chunk_never_reads_arbitrary_file(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    original = client.post
    def post(route,payload,**kwargs):
        response = original(route,payload,**kwargs)
        if route == 'bundles/begin':
            response['missing'] = [{'collection':'../../private','ordinal':0}]
        return response
    client.post = post
    with pytest.raises(runner.RunnerError) as exc:
        runner.flush_completions(client,spool)
    assert exc.value.code == 'BUNDLE_PROTOCOL'
    assert not client.puts and store.root.exists() and list(spool.pending())


def test_replay_download_uses_chunks_and_frozen_values_without_provider(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    monkeypatch.setattr(runner,'_wait',lambda seconds:None)
    runner.flush_completions(client,spool)
    identity = {'id':'replay','leaseToken':'new-lease'}
    reference = runner.fetch_bundle_replay(client,spool,identity,{'bundleId':sha(client.manifest_raw)},deadline=runner.time.monotonic()+30)
    assert set(reference) == {'bundleId','_bundleKey'} and len(json.dumps(reference)) < 256
    reader = BundleSpool.from_spool(spool,identity).reader(reference['bundleId'])
    reader.verify_integrity()
    replay = {'artifact':dict(reader.document('forecast'),artifactId=reader.manifest['forecastArtifactId']),
              'snapshot':reader.document('snapshot'),'coverage':reader.document('coverage')}
    assert replay['artifact'] == research[0]['forecasts']
    assert replay['snapshot'] == research[1] and replay['coverage'] == research[2]
    monkeypatch.setattr(runner,'load_tushare_proxy',lambda *a,**k:pytest.fail('replay cannot acquire provider data'))
    strategy = copy.deepcopy(research[0]['strategy'])
    strategy['execution']['enabled'] = True
    result = runner.run_job({'jobKind':'execution','strategy':strategy,'forecastArtifactId':replay['artifact']['artifactId'],'replay':replay},result_limit=256*1024*1024)
    assert result['forecasts'] == research[0]['forecasts'] and result['research']['predictionRefitPerformed'] is False


def test_child_returns_only_small_encrypted_bundle_reference(tmp_path):
    cfg = config(tmp_path)
    spool = runner.CompletionSpool(cfg)
    strategy = json.loads((Path(__file__).parents[1]/'examples/statistical-quant.json').read_text())
    identity = {'id':'bounded','leaseToken':'lease'}
    context = BundleSpool.context_for(spool,identity)
    answer = runner.execute_bounded({'strategy':strategy,'dataSource':'demo'},timeout=30,capture_snapshot=True,bundle_context=context)
    assert set(answer) == {'bundleId','_bundleKey'} and len(json.dumps(answer)) < 256
    reader = BundleSpool(context).reader(answer['bundleId'])
    assert reader.verify_integrity()['forecastRows'] > 0


def test_cleanup_crash_resumes_after_terminal_receipt_without_reupload(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    monkeypatch.setattr(runner,'_wait',lambda seconds:None)
    original = BundleSpool.cleanup
    def interrupted(self):
        next(self.root.glob('*.enc')).unlink()
        raise runner.RunnerError('DELIVERY_WRITE','cleanup interrupted')
    monkeypatch.setattr(BundleSpool,'cleanup',interrupted)
    with pytest.raises(runner.RunnerError):
        runner.flush_completions(client,spool)
    assert list(spool.pending())[0][1]['_terminalConfirmed'] is True
    before = len(client.puts)
    monkeypatch.setattr(BundleSpool,'cleanup',original)
    runner.flush_completions(client,runner.CompletionSpool(cfg))
    assert len(client.puts) == before and not list(spool.pending()) and not store.root.exists()


def test_delivery_total_deadline_bounds_all_chunks_and_preserves_spool(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    clock = [100.]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runner,'BUNDLE_DELIVERY_SECONDS',3)
    original = client.bundle_chunk
    def delayed(*args,**kwargs):
        assert kwargs['deadline'] == 103.
        clock[0] += 2.
        return original(*args,**kwargs)
    client.bundle_chunk = delayed
    with pytest.raises(runner.RunnerError) as exc:
        runner.flush_completions(client,spool)
    assert exc.value.code == 'DELIVERY_DEADLINE'
    assert len(client.puts) == 2 and not client.completes
    assert store.root.exists() and list(spool.pending()) and ClaimIntent(spool).read()


def test_local_stop_interrupts_upload_without_recompute_or_cleanup(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    original = client.bundle_chunk
    def stopped(*args,**kwargs):
        reply = original(*args,**kwargs)
        monkeypatch.setattr(runner,'STOP',True)
        return reply
    client.bundle_chunk = stopped
    with pytest.raises(runner.RunnerError) as exc:
        runner.flush_completions(client,spool)
    assert exc.value.code == 'DELIVERY_INTERRUPTED'
    assert len(client.puts) == 1 and not client.completes
    assert store.root.exists() and list(spool.pending())


def test_server_cancellation_stops_chunks_and_requires_terminal_receipt(tmp_path, monkeypatch, research):
    cfg, spool, store, intent, client = ready(tmp_path,research)
    original = client.post
    def cancelled(route,payload,**kwargs):
        if route == 'heartbeat':
            client.terminal = True
            return {'cancelled':True,'leaseValid':False}
        if route == 'claim':
            response = original(route,payload,**kwargs)
            response['claim']['status'] = 'cancelled'
            return response
        return original(route,payload,**kwargs)
    client.post = cancelled
    runner.flush_completions(client,spool)
    assert not client.puts and not client.completes
    assert not list(spool.pending()) and not store.root.exists() and ClaimIntent(spool).read() is None
