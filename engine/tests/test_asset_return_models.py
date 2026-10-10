"""True single-output fits and legacy-safe engine integration."""
import copy
import json
import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits
from atlas_quant.fixtures import make_demo_data
from atlas_quant.engine import _prepare_data, run_research
from atlas_quant.statistical_quant.schema import validate, digest
from atlas_quant.statistical_quant.models import candidates, fit
from atlas_quant.statistical_quant.validation import _train, mature_mask, select
from atlas_quant.statistical_quant.return_study.samples import build_samples
from atlas_quant.statistical_quant.return_study.diagnostics import metrics


def config(estimator='ridge', mode='forecast', normalization=None):
    return validate({'schemaVersion': 2, 'name': 'Independent return study',
        'universe': {'symbols': ['000001.SZ', '600000.SH'], 'start': '20230102', 'end': '20241231'},
        'research': {'mode': 'statistical_quant', 'returnStudy': {'schema': 'asset-return-study/1', 'mode': mode}},
        'factors': [{'id': 'volume', 'expression': 'vol', 'direction': 1, 'role': 'predictor'},
                    {'id': 'turnover', 'expression': 'amount', 'direction': 1, 'role': 'predictor'}],
        'preprocess': {'automatic': {'schema': 'auto-factor-preprocess/2'}},
        'target': {'kind': 'asset_return', 'horizonSessions': 5, 'normalization': normalization or {'kind': 'none'}},
        'model': {'family': 'mean_reversion', 'estimator': estimator, 'parameterSharing': 'per_target', 'refitDays': 126,
                  'search': {'schema': 'factor-model-search/1'}},
        'validation': {'minTrainDates': 40}, 'execution': {'enabled': False}})


def real_fit(estimator='ridge'):
    from dataclasses import replace
    s = config(estimator)
    data, provenance = make_demo_data(s)
    panel, dates, audit = _prepare_data(data, s, provenance)
    samples = build_samples(panel, dates, s)
    target = next(iter(samples.definitions))
    idx = samples.meta.index[samples.meta.targetId == target]
    scoped = replace(samples, X=samples.X.loc[idx], y=samples.y.loc[idx], meta=samples.meta.loc[idx],
                     definitions={target: samples.definitions[target]})
    s['universe']['symbols'] = samples.definitions[target]['symbols']
    with threadpool_limits(limits=1):
        fitted, audit = _train(scoped, candidates(estimator, s['model']['search'])[0], None, dates[350], s)
    return fitted, audit, s, scoped


@pytest.mark.parametrize('estimator', list(dict.fromkeys(spec['estimator'] for spec in candidates('auto', {}))))
def test_all_registered_model_families_are_true_single_output(estimator):
    fitted, audit, strategy, samples = real_fit(estimator)
    actual = fitted.predict(samples.X.iloc[-20:])
    assert actual.shape == (20, 1)
    assert audit['outputs'] == ['asset_return']
    if 'coefficients' in audit:
        assert len(audit['coefficients']) == len(audit['intercepts']) == 1
    if 'constantPrediction' in audit:
        assert len(audit['constantPrediction']) == 1
    assert audit['returnInputDescriptors'] == samples.automatic_preprocessing['factors']
    assert audit['labelEndMax'] < audit['informationCutoff']


def test_scalar_metrics_are_response_metrics_not_entry_exit():
    actual = metrics([[1], [3], [5]], [[2], [4], [6]], unit='asset_return')
    assert actual['mse'] == 1 and actual['bias'] == -1
    assert actual['rSquared'] == pytest.approx(1-1/np.var([2,4,6]))
    assert 'entryRmse' not in actual and actual['observations'] == 3


def test_legacy_fit_rejects_one_output_without_explicit_protocol():
    with pytest.raises(ValueError):
        fit(candidates('ridge')[0], pd.DataFrame({'x': range(20)}), pd.DataFrame({'response': range(20)}), {})


def test_complete_engine_report_uses_independent_asset_models():
    strategy = config()
    strategy['universe']['symbols'] = ['000001.SZ', '000568.SZ', '600000.SH']
    data, provenance = make_demo_data(strategy)
    plans = []
    with threadpool_limits(limits=1):
        result = run_research(strategy, data, provenance, forecast_plan_sink=plans.append)
    artifact = result['forecasts']
    assert artifact['studyProtocol'] == 'asset-return-study/1'
    assert result['metrics'] is None and result['trades'] == [] and not result['execution']['enabled']
    assert digest({k:v for k,v in artifact.items() if k != 'artifactId'}) == artifact['artifactId']
    assert len(plans) == 1 and not plans[0]['baselineRequired']
    panel = artifact['factorResearch']['panel']
    assert panel['complete'] and panel['rowCount'] == len(panel['dates'])*3 == len(data)
    assert len(panel['rows']) == panel['rowCount']
    assert all(set(row['features']) == {'factor:volume', 'factor:turnover'} for row in panel['rows'])
    models = artifact['diagnostics']['assetModels']
    assert len(models) == 3 and len({x['modelFitId'] for x in models}) == 3
    assert [r['targetId'] for r in artifact['rows'][:3]] != sorted(r['targetId'] for r in artifact['rows'][:3])
    assert [(x['date'],x['targetId']) for x in plans[0]['origins']] == [(x['date'],x['targetId']) for x in artifact['rows']]
    for row in artifact['rows']:
        assert row['schema'] == 'asset-return-observation/1'
        assert 'expectedEntry' not in row
        if row['status'] == 'valid':
            assert row['observedResponse'] == row['observedReturn']
            assert row['predictedResponse'] == row['predictedReturn']
            assert row['responseResidual'] == pytest.approx(row['observedResponse']-row['predictedResponse'])
    for fit in artifact['modelFits']:
        assert fit['functionArtifact']['outputs'] == ['asset_return']
        assert len(fit['functionArtifact']['scope']['symbols']) == 1
    for item in artifact['diagnostics']['perTarget']:
        assert item['factorDiagnostics']['dependence']['featureNames'] == ['factor:volume','factor:turnover']
        assert all(f['ic']['status'] == 'unavailable' for f in item['factorDiagnostics']['features'])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('mode', ['forecast', 'association'])
def test_later_prices_and_factors_cannot_change_training_transform_or_equation(mode):
    from dataclasses import replace
    from atlas_quant.statistical_quant.return_study.contract import NORMALIZATION
    s = config('ridge', mode, NORMALIZATION)
    data, provenance = make_demo_data(s)
    panel, dates, _ = _prepare_data(data, s, provenance)
    cutoff = dates[350]
    changed = panel.copy()
    future = changed.index.get_level_values('trade_date') >= cutoff
    changed.loc[future, 'close'] *= 1000
    changed.loc[future, 'vol'] *= 100
    changed.loc[future, 'amount'] *= 100
    fits = []
    for source in [panel, changed]:
        samples = build_samples(source, dates, s)
        target = next(iter(samples.definitions))
        idx = samples.meta.index[samples.meta.targetId == target]
        scoped = replace(samples, X=samples.X.loc[idx], y=samples.y.loc[idx], meta=samples.meta.loc[idx], definitions={target:samples.definitions[target]})
        with threadpool_limits(limits=1):
            fitted, audit = _train(scoped, candidates('ridge')[0], None, cutoff, s)
        fits.append((fitted, audit, scoped))
    assert fits[0][1] == fits[1][1]
    mask = mature_mask(fits[0][2], cutoff, window=s['model']['trainWindow'])
    np.testing.assert_array_equal(fits[0][0].predict(fits[0][2].X.loc[mask]), fits[1][0].predict(fits[1][2].X.loc[mask]))
    assert fits[0][1]['labelEndMax'] < cutoff


def test_scalar_selection_reviewer_receives_only_development_evidence():
    from dataclasses import replace
    model, audit, s, samples = real_fit('ridge')
    cutoff = samples.dates[350]
    development = sorted(samples.meta.loc[mature_mask(samples, cutoff), 'date'].unique())
    packets = []
    class Recorder:
        enabled = True
        def before_fit(self,*args): pass
        def after_fit(self): pass
        def review_candidates(self, packet):
            packets.append(copy.deepcopy(packet))
            return {'candidateId':packet['defaultCandidateId'], 'receipt':{'status':'reviewed'}}
    with threadpool_limits(limits=1):
        winner, trials = select(samples,candidates('ridge',s['model']['search']),development,s,Recorder(),review=True)
    assert len(packets) == 1
    packet = packets[0]
    assert packet['target'] == 'equal_date_single_asset_response_mse'
    assert packet['outerOrTerminalDataIncluded'] is False and packet['developmentEnd'] < cutoff
    for trial in packet['trials']:
        for fold in trial['folds']:
            assert fold['labelEndMax'] < fold['testStart'] <= fold['testEnd'] < cutoff
            assert fold['outputs'] == ['asset_return']


def test_declared_runtime_resource_failure_is_not_a_trial_rejection():
    from atlas_quant.statistical_quant.return_study.research import Runtime
    from atlas_quant.engine import ResearchError
    runtime=Runtime(1)
    runtime.before_fit('a','20240101',100)
    with pytest.raises(ResearchError,match='拟合次数') as caught:
        runtime.before_fit('a','20240101',100)
    assert caught.value.code == 'CAPACITY_FITS'
