"""Offline adapter acquisition: index observations are never synthesized."""
import copy
import pandas as pd
import pytest
from atlas_quant.provider import TushareClient, ProviderError, _load, DATASETS
from atlas_quant.context_sources import FIELDS, REGISTRY, load_context_fields
from test_provider import Session, Response, strategy

class ContextSession(Session):
    def post(self, url, **kwargs):
        payload=kwargs['json'];api=payload['api_name'];params=payload['params']
        if api not in {'index_daily','sw_daily'}:
            return super().post(url,**kwargs)
        self.calls.append((url,kwargs))
        fields=DATASETS[api].split(',')
        rows=[]
        for i,date in enumerate(pd.bdate_range(params['start_date'],params['end_date'])):
            values={'ts_code':params['ts_code'],'trade_date':date.strftime('%Y%m%d'),'close':3000.1234567890123+i,'vol':100.,'amount':200.,'pe':-3.,'pb':2.,'total_mv':300.,'float_mv':100.}
            rows.append([values[f] for f in fields])
        return Response({'code':0,'data':{'fields':fields,'items':rows}})

def test_index_join_one_request_per_source_exact_cache_and_independent_from_pool(tmp_path):
    s=strategy(); fields=['ext_ctx_000300_sh_close','ext_ctx_000300_sh_amount','ext_ctx_801780_si_close']
    assert all(f in FIELDS for f in fields)
    s['factors']=[{'expression':f} for f in fields]
    session=ContextSession();frame,provenance=_load(s,TushareClient('offline',session=session),tmp_path,'offline')
    index_calls=[x for x in session.calls if x[1]['json']['api_name'] in {'index_daily','sw_daily'}]
    assert len(index_calls)==2
    assert all(len(set(g[fields[0]]))==1 for _,g in frame.groupby('trade_date'))
    assert len(provenance['contextSources'])==2
    assert provenance['contextSources'][0]['classification']=='PARSED_PROVIDER_RESPONSE'
    blocked=Session(error={'code':-1})
    cached,p=_load(s,TushareClient('offline',session=blocked),tmp_path,'offline')
    assert not blocked.calls and p['cacheHit']
    pd.testing.assert_frame_equal(frame,cached)
    assert frame[fields[0]].iloc[0]==3000.1234567890123
    # An independent source does not need to be a stock universe member.
    assert all(x[1]['json']['params']['ts_code'] not in s['universe']['symbols'] for x in index_calls)

@pytest.mark.parametrize('api,params',[
 ('index_daily',{'ts_code':'000001.SZ','start_date':'20230102','end_date':'20230106'}),
 ('sw_daily',{'ts_code':'000300.SH','start_date':'20230102','end_date':'20230106'}),
 ('index_daily',{'ts_code':'000300.SH','start_date':'20230102','end_date':'20230106','trade_date':'20230102'}),
])
def test_reject_unregistered_index_request_before_network(api,params):
    session=Session()
    with pytest.raises(ProviderError):TushareClient('offline',session=session).call(api,params)
    assert not session.calls

def test_catalog_has_named_identity_and_no_constituent_backfill():
    assert len(REGISTRY['items'])==37
    assert len({x['ts_code'] for x in REGISTRY['items']})==37
    assert all(x['scope']=='global' for x in FIELDS.values())
    assert not any(x.get('historicalMembershipVerified') for x in FIELDS.values())
