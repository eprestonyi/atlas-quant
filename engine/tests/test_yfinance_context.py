"""Offline Yahoo source contracts; no network or model fitting in this module."""
import copy
from types import SimpleNamespace

import pandas as pd
import pytest

from atlas_quant import yfinance_provider as yahoo
from atlas_quant.context_sources import FIELDS, load_context_fields, validate_context_sources, project_context_records
from atlas_quant.provider import ProviderError, _exact_records, canonical_hash
from test_context_snapshot_audit import make_auditor

FIELD = 'ext_ctx_yf_xsd_close'
DATES = ['20240311', '20240312', '20240318', '20240325', '20240326']


def metadata():
    return {'symbol':'XSD','currency':'USD','instrumentType':'ETF','exchangeTimezoneName':'America/New_York'}


def details():
    return {'provider':'YAHOO_YFINANCE','libraryVersion':'1.7.0','retrievedAt':'2026-10-10T00:00:00Z',
            **{key:value for key,value in metadata().items() if key != 'symbol'},'libraryCalls':1,
            'httpReceipts':[{'host':'query2.finance.yahoo.com','path':'/v8/finance/chart/XSD','status':200,'bytes':123,'sha256':'a'*64}]}


def history_frame():
    dates=['2024-03-08','2024-03-11','2024-03-12','2024-03-15','2024-03-18']
    return pd.DataFrame({'Close':[100.,51.,52.,53.,400.], 'Adj Close':[49.1234567890123,51.,52.,53.,400.],
                         'Volume':[10.]*5,'Dividends':[0.]*5,'Stock Splits':[0.,2.,0.,0.,0.]},
                        index=pd.DatetimeIndex(dates,tz='America/New_York'))


def fixture(monkeypatch):
    def load(params):
        assert params == {'ts_code':'XSD','start_date':'20240304','end_date':DATES[-1]}
        return yahoo.parse_history('XSD',history_frame(),metadata(),params['start_date'],params['end_date']), details()
    monkeypatch.setattr(yahoo,'history',load)
    class Client:
        def call(self,*args): raise AssertionError('Yahoo cannot route through Tushare')
    frame=pd.DataFrame([{'ts_code':symbol,'trade_date':day} for day in DATES for symbol in ['000001.SZ','600000.SH']])
    return load_context_fields(Client(),frame,[FIELD,'ext_ctx_yf_xsd_vol'],DATES,DATES[0],DATES[-1])


def test_yahoo_identity_preserves_tushare_and_omits_fabricated_amount():
    assert FIELDS['ext_ctx_xsd_close']['api'] == 'us_daily_adj'
    assert FIELDS[FIELD]['api'] == 'yfinance_history'
    assert FIELDS[FIELD]['source'] == 'YAHOO_YFINANCE'
    assert 'ext_ctx_yf_xsd_amount' not in FIELDS


def test_direct_adjusted_close_and_source_archive_audit(monkeypatch):
    frame, provenance=fixture(monkeypatch)
    observed=frame.groupby('trade_date')[FIELD].first()
    assert observed.iloc[:4].tolist() == [49.1234567890123,51.,53.,400.]
    assert pd.isna(observed.iloc[4])
    assert frame.iloc[0][FIELD+'__available_date'] == '20240309'
    source=provenance['contextSources'][0]
    assert source['providerDetails']['libraryVersion'] == '1.7.0'
    assert source['records'][0]['close'] == 100.
    assert source['records'][0]['adj_close'] == 49.1234567890123
    validate_context_sources(frame,provenance,[FIELD],DATES,DATES[0],DATES[-1])
    auditor=make_auditor({'provenance':provenance,'rows':_exact_records(frame),'dataFingerprint':'a'*64})
    try: auditor.validate_context_sources(provenance)
    finally: auditor.db.close()


@pytest.mark.parametrize('damage',['currency','instrumentType','exchangeTimezoneName','symbol','missing_adjustment','naive_time','missing_volume','bad_price','out_of_range'])
def test_library_result_requires_etf_identity_complete_adjustment_and_dates(damage):
    frame,meta=history_frame(),metadata()
    if damage in meta: meta[damage]='wrong'
    elif damage=='missing_adjustment': frame=frame.drop(columns='Adj Close')
    elif damage=='naive_time': frame.index=frame.index.tz_localize(None)
    elif damage=='missing_volume': frame.loc[frame.index[0],'Volume']=float('nan')
    elif damage=='bad_price': frame.loc[frame.index[0],'Adj Close']=0
    elif damage=='out_of_range': frame.index=pd.DatetimeIndex(['2023-01-01','2023-01-02','2023-01-03','2023-01-04','2023-01-05'],tz='America/New_York')
    with pytest.raises(ProviderError): yahoo.parse_history('XSD',frame,meta,'20240301','20240331')


@pytest.mark.parametrize('damage',['version','secret','same_day','unadjusted','provider','receipt'])
def test_rehashed_bad_yahoo_archives_fail(monkeypatch,damage):
    frame, provenance=fixture(monkeypatch)
    source=provenance['contextSources'][0]
    if damage=='version': source['providerDetails']['libraryVersion']='other'
    elif damage=='secret': source['providerDetails']['cookie']='must not retain'
    elif damage=='same_day': frame.loc[0,FIELD]=51.
    elif damage=='unadjusted': frame.loc[0,FIELD]=100.
    elif damage=='provider': provenance['externalFields'][FIELD]['source']='TUSHARE_PRO'
    else: source['providerDetails']['httpReceipts'][0]['path']+='?crumb=private'
    provenance['contextSourceRoot']=canonical_hash(provenance['contextSources'])
    with pytest.raises(ProviderError): validate_context_sources(frame,provenance,[FIELD],DATES,DATES[0],DATES[-1])
    auditor=make_auditor({'provenance':provenance,'rows':_exact_records(frame),'dataFingerprint':'a'*64})
    try:
        with pytest.raises(ValueError): auditor.validate_context_sources(provenance)
    finally: auditor.db.close()


def test_library_call_uses_exclusive_end_and_no_adjustment_repair_or_info(monkeypatch):
    import yfinance as yf
    from curl_cffi.requests import Session
    calls=[]
    def request(self,method,url,*args,**kwargs):
        return SimpleNamespace(content=b'{}',status_code=200)
    monkeypatch.setattr(Session,'request',request)
    class Ticker:
        def __init__(self,code,session): self.session=session;self._price_history=SimpleNamespace(_history_metadata=metadata())
        def history(self,**kwargs):
            calls.append(kwargs)
            self.session.request('GET','https://fc.yahoo.com')
            self.session.request('GET','https://query2.finance.yahoo.com/v8/finance/chart/XSD')
            return history_frame()
    monkeypatch.setattr(yf,'Ticker',Ticker)
    frame, evidence=yahoo.history({'ts_code':'XSD','start_date':'20240301','end_date':'20240331'})
    assert len(calls)==1 and calls[0]['end']=='2024-04-01' and calls[0]['start']=='2024-03-01'
    assert all(calls[0][key] == value for key,value in yahoo.OPTIONS.items())
    assert evidence['httpReceipts'][0]['path']=='/'
    assert len(frame)==5 and yahoo.validate_details(evidence)


@pytest.mark.parametrize('case',['error_replay','count','target'])
def test_http_calls_bounded_without_provider_fallback(monkeypatch,case):
    import yfinance as yf
    from curl_cffi.requests import Session
    requests=[]
    def request(self,method,url,*args,**kwargs):
        requests.append(url)
        return SimpleNamespace(content=b'{}',status_code=429 if case=='error_replay' else 200)
    monkeypatch.setattr(Session,'request',request)
    class Ticker:
        def __init__(self,code,session): self.session=session
        def history(self,**kwargs):
            for i in range(9):
                self.session.request('GET','https://bad.invalid/' if case=='target' else 'https://query2.finance.yahoo.com/v8/finance/chart/XSD')
            raise AssertionError('Guard should stop calls')
    monkeypatch.setattr(yf,'Ticker',Ticker)
    with pytest.raises(ProviderError): yahoo.history({'ts_code':'XSD','start_date':'20240301','end_date':'20240331'})
    assert len(requests)=={'error_replay':1,'count':8,'target':0}[case]


def test_unverified_yahoo_source_stops_before_any_call(monkeypatch):
    monkeypatch.setattr(yahoo,'history',lambda params: pytest.fail('unverified source must not acquire'))
    frame=pd.DataFrame([{'ts_code':'000001.SZ','trade_date':'20240311'}])
    with pytest.raises(ProviderError) as failure:
        load_context_fields(None,frame,['ext_ctx_yf_xbi_close'],['20240311'],'20240311','20240311')
    assert failure.value.code == 'YAHOO_HISTORY_UNVERIFIED'
