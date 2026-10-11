"""Frozen engine rows and independent archive arithmetic; no provider or fitting."""
import copy
import importlib.util
import json
import sys
from pathlib import Path
import pytest

from atlas_quant.bundle_return import evidence_contract, verify_coordinate_row, verify_coordinate_fit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from return_study_audit import coordinate_contract, check_return_coordinates, check_return_row, check_return_fit
SPEC = importlib.util.spec_from_file_location('timed_manual_factors', ROOT/'scripts/audit-return-factors.py')
MANUAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANUAL)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def fail(code, message):
    raise ValueError(code+': '+message)


def near(left, right, label):
    assert left == pytest.approx(right, rel=1e-11, abs=1e-12), label


@pytest.fixture
def frozen():
    fixture = json.loads((ROOT/'tests/fixtures/factor-time-report-projection.json').read_text())
    assert fixture['source']['synthetic'] is True and fixture['source']['fittingPerformedByFixture'] is False
    report = fixture['report']
    strategy, forecast = report['strategy'], report['forecasts']
    dates = forecast['factorResearch']['panel']['dates']
    return report, evidence_contract(strategy, dates), coordinate_contract(strategy, dates)


def test_frozen_actual_engine_rows_pass_both_independent_and_reader_contracts(frozen):
    report, contract, independent = frozen
    forecast, strategy = report['forecasts'], report['strategy']
    for row in forecast['factorResearch']['panel']['rows'] + forecast['rows']:
        verify_coordinate_row(row, contract, fail)
        check_return_coordinates(row, independent, require)
        if row['schema'] == 'asset-return-observation/2':
            check_return_row(row, strategy, near, require)
    for fit in forecast['modelFits']:
        verify_coordinate_fit(fit, strategy, fail)
        check_return_fit(fit, require)


@pytest.mark.parametrize('damage', ['known', 'date', 'source', 'origin', 'class', 'ready', 'missing', 'status'])
def test_rehashed_evidence_claims_cannot_change_time_semantics(frozen, damage):
    report, contract, independent = frozen
    row = copy.deepcopy(next(r for r in report['forecasts']['factorResearch']['panel']['rows'] if r['availabilityClass'] == 'conditional_realized'))
    evidence = row['inputEvidence']['factor:future']
    if damage == 'known':
        evidence['knownAtOrigin'] = True
    elif damage == 'date':
        evidence['availableAt'] = row['date']
    elif damage == 'source':
        evidence['sourceDate'] = row['date']
    elif damage == 'origin':
        evidence['valueOrigin'] = 'predicted'
    elif damage == 'class':
        row['availabilityClass'] = 'origin_known_by_policy'
    elif damage == 'ready':
        row['sampleReadyAt'] = row['date']
    elif damage == 'missing':
        del row['inputEvidence']['factor:past']
    else:
        evidence['status'] = 'missing_observation'
    with pytest.raises(ValueError):
        verify_coordinate_row(row, contract, fail)
    with pytest.raises(ValueError):
        check_return_coordinates(row, independent, require)


def test_all_lag_output_cannot_predate_fit_origin_and_training_waits_for_leads(frozen):
    report, _, _ = frozen
    row = copy.deepcopy(report['forecasts']['rows'][0])
    row['inputAvailableAt'] = '20230102'
    row['informationCutoff'] = row['date']+'_AFTER_CLOSE'
    check_return_row(row, report['strategy'], near, require)
    row['informationCutoff'] = '20230102_AFTER_CLOSE'
    with pytest.raises(ValueError, match='cutoff'):
        check_return_row(row, report['strategy'], near, require)
    fit = copy.deepcopy(next(f for f in report['forecasts']['modelFits'] if f['status'] == 'valid'))
    fit['inputAvailableAtMax'] = fit['sampleReadyAtMax'] = fit['fitDate']
    with pytest.raises(ValueError, match='immature'):
        check_return_fit(fit, require)


def test_independent_expression_oracle_preserves_missing_rows_and_window_then_offset():
    prices = [100.,110.,None,132.,145.2,159.72]
    values = MANUAL.expression_values('returns(close,2)', lambda _:prices, len(prices))
    assert values[:3] == [None,None,None]
    assert values[3] == pytest.approx(.2)
    assert values[4] is None
    assert values[5] == pytest.approx(.21)
    lead = [values[i+1] if i+1<len(values) else None for i in range(len(values))]
    assert lead[2] == pytest.approx(.2) and lead[-1] is None
    # A three-period return is a different economic interval, not a lead-1 shift.
    wrong = MANUAL.expression_values('returns(close,3)',lambda _:prices,len(prices))
    assert wrong[2] is None and wrong[3] == pytest.approx(.32)


def test_independent_audit_checks_the_inner_function_identity_not_only_archive_hash(frozen):
    report, _, _ = frozen
    fit = copy.deepcopy(next(f for f in report['forecasts']['modelFits'] if f['status'] == 'valid'))
    fit['functionArtifact']['estimator']['intercepts'][0] += .01
    with pytest.raises(ValueError, match='inner content hash'):
        check_return_fit(fit, require,report['strategy'])
