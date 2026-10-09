"""Calendar-exact boundaries, maturity purging and independent coverage; zero fits."""
import copy
from datetime import date, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from atlas_quant.engine import ResearchError
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.validation import forecast_origins, mature_mask

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from market_dataset_audit import AuditError, Checks, asset_target_definition, validate_asset_coverage


def calendar():
    current, result = date(2024, 5, 1), []
    while current <= date(2024, 10, 31):
        value = current.strftime('%Y%m%d')
        if current.weekday() < 5 and not '20241001' <= value <= '20241007':
            result.append(value)
        current += timedelta(days=1)
    return result


def strategy(test_start=None):
    raw = {'schemaVersion': 2, 'name': 'SYNTHETIC date-boundary unit fixture',
           'universe': {'symbols': ['000001.SZ'], 'start': '20240501', 'end': '20241031'},
           'research': {'mode': 'statistical_quant', 'observationDays': 3}, 'factors': [],
           'target': {'kind': 'asset_price', 'horizonSessions': 5},
           'model': {'family': 'mean_reversion', 'estimator': 'ridge', 'refitDays': 20},
           'execution': {'enabled': False}}
    if test_start is not None:
        raw['validation'] = {'testStart': test_start}
    return raw


def samples():
    dates = calendar()
    positions = range(61, len(dates), 3)
    meta = pd.DataFrame([{'date': dates[i], 'inputValid': True,
                          'targetDate': dates[i + 6] if i + 6 < len(dates) else None}
                         for i in positions])
    return SimpleNamespace(dates=dates, start_index=61, meta=meta,
                           y=pd.DataFrame({'target': [0.0] * len(meta)}))


def test_absent_field_preserves_legacy_and_explicit_is_idempotent():
    legacy = validate(strategy())
    assert 'testStart' not in legacy['validation']
    assert validate(legacy) == legacy
    explicit = validate(strategy('20241001'))
    assert explicit['validation']['testStart'] == '20241001'
    assert validate(explicit) == explicit


@pytest.mark.parametrize('value', [None, True, 20241001, '', '2024-10-01', '20240931', '20240430', '20241101'])
def test_schema_rejects_invalid_dates(value):
    raw = strategy()
    raw['validation'] = {'testStart': value}
    with pytest.raises(ResearchError, match='validation|testStart'):
        validate(raw)


def test_holiday_uses_next_eligible_session_and_maturity_is_still_purged():
    s, sample = validate(strategy('20241001')), samples()
    holdout, indices = forecast_origins(sample, s)
    assert holdout == '20241008'
    assert list(sample.meta.loc[indices, 'date']) == [d for i, d in enumerate(sample.dates) if i >= 61 and (i - 61) % 3 == 0 and d >= holdout]
    changed = copy.deepcopy(s)
    changed['validation']['holdoutFraction'] = .4
    other, other_indices = forecast_origins(sample, changed)
    assert other == holdout and other_indices.equals(indices)
    mask = mature_mask(sample, holdout)
    assert mask.any()
    assert (sample.meta.loc[mask, 'targetDate'] < holdout).all()
    assert not mask[sample.meta.targetDate == holdout].any()
    assert not mask[sample.meta.date >= holdout].any()


def test_legacy_fraction_and_exact_eligible_boundaries():
    sample = samples()
    assert forecast_origins(sample, validate(strategy()))[0] == sample.dates[61 + int((len(sample.dates) - 61) * .8)]
    for day in [sample.dates[62], sample.dates[-10]]:
        assert forecast_origins(sample, validate(strategy(day)))[0] == day
    for day in ['20240501', sample.dates[61], sample.dates[-9], '20241031']:
        with pytest.raises(ResearchError) as caught:
            forecast_origins(sample, validate(strategy(day)))
        assert caught.value.code == 'INSUFFICIENT_FORECAST_DATA'


def coverage_fixture():
    """Hand-declared October test domain, independent of runtime split helpers."""
    dates, s = calendar(), validate(strategy('20241001'))
    target = asset_target_definition('000001.SZ')
    origins = []
    for i in range(61, len(dates), 3):
        if dates[i] < '20241008':
            continue
        origins.append({'date': dates[i], 'targetId': target['id'],
                        'entryDate': dates[i + 1] if i + 1 < len(dates) else None,
                        'targetDate': dates[i + 6] if i + 6 < len(dates) else None,
                        'horizonSessions': 5, 'informationCutoff': dates[i] + '_AFTER_CLOSE',
                        'status': 'invalid'})
    fits, last = [], -100000
    for i in range(61, len(dates), 3):
        if i - last >= 20:
            fits.append({'date': dates[i], 'informationCutoff': dates[i - 1],
                         'targetIds': [target['id']], 'status': 'valid'})
            last = i
    collections = {'targets': [target], 'plannedOrigins': copy.deepcopy(origins),
                   'forecasts': origins, 'baselineRows': [], 'baselineModelFits': [], 'hedgeFits': fits}
    clock = {'holdoutStart': '20241008', 'holdoutEnd': dates[-1]}
    docs = {'report': {'research': {'observationDays': 3}, 'validation': dict(clock)},
            'forecast': {'diagnostics': dict(clock)},
            'coverage': {'source': 'samples_before_model_fitting', 'baselineRequired': False, 'holdoutStart': '20241008'}}
    audit = SimpleNamespace(documents=docs, count=lambda k: len(collections[k]), rows=lambda k: iter(collections[k]))
    manifest = {'calendar': dates, 'scope': {'symbols': ['000001.SZ']}, 'fields': []}
    return audit, manifest, s


def test_independent_auditor_accepts_holiday_boundary_and_full_grid():
    audit, manifest, s = coverage_fixture()
    result = validate_asset_coverage(audit, manifest, s, Checks())
    assert result['holdoutStart'] == '20241008'
    assert result['fullAssetCoverageVerified']
    assert result['expectedForecastRows'] == audit.count('forecasts')


@pytest.mark.parametrize('damage', ['fraction_clock', 'out_of_scope', 'impossible_date', 'nine_test_dates', 'no_development', 'dropped_origin'])
def test_independent_auditor_rejects_date_or_coverage_substitution(damage):
    audit, manifest, s = coverage_fixture()
    if damage == 'fraction_clock':
        # All result clocks agree, but they ignore the declared explicit date.
        wrong = manifest['calendar'][61 + int((len(manifest['calendar']) - 61) * .8)]
        for declaration in [audit.documents['report']['validation'], audit.documents['forecast']['diagnostics'], audit.documents['coverage']]:
            declaration['holdoutStart'] = wrong
    elif damage == 'out_of_scope':
        s['validation']['testStart'] = '20241101'
    elif damage == 'impossible_date':
        s['validation']['testStart'] = '20240931'
    elif damage == 'nine_test_dates':
        s['validation']['testStart'] = manifest['calendar'][-9]
    elif damage == 'no_development':
        s['validation']['testStart'] = manifest['calendar'][61]
    elif damage == 'dropped_origin':
        original = audit.rows
        audit.rows = lambda k: iter(list(original(k))[1:]) if k == 'forecasts' else original(k)
    with pytest.raises(AuditError):
        validate_asset_coverage(audit, manifest, s, Checks())


def test_unfinished_pair_stage2b_contract_still_rejects_explicit_date_before_fit():
    from atlas_quant.pair_research.fit_contract import _controls
    from atlas_quant.pair_research.contract import PairContractError
    s = validate(strategy('20241001'))
    s['model'].update(family='pair_reversion', estimator='auto', trainWindow=252)
    with pytest.raises(PairContractError):
        _controls(s['model'], s['preprocess'], s['validation'], {})
