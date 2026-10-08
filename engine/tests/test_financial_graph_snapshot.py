"""New explicit local admission; old source codecs and admissions stay separate."""
from copy import deepcopy
from dataclasses import replace
import pytest

from atlas_quant.research_dataset.codec import encode
from atlas_quant.research_dataset.profile import DatasetProfile
from atlas_quant.research_dataset.snapshot import freeze_financial_input,restore_financial_input
from atlas_quant.research_dataset.research_profile import AUTO_PROFILE
from atlas_quant.research_dataset.graph_v3.dataset import restore_graph_dataset
from atlas_quant.research_dataset.graph_v3.snapshot import (RESEARCH_PROFILE,restore_graph_for_research,
    freeze_graph_input,restore_graph_input,validate_snapshot,validate_research_profile)
from atlas_quant.research_dataset.graph_v3.columns import iter_rows
from atlas_quant.financial_statements.admission import assert_composed
from atlas_quant.financial_statements.prepare import _safe_rows
from atlas_quant.engine import ResearchError
from test_research_dataset_components import sources,long_sources,strategy
from test_snapshot_market_view import legacy_source,derive,publication
from test_financial_graph_dataset import build


@pytest.fixture(scope='module')
def snapshots(long_sources,legacy_source):
    view=derive(legacy_source,long_sources['scope']);new,_,reader=build(view,long_sources)
    old,_,old_reader=publication(view,long_sources)
    config=strategy(long_sources);config['model'].update(estimator='auto',refitDays=20)
    config['validation']={'innerFolds':2,'outerFolds':2}
    ref={'datasetId':'00000000-0000-0000-0000-000000000033','datasetRoot':new.dataset_root,
         'format':'atlas.quant.research_dataset','version':3}
    admitted=restore_graph_for_research(config,reader,long_sources['registry'],research_profile=RESEARCH_PROFILE)
    snap=freeze_graph_input(config,admitted,ref,manifest_bytes=new.manifest_bytes,research_profile=RESEARCH_PROFILE)
    old_ref={**ref,'version':2,'datasetRoot':old.dataset_root}
    old_snap=freeze_financial_input(config,old.result,old_ref,manifest_bytes=old.manifest_bytes,research_profile=AUTO_PROFILE)
    return config,reader,snap,old_reader,old_snap,admitted


def test_thin_exact_snapshot_matches_actual_old_input_and_preserves_research_fingerprint(snapshots,long_sources):
    config,reader,snap,old_reader,old_snap,admitted=snapshots
    assert encode(list(iter_rows(snap['numericInput'])))==encode(old_snap['rows'])
    for key in ['provenance','dataFingerprint','sourceDataFingerprint','financialSourceCommitment']:
        assert encode(snap[key])==encode(old_snap[key])
    report=validate_snapshot(snap)
    assert report['logicalExpandedSnapshot']['byteLength']<24*1024**2
    assert report['physicalSnapshot']['byteLength']<report['logicalExpandedSnapshot']['byteLength']
    assert report['modelAdmissionRegistered'] is False
    assert admitted.model_admission_registered is True
    restored=restore_graph_input(config,encode(snap),reader,long_sources['registry'],research_profile=RESEARCH_PROFILE)
    assert encode(_safe_rows(restored.data))==encode(old_snap['rows'])
    assert_composed(restored.data,restored.data.copy(),restored.provenance,set(restored.data.columns),config)
    with pytest.raises(ValueError):restore_financial_input(config,encode(snap),old_reader,long_sources['registry'],research_profile=AUTO_PROFILE)
    with pytest.raises(ValueError):restore_graph_input(config,encode(old_snap),reader,long_sources['registry'],research_profile=RESEARCH_PROFILE)


@pytest.mark.parametrize('change',['profile','ridge','mechanism','execution','scope','folds','refit','version'])
def test_new_profile_is_explicit_and_separate_from_legacy_source_rules(snapshots,change):
    config,reader,*_=snapshots;config=deepcopy(config);profile=RESEARCH_PROFILE;version=3
    if change=='profile':profile=AUTO_PROFILE
    if change=='ridge':config['model']['estimator']='ridge'
    if change=='mechanism':config['model']['family']='mean_reversion'
    if change=='execution':config['execution']['enabled']=True
    if change=='scope':config['universe']['start']='20240102'
    if change=='folds':config['validation']['innerFolds']=3
    if change=='refit':config['model']['refitDays']=19
    if change=='version':version=2
    with pytest.raises(ValueError):validate_research_profile(config,reader.manifest['scope'],research_profile=profile,dataset_version=version)


def test_source_only_or_forged_admission_flag_cannot_freeze_input(snapshots,long_sources):
    config,reader,snap,*_=snapshots;source=restore_graph_dataset(reader,long_sources['registry'])
    with pytest.raises(ValueError):freeze_graph_input(config,source,snap['datasetRef'],manifest_bytes=reader.manifest_bytes,research_profile=RESEARCH_PROFILE)
    forged=replace(source,model_admission_registered=True)
    with pytest.raises(ResearchError,match='重新 compose'):
        freeze_graph_input(config,forged,snap['datasetRef'],manifest_bytes=reader.manifest_bytes,research_profile=RESEARCH_PROFILE)


@pytest.mark.parametrize('change',['input','provenance','commitment','root','profile','registry','logical_budget'])
def test_snapshot_changes_cannot_reuse_previous_same_process_admission(snapshots,long_sources,change):
    config,reader,snap,*_=snapshots;snap=deepcopy(snap);profile=RESEARCH_PROFILE;registry=long_sources['registry']
    if change=='input':
        column=next(c for c in snap['numericInput']['columns'] if c['kind']=='number');column['values'][0]=987654.
    if change=='provenance':snap['provenance']['synthetic']=False
    if change=='commitment':snap['financialSourceCommitment']['marketRoot']='0'*64
    if change=='root':snap['datasetRef']['datasetRoot']='0'*64
    if change=='profile':profile=AUTO_PROFILE
    if change=='registry':registry={}
    if change=='logical_budget':
        physical=len(encode(snap))
        with pytest.raises(ValueError):validate_snapshot(snap,profile=replace(DatasetProfile(),joined_bytes=physical+16))
        return
    with pytest.raises(ValueError):restore_graph_input(config,encode(snap),reader,registry,research_profile=profile)
