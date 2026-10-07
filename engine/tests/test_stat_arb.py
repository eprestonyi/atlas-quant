import copy
import json
import numpy as np
import pandas as pd
import pytest
from atlas_quant.stat_arb import validate_stat_arb, hedge_projection, residual_statistics, run_stat_arb, _signals, _simulate_baskets
from atlas_quant.engine import run_research, _prepare_data, ResearchError


def case():
    rng=np.random.default_rng(739)
    dates=pd.bdate_range('2021-01-01',periods=620).strftime('%Y%m%d').tolist()
    symbols=['000001.SZ','000002.SZ','600000.SH','600036.SH','600519.SH','000333.SZ']
    common=np.cumsum(rng.normal(0,.007,len(dates)))
    deviations=np.zeros((len(dates),len(symbols)))
    for t in range(1,len(dates)):deviations[t]=.85*deviations[t-1]+rng.normal(0,.013,len(symbols))
    close=30*np.exp(common[:,None]+deviations)
    opens=np.vstack([close[0],close[:-1]])*np.exp(rng.normal(0,.001,close.shape))
    rows=[]
    for t,date in enumerate(dates):
        for k,sym in enumerate(symbols):
            o,c=opens[t,k],close[t,k]
            rows.append(dict(ts_code=sym,trade_date=date,open=o,close=c,high=max(o,c)*1.01,low=min(o,c)*.99,raw_close=c,adj_factor=1.,vol=1e6,amount=1e8))
    strategy=dict(schemaVersion=1,name='Synthetic mean-reverting basket acceptance',research=dict(mode='stat_arb',observationDays=1),universe=dict(symbols=symbols,start=dates[0],end=dates[-1]),factors=[],statArb=dict(method='market_residual',entryZ=1.2,exitZ=.3,stopZ=4.,formationDays=126,residualWindow=60,refitDays=20,maxHoldingDays=20,maxHalfLife=60),portfolio=dict(initialCapital=1e6,rebalanceDays=1,rebalanceThresholdBps=25),costs=dict(commissionBps=2.5,slippageBps=3,sellTaxBps=5,transferBps=.1,minCommission=5))
    return strategy,pd.DataFrame(rows),dict(source='TEST_SYNTHETIC_MEAN_REVERSION',synthetic=True,tradingDates=dates)


@pytest.fixture(scope='module')
def result_case():
    s,d,p=case();return s,d,p,run_research(s,d,p)


def test_projection_is_dollar_and_common_exposure_neutral():
    rng=np.random.default_rng(17);r=rng.normal(size=(126,8))
    a=validate_stat_arb(case()[0])['statArb'];a.update(method='pca_residual',components=2)
    M,B,meta=hedge_projection(r,{'value':np.arange(8),'duplicate':np.arange(8)*2},a,dict(decorrelation='drop_correlated',correlationThreshold=.9))
    np.testing.assert_allclose(M@M,M,atol=1e-12)
    np.testing.assert_allclose(B.T@M,0,atol=1e-12)
    np.testing.assert_allclose(M.sum(axis=0),0,atol=1e-12)
    assert any(x['id']=='duplicate' for x in meta['dropped'])


def test_real_basket_report_not_return_forecast(result_case):
    s,d,p,r=result_case
    assert r['research']['mode']=='stat_arb' and r['predictions'] is None
    assert r['metrics']['tradeCount']>0
    assert any(t['positionAfter']<0 for t in r['trades'])
    assert r['selection']['qualified'] is False
    assert r['statArb']['evidence']['shortInventoryVerified'] is False
    assert r['statArb']['evidence']['cointegrationTestPerformed'] is False
    assert r['statArb']['signals']['totalRows']>100
    assert all(f['formationEnd']<f['signalDate'] for f in r['statArb']['modelFits'])
    json.dumps(r,allow_nan=False)


def test_ledger_and_costs_reconcile_including_shorts(result_case):
    _,_,_,r=result_case
    initial=r['strategy']['portfolio']['initialCapital'];cash=initial
    bydate={}
    for t in r['trades']:
        assert t['signalDate']<t['date']
        assert t['cost']==pytest.approx(t['commission']+t['slippage']+t['tax']+t['transfer'])
        bydate.setdefault(t['date'],[]).append(t)
    for row in r['execution']['ledger']:
        for trade in bydate.get(row['date'],[]):cash-=trade['signedQuantity']*trade['price']+trade['cost']
        cash-=row['borrowCost']
        assert cash==pytest.approx(row['cash'],abs=1e-7)
        assert cash+sum(p['value'] for p in row['positions'])==pytest.approx(row['equity'],abs=1e-7)
    assert sum(r['metrics']['costBreakdown'].values())==pytest.approx(r['metrics']['totalCosts'])
    assert r['metrics']['costBreakdown']['borrow']>0
    assert r['statArb']['costComparison']['zeroCostCounterfactual']['totalCosts']==0


def test_no_future_prices_in_earlier_signals_or_models(result_case):
    s,d,p,r=result_case
    later=copy.deepcopy(d);cut=p['tradingDates'][-30]
    mask=later.trade_date>=cut
    later.loc[mask,['open','high','low','close','raw_close']]*=2
    other=run_research(s,later,p)
    before=lambda x:[f for f in x['statArb']['modelFits'] if f['signalDate']<cut]
    signals=lambda x:[f for f in x['statArb']['signals']['rows'] if f['date']<cut]
    assert before(r)==before(other)
    assert signals(r)==signals(other)
    assert [t for t in r['trades'] if t['date']<cut]==[t for t in other['trades'] if t['date']<cut]


def test_added_factor_has_same_window_baseline_comparison(result_case):
    s,d,p,_=result_case;s=copy.deepcopy(s)
    s['factors']=[dict(id='volatility',expression='ts_std(returns(close,1),20)',direction=1)]
    r=run_research(s,d,p)
    comparison=r['statArb']['baselineComparison']
    assert comparison['sameHoldout'] and comparison['sameCosts'] and not comparison['parameterRetuning']
    assert comparison['extendedMetrics']==r['metrics']
    assert any('volatility' in f['exposures'] for f in r['statArb']['modelFits'])


def test_cost_threshold_frequency_and_entry_change_actual_trades(result_case):
    s,d,p,r=result_case
    high=copy.deepcopy(s);high['portfolio']['rebalanceThresholdBps']=10000
    a=run_research(high,d,p)
    assert a['metrics']['tradeCount']<r['metrics']['tradeCount']
    rare=copy.deepcopy(s);rare['research']['observationDays']=5;rare['portfolio']['rebalanceDays']=7
    b=run_research(rare,d,p)
    assert b['statArb']['signals']['totalRows']<r['statArb']['signals']['totalRows']
    assert len(b['equity'])==len(r['equity'])
    assert b['trades']!=r['trades']
    disabled=copy.deepcopy(s);disabled['statArb'].update(entryZ=6,stopZ=10)
    c=run_research(disabled,d,p)
    assert c['metrics']['tradeCount']<r['metrics']['tradeCount']


def test_invalid_or_unproven_shorting_rejected():
    s,_,_=case()
    for delta in [dict(shorting='confirmed'),dict(entryZ=.5,exitZ=1),dict(formationDays=60,residualWindow=126),dict(method='factor_residual')]:
        x=copy.deepcopy(s);x['statArb'].update(delta)
        with pytest.raises(ResearchError):validate_stat_arb(x)


def test_missing_basket_leg_never_silently_fills(result_case):
    s,d,p,_=result_case;s=validate_stat_arb(s)
    panel,dates,_=_prepare_data(d,s,p);sig=_signals(panel,dates,s)
    next_entry=next(i for i in range(sig['holdoutIndex']+1,len(dates)) if np.abs(sig['targets'].get(dates[i-1],np.zeros(6))).sum()>.1)
    panel.loc[(dates[next_entry],sig['symbols'][0]),'vol']=0
    _,_,trades,_,skipped=_simulate_baskets(panel,dates,sig,s)
    assert any(x['reason']=='basket_atomic_wait' and x['date']==dates[next_entry] for x in skipped)
    assert not any(x['date']==dates[next_entry] for x in trades)


def test_three_stock_default_pca_keeps_one_residual_degree_of_freedom():
    s,_,_=case()
    s['universe']['symbols']=s['universe']['symbols'][:3]
    s['statArb']['method']='pca_residual'
    s['statArb'].pop('components',None)
    assert validate_stat_arb(s)['statArb']['components']==1
