"""Independent index arithmetic for per-factor lags/leads, readiness and F5."""
import copy
import json
from pathlib import Path
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits
from atlas_quant.fixtures import make_demo_data
from atlas_quant.engine import _prepare_data, run_research
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.models import candidates
from atlas_quant.statistical_quant.validation import _train, mature_mask
from atlas_quant.statistical_quant.return_study.samples import build_samples, panel_document
from atlas_quant.statistical_quant.return_study.contract import input_descriptors
from atlas_quant.statistical_quant.model_function import export_function, predict_function, validate_function, _seal
from test_asset_return_models import config


def coordinate_strategy():
    strategy = config()
    strategy['research']['returnStudy'] = {'schema': 'asset-return-study/2'}
    strategy['model'].pop('search')
    strategy['preprocess']['decorrelation'] = 'none'
    strategy['factors'] = [dict(id=name, baseFactorId='momentum', expression='returns(close,20)',
        direction=1, role='predictor', timing={'anchor': 'target_origin', 'offsetSessions': offset})
        for name, offset in [('past', -3), ('now', 0), ('future', 10)]]
    return validate(strategy)


def sample_case(strategy=None):
    strategy = strategy or coordinate_strategy()
    data, provenance = make_demo_data(strategy)
    panel, dates, audit = _prepare_data(data, strategy, provenance)
    return strategy, panel, dates, build_samples(panel, dates, strategy), data, provenance


def scope(samples, strategy, symbol='000001.SZ'):
    target = next(key for key, value in samples.definitions.items() if value['symbols'] == [symbol])
    indices = samples.meta.index[samples.meta.targetId == target]
    config_ = copy.deepcopy(strategy); config_['universe']['symbols'] = [symbol]
    return replace(samples, X=samples.X.loc[indices], y=samples.y.loc[indices], meta=samples.meta.loc[indices],
                   definitions={target: samples.definitions[target]}), config_


def test_same_factor_instances_keep_distinct_offsets_without_rewriting_window():
    s, panel, dates, samples, *_ = sample_case()
    for item in input_descriptors(s):
        assert item['transform'] == {'kind': 'identity'}
        assert item['expression'] == 'returns(close,20)'
        assert item['baseFactorId'] == 'momentum'
    for t in (40, 100, 300):
        for symbol in s['universe']['symbols']:
            idx = samples.meta.index[(samples.meta.date == dates[t]) & (samples.meta.assetSymbol == symbol)][0]
            for name, k in [('past',-3),('now',0),('future',10)]:
                expected = panel.loc[(dates[t+k],symbol),'close']/panel.loc[(dates[t+k-20],symbol),'close']-1
                assert samples.X.loc[idx,'factor:'+name] == pytest.approx(expected, abs=1e-14)
            expected_y = panel.loc[(dates[t+5],symbol),'close']/panel.loc[(dates[t],symbol),'close']-1
            assert samples.y.loc[idx,'response'] == pytest.approx(expected_y)
            row = samples.meta.loc[idx]
            assert row.sampleReadyAt == dates[t+10] and row.inputAvailableAt == dates[t+10]
            assert row.availabilityClass == 'conditional_realized'
    assert len(panel_document(samples, s['universe']['symbols'])['rows']) == len(dates)*2


def test_coordinate_defaults_and_legacy_definitions_remain_distinct():
    s = coordinate_strategy()
    s['factors'] = [dict(id='raw', expression='vol', role='predictor', direction=1)]
    normalized = validate(s)
    assert normalized['factors'][0]['baseFactorId'] == 'raw'
    assert normalized['factors'][0]['timing'] == {'anchor':'target_origin','offsetSessions':0}
    legacy = config()
    assert all('baseFactorId' not in f and 'timing' not in f for f in legacy['factors'])
    assert input_descriptors(legacy)[0]['timing'] == {'kind':'origin_known','horizonSessions':5}


@pytest.mark.parametrize('k',[True,1.0,253,-253,None])
def test_offsets_are_bounded_strict_integers(k):
    s = coordinate_strategy(); s['factors'][0]['timing']['offsetSessions'] = k
    with pytest.raises(ValueError): validate(s)


def test_reject_model_mode_and_duplicate_instance_ids_not_duplicate_catalogue_ids():
    s = coordinate_strategy(); s['research']['returnStudy']['mode'] = 'forecast'
    with pytest.raises(ValueError): validate(s)
    s = coordinate_strategy(); s['factors'][1]['id'] = s['factors'][0]['id']
    with pytest.raises(ValueError): validate(s)


def test_positive_offset_tail_and_missing_lead_are_never_imputable():
    s, panel, dates, samples, *_ = sample_case()
    tails = samples.meta.date.isin(dates[-10:])
    assert not samples.meta.loc[tails,'inputValid'].any()
    assert set(samples.meta.loc[tails,'invalidReason']) == {'future_input_outside_available_calendar'}
    assert samples.meta.loc[tails,'inputAvailableAt'].isna().all()
    changed = panel.copy(); changed.loc[(dates[110],'000001.SZ'),'close'] = np.nan
    changed = build_samples(changed, dates, s)
    row = changed.meta[(changed.meta.date==dates[100]) & (changed.meta.assetSymbol=='000001.SZ')].iloc[0]
    assert not row.inputValid and row.invalidReason == 'future_input_unavailable'


def test_shift_uses_full_calendar_not_compressed_observed_asset_rows():
    s, panel, dates, _, *_ = sample_case()
    sparse = panel.drop((dates[110],'000001.SZ'))
    samples = build_samples(sparse, dates, s)
    row = samples.meta[(samples.meta.date==dates[100]) & (samples.meta.assetSymbol=='000001.SZ')].iloc[0]
    assert not row.inputValid
    assert np.isnan(samples.X.loc[row.name,'factor:future'])


def test_positive_factor_beyond_label_is_purged_at_every_training_cutoff():
    s, _, dates, samples, *_ = sample_case()
    mask = mature_mask(samples, dates[200])
    assert samples.meta.loc[mask,'date'].max() == dates[189]
    assert not mask[samples.meta.date == dates[190]].any()
    past_only = copy.deepcopy(s); past_only['factors'] = past_only['factors'][:1]
    past = sample_case(past_only)[3]
    assert past.meta.loc[mature_mask(past,dates[200]),'date'].max() == dates[194]


def test_all_lag_output_cutoff_cannot_predate_its_origin_or_fitted_model():
    from atlas_quant.statistical_quant.return_study.research import _record
    s = coordinate_strategy(); s['factors'] = s['factors'][:1]
    samples = sample_case(s)[3]
    row = next(row for row in samples.meta.itertuples() if row.inputValid and row.observedResponse == row.observedResponse)
    record = _record(row, .001, 'fitted_at_origin')
    assert row.inputAvailableAt < row.date
    assert record['informationCutoff'] == row.date+'_AFTER_CLOSE'


def test_financial_alignment_does_not_claim_verified_vintage_or_origin_knowledge():
    s,panel,dates,*_ = sample_case()
    s['factors'] = [dict(id='quality',expression='fd_roe',direction=1,role='predictor',
        baseFactorId='quality',timing={'anchor':'target_origin','offsetSessions':0})]
    panel['fd_roe'] = 10.
    samples = build_samples(panel,dates,s)
    row = samples.meta.iloc[100]
    evidence = row.inputEvidence['factor:quality']
    assert evidence['availableAt'] == row.date
    assert evidence['knownAtOrigin'] is None
    assert evidence['availabilityBasis'] == 'declared_data_alignment'
    assert row.availabilityClass == 'availability_unverified'


def test_native_source_processing_is_not_replaced_by_research_session_returns():
    from test_context_factor_clock import ALIAS
    from atlas_quant.context_factor_clock import NativeContextSeries
    s,panel,dates,*_ = sample_case()
    source_dates = tuple(dates[::2])
    prices = tuple(100.*1.01**i for i in range(len(source_dates)))
    panel[ALIAS] = 100.  # Numeric broadcast is deliberately not the return source.
    panel.attrs['atlas_context_source_series'] = NativeContextSeries(
        ((ALIAS,'yfinance_history','XLK',source_dates,prices),),'test-source-root')
    s['factors'] = [dict(id='foreign',expression=ALIAS,direction=1,role='predictor',baseFactorId='foreign',
        timing={'anchor':'target_origin','offsetSessions':1})]
    samples = build_samples(panel,dates,s)
    for t in (41,42,43):
        row = samples.meta[(samples.meta.date==dates[t]) & (samples.meta.assetSymbol=='000001.SZ')].iloc[0]
        assert samples.X.loc[row.name,'factor:foreign'] == pytest.approx(.01)
        evidence = row.inputEvidence['factor:foreign']
        assert evidence['evaluationDate'] == evidence['availableAt'] == dates[t+1]
        assert evidence['sourceDate'] == max(date for date in source_dates if date < dates[t+1])
        assert evidence['availabilityBasis'] == 'native_source_session_policy'


@pytest.mark.parametrize('expression', ['returns(close,5)', 'close/lag(close,5)-1',
    'delta(close,5)/lag(close,5)', 'delta(log(close),5)', 'log(close/lag(close,5))'])
def test_explicit_own_target_forms_are_labeled_but_not_blanket_forbidden(expression):
    from atlas_quant.statistical_quant.return_study.coordinates import target_relationships
    s = coordinate_strategy()
    s['factors'] = [dict(id='identity',expression=expression,direction=1,role='predictor',
        baseFactorId='identity',timing={'anchor':'target_origin','offsetSessions':5})]
    s = validate(s)
    relation = target_relationships(s,input_descriptors(s))['factor:identity']
    assert relation['classification'] == 'target_derived'
    assert relation['absenceProvesTargetIndependence'] is False


def actual_coordinate_fit(estimator='ridge'):
    s, _, dates, samples, *_ = sample_case()
    s['model']['estimator'] = estimator
    scoped, s = scope(samples,s)
    with threadpool_limits(limits=1):
        model, audit = _train(scoped, candidates(estimator)[0], None, dates[350], s)
    return model, audit, s, scoped


def test_future_perturbation_cannot_change_train_fold_transform_or_parameters():
    s, panel, dates, samples, *_ = sample_case()
    changed = panel.copy()
    changed.loc[changed.index.get_level_values('trade_date') >= dates[350],'close'] *= 1000
    other = build_samples(changed, dates,s)
    a,s = scope(samples,s); b,_ = scope(other,s)
    with threadpool_limits(limits=1):
        ma,aa = _train(a,candidates('ridge')[0],None,dates[350],s)
        mb,ab = _train(b,candidates('ridge')[0],None,dates[350],s)
    assert export_function(ma,aa,s) == export_function(mb,ab,s)
    assert aa['inputAvailableAtMax'] == aa['sampleReadyAtMax'] < aa['informationCutoff']


def test_actual_f5_portable_equation_and_caller_declared_evidence():
    model,audit,s,samples = actual_coordinate_fit()
    artifact = export_function(model,audit,s)
    assert artifact['schema'] == 'atlas-model-function/5'
    assert 'studyMode' not in artifact['scope']
    frame = samples.X[model.columns].iloc[100:103]
    rows = frame.to_dict('records'); dates = samples.meta.loc[frame.index,'date'].tolist()
    raw = predict_function(artifact,rows)
    np.testing.assert_allclose(raw['predictedResponse'],model.predict(frame)[:,0],atol=1e-14)
    assert raw['availabilityClasses'] == ['availability_unverified']*3
    evidence = [{key:{field:value[field] for field in ('valueOrigin','evaluationDate','availableAt')}
        for key,value in samples.meta.loc[i,'inputEvidence'].items() if key in model.columns} for i in frame.index]
    realized = predict_function(artifact,rows,origin_dates=dates,input_evidence=evidence,origin_price=[100.]*3)
    assert realized['availabilityClasses'] == ['conditional_realized']*3
    assert realized['inputEvidenceStatus'] == 'caller_declared'
    changed = copy.deepcopy(evidence)
    for item in changed: item['factor:future']['valueOrigin'] = 'scenario_assumption'
    scenario = predict_function(artifact,rows,origin_dates=dates,input_evidence=changed)
    assert scenario['availabilityClasses'] == ['conditional_scenario']*3
    assert scenario['predictedResponse'] == realized['predictedResponse']
    missing = copy.deepcopy(rows); missing[0]['factor:future'] = None
    with pytest.raises(ValueError,match='future input'): predict_function(artifact,missing)
    with pytest.raises(ValueError): predict_function(artifact,rows,mode='forecast')
    invalid = copy.deepcopy(artifact); invalid['training']['sampleReadyAtMax'] = invalid['training']['informationCutoff']
    with pytest.raises(ValueError): validate_function(_seal(invalid))


@pytest.mark.parametrize('estimator',['no_change','transformed_ridge','hist_gradient_boosting'])
def test_f5_scalar_dispatch_also_preserves_constant_basis_and_tree_numerics(estimator):
    model,audit,strategy,samples = actual_coordinate_fit(estimator)
    artifact = export_function(model,audit,strategy)
    frame = samples.X[model.columns].iloc[80:95]
    with threadpool_limits(limits=1):
        expected = model.predict(frame)[:,0]
    actual = predict_function(artifact,frame.to_dict('records'))
    np.testing.assert_allclose(actual['predictedResponse'],expected,atol=1e-14,rtol=1e-12)


@pytest.mark.parametrize('field',['originDates','inputEvidence'])
def test_cli_rejects_explicit_null_time_context_instead_of_treating_it_as_omitted(tmp_path,field):
    from atlas_quant.statistical_quant.model_function import main
    golden = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/model-function-v5-golden.json').read_text())
    inputs = copy.deepcopy(golden['input']); inputs[field] = None
    artifact_path,input_path,output_path = [tmp_path/name for name in ('function.json','input.json','output.json')]
    artifact_path.write_text(json.dumps(golden['artifact'])); input_path.write_text(json.dumps(inputs))
    with pytest.raises(ValueError,match='not null'):
        main([str(artifact_path),str(input_path),'--output',str(output_path)])
    assert not output_path.exists()


def test_complete_synthetic_coordinate_report_and_prefit_coverage():
    strategy = coordinate_strategy()
    data,provenance = make_demo_data(strategy)
    plans = []
    with threadpool_limits(limits=1):
        report = run_research(strategy,data,provenance,forecast_plan_sink=plans.append)
    forecasts = report['forecasts']
    assert plans[0]['studyProtocol'] == forecasts['studyProtocol'] == 'asset-return-study/2'
    assert forecasts['factorResearch']['panel']['schema'] == 'asset-return-panel/2'
    assert all(row['schema'] == 'asset-return-observation/2' for row in forecasts['rows'])
    assert all(fit['functionArtifact']['schema'] == 'atlas-model-function/5' for fit in forecasts['modelFits'])
    assert report['selection']['qualified'] is False
    assert all(row['informationCutoff'] == (max(row['date'],row['inputAvailableAt'])+'_AFTER_CLOSE' if row['inputAvailableAt'] else None) for row in forecasts['rows'])
    assert [(r['date'],r['targetId']) for r in forecasts['rows']] == [(r['date'],r['targetId']) for r in plans[0]['origins']]
    json.dumps(report,allow_nan=False)
