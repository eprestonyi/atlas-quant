"""Return protocol guards: no provider access or fitted model execution."""
import copy
import pytest
from atlas_quant.engine import ResearchError
from atlas_quant.statistical_quant.return_study.contract import input_descriptors, target_contract, validate


def study(expression, mode='association', horizon=5):
    return {'schemaVersion': 2, 'name': 'Return timing',
            'universe': {'symbols': ['600519.SH'], 'start': '20230101', 'end': '20250930'},
            'research': {'mode': 'statistical_quant', 'returnStudy': {'schema': 'asset-return-study/1', 'mode': mode}},
            'target': {'kind': 'asset_return', 'horizonSessions': horizon, 'normalization': {'kind': 'none'}},
            'model': {'family': 'trend', 'estimator': 'ridge', 'parameterSharing': 'per_target'},
            'factors': [{'id': 'input', 'expression': expression, 'direction': 1, 'role': 'predictor'}],
            'preprocess': {'automatic': {'schema': 'auto-factor-preprocess/2'}}, 'execution': {'enabled': False}}


@pytest.mark.parametrize('expression', [
    'ext_ctx_000300_sh_close/lag(ext_ctx_000300_sh_close,20)-1',
    'delta(log(ext_ctx_000300_sh_close),20)',
    'log(ext_ctx_000300_sh_close)-log(lag(ext_ctx_000300_sh_close,20))',
    'returns(lag(ext_ctx_000300_sh_close,5),5)',
    'lag(ext_ctx_000300_sh_close,5)',
    'log(lag(ext_ctx_000300_sh_close,5))',
    'returns(returns(ext_ctx_000300_sh_close,5),5)',
    'returns(ext_ctx_000300_sh_close,+5)',
])
def test_obvious_shifted_or_mismatched_price_return_forms_reject(expression):
    with pytest.raises(ResearchError) as error:
        input_descriptors(study(expression))
    assert error.value.code == 'ASSOCIATION_PERIOD_MISMATCH'


@pytest.mark.parametrize('expression', [
    'ext_ctx_000300_sh_close/lag(ext_ctx_000300_sh_close,5)-1',
    'delta(log(ext_ctx_000300_sh_close),5)',
    'log(ext_ctx_000300_sh_close)-log(lag(ext_ctx_000300_sh_close,5))',
])
def test_equivalent_matched_period_returns_are_not_transformed_twice(expression):
    descriptor = input_descriptors(study(expression))[0]
    assert descriptor['transform'] == {'kind': 'identity'}
    assert descriptor['clock'] == 'research_sessions'


def test_same_reference_identity_rejects_but_equity_index_namespace_does_not_collide():
    same = study('ext_ctx_xsd_close'); same['universe']['symbols'] = ['XSD']
    with pytest.raises(ResearchError) as error:
        input_descriptors(same)
    assert error.value.code == 'ASSOCIATION_TARGET_LEAKAGE'
    index = study('ext_ctx_000300_sh_close'); index['universe']['symbols'] = ['000300.SH']
    assert input_descriptors(index)[0]['scope'] == 'global'


def test_annual_return_protocol_restores_252_horizon_after_legacy_common_validation():
    s = study('returns(close,20)', mode='forecast', horizon=252)
    assert validate(s)['target']['horizonSessions'] == 252
    s['target']['horizonSessions'] = 253
    with pytest.raises(ResearchError):
        validate(s)
    legacy = study('returns(close,20)', mode='forecast', horizon=61)
    legacy['research'].pop('returnStudy'); legacy['target'] = {'kind': 'asset_price', 'horizonSessions': 61}
    from atlas_quant.statistical_quant.schema import validate as all_validate
    with pytest.raises(ResearchError):
        all_validate(legacy)


def test_python_bool_cannot_pass_as_numeric_normalization_contract():
    target = study('fd_netprofit_margin')['target']
    target['normalization'] = {'kind': 'trailing_volatility', 'windowSessions': 20, 'ddof': 1, 'horizonScale': 'sqrt_h', 'minimum': 1e-8}
    assert target_contract(target) == target
    for key in ('windowSessions', 'ddof', 'minimum'):
        changed = copy.deepcopy(target); changed['normalization'][key] = True
        with pytest.raises(ResearchError):
            target_contract(changed)
