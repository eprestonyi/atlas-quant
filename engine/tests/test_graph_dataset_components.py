"""Provider-free composition transport adapters; no F and no hosted claim."""
from copy import deepcopy
import shutil
import pytest

from atlas_quant.runner import RunnerError
from atlas_quant.dataset_runner.client import DatasetClient
from atlas_quant.dataset_runner.publication import compute_publication as legacy_compute
from atlas_quant.dataset_runner.spool import DatasetSpool
from atlas_quant.graph_dataset_runner.client import GraphDatasetClient
from atlas_quant.graph_dataset_runner.spool import GraphDatasetSpool
from atlas_quant.graph_dataset_runner.publication import compute_publication
from atlas_quant.graph_dataset_runner.protocol import PROFILE,JOB_KIND
from atlas_quant.dataset_runner.protocol import encode
from atlas_quant.research_dataset.graph_v3.dataset import GraphDatasetReader,restore_graph_dataset
from atlas_quant.research_dataset.graph_v3.manifest import validate_manifest
from atlas_quant.research_dataset.manifest import validate_manifest as legacy_manifest
from dataset_runner_support import config,inputs,job
from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source


def configuration(tmp_path):
    return {**config(tmp_path),'graph_dataset_delivery_dir':str(tmp_path/'graph-composition')}


def graph_inputs(sources,legacy_source,task):
    data=inputs(sources,legacy_source,task)
    def rewrite(value):
        if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
        if isinstance(value,list):return [rewrite(v) for v in value]
        if isinstance(value,str):return value.replace('/runner/datasets/','/runner/dataset-graphs/')
        return value
    meta=rewrite(data[0]);meta['plan']['profile']=PROFILE
    return (meta,*data[1:])


def test_graph_composition_publication_is_new_format_and_exact_fresh_source(tmp_path,sources,legacy_source):
    task={**job(),'kind':JOB_KIND};data=graph_inputs(sources,legacy_source,task);settings=configuration(tmp_path)
    client=GraphDatasetClient(settings)
    assert client.source_plan(data[0],task)
    spool=GraphDatasetSpool(settings);publication=spool.publication(task)
    manifest=compute_publication(task,data,publication.write_chunk);publication.finalize(manifest)
    raw=encode(publication.manifest());validate_manifest(raw)
    descriptors={(c['componentId'],p['ordinal']):p for c in manifest['components'] for p in c['parts']}
    reader=GraphDatasetReader(raw,lambda c,n:publication.read_chunk(c,descriptors[c,n]))
    restored=restore_graph_dataset(reader,sources['registry'])
    assert restored.validation_report['sourceAuthorityVerified']
    assert restored.model_admission_registered is False
    assert 'researchColumns' in reader.components and 'researchRows' not in reader.components
    with pytest.raises(ValueError):legacy_manifest(raw)
    with pytest.raises((ValueError,RunnerError)):legacy_compute(task,data,lambda *_:pytest.fail('Legacy accepted graph'))
    with pytest.raises((ValueError,RunnerError)):DatasetClient(settings).source_plan(data[0],task)


@pytest.mark.parametrize('attack',['legacy_profile','unknown_profile','calendar_days','component','ordinal','overlap','nested'])
def test_graph_compose_adapters_fail_closed_without_source_or_result_mutation(tmp_path,sources,legacy_source,attack):
    task={**job(),'kind':JOB_KIND};data=list(graph_inputs(sources,legacy_source,task));settings=configuration(tmp_path)
    if attack in ('legacy_profile','unknown_profile','calendar_days'):
        data[0]=deepcopy(data[0])
        if attack=='legacy_profile':data[0]['plan']['profile']='financial_snapshot_view_50_v1'
        elif attack=='unknown_profile':data[0]['plan']['profile']=PROFILE+'x'
        else:data[0]['plan']['scope']['end']='20251231'
        with pytest.raises((ValueError,RunnerError)):GraphDatasetClient(settings).source_plan(data[0],task)
    elif attack in ('overlap','nested'):
        settings['graph_dataset_delivery_dir']=settings['dataset_delivery_dir']+('/nested' if attack=='nested' else '')
        with pytest.raises(RunnerError):GraphDatasetSpool(settings)
    else:
        publication=GraphDatasetSpool(settings).publication(task)
        with pytest.raises((ValueError,RunnerError)):
            publication.write_chunk('researchRows' if attack=='component' else 'researchColumns',True if attack=='ordinal' else 0,b'{}')
        assert not list(publication.root.iterdir())


def test_graph_composer_claim_encryption_is_distinct_from_legacy(tmp_path):
    settings=configuration(tmp_path);graph=GraphDatasetSpool(settings);legacy=DatasetSpool(settings)
    state=graph.current_or_create()
    shutil.copyfile(graph.root/'current.enc',legacy.root/'current.enc')
    with pytest.raises(RunnerError):legacy.read()
    assert graph.read()==state and graph.key!=legacy.key and graph.aad!=legacy.aad
