"""No-fit graph capability, dispatch, queue routing and parent budget tests."""
from copy import deepcopy
import json
import subprocess
import time
from types import SimpleNamespace
import pytest

from atlas_quant import runner
from atlas_quant.runner_claims import claim_request,ClaimIntent
from atlas_quant.graph_research_runner import compute as graph_compute
from atlas_quant.graph_research_runner.client import GraphResearchClient
from atlas_quant.graph_research_runner.limits import GraphProcessBudget
from atlas_quant.graph_research_runner.spool import GraphResearchSpool
from atlas_quant.financial_graph_bundle_spool import FinancialGraphBundleSpool
from atlas_quant.dataset_runner.protocol import encode
from dataset_runner_support import JOB,LEASE,config
from test_graph_research_transport import graph_wire
from test_research_dataset_components import sources,long_sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_snapshot import snapshots


def test_four_capabilities_are_explicit_and_legacy_not_implied():
    assert claim_request(JOB)['transportFormats']==['atlas.quant.bundle/1']
    graph=claim_request(JOB,financial_graphs=True)
    assert graph['transportFormats']==['atlas.quant.bundle/1','atlas.quant.financial_bundle/2']
    assert graph['datasetFormats']==['atlas.quant.research_dataset/3']
    assert graph['snapshotFormats']==['financial_column_snapshot_v1']
    assert graph['financialResearchProfiles']==['financial_fundamental_graph_auto_50_v1']
    both=claim_request(JOB,financial_datasets=True,financial_graphs=True)
    assert len(both['datasetFormats'])==2 and len(both['financialResearchProfiles'])==3
    assert 'datasetFormats' not in claim_request(JOB,financial_graphs='true')


@pytest.mark.parametrize('value',[1,'true',None])
def test_graph_config_requires_exact_boolean_and_shared_lock(tmp_path,value):
    path=tmp_path/'config.json';settings={**config(tmp_path),'financial_graph_research_enabled':value}
    path.write_text(json.dumps(settings));path.chmod(0o600)
    with pytest.raises(runner.RunnerError):runner.load_config(path)
    settings['financial_graph_research_enabled']=True;path.write_text(json.dumps(settings))
    with pytest.raises(runner.RunnerError,match='计算锁'):runner.load_config(path)


def test_graph_child_dispatch_strips_provider_env_without_legacy_compute(tmp_path,graph_wire,monkeypatch):
    job=graph_wire[0];context=FinancialGraphBundleSpool.context_for(runner.CompletionSpool(config(tmp_path)),job);calls=[]
    monkeypatch.setenv('TUSHARE_TOKEN','must-not-reach')
    def compute(actual,received,**kw):
        import os
        assert 'TUSHARE_TOKEN' not in os.environ and actual==job and received==context
        calls.append(1);return {'bundleId':'a'*64,'_bundleFormat':'atlas.quant.financial_bundle/2'}
    monkeypatch.setattr(graph_compute,'compute',compute)
    class Pipe:
        def send(self,value):self.value=value
        def close(self):pass
    pipe=Pipe();runner._child_entry(pipe,job,None,None,None,bundle_context=context,compute_lock_path=str(tmp_path/'slot.lock'),deadline=time.monotonic()+10)
    assert calls==[1] and pipe.value['bundleId']=='a'*64


@pytest.mark.parametrize('enabled',[False,True])
def test_graph_flag_selects_separate_prepare_and_persists_source_format(tmp_path,graph_wire,monkeypatch,enabled):
    job=deepcopy(graph_wire[0]);prepared=[];fitted=[];spool=runner.CompletionSpool(config(tmp_path))
    class Queue:
        def __init__(self,*args):pass
        def post(self,route,payload,**kw):
            assert route=='claim'
            assert ('atlas.quant.research_dataset/3' in payload.get('datasetFormats',[]))==enabled
            return {'job':job,'claim':{'requestId':payload['requestId'],'status':'running','jobId':JOB}}
    def prepare(self,value,*args,**kw):prepared.append(1);return value
    def compute(*args,**kw):fitted.append(1);return {'error':{'code':'STUB_NO_F','message':'No fitting performed'}}
    monkeypatch.setattr(runner,'QueueClient',Queue);monkeypatch.setattr(GraphResearchClient,'prepare',prepare)
    monkeypatch.setattr(runner,'execute_bounded',compute);monkeypatch.setattr(runner,'flush_completions',lambda *a:None);monkeypatch.setattr(runner,'STOP',False)
    settings={**config(tmp_path),'financial_dataset_research_enabled':True,'financial_graph_research_enabled':enabled,'compute_lock_path':str(tmp_path/'slot.lock')}
    assert runner._serve(settings,spool,once=True)==0
    assert bool(prepared)==bool(fitted)==enabled
    payload=list(spool.pending())[0][1]
    assert payload['_bundleFormat']=='atlas.quant.financial_bundle/2'
    assert ClaimIntent(spool).read()['sourceFormat']=='atlas.quant.research_dataset/3'
    assert payload['error']['code']==('STUB_NO_F' if enabled else 'DATASET_RESEARCH_DISABLED')


@pytest.mark.parametrize('attack,code',[('memory','CAPACITY_MEMORY'),('disk','CAPACITY_DISK'),('fit','CAPACITY_FIT_TIMEOUT'),
    ('ps','CAPACITY_MONITOR'),('nan','CAPACITY_MONITOR'),('negative','CAPACITY_MONITOR'),('future','CAPACITY_MONITOR')])
def test_parent_graph_budget_is_enforced_during_a_stuck_child(tmp_path,monkeypatch,attack,code):
    import atlas_quant.graph_research_runner.limits as limits
    # Host monotonic uptime can be <301s on fresh CI machines. Such a clock
    # minus 301 is an invalid start, not a valid fit that exceeded its budget.
    now=1000.0
    monkeypatch.setattr(limits,'time',SimpleNamespace(monotonic=lambda:now))
    completion=runner.CompletionSpool(config(tmp_path));context=FinancialGraphBundleSpool.context_for(completion,{'id':JOB,'leaseToken':LEASE})
    store=GraphResearchSpool(context)
    starts={'fit':now-301,'nan':'NaN','negative':-1.0,'future':now+1}
    if attack in starts:
        event={'phase':'fit_started','startedMonotonic':starts[attack]}
        store.write('progress',encode(event))
    monkeypatch.setattr(limits.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1 if attack=='ps' else 0,stdout=str(4*1024**2 if attack=='memory' else 1024)))
    monkeypatch.setattr(limits.shutil,'disk_usage',lambda *a:SimpleNamespace(free=499*1024**2 if attack=='disk' else 10*1024**3))
    with pytest.raises(runner.RunnerError) as caught:GraphProcessBudget(context).check(123)
    assert caught.value.code==code


@pytest.mark.parametrize('now,started,code',[
    (100.0,0.0,None),
    (100.0,-201.0,'CAPACITY_MONITOR'),
    (1000.0,700.0,None),
    (1000.0,699.999,'CAPACITY_FIT_TIMEOUT'),
])
def test_graph_fit_clock_boundary_does_not_depend_on_host_uptime(tmp_path,monkeypatch,now,started,code):
    import atlas_quant.graph_research_runner.limits as limits
    context=FinancialGraphBundleSpool.context_for(runner.CompletionSpool(config(tmp_path)),{'id':JOB,'leaseToken':LEASE})
    GraphResearchSpool(context).write('progress',encode({'phase':'fit_started','startedMonotonic':started}))
    monkeypatch.setattr(limits,'time',SimpleNamespace(monotonic=lambda:now))
    monkeypatch.setattr(limits.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='1024'))
    monkeypatch.setattr(limits.shutil,'disk_usage',lambda *a:SimpleNamespace(free=10*1024**3))
    if code:
        with pytest.raises(runner.RunnerError) as caught:GraphProcessBudget(context).check(123)
        assert caught.value.code==code
    else:
        GraphProcessBudget(context).check(123)


def test_queue_graph_route_rejects_legacy_columns_get_and_unknown_namespace(tmp_path):
    client=runner.QueueClient(config(tmp_path));identity={'id':JOB,'leaseToken':LEASE}
    for namespace,method,collection in [('financial-graph-bundles','GET','snapshotColumns'),
        ('financial-graph-bundles','PUT','snapshotRows'),('financial-bundles','PUT','snapshotColumns'),
        ('financial-graph-bundlesX','PUT','snapshotColumns')]:
        with pytest.raises(runner.RunnerError) as caught:
            client.bundle_chunk(method,'a'*64,collection,0,identity,namespace=namespace,raw=b'[]',stage_id='stage')
        assert caught.value.code=='QUEUE_ROUTE'
