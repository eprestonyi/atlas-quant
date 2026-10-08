"""Versioned financial F auto admission never changes dataset source semantics."""
from copy import deepcopy
import pytest
from atlas_quant.research_dataset.research_profile import AUTO_PROFILE, SOURCE_PROFILES, admit_profile, validate_research_profile
from atlas_quant.research_dataset.codec import encode
from atlas_quant.financial_bundle import validate_source_evidence, FinancialBundleReader
from atlas_quant.bundle import encode as manifest_encode
from atlas_quant.financial_bundle_spool import FinancialBundleSpool
from atlas_quant.runner_claims import claim_request
from atlas_quant import runner
from test_research_dataset_runner import frozen, prepare
from test_research_dataset_components import sources, long_sources
from test_snapshot_market_view import legacy_source


def auto(frozen):
    task, meta, raw = deepcopy(frozen[:3])
    task['strategy']['model']['estimator'] = 'auto'
    task['admissionProfile'] = meta['admissionProfile'] = AUTO_PROFILE
    task['sourceEvidence']['admissionProfile'] = meta['sourceEvidence']['admissionProfile'] = AUTO_PROFILE
    return (task,meta,raw,*frozen[3:])


def test_auto_requires_explicit_new_profile_and_original_composition_stays_v2(frozen):
    task,_,_,reader,_ = auto(frozen)
    scope = reader.manifest['scope']
    assert reader.manifest['profile'] == SOURCE_PROFILES[2]
    assert validate_research_profile(task['strategy'],scope,research_profile=AUTO_PROFILE,dataset_version=2)['model']['estimator'] == 'auto'
    with pytest.raises(ValueError): validate_research_profile(task['strategy'],scope)
    for profile, version in [(AUTO_PROFILE,1),('unknown',2),(SOURCE_PROFILES[1],2)]:
        with pytest.raises(ValueError): admit_profile(profile,version)
    for mutate in [lambda s:s['execution'].update(enabled=True),lambda s:s['model'].update(estimator='ridge'),lambda s:s['model'].update(refitDays=1),lambda s:s['model'].update(family='trend'),lambda s:s['validation'].update(innerFolds=3),lambda s:s['universe'].update(start='20230101')]:
        bad=deepcopy(task['strategy']);mutate(bad)
        with pytest.raises(ValueError): validate_research_profile(bad,scope,research_profile=AUTO_PROFILE,dataset_version=2)
    bad=deepcopy(task['sourceEvidence']);bad['admissionProfile']=None
    with pytest.raises(ValueError): validate_source_evidence(bad)
    capabilities=claim_request(task['id'],financial_datasets=True)
    assert AUTO_PROFILE in capabilities['financialResearchProfiles']
    assert 'financialResearchProfiles' not in claim_request(task['id'])


def test_auto_real_spawn_preserves_full_source_baseline_and_callable_F(tmp_path,frozen):
    source=auto(frozen)
    job,spool,session=prepare(tmp_path,source)
    context=FinancialBundleSpool.context_for(spool,job)
    answer=runner.execute_bounded(job,timeout=60,bundle_context=context)
    assert 'error' not in answer,answer
    bundle=FinancialBundleSpool(context).reader(answer['bundleId'])
    report=bundle.document('report')
    assert bundle.verify_integrity()['transportVerified']
    assert bundle.manifest['sourceEvidence']['admissionProfile'] == AUTO_PROFILE
    restored=bundle.restore_sources(source[3],source[4])
    assert encode(restored.to_dataset()) == source[3].payload('researchRows')
    forecast=report['forecasts'];audit=forecast['diagnostics']['selectionAudit']
    assert audit['candidateCount']==8 and audit['researchFitBudget']['branches']==2
    assert len(forecast['rows'])==len(forecast['diagnostics']['factorIncrement']['baselineRows'])
    assert forecast['factorResearch']['modelFunctions']
    assert report['trades']==[] and report['metrics'] is None
    assert all('provider' not in path for _,path,_ in session.calls)
    # Merely rehashing the header cannot relabel an auto F as legacy Ridge or v1.
    for mutate in [lambda m:m['sourceEvidence'].update(admissionProfile=SOURCE_PROFILES[2]),
                   lambda m:m['sourceEvidence']['datasetRef'].update(version=1)]:
        bad=bundle.manifest;mutate(bad)
        with pytest.raises(ValueError):
            FinancialBundleReader(manifest_encode(bad),bundle.read_chunk).verify_integrity()
