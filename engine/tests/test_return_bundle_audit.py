"""Independent arithmetic rejects rehashed scalar research corruption."""
import copy
import importlib.util
import sys
from pathlib import Path
import pytest
from threadpoolctl import threadpool_limits
from atlas_quant.engine import run_research
from atlas_quant.fixtures import make_demo_data
from atlas_quant.runner_artifacts import freeze_input
from atlas_quant.bundle import BundleReader
from test_asset_return_models import config
from test_bundle_audit import export, reidentify

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from bundle_audit import audit_bundle
spec = importlib.util.spec_from_file_location('manual_return_factors', ROOT/'scripts/audit-return-factors.py')
manual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manual)


@pytest.fixture(scope='module')
def research():
    strategy = config('no_change', normalization={'kind': 'trailing_volatility', 'windowSessions': 20,
        'ddof': 1, 'horizonScale': 'sqrt_h', 'minimum': 1e-8})
    data, provenance = make_demo_data(strategy)
    plans = []
    with threadpool_limits(limits=1):
        report = run_research(strategy, data, provenance, forecast_plan_sink=plans.append)
    return report, freeze_input(strategy, data, provenance), plans[0]


def test_complete_scalar_archive_has_independent_source_and_manual_transform_checks(tmp_path, research):
    report, snapshot, plan = research
    directory = export(tmp_path/'returns', report, snapshot, plan)
    assert audit_bundle(directory)['status'] == 'passed'
    assert manual.audit(report, snapshot)['status'] == 'passed'
    reader = BundleReader((directory/'manifest.json').read_bytes(),
                          lambda name, ordinal: (directory/'chunks'/name/f'{ordinal}.json').read_bytes())
    assert reader.verify_integrity()['verified']


@pytest.mark.parametrize('damage,reason', [
    ('residual', 'Response residual'), ('response_scale', 'Source origin-known volatility'),
    ('asset', 'another asset model'), ('panel_asset', 'Panel asset differs'),
    ('panel_price', 'Source origin close'), ('interval', 'Declared h'),
])
def test_validly_rehashed_scalar_damage_is_rejected(tmp_path, research, damage, reason):
    report, snapshot, plan = (copy.deepcopy(x) for x in research)
    row = next(x for x in report['forecasts']['rows'] if x['status'] == 'valid' and x['observedReturn'] is not None)
    if damage == 'residual':
        row['responseResidual'] += .1
    elif damage == 'response_scale':
        row['responseScale'] *= 2
        row['observedResponse'] /= 2
        row['responseResidual'] = row['observedResponse']-row['predictedResponse']
    elif damage == 'asset':
        row['assetSymbol'] = '600519.SH'
    elif damage == 'panel_asset':
        report['forecasts']['factorResearch']['panel']['rows'][0]['assetSymbol'] = '600519.SH'
    elif damage == 'panel_price':
        report['forecasts']['factorResearch']['panel']['rows'][30]['originPrice'] *= 2
    else:
        row['responseEndDate'] = row['labelMaturedAt'] = '20990101'
        next(x for x in plan['origins'] if x['date'] == row['date'] and x['targetId'] == row['targetId'])['responseEndDate'] = '20990101'
    reidentify(report)
    directory = export(tmp_path/damage, report, snapshot, plan)
    with pytest.raises(ValueError, match=reason):
        audit_bundle(directory)


@pytest.mark.parametrize('damage', ['factor', 'OLS'])
def test_manual_factor_oracle_does_not_trust_engine_panel_or_report_statistics(research, damage):
    report, snapshot, _ = (copy.deepcopy(x) for x in research)
    if damage == 'factor':
        report['forecasts']['factorResearch']['panel']['rows'][100]['features']['factor:volume'] += .25
    else:
        report['forecasts']['diagnostics']['perTarget'][0]['factorDiagnostics']['features'][0]['descriptiveFit']['rSquared'] += .25
    with pytest.raises(AssertionError):
        manual.audit(report, snapshot)
