"""Review regressions only: no fitting and no repeated source child."""
from copy import deepcopy
from types import SimpleNamespace
import time
import pytest

from atlas_quant.runner import RunnerError
from atlas_quant.dataset_runner.source_spool import store_sources,read_sources
from atlas_quant.graph_dataset_runner import service
from atlas_quant.graph_dataset_runner.spool import GraphDatasetSpool
from atlas_quant.graph_research_runner.client import GraphResearchClient
from atlas_quant.research_dataset.graph_v3.snapshot import RESEARCH_PROFILE
from test_graph_dataset_service import prepared,writer,Queue
from test_graph_dataset_components import configuration
from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source


def test_transport_version_must_be_integer_not_equal_float():
    value={'datasetRef':{'datasetId':'00000000-0000-4000-8000-000000000000','datasetRoot':'a'*64,
        'format':'atlas.quant.research_dataset','version':3},'admissionProfile':RESEARCH_PROFILE,
        'resultTransport':{'format':'atlas.quant.financial_bundle','version':2.0}}
    with pytest.raises(RunnerError):GraphResearchClient.source_contract(value)
    value['resultTransport']['version']=2
    assert GraphResearchClient.source_contract(value)[1]==RESEARCH_PROFILE


@pytest.mark.parametrize('exhausted',['memory','disk','deadline'])
def test_full_publication_verification_cannot_publish_after_budget_exhaustion(prepared,tmp_path,monkeypatch,exhausted):
    import atlas_quant.graph_dataset_runner.spool as module
    task,_,manifest,parts=prepared;publication=GraphDatasetSpool(configuration(tmp_path)).publication(task)
    for (c,n),raw in parts.items():publication.write_chunk(c,n,raw)
    verify=publication.validate_manifest;checked=[]
    def validate(value):
        result=verify(value);checked.append(1)
        if exhausted=='memory':monkeypatch.setattr(module.resource,'getrusage',lambda *_:SimpleNamespace(ru_maxrss=4*1024**3))
        if exhausted=='disk':monkeypatch.setattr(module.shutil,'disk_usage',lambda *_:SimpleNamespace(free=1))
        return result
    publication.validate_manifest=validate
    if exhausted=='deadline':publication.before_commit=lambda:(_ for _ in ()).throw(RunnerError('DATASET_DEADLINE','expired'))
    with pytest.raises(RunnerError):publication.finalize(manifest)
    assert checked==[1] and not (publication.root/'manifest.enc').exists()


def test_rejected_graph_publication_retains_exact_input_and_output_without_retry(prepared,tmp_path):
    task,data,manifest,parts=prepared;settings=configuration(tmp_path);spool=GraphDatasetSpool(settings)
    calls=[]
    class Reject(Queue):
        uploads=0
        def post(self,route,payload,**kwargs):
            if route.endswith('/publication'):
                self.uploads+=1
                raise RunnerError('DATASET_HTTP','arbitrary sensitive message not retained',http_status=413)
            return super().post(route,payload,**kwargs)
    queue=Reject(data,task)
    def compute(store,job,values,monitor,**kw):
        store_sources(store,job,values,lambda:None)
        writer(manifest,parts,calls)(store,job,values,monitor)
    service.run_once(settings,spool,queue,queue,bounded_compute=compute)
    assert calls==[1] and queue.uploads==1 and queue.status=='failed' and spool.read() is None
    publication=spool.publication(task);assert publication.manifest()==manifest
    assert read_sources(spool,task,lambda:None)==data
    for c in manifest['components']:
        for p in c['parts']:assert publication.read_chunk(c['componentId'],p)==parts[c['componentId'],p['ordinal']]
    record=next((spool.root/'quarantine').glob('*.enc'))
    assert record.exists() and b'arbitrary sensitive' not in record.read_bytes()
    # A terminal queue's later run sees no original publishing intent to replay.
    service.run_once(settings,spool,queue,queue,bounded_compute=lambda *a,**k:pytest.fail('Recomposed'))
    assert queue.uploads==1 and publication.manifest()==manifest


def test_crash_after_graph_rejection_marker_settles_without_republication(prepared,tmp_path):
    task,data,manifest,parts=prepared;settings=configuration(tmp_path);spool=GraphDatasetSpool(settings);queue=Queue(data,task)
    state=spool.save(dict(spool.current_or_create(),phase='publishing',job=task))
    writer(manifest,parts,[])(spool,task,data,None)
    spool.preserve_rejection(state,RunnerError('DATASET_HTTP','ignored',http_status=400))
    assert spool.read()['phase']=='failing'
    service.run_once(settings,spool,queue,queue,bounded_compute=lambda *a,**k:pytest.fail('Recomposed'))
    assert queue.status=='failed' and queue.root is None and spool.read() is None
    assert spool.publication(task).manifest()==manifest
