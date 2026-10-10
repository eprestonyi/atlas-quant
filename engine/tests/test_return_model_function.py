"""Actual scalar fits must reproduce their predictions in Python and JavaScript."""
import copy
import json
import shutil
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits
from atlas_quant.statistical_quant.models import candidates
from atlas_quant.statistical_quant.model_function import export_function, predict_function, validate_function, edit_function, _seal
from test_asset_return_models import real_fit, config

FAMILIES = list(dict.fromkeys(spec['estimator'] for spec in candidates('auto', {})))
ROOT = Path(__file__).resolve().parents[2]


def case(estimator):
    model, audit, strategy, samples = real_fit(estimator)
    artifact = export_function(model, audit, strategy)
    frame = samples.X[model.columns].iloc[-12:].copy()
    frame.iloc[0, :] = np.nan
    frame.iloc[1, 0] = 1e15
    rows = [{k: None if pd.isna(v) else float(v) for k, v in row.items()} for row in frame.to_dict(orient='records')]
    with threadpool_limits(limits=1):
        expected = model.predict(frame)[:, 0]
    return artifact, rows, expected, audit, strategy


@pytest.mark.parametrize('estimator', FAMILIES)
def test_actual_scalar_fit_reconstructs_numerically_in_both_runtimes(estimator):
    artifact, rows, expected, _, _ = case(estimator)
    args = {'rows': rows, 'mode': 'forecast', 'originPrice': [100.]*len(rows)}
    result = predict_function(artifact, rows, mode='forecast', origin_price=args['originPrice'])
    np.testing.assert_allclose(result['predictedResponse'], expected, rtol=1e-12, atol=1e-14)
    np.testing.assert_allclose(result['conditionalPrices'], 100*(1+expected), rtol=1e-12, atol=1e-14)
    assert 'normalizedChanges' not in result and len(artifact['outputs']) == 1
    node = shutil.which('node')
    if node:
        script = "import {evaluateFunction} from './web/model-function-runtime.js'; let s=''; for await(const x of process.stdin)s+=x; const v=JSON.parse(s); console.log(JSON.stringify(await evaluateFunction(v.artifact,v.input)));"
        completed = subprocess.run([node, '--input-type=module', '-e', script], cwd=ROOT,
            input=json.dumps({'artifact': artifact, 'input': args}), text=True, capture_output=True, check=True)
        js = json.loads(completed.stdout)
        np.testing.assert_allclose(js['predictedResponse'], expected, rtol=1e-12, atol=1e-14)
        assert js['artifactId'] == artifact['artifactId']


def test_return_function_rejects_wrong_mode_legacy_context_and_hidden_features():
    artifact, rows, _, _, _ = case('ridge')
    for args in ({}, {'mode': 'association'}, {'mode': 'future_scenario', 'origin_price': [100.]*len(rows)},
                 {'mode': 'forecast', 'current_state': [100.]*len(rows), 'scale': [100.]*len(rows)},
                 {'mode': 'forecast', 'origin_volatility': [.02]*len(rows)}):
        with pytest.raises(ValueError, match='INVALID_MODEL_FUNCTION'):
            predict_function(artifact, rows, **args)
    for mutate in (lambda a: a['inputSchema'][0].update(name='state_deviation20'),
                   lambda a: a['featureConstruction']['inputs'][0]['timing'].update(kind='matched_period'),
                   lambda a: a['estimator']['coefficients'].append([0.]*len(a['inputSchema'])),
                   lambda a: a['scope']['symbols'].append('600000.SH')):
        changed = copy.deepcopy(artifact); mutate(changed)
        with pytest.raises(ValueError, match='INVALID_MODEL_FUNCTION'):
            validate_function(_seal(changed))


def test_single_output_edits_do_not_invent_an_entry_equation():
    artifact, rows, _, _, _ = case('ridge')
    edited = edit_function(artifact, [{'path': '/estimator/intercepts/0', 'value': .123}])
    assert edited['lineage']['status'] == 'UNVALIDATED_USER_EDIT'
    assert artifact['artifactId'] != edited['artifactId']
    with pytest.raises(ValueError, match='outside artifact'):
        edit_function(artifact, [{'path': '/estimator/intercepts/1', 'value': .123}])
    original = predict_function(artifact, rows, mode='forecast')['predictedResponse']
    derived = predict_function(edited, rows, mode='forecast')['predictedResponse']
    np.testing.assert_allclose(np.asarray(derived)-original, .123-artifact['estimator']['intercepts'][0])


def mode_case(estimator, mode, normalized, horizon=5):
    """Fit the declared mode/target itself; never relabel a forecast artifact."""
    from dataclasses import replace
    from atlas_quant.fixtures import make_demo_data
    from atlas_quant.engine import _prepare_data
    from atlas_quant.statistical_quant.schema import validate
    from atlas_quant.statistical_quant.return_study.samples import build_samples
    from atlas_quant.statistical_quant.validation import _train
    normalization = {'kind': 'trailing_volatility', 'windowSessions': 20, 'ddof': 1, 'horizonScale': 'sqrt_h', 'minimum': 1e-8} if normalized else {'kind': 'none'}
    strategy = config(estimator, mode, normalization)
    strategy['target']['horizonSessions'] = horizon
    strategy = validate(strategy)
    data, provenance = make_demo_data(strategy)
    panel, dates, _ = _prepare_data(data, strategy, provenance)
    samples = build_samples(panel, dates, strategy)
    target = next(iter(samples.definitions)); idx = samples.meta.index[samples.meta.targetId == target]
    scoped = replace(samples, X=samples.X.loc[idx], y=samples.y.loc[idx], meta=samples.meta.loc[idx], definitions={target:samples.definitions[target]})
    strategy['universe']['symbols'] = samples.definitions[target]['symbols']
    with threadpool_limits(limits=1):
        model, audit = _train(scoped, candidates(estimator, strategy['model']['search'])[0], None, dates[350], strategy)
    artifact = export_function(model, audit, strategy)
    frame = scoped.X[model.columns].iloc[-8:].copy()
    frame.iloc[0,:] = np.nan; frame.iloc[1,0] = 1e15
    rows = [{k:None if pd.isna(v) else float(v) for k,v in row.items()} for row in frame.to_dict(orient='records')]
    with threadpool_limits(limits=1):
        expected = model.predict(frame)[:,0].tolist()
    inputs = [{'rows':rows,'mode':mode,'originPrice':[100.+i for i in range(len(rows))],**({'originVolatility':[.01+.001*i for i in range(len(rows))]} if normalized else {})}]
    if mode == 'association':
        inputs.append({**copy.deepcopy(inputs[0]),'mode':'future_scenario'})
    results = [predict_function(artifact, rows, mode=item['mode'], origin_price=item['originPrice'], origin_volatility=item.get('originVolatility')) for item in inputs]
    return {'name':f'{estimator}-{mode}-{normalization["kind"]}-h{horizon}', 'artifact':artifact,
            'inputs':inputs,'expected':results,'fittedPrediction':expected,'fitAudit':audit,'strategy':strategy}


@pytest.mark.parametrize('mode,normalized',[('forecast',True),('association',False),('association',True)])
@pytest.mark.parametrize('estimator',['no_change','ridge','transformed_ridge','hist_gradient_boosting'])
def test_actual_fitted_return_modes_restore_declared_response_units(estimator,mode,normalized):
    case = mode_case(estimator,mode,normalized)
    for item,result in zip(case['inputs'],case['expected']):
        np.testing.assert_allclose(result['predictedResponse'],case['fittedPrediction'],rtol=1e-12,atol=1e-14)
        scale = np.asarray(item['originVolatility'])*np.sqrt(5) if normalized else np.ones(len(case['fittedPrediction']))
        returns = np.asarray(case['fittedPrediction'])*scale
        np.testing.assert_allclose(result['simpleReturns'],returns,rtol=1e-12,atol=1e-14)
        np.testing.assert_allclose(result['conditionalPrices'],np.asarray(item['originPrice'])*(1+returns),rtol=1e-12,atol=1e-14)
        assert result['scenarioOnly'] == (item['mode'] == 'future_scenario')
        assert result['evidenceStatus'] == 'fitted'


def test_association_function_refuses_missing_context_or_mislabelled_mode():
    case = mode_case('ridge','association',True)
    artifact, rows = case['artifact'], case['inputs'][0]['rows']
    for args in ({'mode':'forecast'}, {'mode':'future_scenario'}, {'mode':'future_scenario','origin_price':[100.]*8},
                 {'mode':'association','origin_price':[100.]*8}, {'mode':'association','origin_volatility':[1e-8]*8}):
        with pytest.raises(ValueError,match='INVALID_MODEL_FUNCTION'):
            predict_function(artifact,rows,**args)
    for mutate in (lambda a:a['featureConstruction']['targetSpecification']['normalization'].update(ddof=True),
                   lambda a:a['featureConstruction']['inputs'][0]['timing'].update(horizonSessions=True),
                   lambda a:a['featureConstruction']['inputs'][0].update(aggregation='signed_origin_dollar_over_gross'),
                   lambda a:a['outputs'].__setitem__(0,'asset_return')):
        changed = copy.deepcopy(artifact); mutate(changed)
        with pytest.raises(ValueError,match='INVALID_MODEL_FUNCTION'):
            validate_function(_seal(changed))


def write_mode_golden(path):
    cases = [mode_case(estimator,mode,normalized) for mode,normalized in [('forecast',True),('association',False),('association',True)] for estimator in FAMILIES]
    cases.append(mode_case('ridge','association',True,252))
    with Path(path).open('x') as output:
        json.dump({'schema':'atlas-model-function-return-modes-golden/1','evidence':'actual_synthetic_single_output_fits','cases':cases},output,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        output.write('\n')
