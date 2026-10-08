"""One newly identified synthetic child; delivery recovery never repeats F."""
from copy import deepcopy
import multiprocessing
import time
import pytest

from atlas_quant import runner
from atlas_quant.dataset_runner.protocol import encode,sha
from atlas_quant.graph_research_runner import compute as graph_compute
from atlas_quant.graph_research_runner.spool import GraphResearchSpool
from atlas_quant.financial_graph_bundle_spool import FinancialGraphBundleSpool,deliver_graph_bundle
from atlas_quant.financial_bundle_v2 import FinancialGraphBundleReader
from test_graph_research_transport import graph_wire,prepare
from test_research_dataset_components import sources,long_sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_snapshot import snapshots


def child(job,context,slot,pipe):
    try:
        answer=graph_compute.compute(job,context,slot_path=slot,deadline=time.monotonic()+40)
        pipe.send({'answer':answer})
    except Exception as error:pipe.send({'error':str(error),'type':type(error).__name__})
    finally:pipe.close()


@pytest.fixture(scope='module')
def child_result(tmp_path_factory,graph_wire):
    path=tmp_path_factory.mktemp('explicit-new-graph-child');job,completion,_=prepare(path,graph_wire)
    context=FinancialGraphBundleSpool.context_for(completion,job)
    parent,worker=multiprocessing.get_context('spawn').Pipe(duplex=False)
    process=multiprocessing.get_context('spawn').Process(target=child,args=(job,context,str(path/'slot.lock'),worker))
    process.start();worker.close()
    try:
        assert parent.poll(45),'Child did not finish within fixed test wall budget'
        answer=parent.recv();process.join(5)
        assert process.exitcode==0 and 'error' not in answer,answer
    finally:
        if process.is_alive():process.terminate();process.join(5)
        parent.close()
    return job,completion,{**job,**answer['answer']}


def test_graph_child_restores_source_and_full_auto_then_commits_source_verified_output(child_result,graph_wire):
    job,completion,payload=child_result
    output=FinancialGraphBundleSpool.from_spool(completion,payload)
    reader=output.reader(payload['bundleId']);assert reader.verify_integrity()['transportVerified']
    source,registry=GraphResearchSpool.from_spool(completion,job).inputs(job)
    restored=reader.restore_sources(source,registry)
    assert restored.model_admission_registered is True
    assert len(list(reader.rows('forecasts')))==len(list(reader.rows('baselineRows')))==41
    assert list(reader.rows('modelFits'))
    assert reader.manifest['sourceEvidence']==job['sourceEvidence']
    assert payload['_bundleFormat']=='atlas.quant.financial_bundle/2'
    assert reader.document('report')['execution']['enabled'] is False


class Delivery:
    def __init__(self):self.manifest=None;self.chunks={};self.uploads=[];self.calls=[];self.lose=True
    def post(self,route,body,**kwargs):
        self.calls.append(route)
        if route=='heartbeat':return {'leaseValid':True,'cancelled':False}
        if route=='financial-graph-bundles/begin':
            raw=body['manifestText'].encode();assert self.manifest in (None,raw);self.manifest=raw
            reader=FinancialGraphBundleReader(raw,lambda c,n:self.chunks[c,n])
            return {'stageId':'graph-stage','bundleId':sha(raw),'status':'staging','missing':[
                {'collection':c['id'],'ordinal':d['ordinal']} for c in reader.manifest['collections']
                for d in c['chunks'] if (c['id'],d['ordinal']) not in self.chunks]}
        if route=='financial-graph-bundles/finalize':
            reader=FinancialGraphBundleReader(self.manifest,lambda c,n:self.chunks[c,n]);reader.verify_integrity()
            return {'bundleId':reader.bundle_id,'status':'verified'}
        pytest.fail('Unexpected route '+route)
    def bundle_chunk(self,method,bundle_id,collection,ordinal,identity,**kwargs):
        assert method=='PUT' and kwargs['namespace']=='financial-graph-bundles'
        raw=kwargs['raw'];self.chunks[collection,ordinal]=raw;self.uploads.append((collection,ordinal))
        if self.lose:self.lose=False;raise runner.RunnerError('QUEUE_NETWORK','Lost graph ACK')
        return {'bundleId':bundle_id,'collection':collection,'ordinal':ordinal,'sha256':sha(raw)}


def test_lost_graph_ack_resumes_same_encrypted_bytes_without_compute(child_result,monkeypatch):
    job,completion,payload=child_result;delivery=Delivery()
    def forbidden(*args,**kwargs):pytest.fail('Delivery must not re-fit or restore model admission')
    monkeypatch.setattr(graph_compute,'run_graph_research',forbidden);monkeypatch.setattr(runner,'STOP',False)
    with pytest.raises(runner.RunnerError,match='Lost graph ACK'):
        deliver_graph_bundle(delivery,completion,payload,deadline=time.monotonic()+30)
    first=delivery.uploads[0]
    assert deliver_graph_bundle(delivery,completion,payload,deadline=time.monotonic()+30)=='graph-stage'
    assert delivery.uploads.count(first)==1
    assert all(c=='heartbeat' or c.startswith('financial-graph-bundles/') for c in delivery.calls)
    assert FinancialGraphBundleSpool.from_spool(completion,payload).root.exists()


@pytest.mark.parametrize('attack',['legacy','credentials','deadline','registry','profile'])
def test_compute_rejects_unsupported_job_or_source_before_fitting(tmp_path,graph_wire,monkeypatch,attack):
    job,completion,_=prepare(tmp_path,graph_wire);context=FinancialGraphBundleSpool.context_for(completion,job)
    deadline=time.monotonic()+30
    if attack=='legacy':job['resultTransport']['version']=1
    elif attack=='credentials':job['providerAccess']={}
    elif attack=='deadline':deadline=time.monotonic()-1
    elif attack=='profile':job['admissionProfile']='financial_fundamental_auto_50_v1'
    elif attack=='registry':
        inputs=GraphResearchSpool(context);inputs.write('registry-index',encode([]))
    def forbidden(*args,**kwargs):pytest.fail('Rejected graph job reached F')
    monkeypatch.setattr(graph_compute,'run_graph_research',forbidden)
    with pytest.raises((ValueError,runner.RunnerError)):
        graph_compute.compute(job,context,slot_path=str(tmp_path/'slot.lock'),deadline=deadline)
    assert not (FinancialGraphBundleSpool(context).root/'manifest.enc').exists()
