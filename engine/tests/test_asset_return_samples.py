"""Independent arithmetic oracle for return-study labels and economic inputs.

Expected values use scalar division/math/statistics, never engine transforms or
estimators. Synthetic adjusted-price inputs are not evidence of provider data.
"""
import math
import statistics

import numpy as np
import pandas as pd
import pytest
from atlas_quant.statistical_quant.return_study.samples import build_samples, panel_document

MARKET = 'ext_ctx_000300_sh_close'
SYMBOLS = ['000001.SZ', '600519.SH']


def fixture(mode='forecast', normalized=False, horizon=5, count=80, own_price=False):
    dates = pd.bdate_range('20230102', periods=count).strftime('%Y%m%d').tolist()
    values, market = {}, []
    level = 3700.
    for t in range(count):
        level *= 1 + .002 + .009*math.sin(t*.73)
        market.append(level)
    rows = []
    for n, symbol in enumerate(SYMBOLS):
        level = [12., 1700.][n]; prices = []
        for t, date in enumerate(dates):
            level *= 1 + .001*(n+1) + .018*math.sin(t*.41+n)
            prices.append(level)
            rows.append({'trade_date':date, 'ts_code':symbol, 'close':level,
                         'open':level*1.013, MARKET:market[t],
                         'fd_netprofit_margin':7+2*n+.3*math.cos(t*.21)})
        values[symbol] = prices
    panel = pd.DataFrame(rows).set_index(['trade_date','ts_code']).sort_index()
    factors = [{'id':'market','expression':MARKET,'direction':1,'role':'predictor'},
               {'id':'margin','expression':'fd_netprofit_margin','direction':-1,'role':'predictor'}]
    if own_price:
        factors.append({'id':'own','expression':'returns(close,1)','direction':1,'role':'predictor'})
    strategy = {'universe':{'symbols':SYMBOLS.copy()},
                'research':{'observationDays':1,'returnStudy':{'schema':'asset-return-study/1','mode':mode}},
                'target':{'kind':'asset_return','horizonSessions':horizon,'normalization':
                          {'kind':'trailing_volatility','windowSessions':20,'ddof':1,'horizonScale':'sqrt_h','minimum':1e-8} if normalized else {'kind':'none'}},
                'factors':factors,'preprocess':{'automatic':{'schema':'auto-factor-preprocess/2'}},
                'model':{'family':'trend'}}
    return panel, dates, strategy, values, market


@pytest.mark.parametrize('mode', ['forecast','association'])
@pytest.mark.parametrize('normalized', [False,True])
@pytest.mark.parametrize('horizon', [1,5,21,252])
def test_every_asset_session_matches_independent_close_interval_and_scale(mode, normalized, horizon):
    panel, dates, strategy, prices, market = fixture(mode, normalized, horizon, count=max(80,horizon+45))
    samples = build_samples(panel, dates, strategy)
    assert samples.X.columns.tolist() == ['factor:market','factor:margin']
    assert samples.y.columns.tolist() == ['response']
    assert len(samples.meta) == len(dates)*len(SYMBOLS)
    for i, row in samples.meta.iterrows():
        t, symbol = dates.index(row.date), row.assetSymbol
        start, end = (t,t+horizon) if mode == 'forecast' else (t-horizon,t)
        assert row.featureDate == dates[t]
        assert row.responseStartDate == (dates[start] if start >= 0 else None)
        assert row.responseEndDate == (dates[end] if end < len(dates) else None)
        if start < 0:
            assert row.invalidReason == 'response_window_warmup'
            assert math.isnan(samples.y.iloc[i,0])
            continue
        expected_scale = 1.
        if normalized:
            if start < 20:
                assert row.invalidReason == 'response_normalizer_unavailable'
                assert math.isnan(row.responseScale)
                continue
            returns = [prices[symbol][k]/prices[symbol][k-1]-1 for k in range(start-19,start+1)]
            expected_scale = statistics.stdev(returns)*math.sqrt(horizon)
        assert row.originPrice == prices[symbol][start]
        assert row.responseScale == pytest.approx(expected_scale,rel=1e-12,abs=1e-15)
        if end < len(dates):
            expected = prices[symbol][end]/prices[symbol][start]-1
            assert row.observedReturn == pytest.approx(expected,abs=1e-15)
            assert row.observedResponse == pytest.approx(expected/expected_scale,rel=1e-11,abs=1e-13)
            assert samples.y.iloc[i,0] == row.observedResponse
        else:
            assert math.isnan(row.observedReturn) and math.isnan(row.observedResponse)
            assert row.inputValid  # Available X can predict an unobserved tail.
        lag = horizon if mode == 'association' else 1
        expected_market = market[t]/market[t-lag]-1 if t >= lag else math.nan
        assert samples.X.iloc[i]['factor:market'] == pytest.approx(expected_market,nan_ok=True)
        expected_margin = -panel.loc[(row.date,symbol),'fd_netprofit_margin']/100.
        assert samples.X.iloc[i]['factor:margin'] == pytest.approx(expected_margin)
    for target in samples.definitions.values():
        assert len(target['symbols']) == 1 and target['kind'] == 'asset_return'


@pytest.mark.parametrize('mode', ['forecast','association'])
def test_global_factor_broadcast_and_asset_rescaling_do_not_create_an_average_price_target(mode):
    panel, dates, strategy, _, _ = fixture(mode,True)
    original = build_samples(panel,dates,strategy)
    changed = panel.copy()
    for symbol,multiplier in zip(SYMBOLS,[.001,1000]):
        mask = changed.index.get_level_values('ts_code') == symbol
        changed.loc[mask,['close','open']] *= multiplier
    scaled = build_samples(changed,dates,strategy)
    np.testing.assert_allclose(original.X,scaled.X,equal_nan=True,rtol=1e-12,atol=1e-15)
    np.testing.assert_allclose(original.y,scaled.y,equal_nan=True,rtol=1e-11,atol=1e-12)
    np.testing.assert_allclose(original.meta.responseScale,scaled.meta.responseScale,equal_nan=True,rtol=1e-12,atol=1e-15)
    grid = original.X.assign(date=original.meta.date,asset=original.meta.assetSymbol).pivot(index='date',columns='asset',values='factor:market')
    np.testing.assert_array_equal(grid.iloc[:,0],grid.iloc[:,1])
    # Distinct stock paths retain distinct responses despite shared factors.
    assert not np.allclose(original.y.iloc[50::2],original.y.iloc[51::2],equal_nan=True)


@pytest.mark.parametrize('mode', ['forecast','association'])
def test_future_perturbation_never_changes_known_factors_or_origin_scale(mode):
    panel, dates, strategy, _, _ = fixture(mode,True)
    old = build_samples(panel,dates,strategy)
    cutoff = 44
    changed = panel.copy(); future = changed.index.get_level_values('trade_date') > dates[cutoff]
    changed.loc[future,['close','open',MARKET,'fd_netprofit_margin']] *= 7
    new = build_samples(changed,dates,strategy)
    known = old.meta.date <= dates[cutoff]
    np.testing.assert_array_equal(old.X.loc[known],new.X.loc[known])
    np.testing.assert_array_equal(old.meta.loc[known,'responseScale'],new.meta.loc[known,'responseScale'])
    matured = old.meta.responseEndDate.notna() & (old.meta.responseEndDate <= dates[cutoff])
    np.testing.assert_array_equal(old.y.loc[matured],new.y.loc[matured])
    if mode == 'forecast':
        overlap = known & ~matured
        assert not np.allclose(old.y.loc[overlap],new.y.loc[overlap],equal_nan=True)


def test_complete_panel_retains_missing_asset_rows_without_compressing_the_session_clock():
    panel, dates, strategy, prices, _ = fixture(own_price=True)
    missing = (dates[35],SYMBOLS[0])
    sparse = panel.drop(index=missing)
    samples = build_samples(sparse,dates,strategy)
    document = panel_document(samples,SYMBOLS)
    assert document['rowCount'] == len(dates)*2 and document['complete'] is True
    assert len({(row['date'],row['assetSymbol']) for row in document['rows']}) == len(dates)*2
    for t in [35,36]:
        mask = (samples.meta.date == dates[t]) & (samples.meta.assetSymbol == SYMBOLS[0])
        assert math.isnan(samples.X.loc[mask,'factor:own'].iloc[0])
    gap = samples.meta[(samples.meta.date == dates[35]) & (samples.meta.assetSymbol == SYMBOLS[0])].iloc[0]
    assert gap.invalidReason == 'missing_origin_price' and not gap.inputValid
    unaffected = samples.meta[(samples.meta.date == dates[36]) & (samples.meta.assetSymbol == SYMBOLS[1])].index[0]
    assert samples.X.loc[unaffected,'factor:own'] == pytest.approx(prices[SYMBOLS[1]][36]/prices[SYMBOLS[1]][35]-1)


def test_zero_or_missing_origin_volatility_never_invents_a_normalizer():
    panel, dates, strategy, _, _ = fixture(normalized=True)
    constant = panel.copy(); constant['close'] = 100.
    samples = build_samples(constant,dates,strategy)
    assert samples.meta.responseScale.isna().all()
    assert not samples.meta.inputValid.any()
    assert samples.y.isna().all().all()
    missing = panel.copy(); missing.loc[(dates[35],SYMBOLS[0]),'close'] = np.nan
    samples = build_samples(missing,dates,strategy)
    invalid_window = (samples.meta.assetSymbol == SYMBOLS[0]) & samples.meta.date.between(dates[36],dates[55])
    assert samples.meta.loc[invalid_window,'responseScale'].isna().all()
