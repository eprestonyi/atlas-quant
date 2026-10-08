"""Graph-only frozen transport and authenticated spool boundaries; zero fits."""
from copy import deepcopy
import json
import shutil
import time

import pytest

from atlas_quant import runner
from atlas_quant.dataset_runner.protocol import LIMITS,encode,sha
from atlas_quant.graph_research_runner.client import GraphResearchClient
from atlas_quant.graph_research_runner.spool import GraphResearchSpool
from atlas_quant.research_dataset_runner.client import ResearchDatasetClient
from atlas_quant.research_dataset_runner.spool import ResearchDatasetSpool
from atlas_quant.financial_graph_bundle_spool import FinancialGraphBundleSpool
from atlas_quant.financial_bundle_spool import FinancialBundleSpool
from atlas_quant.research_dataset.graph_v3.snapshot import RESEARCH_PROFILE
from dataset_runner_support import JOB,LEASE,config
from test_dataset_client import Response,Session
from test_research_dataset_components import sources,long_sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_snapshot import snapshots


@pytest.fixture(scope='module')
def graph_wire(snapshots,long_sources):
    strategy,reader,snapshot,*_=snapshots
    ref=snapshot['datasetRef'];evidence={'datasetRef':ref,'admissionProfile':RESEARCH_PROFILE}
    prefix=f'/quant/api/runner/research-datasets/{JOB}/';query='?datasetRoot='+reader.dataset_root
    task={'id':JOB,'leaseToken':LEASE,'jobKind':'forecast','dataSource':'ready_dataset','dataset':None,
          'strategy':strategy,**evidence,'sourceEvidence':evidence,'datasetInputUrl':prefix+'input',
          'resultTransport':{'format':'atlas.quant.financial_bundle','version':2}}
    registry=long_sources['registry']
    entries=[{'ref':key,'kind':json.loads(raw)['kind'],'sha256':sha(raw),'byteLength':len(raw),
              'url':prefix+'registry/'+key+query} for key,raw in registry.items()]
    meta={'job':{'id':JOB,'kind':'forecast'},**evidence,'sourceEvidence':evidence,
          'manifest':{'sha256':reader.dataset_root,'byteLength':len(reader.manifest_bytes),'url':prefix+'manifest'+query},
          'partUrlTemplate':prefix+'parts/{componentId}/{ordinal}'+query,
          'registry':{'count':len(entries),'totalBytes':sum(map(len,registry.values())),
                      'listUrl':prefix+'registry'+query+'&offset=0'},'limits':dict(LIMITS)}
    responses={prefix+'input':encode(meta),prefix+'manifest'+query:reader.manifest_bytes,
               prefix+'registry'+query+'&offset=0':encode({'items':entries,'total':len(entries),'offset':0,'nextOffset':None})}
    responses.update({prefix+'registry/'+key+query:raw for key,raw in registry.items()})
    responses.update({prefix+'parts/'+c['componentId']+'/'+str(p['ordinal'])+query:reader.part(c['componentId'],p['ordinal'])
                      for c in reader.manifest['components'] for p in c['parts']})
    return task,meta,responses,reader,registry


def prepare(tmp_path,wire,mutate=None,client_type=GraphResearchClient):
    task,meta,responses=deepcopy(wire[:3])
    if mutate:mutate(task,meta,responses)
    responses[task['datasetInputUrl']]=encode(meta)
    session=Session({url:Response(raw) for url,raw in responses.items()})
    completion=runner.CompletionSpool(config(tmp_path))
    task=client_type(config(tmp_path),session).prepare(task,completion,deadline=time.monotonic()+30,check=lambda:None)
    return task,completion,session


def test_exact_graph_transport_is_encrypted_bounded_and_never_legacy(tmp_path,graph_wire):
    job,completion,session=prepare(tmp_path,graph_wire)
    store=GraphResearchSpool.from_spool(completion,job)
    reader,registry=store.inputs(job)
    assert reader.verify_integrity()['transportVerified']
    assert registry==graph_wire[4]
    assert reader.manifest_bytes==graph_wire[3].manifest_bytes
    assert all(method=='GET' and kw['headers']['X-Dataset-Lease']==LEASE for method,_,kw in session.calls)
    assert all(p.suffix=='.enc' and b'cash_asset_share' not in p.read_bytes() for p in store.root.iterdir())
    legacy=ResearchDatasetSpool.from_spool(completion,job)
    assert store.root!=legacy.root and store.aad!=legacy.aad
    shutil.copyfile(store.root/'manifest.enc',legacy.root/'manifest.enc')
    with pytest.raises(runner.RunnerError):legacy.read('manifest')


@pytest.mark.parametrize('attack',['legacy_ref','legacy_profile','legacy_result','provider','bad_scope','bad_model',
    'execution','bad_root','bad_limits','redirect','registry_hash','part_bytes','registry_page'])
def test_graph_metadata_and_source_adversaries_never_get_complete_input_marker(tmp_path,graph_wire,attack):
    def mutate(task,meta,raw):
        if attack=='legacy_ref':task['datasetRef']['version']=2
        elif attack=='legacy_profile':task['admissionProfile']='financial_fundamental_auto_50_v1'
        elif attack=='legacy_result':task['resultTransport']['version']=1
        elif attack=='provider':task['providerAccess']={'secret':'forbidden'}
        elif attack=='bad_scope':task['strategy']['universe']['start']='20240201'
        elif attack=='bad_model':task['strategy']['model']['estimator']='ridge'
        elif attack=='execution':task['strategy']['execution']['enabled']=True
        elif attack=='bad_root':meta['manifest']['sha256']='0'*64
        elif attack=='bad_limits':meta['limits']['closureBytes']*=2
        elif attack=='redirect':meta['manifest']['url']='https://wrong.test/source'
        elif attack=='registry_hash':
            path=next(p for p in raw if '/registry/' in p);raw[path]+=b' '
        elif attack=='part_bytes':
            path=next(p for p in raw if '/parts/' in p);raw[path]+=b' '
        elif attack=='registry_page':
            path=meta['registry']['listUrl'];value=json.loads(raw[path]);value['items'][0]['url']='https://wrong.test/pin';raw[path]=encode(value)
    with pytest.raises((runner.RunnerError,ValueError)):prepare(tmp_path,graph_wire,mutate)
    root=tmp_path/'research'/'research-graph-inputs'
    assert not root.exists() or not list(root.glob('*/input.enc'))


def test_old_client_cannot_accept_graph_or_similarity_of_version(tmp_path,graph_wire):
    with pytest.raises((runner.RunnerError,ValueError)):
        prepare(tmp_path,graph_wire,client_type=ResearchDatasetClient)
    with pytest.raises((runner.RunnerError,ValueError)):
        prepare(tmp_path,graph_wire,lambda task,meta,raw:task['datasetRef'].update(version='3'))


@pytest.mark.parametrize('attack',['lease','workspace','format','profile','job'])
def test_encrypted_graph_inputs_are_bound_to_identity_and_profile(tmp_path,graph_wire,attack):
    job,completion,_=prepare(tmp_path,graph_wire);store=GraphResearchSpool.from_spool(completion,job)
    if attack=='lease':
        other=GraphResearchSpool.from_spool(completion,{**job,'leaseToken':'different'})
        shutil.copyfile(store.root/'input.enc',other.root/'input.enc')
        with pytest.raises(runner.RunnerError):other.read('input')
    elif attack=='workspace':
        other_completion=runner.CompletionSpool({**config(tmp_path/'other'),'api_base':'https://another.test/quant/api'})
        other=GraphResearchSpool.from_spool(other_completion,job)
        shutil.copyfile(store.root/'input.enc',other.root/'input.enc')
        with pytest.raises(runner.RunnerError):other.read('input')
    else:
        bad=deepcopy(job)
        if attack=='format':bad['datasetRef']['version']=2
        elif attack=='profile':bad['admissionProfile']='financial_fundamental_auto_50_v1'
        else:bad['id']='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        with pytest.raises((runner.RunnerError,ValueError)):store.inputs(bad)


def test_new_result_collection_registry_and_encryption_domain_are_separate(tmp_path):
    completion=runner.CompletionSpool(config(tmp_path));job={'id':JOB,'leaseToken':LEASE}
    result=FinancialGraphBundleSpool.from_spool(completion,job)
    result.write_chunk('snapshotColumns',0,b'[]')
    assert result.read_chunk('snapshotColumns',0)==b'[]'
    for collection,ordinal in [('snapshotRows',0),('snapshotColumns',True),('snapshotColumns',256)]:
        with pytest.raises(ValueError):result.chunk_name(collection,ordinal)
    legacy=FinancialBundleSpool.from_spool(completion,job)
    result.write('manifest',b'{}')
    shutil.copyfile(result.root/'manifest.enc',legacy.root/'manifest.enc')
    with pytest.raises(runner.RunnerError):legacy.read('manifest')
    assert result.root!=legacy.root and result.aad!=legacy.aad
