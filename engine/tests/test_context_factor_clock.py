"""Hand calculations on deliberately different source/research calendars."""
import copy
import math
import statistics

import numpy as np
import pandas as pd
import pytest

from atlas_quant.context_factor_clock import (
    NativeContextSeries, attach_native_context, evaluate_source_clock_factor,
)
from atlas_quant.context_sources import FIELDS
from atlas_quant.engine import ResearchError
from atlas_quant.statistical_quant.preprocessing import PRICE_TRANSFORM


ALIAS = next(k for k, v in FIELDS.items() if v['api'] == 'yfinance_history' and v['ts_code'] == 'XLK' and v['field'] == 'close')


def panel(dates, values, alias=ALIAS):
    result = pd.DataFrame()
    result.attrs['atlas_context_source_series'] = NativeContextSeries(
        ((alias, 'yfinance_history', 'XLK', tuple(dates), tuple(values)),), 'fixture-root')
    return result


def factor(expression=ALIAS, direction=1):
    return {'id': 'test', 'expression': expression, 'direction': direction, 'role': 'predictor'}


def test_native_returns_are_computed_before_holiday_broadcast_and_never_use_same_date_us_close():
    # The unchanged Tuesday mark is not a zero US return. Wednesday's +10%
    # source move cannot become known to the earlier CN Wednesday close.
    p = panel(['20240906', '20240909', '20240911'], [100, 110, 121])
    dates = ['20240909', '20240910', '20240911', '20240912', '20240918', '20240919']
    out = evaluate_source_clock_factor(p, dates, factor(f'returns({ALIAS},1)'), {'kind': 'identity'})
    np.testing.assert_allclose(out, [np.nan, .1, .1, .1, .1, np.nan], rtol=1e-13, equal_nan=True)
    shocked = panel(['20240906', '20240909', '20240911'], [100, 110, 1000])
    new = evaluate_source_clock_factor(shocked, dates, factor(f'returns({ALIAS},1)'), {'kind': 'identity'})
    np.testing.assert_array_equal(out.iloc[:3].to_numpy(), new.iloc[:3].to_numpy())
    assert new.iloc[3] == pytest.approx(1000 / 110 - 1)


def test_source_volatility_counts_source_observations_not_repeated_cn_marks():
    dates = pd.bdate_range('20240102', periods=30).strftime('%Y%m%d').tolist()
    changes = [.01, -.02, .03, -.01, .02] * 6
    prices = [100.]
    for change in changes[:29]:
        prices.append(prices[-1] * (1 + change))
    p = panel(dates, prices)
    research = ['20240201', '20240202', '20240203', '20240204', '20240205']
    out = evaluate_source_clock_factor(p, research, factor(), PRICE_TRANSFORM)
    expected = []
    for date in research:
        known = max(i for i, observed in enumerate(dates) if observed < date)
        prior = changes[known - 21:known - 1]
        expected.append(changes[known - 1] / statistics.stdev(prior))
    np.testing.assert_allclose(out, expected, rtol=2e-13)
    assert out.iloc[2] == out.iloc[3] == out.iloc[4]


def test_yahoo_adjusted_close_is_preserved_and_missing_observation_not_filled():
    p = pd.DataFrame()
    spec = FIELDS[ALIAS]
    source = {'api': 'yfinance_history', 'params': {'ts_code': spec['ts_code']}, 'records': [
        {'trade_date': '20240906', 'close': 200., 'adj_close': 100.},
        {'trade_date': '20240909', 'close': 102., 'adj_close': 102.},
        {'trade_date': '20240910', 'close': 104., 'adj_close': None},
        {'trade_date': '20240911', 'close': 106., 'adj_close': 106.},
    ]}
    attach_native_context(p, {'contextSources': [source], 'contextSourceRoot': 'verified-root'}, [ALIAS])
    out = evaluate_source_clock_factor(p, ['20240910', '20240911', '20240912'], factor(f'returns({ALIAS},1)', -1), {'kind': 'identity'})
    assert out.iloc[0] == pytest.approx(-.02)
    assert math.isnan(out.iloc[1]) and math.isnan(out.iloc[2])
    original = p.attrs['atlas_context_source_series']
    assert copy.deepcopy(original) is original


def test_source_clock_requires_retained_source_and_rejects_mixed_identities():
    with pytest.raises(ResearchError, match='独立原始时间序列'):
        evaluate_source_clock_factor(pd.DataFrame(), ['20240910'], factor(), {'kind': 'identity'})
    other = next(k for k, v in FIELDS.items() if v['api'] == 'yfinance_history' and v['ts_code'] == 'XSD' and v['field'] == 'close')
    p = panel(['20240906', '20240909'], [100., 110.])
    p.attrs['atlas_context_source_series'] = NativeContextSeries(
        p.attrs['atlas_context_source_series'].series + ((other, 'yfinance_history', 'XSD', ('20240906', '20240909'), (50., 60.)),), 'fixture-root')
    with pytest.raises(ResearchError, match='同一证券与来源'):
        evaluate_source_clock_factor(p, ['20240910'], factor(f'{ALIAS}/{other}'), {'kind': 'identity'})


def test_verified_snapshot_restoration_retains_native_clock_end_to_end(monkeypatch):
    from test_yfinance_context import fixture, DATES, FIELD
    from atlas_quant.context_sources import restore_context_fields
    from atlas_quant.provider import ProviderError

    frame, provenance = fixture(monkeypatch)
    for column, value in {'open': 10., 'high': 11., 'low': 9., 'close': 10.,
                          'raw_close': 10., 'adj_factor': 1., 'vol': 100., 'amount': 100.}.items():
        frame[column] = value
    provenance.update(tradingDates=DATES, synthetic=True, source='SYNTHETIC')
    definition = factor(f'returns({FIELD},1)')
    prepared = frame.set_index(['trade_date', 'ts_code']).sort_index()
    dates = DATES
    audit = restore_context_fields(prepared, frame, provenance, [FIELD], dates, dates[0], dates[-1])
    assert audit['contextSourceRoot'] == prepared.attrs['atlas_context_source_series'].root
    result = evaluate_source_clock_factor(prepared, dates, definition, {'kind': 'identity'})
    np.testing.assert_allclose(result, [np.nan, 51 / 49.1234567890123 - 1, 53 / 52 - 1,
                                       400 / 53 - 1, np.nan], rtol=1e-13, equal_nan=True)
    damaged = frame.copy()
    damaged.loc[0, FIELD] += 1
    with pytest.raises(ProviderError):
        restore_context_fields(prepared, damaged, provenance, [FIELD], dates, dates[0], dates[-1])
