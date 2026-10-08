"""Actual small F parity and independent new-format transport boundary tests."""
from copy import deepcopy
import json
import pytest

from atlas_quant import bundle as legacy
from atlas_quant.engine import run_research
from atlas_quant.financial_bundle import FinancialBundleReader
from atlas_quant.financial_bundle_v2 import (FinancialGraphBundleReader,build_financial_graph_bundle,
    validate_manifest,export_financial_graph_bundle,financial_graph_directory_reader,COLLECTIONS)
from atlas_quant.research_dataset.codec import encode
from atlas_quant.research_dataset.graph_v3.snapshot import RESEARCH_PROFILE
from atlas_quant.research_dataset.graph_v3.archive import export_graph_dataset_archive,extract_graph_dataset_archive
from atlas_quant.research_dataset.archive import extract_dataset_archive
from atlas_quant.research_dataset.graph_v3.dataset import DirectoryGraphDatasetReader
from test_research_dataset_components import sources,long_sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_snapshot import snapshots


@pytest.fixture(scope='module')
def graph_research(snapshots,long_sources):
    config,reader,snap,oldreader,old_snap,admitted=snapshots
    plans=[];report=run_research(config,admitted.data,admitted.provenance,forecast_plan_sink=plans.append)
    from atlas_quant.research_dataset.reader import restore_dataset_for_research
    from atlas_quant.research_dataset.research_profile import AUTO_PROFILE
    previous=restore_dataset_for_research(config,oldreader,long_sources['registry'],research_profile=AUTO_PROFILE)
    prior_report=run_research(config,previous.data,previous.provenance)
    assert legacy.encode(report['forecasts'])==legacy.encode(prior_report['forecasts'])
    evidence={'datasetRef':snap['datasetRef'],'admissionProfile':RESEARCH_PROFILE}
    return report,encode(snap),plans[0],evidence,reader,long_sources['registry']


def pack(values,target=65536):
    report,snapshot,coverage,evidence,*_=values;chunks={}
    raw=build_financial_graph_bundle(report,snapshot,coverage,evidence,lambda c,n,b:chunks.__setitem__((c,n),b),
        lambda c,n:chunks[c,n],chunk_target=target)
    return raw,chunks,FinancialGraphBundleReader(raw,lambda c,n:chunks[c,n])


def test_actual_old_new_F_identical_and_new_snapshot_exact_transport(graph_research):
    raw,chunks,reader=pack(graph_research)
    assert reader.verify_integrity()['sourceEvidenceClosed'] is False
    assert reader.snapshot_bytes()==graph_research[1]
    assert reader.manifest['forecastArtifactId']==graph_research[0]['forecasts']['artifactId']
    assert set(reader.collections)==set(COLLECTIONS)
    assert all(len(x)<=legacy.CHUNK_LIMIT for x in chunks.values())
    restored=reader.restore_sources(graph_research[4],graph_research[5])
    assert restored.model_admission_registered is True
    assert reader.document('report')==graph_research[0]
    with pytest.raises(ValueError):FinancialBundleReader(raw,lambda c,n:chunks[c,n])
    with pytest.raises(ValueError):legacy.BundleReader(raw,lambda c,n:chunks[c,n])
    with pytest.raises(ValueError):reader.restore_sources(graph_research[4],{})
    assert pack(graph_research,32768)[2].snapshot_bytes()==reader.snapshot_bytes()


def test_source_archive_and_result_directory_have_distinct_closed_formats(graph_research,tmp_path):
    raw,chunks,reader=pack(graph_research)
    export_financial_graph_bundle(reader,tmp_path/'result')
    reopened=financial_graph_directory_reader(tmp_path/'result');assert reopened.verify_integrity()['verified']
    export_graph_dataset_archive(graph_research[4],tmp_path/'source.tar')
    report=extract_graph_dataset_archive(tmp_path/'source.tar',tmp_path/'source',expected_root=graph_research[4].dataset_root)
    assert report['transportVerified'] and report['sourceAuthorityVerified'] is False
    reopened.restore_sources(DirectoryGraphDatasetReader(tmp_path/'source'),graph_research[5])
    with pytest.raises(ValueError):extract_dataset_archive(tmp_path/'source.tar',tmp_path/'old')
    with pytest.raises(ValueError):export_graph_dataset_archive(graph_research[4],tmp_path/'source.tar')
    with pytest.raises(ValueError):extract_graph_dataset_archive(tmp_path/'source.tar',tmp_path/'source')
    (tmp_path/'result'/'extra').write_text('unregistered')
    with pytest.raises(ValueError):financial_graph_directory_reader(tmp_path/'result')


@pytest.mark.parametrize('change',['version','legacy_columns','codec','source_version','profile','chunk_path','dangling_collection'])
def test_transport_registry_is_explicit_not_legacy_monkeypatch(graph_research,change):
    raw,chunks,reader=pack(graph_research);m=json.loads(raw)
    if change=='version':m['version']=1
    if change=='legacy_columns':next(x for x in m['collections'] if x['id']=='snapshotColumns')['id']='snapshotRows'
    if change=='codec':m['documents']['snapshot']['codec']='financial_json_v1'
    if change=='source_version':m['sourceEvidence']['datasetRef']['version']=2
    if change=='profile':m['sourceEvidence']['admissionProfile']='financial_fundamental_auto_50_v1'
    if change=='chunk_path':m['collections'][0]['path']='/escape'
    if change=='dangling_collection':m['collections'].pop()
    with pytest.raises(ValueError):validate_manifest(legacy.encode(m))


def test_corrupt_numeric_chunk_or_false_source_reference_cannot_hide_behind_metadata(graph_research):
    raw,chunks,reader=pack(graph_research)
    chunks['snapshotColumns',0]=chunks['snapshotColumns',0].replace(b'1',b'2',1)
    with pytest.raises(ValueError):reader.verify_integrity()
    raw,chunks,_=pack(graph_research);m=json.loads(raw);m['sourceEvidence']['datasetRef']['datasetRoot']='0'*64
    with pytest.raises(ValueError):FinancialGraphBundleReader(legacy.encode(m),lambda c,n:chunks[c,n]).verify_integrity()


def test_retained_two_archive_fixture_for_independent_verifier(graph_research,tmp_path):
    from atlas_quant.financial_bundle_v2 import export_financial_graph_archive
    raw,chunks,reader=pack(graph_research)
    export_financial_graph_bundle(reader,tmp_path/'result')
    export_financial_graph_archive(reader,tmp_path/'financial.tar')
    export_graph_dataset_archive(graph_research[4],tmp_path/'dataset.tar')
    extract_graph_dataset_archive(tmp_path/'dataset.tar',tmp_path/'dataset')
    # These caller-authorized fixture pins come directly from the test's
    # original package registry argument, never from an uploaded source graph.
    (tmp_path/'caller-registry').mkdir(mode=0o700)
    pins={}
    for ref,raw in graph_research[5].items():
        path=tmp_path/'caller-registry'/f'{ref}.json';path.write_bytes(raw);path.chmod(0o600);pins[ref]=str(path)
    (tmp_path/'registry-pins.json').write_text(json.dumps(pins,sort_keys=True))
    assert reader.manifest['sourceEvidence']['datasetRef']['datasetRoot']==graph_research[4].dataset_root
    with pytest.raises(ValueError):export_financial_graph_archive(reader,tmp_path/'financial.tar')
