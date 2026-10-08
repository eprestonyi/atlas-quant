"""Sixth service is explicit; losing any ACK resumes graph bytes without F."""
from copy import deepcopy
import json
import os
import pytest

from atlas_quant.runner import RunnerError
from atlas_quant.graph_dataset_runner import service
from atlas_quant.graph_dataset_runner.protocol import CAPABILITY,JOB_KIND
from atlas_quant.graph_dataset_runner.spool import GraphDatasetSpool
from atlas_quant.graph_dataset_runner.publication import compute_publication
from atlas_quant.graph_dataset_runner.compute import execute_bounded
from atlas_quant.graph_dataset_runner.__main__ import load_config
from atlas_quant.dataset_runner.lease import LeaseMonitor
from atlas_quant.dataset_runner.service import validate_claim as legacy_claim
from atlas_quant.dataset_runner.delivery import dataset_ref
from dataset_runner_support import Queue as LegacyQueue,job,config
from test_graph_dataset_components import graph_inputs,configuration
from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source


class Queue(LegacyQueue):
    def ref(self):return {**super().ref(),'version':3}
    def post(self,route,payload,**kwargs):
        if route in ('claim','heartbeat'):assert payload['capability']==CAPABILITY
        return super().post(route,payload,**kwargs)


@pytest.fixture(scope='module')
def prepared(sources,legacy_source):
    task={**job(),'kind':JOB_KIND};task['inputUrl']=task['inputUrl'].replace('/runner/datasets/','/runner/dataset-graphs/')
    data=graph_inputs(sources,legacy_source,task);parts={}
    manifest=compute_publication(task,data,lambda c,n,raw:parts.__setitem__((c,n),raw))
    return task,data,manifest,parts


def writer(manifest,parts,calls):
    def build(spool,job,inputs,monitor,**kw):
        calls.append(1);publication=spool.publication(job)
        for (c,n),raw in parts.items():publication.write_chunk(c,n,raw)
        publication.finalize(manifest)
    return build


@pytest.mark.parametrize('lost',['claim','begin','part','complete'])
def test_graph_service_ack_loss_never_recomposes_or_changes_identity(prepared,tmp_path,lost):
    task,data,manifest,parts=prepared;settings=configuration(tmp_path);spool=GraphDatasetSpool(settings);queue=Queue(data,task)
    queue.lost.add(lost);calls=[]
    with pytest.raises(RunnerError,match='Lost fixture response'):
        service.run_once(settings,spool,queue,queue,bounded_compute=writer(manifest,parts,calls))
    service.run_once(settings,GraphDatasetSpool(settings),queue,queue,bounded_compute=writer(manifest,parts,calls))
    assert calls==[1] and queue.input_calls==1 and len(set(queue.claims))==1
    assert queue.status=='completed' and queue.parts==parts and GraphDatasetSpool(settings).read() is None


def test_graph_service_actual_source_only_spawn_has_exact_components(prepared,tmp_path):
    task,data,manifest,parts=prepared;settings=configuration(tmp_path);spool=GraphDatasetSpool(settings);queue=Queue(data,task)
    os.chmod(tmp_path,0o700)
    with LeaseMonitor(queue,task,capability=CAPABILITY) as monitor:
        execute_bounded(spool,task,data,monitor,slot_path=str(tmp_path/'slot.lock'))
    publication=spool.publication(task);assert publication.manifest()==manifest
    for c in manifest['components']:
        for p in c['parts']:assert publication.read_chunk(c['componentId'],p)==parts[c['componentId'],p['ordinal']]


def test_legacy_claim_and_completion_validator_cannot_accept_graph(prepared):
    task=prepared[0];state={'requestId':'00000000-0000-4000-8000-000000000000'}
    response={'job':task,'claim':{**state,'jobId':task['id'],'status':'running'}}
    assert service.validate_claim(response,state,'https://queue.test/quant/api')[0]=='running'
    with pytest.raises(RunnerError):legacy_claim(response,state,'https://queue.test/quant/api')
    reference={'datasetId':state['requestId'],'datasetRoot':'a'*64,'format':'atlas.quant.research_dataset','version':3}
    with pytest.raises(RunnerError):dataset_ref(reference)


def test_config_and_idle_gate_require_separate_opt_in(tmp_path):
    settings={**configuration(tmp_path),'poll_seconds':3};path=tmp_path/'graph.json';path.write_text(json.dumps(settings));path.chmod(0o600)
    with pytest.raises(RunnerError):load_config(path)
    settings.update(graph_dataset_enabled=True,compute_lock_path=str(tmp_path/'slot.lock'));path.write_text(json.dumps(settings))
    assert load_config(path)['graph_dataset_enabled'] is True
    class Idle:
        def post(self,route,payload,**kw):
            assert route=='heartbeat' and payload['capability']==CAPABILITY
            return {'ok':True,'canClaim':False}
    assert service.serve(settings,once=True,client_factory=lambda _:Idle())==0
    assert GraphDatasetSpool(settings).read() is None
    settings['graph_dataset_enabled']=False
    with pytest.raises(RunnerError):service.serve(settings,once=True,client_factory=lambda _:pytest.fail('Disabled entered network'))


def test_terminal_old_version_or_wrong_lease_retains_graph_bytes(prepared,tmp_path):
    task,data,manifest,parts=prepared;settings=configuration(tmp_path);spool=GraphDatasetSpool(settings);queue=Queue(data,task)
    queue.ref=lambda:{'datasetId':'00000000-0000-4000-8000-000000000000','datasetRoot':queue.root,'format':'atlas.quant.research_dataset','version':2}
    with pytest.raises(RunnerError):service.run_once(settings,spool,queue,queue,bounded_compute=writer(manifest,parts,[]))
    assert spool.read() is not None and spool.publication(task).manifest()==manifest
