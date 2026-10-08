"""Real evaluator checks for the UI's shared semantics; all inputs are synthetic."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from atlas_quant.factors import DSL_CONTRACT, evaluate_expression, validate_expression, FactorError

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/dsl-conformance.json').read_text())


def frame():
    index = pd.MultiIndex.from_product([[f't{i:02}' for i in range(25)], ['A', 'B']], names=['trade_date', 'ts_code'])
    return pd.DataFrame({'close': np.array([FIXTURE['closeA'], FIXTURE['closeB']]).T.flatten(),
                         'vol': FIXTURE['vol'], 'ext_observation': 7}, index=index)


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['expression'])
def test_contract_expression_matches_real_parser_and_hand_values(case):
    assert validate_expression(case['expression'])['lookback'] == case['lookback']
    output = evaluate_expression(case['expression'], frame()).loc['t24']
    if case['expected'] is not None:
        for actual, expected in zip(output, case['expected']):
            assert pd.isna(actual) if expected is None else actual == pytest.approx(expected)


@pytest.mark.parametrize('expression', FIXTURE['invalid'])
def test_contract_rejects_invalid_expressions(expression):
    with pytest.raises(FactorError):
        validate_expression(expression)


def test_returns_uses_endpoints_but_rolling_requires_every_grid_value():
    data = frame().astype(float)
    data.loc[('t10', 'A'), 'close'] = np.nan
    # An absent interior point is not silently compressed out of the grid.
    assert evaluate_expression('returns(close,20)', data).loc[('t24', 'A')] == pytest.approx(124/104-1)
    assert pd.isna(evaluate_expression('ts_mean(close,20)', data).loc[('t24', 'A')])
    data.loc[('t23', 'A'), 'close'] = np.nan
    assert pd.isna(evaluate_expression('returns(close,1)', data).loc[('t24', 'A')])
    assert evaluate_expression('lag(close,2)', data).loc[('t24', 'A')] == 122
    data.loc[('t24', 'A'), 'close'] = np.nan
    assert pd.isna(evaluate_expression('vol', data).loc[('t24', 'A')])


def test_cross_section_and_time_rank_are_different_axes_with_average_ties():
    data=frame().astype(float)
    data.loc[('t24','B'),'close']=124
    assert list(evaluate_expression('rank(close)',data).loc['t24']) == [.75,.75]
    data.loc[('t24','B'),'close']=np.nan
    assert evaluate_expression('rank(close)',data).loc[('t24','A')] == 1
    data.loc[('t23','A'),'close']=124
    assert evaluate_expression('ts_rank(close,3)',data).loc[('t24','A')] == pytest.approx(2.5/3)


def test_cross_section_uses_operand_validity_before_final_market_close_mask():
    data = frame().astype(float)
    data.loc[('t24', 'A'), 'vol'] = 1
    data.loc[('t24', 'B'), 'vol'] = 2
    data.loc[('t24', 'A'), 'close'] = np.nan
    ranked = evaluate_expression('rank(vol)', data).loc['t24']
    standardized = evaluate_expression('zscore(vol)', data).loc['t24']
    assert pd.isna(ranked['A']) and pd.isna(standardized['A'])
    assert ranked['B'] == 1
    assert standardized['B'] == 1


def test_shared_window_contract_does_not_change_historical_execution_metadata():
    assert DSL_CONTRACT['version']=='atlas-factor-semantics/v1'
    assert validate_expression('returns(close,20)') == {'fields':['close'],'lookback':20,'causal':True}


def test_engine_copy_runs_independently_with_its_packaged_contract(tmp_path):
    """No dependency on edge/web/repository-relative files at execution time."""
    package = Path(__file__).resolve().parents[1] / 'atlas_quant'
    shutil.copytree(package, tmp_path / 'atlas_quant', ignore=shutil.ignore_patterns('__pycache__'))
    script = (
        'import json\nfrom atlas_quant.factors import DSL_CONTRACT, validate_expression\n'
        'print(json.dumps({"version":DSL_CONTRACT["version"],'
        '"parsed":validate_expression("returns(close,20)")}))'
    )
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(tmp_path), 'PYTHONDONTWRITEBYTECODE': '1'},
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {
        'version': 'atlas-factor-semantics/v1',
        'parsed': {'fields': ['close'], 'lookback': 20, 'causal': True},
    }
