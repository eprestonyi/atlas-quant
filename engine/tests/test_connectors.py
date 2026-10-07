import copy

import numpy as np
import pandas as pd
import pytest

from atlas_quant.connectors import (EXTRA_DATASETS, FINANCIAL_FIELDS, build_universe_catalog,
    join_financial_asof, load_financial_history, validate_endpoint, validate_external_fields, join_pcd_asof, PCDReadClient)
from atlas_quant.fixtures import make_demo_data
from atlas_quant.provider import ProviderError, TushareClient, validate_upload


def test_extra_endpoints_remain_strictly_bounded_and_read_only():
    validate_endpoint('stock_basic', {'exchange':'SSE','list_status':'D'})
    validate_endpoint('index_member_all', {'l1_code':'801010.SI','is_new':'N'})
    validate_endpoint('fina_indicator', {'ts_code':'600000.SH','start_date':'20200101','end_date':'20201231'})
    for api, params in [('stock_basic',{'token':'x'}),('stock_basic',{'exchange':'unknown'}),
                        ('index_member_all',{}),('index_member_all',{'ts_code':'600000.SH','is_new':'arbitrary'}),
                        ('fina_indicator',{'ts_code':'600000.SH'}),('fina_indicator',{'ts_code':'600000.SH','period':'20200230'})]:
        with pytest.raises(ProviderError):validate_endpoint(api,params)


def panel():
    dates=['20230428','20230504','20230505','20230508','20230509']
    return pd.DataFrame({'ts_code':['600000.SH']*len(dates),'trade_date':dates}),dates


def reports(rows):
    return pd.DataFrame([{'ts_code':'600000.SH','ann_date':ann,'end_date':period,'roe':value}
                         for ann,period,value in rows])


def test_fundamentals_first_session_after_announcement_never_period_end():
    data,dates=panel()
    financial=reports([('20230428','20230331',12.)])
    frame,p=join_financial_asof(data,financial,['fd_roe'],dates)
    assert np.isnan(frame.loc[0,'fd_roe'])
    assert frame.loc[1,'fd_roe']==12
    assert frame.loc[1,'fd_roe__available_date']=='20230504'
    assert p['externalFields']['fd_roe']['availabilityPolicy']=='point_in_time_asof'
    assert p['financialRevisionHistory']=='PROVIDER_ORIGINAL_AS_PUBLISHED_VERSIONS_UNVERIFIED'


def test_late_old_period_revision_does_not_replace_newer_report_and_null_not_backfilled():
    data,dates=panel()
    financial=reports([('20230401','20221231',9.),('20230428','20230331',12.),
                       ('20230504','20221231',8.),('20230508','20230331',None)])
    frame,_=join_financial_asof(data,financial,['fd_roe'],dates)
    assert frame.fd_roe.tolist()[:4]==[9.,12.,12.,12.]
    assert np.isnan(frame.loc[4,'fd_roe'])
    assert frame.loc[4,'fd_roe__available_date'] is None


def test_conflicting_same_announcement_is_null_and_audited_not_silently_selected():
    data,dates=panel()
    conflicted=reports([('20230428','20230331',12.),('20230428','20230331',13.),('20230428','20230331',12.)])
    frame,p=join_financial_asof(data,conflicted,['fd_roe'],dates)
    assert frame.fd_roe.isna().all() and p['financialAmbiguousDisclosures']==1
    assert p['financialAmbiguitySample'][0]['field']=='roe'
    good=reports([('20230428','20230331',12.),('20230428','20230331',12.)])
    assert join_financial_asof(data,good,['fd_roe'],dates)[0].loc[1,'fd_roe']==12.


def test_future_disclosures_never_backfill_earlier_dates():
    data,dates=panel()
    frame,_=join_financial_asof(data,reports([('20230509','20230331',12.)]),['fd_roe'],dates)
    assert frame.fd_roe.isna().all()


def test_external_upload_preserves_numeric_alias_dates_and_provenance():
    strategy={'universe':{'symbols':['600000.SH'],'start':'20230102','end':'20230201'}}
    data,_=make_demo_data(strategy)
    rows=data.to_dict('records')
    for r in rows:r.update(pcd_reported_ratio=.12,pcd_reported_ratio__available_date='20230102')
    metadata={'pcd_reported_ratio':{'source':'PCD','path':'A4.reported_ratio','dataType':'number','availabilityPolicy':'point_in_time_asof','availableDateColumn':'pcd_reported_ratio__available_date'}}
    dataset={'rows':rows,'provenance':{'externalFields':metadata}}
    frame,p=validate_upload(strategy,dataset)
    assert frame.pcd_reported_ratio.eq(.12).all()
    assert p['externalFields']['pcd_reported_ratio']['path']=='A4.reported_ratio'
    assert len(p['externalAvailabilityFingerprint'])==64
    for change,code in [('future','EXTERNAL_FUTURE_DATA'),('text','EXTERNAL_FIELD_TYPE'),('mapping','EXTERNAL_FIELD_MAPPING')]:
        bad=copy.deepcopy(dataset)
        if change=='future':bad['rows'][0]['pcd_reported_ratio__available_date']='20230103'
        elif change=='text':bad['rows'][0]['pcd_reported_ratio']='0.12'
        else:bad['provenance']['externalFields']={}
        with pytest.raises(ProviderError) as e:validate_upload(strategy,bad)
        assert e.value.code==code


def test_bounded_financial_fetch_includes_prestart_disclosures():
    class Client:
        def __init__(self):self.calls=[]
        def call(self,api,params):
            self.calls.append((api,params))
            return reports([(params['end_date'],params['end_date'],1.)])
    c=Client(); result=load_financial_history(c,'600000.SH','20230101','20240930')
    assert len(result)==2 and c.calls[0][1]['start_date']=='20210101'
    assert c.calls[-1][1]['end_date']=='20240930'


def test_truncated_financial_interval_is_split_without_using_partial_rows():
    class Client:
        def __init__(self): self.calls=[]
        def call(self,api,params):
            self.calls.append(params)
            if params['start_date']=='20210101' and params['end_date']=='20221231':
                raise ProviderError('TUSHARE_TRUNCATED','bounded split required')
            return reports([(params['end_date'],params['end_date'],1.)])
    c=Client(); frame=load_financial_history(c,'600000.SH','20230101','20231231')
    assert len(c.calls)==4 and len(frame)==3
    assert c.calls[1]['end_date'] < c.calls[2]['start_date']


def test_universe_retains_excluded_source_identifiers_but_never_in_a_share_pools():
    class Client:
        def call(self,api,params):
            columns=EXTRA_DATASETS[api].split(',')
            codes=['600000.SH','600001.SH','600002.SH','900901.SH','T600018.SH']
            if params=={'exchange':'SSE','list_status':'L'}:
                return pd.DataFrame([[s,s.split('.')[0],s,'上海','银行','主板','SSE','L','20000101',None,'H'] for s in codes],columns=columns)
            return pd.DataFrame(columns=columns)
    result=build_universe_catalog(Client(),include_industries=False)
    assert result['counts']['securities']==5 and result['counts']['currentlyListed']==3
    assert result['counts']['excludedFromASharePools']==2
    assert all(len(p['symbols'])==3 for p in result['items'])


def test_universes_keep_complete_source_memberships_and_no_invented_index():
    class Client:
        calls=0
        def call(self,api,params):
            self.calls+=1
            if api=='stock_basic':
                fields=EXTRA_DATASETS[api].split(',')
                if params=={'exchange':'SSE','list_status':'L'}:
                    return pd.DataFrame([[f'60000{i}.SH',f'60000{i}',f'Name{i}','广东','银行','主板','SSE','L','20000101',None,'H'] for i in range(7)],columns=fields)
                return pd.DataFrame(columns=fields)
            raise ProviderError('TUSHARE_PERMISSION','unavailable')
    c=Client(); result=build_universe_catalog(c,indexes=[('000300.SH','沪深300')])
    assert result['counts']['securities']==7
    assert all(p['memberCount']==7 and len(p['symbols'])==7 for p in result['items'])
    assert all(not p['historicalMembershipVerified'] for p in result['items'])
    assert not any(p['category']=='index' for p in result['items'])
    assert {g['source'] for g in result['gaps']}=={'index_member_all','index_weight'}


class PCDExample:
    def __init__(self):
        self.data={
            '/v1/records/record':{'record':{'record_id':'record','entity_id':'entity','context_id':'context','created_at':'2023-01-01T00:00:00Z'},'cells':[{'field_id':'A4.ratio','dtype':'decimal'}]},
            '/v1/registry/context/context':{'period_id':'period'},
            '/v1/registry/period/period':{'end_date':'2023-03-31'},
            '/v1/observations/o1':{'observation':{'observation_id':'o1','record_id':'record','field_id':'A4.ratio','value_state':'PRESENT','value_decimal':'12.125','unit_code':'percent','assertion_kind':'REPORTED','recorded_at':'2023-05-04T04:00:00Z'},'evidence':{'document_id':'document'},'inputs':[]},
            '/v1/observations/o2':{'observation':{'observation_id':'o2','record_id':'record','field_id':'A4.ratio','value_state':'UNKNOWN','value_decimal':None,'unit_code':'percent','assertion_kind':'REPORTED','recorded_at':'2023-05-08T04:00:00Z'},'evidence':{'document_id':'document'},'inputs':[]},
            '/v1/sources/document':{'publication_precision':'UNKNOWN','retrieved_at':'2023-04-28T00:00:00Z'},
        }
    def get(self,path):return copy.deepcopy(self.data[path])
    def selections(self,*args):return [
        {'record_id':'record','field_id':'A4.ratio','observation_id':'o1','revision':1,'recorded_at':'2023-05-04T05:00:00Z'},
        {'record_id':'record','field_id':'A4.ratio','observation_id':'o2','revision':2,'recorded_at':'2023-05-08T05:00:00Z'}]


def test_pcd_unknown_publication_is_bounded_by_known_time_and_selection_history():
    data,dates=panel()
    binding={'pcd_ratio':{'fieldId':'A4.ratio','unitCode':'percent','records':[{'ts_code':'600000.SH','entityId':'entity','recordId':'record'}]}}
    frame,p=join_pcd_asof(data,dates,binding,PCDExample())
    assert frame.pcd_ratio.isna().tolist()==[True,True,False,False,True]
    assert frame.loc[2,'pcd_ratio']==12.125
    assert frame.loc[2,'pcd_ratio__available_date']=='20230505'
    assert p['pcdSelectionEvents']==2 and len(p['pcdSnapshotHash'])==64
    assert p['externalFields']['pcd_ratio']['source']=='PCD'


def test_pcd_mapping_never_guesses_scope_units_or_security_identity():
    data,dates=panel()
    base={'pcd_ratio':{'fieldId':'A4.ratio','unitCode':'percent','records':[{'ts_code':'600000.SH','entityId':'entity','recordId':'record'}]}}
    for changed,code in [('entity','PCD_IDENTITY'),('unit','PCD_UNIT_MISMATCH'),('repeat','PCD_MAPPING_DUPLICATE')]:
        binding=copy.deepcopy(base)
        if changed=='entity':binding['pcd_ratio']['records'][0]['entityId']='other'
        elif changed=='unit':binding['pcd_ratio']['unitCode']='USD'
        else:binding['pcd_ratio']['records']*=2
        with pytest.raises(ProviderError) as e:join_pcd_asof(data,dates,binding,PCDExample())
        assert e.value.code==code


def test_pcd_reader_cannot_send_credential_to_arbitrary_endpoints():
    for url in ['http://yicapital-pcd-v3.eprestonyi.workers.dev','https://example.com','https://user@yicapital-pcd-v3.eprestonyi.workers.dev','https://yicapital-pcd-v3.eprestonyi.workers.dev/other']:
        with pytest.raises(ProviderError) as e:PCDReadClient(url,'secret'*8)
        assert e.value.code=='PCD_ENDPOINT'


def test_pcd_supports_32_fields_and_rejects_conflicting_entity_mapping():
    data,dates=panel()
    bindings={f'pcd_ratio_{i}':{'fieldId':'A4.ratio','unitCode':'percent','records':[{'ts_code':'600000.SH','entityId':'entity','recordId':'record'}]} for i in range(32)}
    frame,provenance=join_pcd_asof(data,dates,bindings,PCDExample())
    assert len(provenance['externalFields'])==32 and frame.loc[2,'pcd_ratio_31']==12.125
    bindings['pcd_ratio_31']['records'][0]['entityId']='different'
    with pytest.raises(ProviderError) as error:
        join_pcd_asof(data,dates,bindings,PCDExample())
    assert error.value.code=='PCD_ENTITY_CONFLICT'


def test_pcd_distinct_period_records_are_allowed_same_period_scope_is_not():
    data,dates=panel();client=PCDExample()
    client.data['/v1/records/older']={'record':{'record_id':'older','entity_id':'entity','context_id':'older_context','created_at':'2023-01-01T00:00:00Z'},'cells':[{'field_id':'A4.ratio','dtype':'decimal'}]}
    client.data['/v1/registry/context/older_context']={'period_id':'older_period'}
    client.data['/v1/registry/period/older_period']={'end_date':'2022-12-31'}
    original=client.selections
    client.selections=lambda record_id,field_id: [] if record_id=='older' else original(record_id,field_id)
    bindings={'pcd_ratio':{'fieldId':'A4.ratio','unitCode':'percent','records':[{'ts_code':'600000.SH','entityId':'entity','recordId':r} for r in ['record','older']]}}
    frame,_=join_pcd_asof(data,dates,bindings,client)
    assert frame.loc[2,'pcd_ratio']==12.125
    client.data['/v1/registry/period/older_period']['end_date']='2023-03-31'
    with pytest.raises(ProviderError) as error:
        join_pcd_asof(data,dates,bindings,client)
    assert error.value.code=='PCD_SCOPE_AMBIGUOUS'
