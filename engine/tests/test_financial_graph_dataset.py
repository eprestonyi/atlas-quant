"""Fresh graph source closure: numerical identity is distinct from model admission."""
from copy import deepcopy
from dataclasses import replace
import json
import pandas as pd
import pytest

from atlas_quant.research_dataset import DatasetReader,DatasetProfile,restore_dataset
from atlas_quant.research_dataset.codec import encode,sha
from atlas_quant.research_dataset.graph_v3.dataset import (compose_graph_dataset_components,GraphDatasetReader,
    restore_graph_dataset,verify_numeric_envelope)
from atlas_quant.research_dataset.graph_v3.manifest import validate_manifest,component_root
from atlas_quant.financial_statements.admission import assert_composed
from atlas_quant.engine import ResearchError
from test_research_dataset_components import sources,strategy
from test_snapshot_market_view import legacy_source,derive,publication


def build(view,sources,**kwargs):
    parts={}
    pub=compose_graph_dataset_components(view,[sources['source']],sources['registry'],
        lambda c,n,b:parts.__setitem__((c,n),b),market_calendar_ref=sources['calendar'],**kwargs)
    reader=GraphDatasetReader(pub.manifest_bytes,lambda c,n:parts[c,n],expected_root=pub.dataset_root,**kwargs)
    return pub,parts,reader


@pytest.fixture(scope='module')
def graph_source(sources,legacy_source):
    view=derive(legacy_source,sources['scope'])
    return view,build(view,sources),publication(view,sources)


def test_graph_fresh_recomposition_preserves_all_old_logical_inputs_but_does_not_admit_model(graph_source,sources):
    view,(new,parts,reader),(old,_,oldreader)=graph_source
    assert new.dataset_root!=old.dataset_root
    assert encode(new.result.to_dataset())==oldreader.payload('researchRows')
    assert reader.payload('schema')==oldreader.payload('schema')
    assert reader.payload('coverage')==oldreader.payload('coverage')
    assert reader.payload('financialInput0')==oldreader.payload('financialInput0')
    assert reader.payload('marketOrigin')==oldreader.payload('marketOrigin')
    assert new.result.model_admission_registered is False
    transport=reader.verify_integrity()
    assert transport['sourceAuthorityVerified'] is False
    restored=restore_graph_dataset(reader,sources['registry'])
    pd.testing.assert_frame_equal(restored.data,old.result.data,check_exact=True)
    assert encode(restored.provenance)==encode(old.result.provenance)
    assert restored.logical_joined=={'sha256':sha(oldreader.payload('researchRows')),'byteLength':len(oldreader.payload('researchRows'))}
    with pytest.raises(ResearchError,match='重新 compose'):
        assert_composed(restored.data,restored.data.copy(),restored.provenance,set(restored.data.columns),strategy(sources))
    with pytest.raises(ValueError):DatasetReader(new.manifest_bytes,lambda c,n:parts[c,n])
    with pytest.raises(ValueError):restore_dataset(reader,sources['registry'])
    with pytest.raises(ValueError):GraphDatasetReader(old.manifest_bytes,lambda c,n:parts[c,n])


def test_registry_authority_never_inferred_from_self_consistent_archive(graph_source,sources):
    reader=graph_source[1][2]
    for registry in [{},{**sources['registry'],'00000000-0000-0000-0000-000000000099':b'{}'},
                     {k:b'{}' for k in sources['registry']}]:
        with pytest.raises(ValueError,match='independent registry'):restore_graph_dataset(reader,registry)


def test_physical_and_logical_guards_fail_before_staging_and_immutable_reader(graph_source,sources):
    view,(pub,parts,reader),_=graph_source
    for profile in [replace(DatasetProfile(),total_bytes=150000),replace(DatasetProfile(),joined_bytes=2000),
                    replace(DatasetProfile(),max_parts=1),replace(DatasetProfile(),package_bytes=10)]:
        written=[]
        with pytest.raises(ValueError):compose_graph_dataset_components(view,[sources['source']],sources['registry'],
            lambda *args:written.append(args),market_calendar_ref=sources['calendar'],profile=profile)
        assert written==[]
    edited=reader.manifest;edited['scope']['symbols'].clear()
    assert reader.manifest['scope']['symbols']
    broken=dict(parts);broken['financialGraph0',0]=broken['financialGraph0',0]+b' '
    with pytest.raises(ValueError):GraphDatasetReader(pub.manifest_bytes,lambda c,n:broken[c,n]).verify_integrity()


def repackage(pub,parts,name,payload):
    manifest=json.loads(pub.manifest_bytes);parts=deepcopy(parts);changed={}
    for item in manifest['components']:
        old=item['componentRoot'];item['dependencies']=sorted(changed.get(x,x) for x in item['dependencies'])
        if item['componentId']==name:
            raw=encode(payload);parts[name,0]=raw
            for key in [k for k in parts if k[0]==name and k[1]>0]:del parts[key]
            item.update(payloadSha256=sha(raw),byteLength=len(raw),parts=[{'ordinal':0,'byteLength':len(raw),'sha256':sha(raw)}])
        item['componentRoot']=component_root(item);changed[old]=item['componentRoot']
    return GraphDatasetReader(encode(manifest),lambda c,n:parts[c,n])


def test_self_rehashed_coverage_cannot_replace_fresh_raw_source_computation(graph_source,sources):
    pub,parts,reader=graph_source[1];coverage=json.loads(reader.payload('coverage'))
    coverage['financial'][0]['completeHistoricalVersionsVerified']=True
    forged=repackage(pub,parts,'coverage',coverage)
    assert forged.verify_integrity()['sourceAuthorityVerified'] is False
    with pytest.raises(ValueError,match='Fresh source-derived'):restore_graph_dataset(forged,sources['registry'])


@pytest.mark.parametrize('mutation',['rows','provenance','size','bool_version'])
def test_entire_joined_document_is_checked_not_just_numeric_self_hash(graph_source,mutation):
    value=json.loads(graph_source[1][2].payload('researchColumns'))
    if mutation=='rows':value['numericInput']['columns'][0]['dictionary'][0]='changed'
    if mutation=='provenance':value['provenance']['synthetic']=False
    if mutation=='size':value['logicalJoined']['byteLength']-=1
    if mutation=='bool_version':value['schemaVersion']=True
    with pytest.raises(ValueError):verify_numeric_envelope(value)
