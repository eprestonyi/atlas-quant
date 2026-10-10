"""Strict opt-in admission and effective factor timing for return studies."""
from __future__ import annotations
import ast
import copy
from ..schema import fail, number
from ..typed_preprocessing import descriptor, field_quantity
from ...factors import _parse
from ...context_sources import context_field

SCHEMA = 'asset-return-study/1'
FUNCTION_SCHEMA = 'atlas-model-function/4'
PRICE_IDENTITY_FIELDS = {'open', 'high', 'low', 'close', 'raw_close', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm',
                         'total_mv', 'circ_mv', 'float_mv', 'dv_ratio', 'dv_ttm'}
NORMALIZATION = {'kind': 'trailing_volatility', 'windowSessions': 20, 'ddof': 1,
                 'horizonScale': 'sqrt_h', 'minimum': 1e-8}


def enabled(strategy):
    return isinstance(strategy, dict) and isinstance(strategy.get('research'), dict) and 'returnStudy' in strategy['research']


def target_contract(target):
    if not isinstance(target, dict) or set(target) != {'kind', 'horizonSessions', 'normalization'} or target['kind'] != 'asset_return':
        fail('RETURN_TARGET', '收益研究需要明确 asset_return、期限与归一化方式')
    horizon = number(target['horizonSessions'], 'horizonSessions', 1, 252, True)
    norm = target['normalization']
    if norm != {'kind': 'none'}:
        if not isinstance(norm, dict) or set(norm) != set(NORMALIZATION) or any(type(norm.get(k)) is bool or norm.get(k) != v for k, v in NORMALIZATION.items() if k != 'windowSessions'):
            fail('RETURN_NORMALIZATION', '波动率归一化需要固定样本标准差、期限尺度和最小值')
        number(norm.get('windowSessions'), 'windowSessions', 20, 252, True)
    return {'kind': 'asset_return', 'horizonSessions': horizon, 'normalization': copy.deepcopy(norm)}


def _price_periods(tree, horizon, transform):
    """Reject obvious shifted/mismatched returns, including equivalent DSL forms."""
    def price_derived(node):
        return any(isinstance(x, ast.Name) and field_quantity(x.id).kind == 'price' for x in ast.walk(node))

    def visit(node, used):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            period = name == 'returns' or name in {'lag', 'delta'} and price_derived(node.args[0])
            if period:
                if len(node.args) != 2 or not isinstance(node.args[1], ast.Constant) or type(node.args[1].value) not in {int, float} or node.args[1].value != horizon:
                    fail('ASSOCIATION_PERIOD_MISMATCH', '同期收益因子的显式收益窗口必须与响应期限一致')
                used += horizon
                if used > horizon:
                    fail('ASSOCIATION_PERIOD_MISMATCH', '同期收益因子不能重复移位或使用上一响应区间')
        for child in ast.iter_child_nodes(node):
            visit(child, used)

    implicit = horizon if transform['kind'] in {'simple_return', 'log_return', 'first_difference'} else 0
    visit(tree, implicit)


def _own_reference(fields, symbols):
    for field in fields:
        spec = context_field(field)
        # Index identifiers occupy a different namespace from equity tickers.
        if spec and spec['api'] not in {'index_daily', 'sw_daily'} and spec['ts_code'] in symbols:
            return True
    return False


def input_descriptors(strategy):
    mode = strategy['research']['returnStudy']['mode']
    horizon = strategy['target']['horizonSessions']
    overrides = strategy['preprocess']['automatic'].get('overrides', {})
    inputs = []
    for factor in strategy['factors']:
        if factor['role'] == 'hedge':
            fail('RETURN_FACTOR_ROLE', '独立收益方程不接受篮子对冲腿')
        item = descriptor(factor, overrides.get(factor['id']))
        tree, info = _parse(factor['expression'])
        if mode == 'association':
            if PRICE_IDENTITY_FIELDS.intersection(info['fields']) or _own_reference(info['fields'], strategy.get('universe', {}).get('symbols', [])):
                fail('ASSOCIATION_TARGET_LEAKAGE', '同期关联不能用本证券的价格或含本证券价格的估值构造解释自身收益；可选择明确的市场、行业或其他证券参考')
            _price_periods(tree, horizon, item['transform'])
            if item['transform']['kind'] == 'return_over_trailing_volatility':
                fail('ASSOCIATION_PERIOD_MISMATCH', '同期关联请选择明确的同期限简单收益或对数收益构造')
            if item['transform']['kind'] in ('simple_return', 'log_return', 'first_difference'):
                item['transform']['lag'] = horizon
            # Match the response's research-session endpoints, using only the
            # as-of source prices already admitted on that research calendar.
            item['clock'] = 'research_sessions'
        item['aggregation'] = 'global_once' if item['scope'] == 'global' else 'asset_direct'
        item['timing'] = {'kind': 'origin_known' if mode == 'forecast' else 'matched_period', 'horizonSessions': horizon}
        inputs.append(item)
    return inputs


def validate(strategy, *, capacity_profile=None):
    from ..schema import validate as legacy_validate
    original = copy.deepcopy(strategy)
    study = original.get('research', {}).get('returnStudy')
    if not isinstance(study, dict) or set(study) != {'schema', 'mode'} or study['schema'] != SCHEMA or study['mode'] not in ('forecast', 'association'):
        fail('RETURN_STUDY', '收益研究协议或研究时序无效')
    target = target_contract(original.get('target'))
    if original.get('model', {}).get('parameterSharing') != 'per_target':
        fail('RETURN_MODEL_SCOPE', '收益研究集合中的每个证券必须独立拟合参数')
    if original.get('execution', {}).get('enabled') is not False:
        fail('RETURN_RESEARCH_ONLY', '收益方程当前只进行研究，不进入交易执行')
    if original.get('preprocess', {}).get('automatic', {}).get('schema') != 'auto-factor-preprocess/2':
        fail('RETURN_PREPROCESS', '收益研究需要按经济类型处理的自动因子 /2')
    if original.get('model', {}).get('family') == 'pair_reversion':
        fail('RETURN_PAIR_SEPARATE', '配对价差是独立的篮子机制，不可转换为股票集合平均价格')
    if not original.get('factors') or any(f.get('role', 'predictor') == 'hedge' for f in original['factors']):
        fail('RETURN_FACTOR_ROLE', '请明确选择至少一个非对冲输入因子')
    temporary = copy.deepcopy(original)
    temporary['research'].pop('returnStudy')
    temporary['target'] = {'kind': 'asset_price', 'horizonSessions': min(target['horizonSessions'], 60)}
    # Other existing names/fields/PIT bindings retain their established rules.
    result = legacy_validate(temporary, capacity_profile=capacity_profile)
    result['research']['returnStudy'] = study
    result['target'] = target
    input_descriptors(result)
    return result


def outputs(strategy):
    return ['asset_return' if strategy['target']['normalization']['kind'] == 'none' else 'volatility_standardized_asset_return']
