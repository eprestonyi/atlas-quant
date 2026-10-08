"""Small offline source/view tests; no financial compose, network or model fit."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from atlas_quant.market_source_bridge import (
    SourceError,SourceLimits,freeze_legacy_cache,freeze_market_dataset,derive_view,export_view,
)
from atlas_quant.market_source_bridge.contract import encode,sha
from test_market_research_runner_review import market_source

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('independent_bridge_audit',ROOT/'tools/audit_market_source_bridge.py')
auditor=importlib.util.module_from_spec(spec);spec.loader.exec_module(auditor)


def cache_value():
    symbols=['000001.SZ','600519.SH'];dates=['20240102','20240103','20240104','20240105','20240108','20240109']
    rows=[]
    for t,d in enumerate(dates):
        for j,s in enumerate(symbols):
            factor=2.0 if t<2 else 4.0;close=100.0+t+j
            rows.append({'ts_code':s,'trade_date':d,'open':close,'high':close+1,'low':close-1,'close':close,'raw_close':close*2/factor,'vol':0.0,'amount':0.0,'adj_factor':factor,'pb':-0.0 if t==0 else None})
    meta={'source':'SYNTHETIC_CACHE_FIXTURE','classification':'SYNTHETIC_FIXTURE','synthetic':True,'transport':'offline_fixture','retrievedAt':'2026-10-09T00:00:00Z',
          'symbols':symbols,'start':'20240101','end':'20240110','tradingDates':dates,'rows':len(rows),'dataFingerprint':sha(encode(rows)),'cacheHit':False,'providerCalls':0,
          'datasets':['daily','adj_factor','trade_cal'],'adjustment':'OHLC multiplied by adj_factor / first observed adj_factor per symbol','calendar':'SYNTHETIC_FIXTURE_CALENDAR_DECLARATION','warnings':[],
          'observedColumns':['raw_close','vol','amount','adj_factor'],'derivedColumns':{},'optionalFieldCoverage':{'pb':1/6}}
    return {'rows':rows,'provenance':meta}


def filter_scope(symbols=None,start='20240101',end='20240110'):
    value=json.loads((ROOT/'contracts/fixtures/market-scope-v1.json').read_bytes())
    symbols=symbols or ['000001.SZ','600519.SH'];value.update(symbols=symbols,symbolCount=len(symbols),start=start,end=end)
    value['selection']['includeSymbols']=symbols
    raw=encode(value);ref={'scopeId':str(uuid.uuid4()),'scopeRoot':sha(raw),'format':'atlas.quant.universe_scope','version':1}
    return raw,ref


def frozen(value=None):
    raw=json.dumps(value or cache_value(),ensure_ascii=False,indent=2,allow_nan=False).encode()
    return freeze_legacy_cache(raw,expected_sha256=sha(raw))


def view(source=None):
    source=source or frozen();raw,ref=filter_scope(['600519.SH'],'20240104','20240109')
    return derive_view(source,raw,ref,mode='explicit_subset')


def pins(v):
    return {'sourceRoot':v.source.source_root,'filterScopeRoot':sha(v.scope_bytes),
            'originals':{n:{'sha256':sha(raw),'byteLength':len(raw)} for n,raw in v.source.originals}}


def test_exact_and_subset_preserve_complete_source_and_old_adjustment_base(tmp_path):
    source=frozen();raw,ref=filter_scope();exact=derive_view(source,raw,ref,mode='exact')
    assert json.loads(exact.market_bytes)['rows']==cache_value()['rows']
    assert b'-0.0' in exact.market_bytes
    subset=view(source);out=json.loads(subset.market_bytes)
    assert len(out['rows'])==4 and out['rows'][0]['close']==103.0
    assert out['provenance']['originalAdjustment']['bases'][1]['date']=='20240102'
    assert len(dict(subset.source.originals)['cache.json'])>len(subset.market_bytes)/2
    assert out['provenance']['evidence']['ownerGrantVerified'] is False
    assert out['provenance']['evidence']['providerReceiptBytesRetained'] is False
    assert out['provenance']['evidence']['providerOriginIndependentlyAttested'] is False
    result=export_view(subset,tmp_path/'archive')
    report=auditor.audit(tmp_path/'archive',pins=pins(subset),expected_view_root=subset.view_root)
    assert report['status']=='PASS' and report['selectedRows']==4 and report['originalRows']==12
    assert report['ownerGrantVerified'] is False and report['researchFits']==0
    assert result['modelAdmissionRegistered'] is False
    assert (tmp_path/'archive/originals/cache.json').read_bytes()==dict(source.originals)['cache.json']
    with pytest.raises(SourceError):export_view(subset,tmp_path/'archive')


@pytest.mark.parametrize('attack',['bool','null_required','negative','ohcl','duplicate','missing_member','unknown_column','financial_column','unknown_meta','adjustment','unselected_bad_price','bad_calendar','wrong_count','fingerprint'])
def test_entire_legacy_source_checked_before_view_even_outside_requested_filter(attack):
    v=cache_value();r=v['rows'][0]
    if attack=='bool':r['vol']=True
    elif attack=='null_required':r['amount']=None
    elif attack=='negative':r['adj_factor']=-1.0
    elif attack=='ohcl':r['high']=1.0
    elif attack=='duplicate':v['rows'].append(deepcopy(r))
    elif attack=='missing_member':v['rows']=[x for x in v['rows'] if x['ts_code']!='000001.SZ']
    elif attack=='unknown_column':r['other']=1.0
    elif attack=='financial_column':r['model_fin_cash_asset_share']=0.2
    elif attack=='unknown_meta':v['provenance']['ownerGrantVerified']=True
    elif attack=='adjustment':v['provenance']['adjustment']='rebase_slice'
    elif attack=='unselected_bad_price':r['close']=999.0
    elif attack=='bad_calendar':v['provenance']['tradingDates'].pop(0)
    elif attack=='wrong_count':v['provenance']['rows']+=1
    elif attack=='fingerprint':v['provenance']['dataFingerprint']='0'*64
    if attack!='fingerprint':v['provenance']['dataFingerprint']=sha(encode(v['rows']))
    with pytest.raises((SourceError,ValueError)):view(frozen(v))


@pytest.mark.parametrize('raw',[b'{"rows":[],"rows":[],"provenance":{}}',b'{"rows":[NaN],"provenance":{}}',b'{"rows":[1e999],"provenance":{}}'])
def test_original_json_is_strict_even_though_original_whitespace_is_preserved(raw):
    with pytest.raises(SourceError):freeze_legacy_cache(raw,expected_sha256=sha(raw))


def test_source_and_view_full_budgets_cannot_be_bypassed_by_tiny_selected_scope():
    source=frozen();raw=dict(source.originals)['cache.json']
    with pytest.raises(SourceError):freeze_legacy_cache(raw,expected_sha256=sha(raw),limits=SourceLimits(document_bytes=len(raw)-1))
    only_source=len(raw)+len(source.descriptor_bytes)
    with pytest.raises(SourceError):view(replace(source,rows_bytes=encode(cache_value()['rows'][1:])))
    fr,ref=filter_scope(['600519.SH'],'20240104','20240109')
    with pytest.raises(SourceError) as e:derive_view(source,fr,ref,mode='explicit_subset',limits=SourceLimits(total_bytes=only_source+100))
    assert e.value.code=='SOURCE_BUDGET'
    with pytest.raises(SourceError):SourceLimits(total_bytes=65*1024**2).check()
    unsupported=source.descriptor;unsupported['originKind']='forecast_snapshot'
    with pytest.raises(SourceError) as e:derive_view(replace(source,descriptor_bytes=encode(unsupported)),fr,ref,mode='explicit_subset')
    assert e.value.code=='SOURCE_ORIGIN_UNSUPPORTED'


@pytest.mark.parametrize('attack',['root','manual_subset','count','outside','date','extra','bool_version','mode'])
def test_membership_is_exactly_the_complete_pinned_filter_scope(attack):
    source=frozen();raw,ref=filter_scope();f=json.loads(raw)
    if attack=='root':ref['scopeRoot']='0'*64
    elif attack=='manual_subset':f['membershipPolicy']='selected_subset'
    elif attack=='count':f['symbolCount']=1
    elif attack=='outside':f['symbols']=['999999.SH'];f['symbolCount']=1
    elif attack=='date':f['start']='20231229'
    elif attack=='extra':f['selectedSymbols']=['600519.SH']
    elif attack=='bool_version':f['version']=True
    raw=encode(f)
    if attack!='root':ref['scopeRoot']=sha(raw)
    with pytest.raises(SourceError):derive_view(source,raw,ref,mode='explicit_subset' if attack=='mode' else 'exact')


def native_frozen(market_source,limits=SourceLimits(),read_log=None):
    job,meta,responses,_=market_source;docs=meta['documents'];root=job['marketDatasetRef']['datasetRoot']
    def read(c,i):
        if read_log is not None:read_log.append((c,i))
        return responses[job['marketInputUrl'].replace('/input',f'/parts/{c}/{i}?datasetRoot={root}')]
    return freeze_market_dataset(responses[docs['manifest']['url']],responses[docs['plan']['url']],responses[docs['scope']['url']],read,expected_root=root,limits=limits)


def test_native_market_dataset_preserves_all_raw_and_independent_full_recomposition(tmp_path,market_source):
    source=native_frozen(market_source);u=source.descriptor['originalScope']
    raw,ref=filter_scope([u['symbols'][-1]],'20240603','20240628')
    v=derive_view(source,raw,ref,mode='explicit_subset');export_view(v,tmp_path/'native')
    report=auditor.audit(tmp_path/'native',pins=pins(v),expected_view_root=v.view_root)
    assert report['status']=='PASS' and report['nativeSourceChecks']>100
    assert report['allOriginalsVerifiedBeforeProjection'] is True
    assert source.descriptor['evidence']['synthetic'] is True
    assert source.descriptor['origin']['sourceKind']=='fixture'
    assert source.descriptor['evidence']['providerReceiptBytesRetained'] is True
    assert source.descriptor['evidence']['providerOriginIndependentlyAttested'] is False
    calls=[]
    with pytest.raises(SourceError):native_frozen(market_source,SourceLimits(total_bytes=1000),calls)
    assert calls==[], 'An oversized complete origin must fail before fetching parts'


@pytest.mark.parametrize('attack',['market_value','market_bool','owner_upgrade','dropped_member','origin_change','scope_change','extra_file'])
def test_independent_audit_rejects_rehashed_changed_views_and_false_authority(tmp_path,attack):
    v=view();export_view(v,tmp_path/'view');external=pins(v);root=v.view_root
    if attack=='origin_change':
        p=tmp_path/'view/originals/cache.json';p.write_bytes(p.read_bytes()+b' ')
    elif attack=='scope_change':
        p=tmp_path/'view/filter-scope.json';s=json.loads(p.read_bytes());s['selection']['includeSymbols']=[];p.write_bytes(encode(s))
    elif attack=='extra_file':(tmp_path/'view/extra').write_text('x')
    else:
        p=tmp_path/'view/market.json';m=json.loads(p.read_bytes())
        if attack=='market_value':m['rows'][0]['close']+=1
        elif attack=='market_bool':m['rows'][0]['vol']=False
        elif attack=='owner_upgrade':m['provenance']['evidence']['ownerGrantVerified']=True
        else:m['rows']=m['rows'][:-1]
        raw=encode(m);p.write_bytes(raw);mp=tmp_path/'view/view.json';manifest=json.loads(mp.read_bytes());manifest['marketSha256']=sha(raw);manifest['marketByteLength']=len(raw);mp.write_bytes(encode(manifest));root=sha(encode(manifest))
    with pytest.raises((auditor.AuditError,ValueError,OSError)):auditor.audit(tmp_path/'view',pins=external,expected_view_root=root)


def test_stdlib_cli_audits_without_engine_or_site_packages_and_never_overwrites(tmp_path):
    v=view();export_view(v,tmp_path/'view');pin_path=tmp_path/'pins.json';pin_path.write_bytes(encode(pins(v)))
    command=[sys.executable,'-S',str(ROOT/'tools/audit_market_source_bridge.py'),str(tmp_path/'view'),'--pins',str(pin_path),'--expected-view-root',v.view_root,'--output',str(tmp_path/'audit.json')]
    first=subprocess.run(command,capture_output=True,text=True,env={**os.environ,'PYTHONPATH':''},timeout=30)
    assert first.returncode==0,first.stdout+first.stderr
    saved=(tmp_path/'audit.json').read_bytes();r=json.loads(saved)
    assert r['ownerGrantVerified'] is False and r['researchFits']==r['providerCalls']==0
    second=subprocess.run(command,capture_output=True,text=True,env={**os.environ,'PYTHONPATH':''},timeout=30)
    assert second.returncode==2 and (tmp_path/'audit.json').read_bytes()==saved


def test_frozen_scope_keeps_exact_foreign_json_number_encoding(tmp_path):
    raw,ref=filter_scope(['600519.SH'],'20240104','20240109')
    f=json.loads(raw);f['steps']=[{'threshold':0.000001,'notAnAuthorization':True}]
    # JS JSON.stringify uses decimal notation here; Python uses 1e-06.
    raw=encode(f).replace(b'1e-06',b'0.000001');assert raw!=encode(f)
    ref['scopeRoot']=sha(raw)
    v=derive_view(frozen(),raw,ref,mode='explicit_subset');assert v.scope_bytes==raw
    export_view(v,tmp_path/'scope');report=auditor.audit(tmp_path/'scope',pins=pins(v),expected_view_root=v.view_root)
    assert report['status']=='PASS' and report['filterResolutionVerified'] is False
    assert (tmp_path/'scope/filter-scope.json').read_bytes()==raw


@pytest.mark.parametrize('attack',['huge_integer','empty_directory','numeric_evidence','original_bad_unselected','declared_whole_budget'])
def test_closed_source_contract_rejections_are_independently_enforced(tmp_path,attack):
    v=view();out=tmp_path/'source';export_view(v,out);external=pins(v)
    s=json.loads((out/'source.json').read_bytes())
    if attack=='empty_directory':
        (out/'unexpected').mkdir()
    elif attack=='numeric_evidence':
        s['evidence']['originalBytesVerified']=1
    elif attack=='declared_whole_budget':
        s['artifacts']=[{'name':n,'sha256':'a'*64,'byteLength':24*1024**2} for n in ['cache.json','manifest.json','plan.json']]
    else:
        c=json.loads((out/'originals/cache.json').read_bytes())
        if attack=='huge_integer':c['rows'][0]['vol']=10**400
        else:c['rows'][0]['close']=999
        c['provenance']['dataFingerprint']=sha(encode(c['rows']))
        raw=encode(c);(out/'originals/cache.json').write_bytes(raw)
        s['origin']['originalSha256']=sha(raw);s['artifacts'][0].update(sha256=sha(raw),byteLength=len(raw))
        external['originals']['cache.json']={'sha256':sha(raw),'byteLength':len(raw)}
        with pytest.raises(SourceError):freeze_legacy_cache(raw,expected_sha256=sha(raw))
    sr=encode(s);(out/'source.json').write_bytes(sr);external['sourceRoot']=sha(sr)
    with pytest.raises(auditor.AuditError) as e:auditor.audit(out,pins=external,expected_view_root=v.view_root)
    if attack=='declared_whole_budget':assert e.value.code=='BUDGET'


def test_native_corrupt_unselected_raw_part_is_not_hidden_by_projection(market_source):
    job,meta,responses,_=market_source;docs=meta['documents'];dataset_root=job['marketDatasetRef']['datasetRoot']
    manifest=json.loads(responses[docs['manifest']['url']]);last=manifest['rawArchive']['chunks'][-1]['ordinal']
    def tampered(c,i):
        raw=responses[job['marketInputUrl'].replace('/input',f'/parts/{c}/{i}?datasetRoot={dataset_root}')]
        return raw+b' ' if c=='raw' and i==last else raw
    # Full original source ingestion fails before there is any selected-view argument.
    with pytest.raises(ValueError):freeze_market_dataset(responses[docs['manifest']['url']],responses[docs['plan']['url']],responses[docs['scope']['url']],tampered,expected_root=dataset_root)


@pytest.mark.parametrize('key,value',[('source','UNIMPLEMENTED_PROVIDER'),('calendar','arbitrary assertion'),('transport','unknown_transport'),('synthetic',False)])
def test_legacy_origin_dialect_is_closed_and_never_upgraded(key,value):
    v=cache_value();v['provenance'][key]=value
    with pytest.raises(SourceError) as e:frozen(v)
    assert e.value.code=='SOURCE_ORIGIN_UNSUPPORTED'


def test_audit_cli_does_not_insert_its_report_into_the_audited_immutable_directory(tmp_path):
    v=view();export_view(v,tmp_path/'view');pin=tmp_path/'pins.json';pin.write_bytes(encode(pins(v)))
    command=[sys.executable,'-S',str(ROOT/'tools/audit_market_source_bridge.py'),str(tmp_path/'view'),'--pins',str(pin),'--expected-view-root',v.view_root,'--output',str(tmp_path/'view/audit.json')]
    result=subprocess.run(command,capture_output=True,text=True,env={**os.environ,'PYTHONPATH':''},timeout=30)
    assert result.returncode==2 and not (tmp_path/'view/audit.json').exists()
    assert auditor.audit(tmp_path/'view',pins=pins(v),expected_view_root=v.view_root)['status']=='PASS'


def test_export_fsyncs_files_created_directories_and_parent_entries(tmp_path,monkeypatch):
    from atlas_quant.market_source_bridge import archive
    import stat
    synced=[];real=archive.os.fsync
    def sync(fd):
        info=os.fstat(fd);synced.append((info.st_dev,info.st_ino,stat.S_ISDIR(info.st_mode)))
        return real(fd)
    monkeypatch.setattr(archive.os,'fsync',sync)
    v=view();out=tmp_path/'durable';result=export_view(v,out)
    for path in [tmp_path,out,out/'originals']:
        info=path.stat();assert (info.st_dev,info.st_ino,True) in synced
    assert sum(not d for _,_,d in synced)==result['files']==5
    assert auditor.audit(out,pins=pins(v),expected_view_root=v.view_root)['status']=='PASS'


@pytest.mark.parametrize('kind',['file','created_directory','parent_directory','final_directory'])
def test_export_fsync_failure_never_reports_success_and_keeps_partial(tmp_path,monkeypatch,kind):
    from atlas_quant.market_source_bridge import archive
    import stat
    out=tmp_path/'failed';real=archive.os.fsync;files=0;parent_id=(tmp_path.stat().st_dev,tmp_path.stat().st_ino)
    def sync(fd):
        nonlocal files
        info=os.fstat(fd);directory=stat.S_ISDIR(info.st_mode)
        if not directory:files+=1
        fail=(kind=='file' and not directory or kind=='created_directory' and directory and (info.st_dev,info.st_ino)!=parent_id
              or kind=='parent_directory' and directory and (info.st_dev,info.st_ino)==parent_id
              or kind=='final_directory' and directory and files==5)
        if fail:raise OSError('SIMULATED_FSYNC_FAILURE')
        return real(fd)
    monkeypatch.setattr(archive.os,'fsync',sync)
    with pytest.raises(OSError,match='SIMULATED_FSYNC_FAILURE'):export_view(view(),out)
    assert out.is_dir()
    if kind=='file':assert (out/'source.json').stat().st_size>0
    with pytest.raises(SourceError):export_view(view(),out)


def test_export_disk_drop_after_first_file_stops_remaining_writes(tmp_path,monkeypatch):
    from atlas_quant.market_source_bridge import archive
    from types import SimpleNamespace
    import stat
    free=501*1024**2;checks=[];writes=[];real_sync=archive.os.fsync;real_write=archive.os.write
    def usage(fd):checks.append(free);return SimpleNamespace(free=free)
    def sync(fd):
        nonlocal free
        if stat.S_ISREG(os.fstat(fd).st_mode):free=499*1024**2
        return real_sync(fd)
    def write(fd,raw):writes.append(free);return real_write(fd,raw)
    monkeypatch.setattr(archive.shutil,'disk_usage',usage);monkeypatch.setattr(archive.os,'fsync',sync);monkeypatch.setattr(archive.os,'write',write)
    out=tmp_path/'diskdrop'
    with pytest.raises(SourceError) as e:export_view(view(),out)
    assert e.value.code=='SOURCE_DISK' and len(checks)>1 and len(writes)==1
    assert writes[0]>=500*1024**2 and (out/'source.json').is_file() and not (out/'view.json').exists()


def test_export_checks_final_floor_even_after_all_bytes_and_directory_syncs(tmp_path,monkeypatch):
    from atlas_quant.market_source_bridge import archive
    real=archive._space;remaining_values=[]
    def space(fd,remaining):
        remaining_values.append(remaining)
        if remaining_values.count(0)==3:raise SourceError('SOURCE_DISK','SIMULATED_FINAL_FLOOR_FAILURE')
        return real(fd,remaining)
    monkeypatch.setattr(archive,'_space',space);out=tmp_path/'finalfloor'
    with pytest.raises(SourceError,match='SIMULATED_FINAL_FLOOR_FAILURE'):export_view(view(),out)
    assert remaining_values.count(0)==3 and len([p for p in out.rglob('*') if p.is_file()])==5


def test_export_reserves_all_remaining_payload_and_metadata_before_creation(tmp_path,monkeypatch):
    from atlas_quant.market_source_bridge import archive
    from types import SimpleNamespace
    v=view();raw_bytes=sum(len(b) for _,b in v.source.originals)+len(v.source.descriptor_bytes)+len(v.scope_bytes)+len(v.market_bytes)+len(v.manifest_bytes)
    monkeypatch.setattr(archive.shutil,'disk_usage',lambda fd:SimpleNamespace(free=archive.RESERVE+raw_bytes))
    out=tmp_path/'insufficient_remaining'
    with pytest.raises(SourceError) as e:export_view(v,out)
    assert e.value.code=='SOURCE_DISK' and not out.exists()


@pytest.mark.parametrize('replace_name',['archive','originals'])
def test_export_replaced_directory_symlink_never_writes_outside(tmp_path,monkeypatch,replace_name):
    from atlas_quant.market_source_bridge import archive
    outside=tmp_path/'outside';outside.mkdir();out=tmp_path/'archive';real=archive.os.mkdir
    def mkdir(name,*args,**kwargs):
        result=real(name,*args,**kwargs)
        if name==replace_name:
            fd=kwargs['dir_fd'];os.rmdir(name,dir_fd=fd);os.symlink(str(outside),name,dir_fd=fd)
        return result
    monkeypatch.setattr(archive.os,'mkdir',mkdir)
    with pytest.raises((OSError,SourceError)):export_view(view(),out)
    assert not list(outside.iterdir())
    assert (out if replace_name=='archive' else out/'originals').is_symlink()


def test_export_detects_ancestor_changed_after_open_before_success(tmp_path,monkeypatch):
    from atlas_quant.market_source_bridge import archive
    out=tmp_path/'archive';moved=tmp_path/'retained-partial';outside=tmp_path/'outside';outside.mkdir();real=archive.os.write;changed=False
    def write(fd,raw):
        nonlocal changed
        result=real(fd,raw)
        if not changed:
            changed=True;out.rename(moved);out.symlink_to(outside,target_is_directory=True)
        return result
    monkeypatch.setattr(archive.os,'write',write)
    with pytest.raises(SourceError) as e:export_view(view(),out)
    assert e.value.code=='SOURCE_PATH' and not list(outside.iterdir())
    assert (moved/'source.json').is_file() and not (moved/'view.json').exists()


@pytest.mark.parametrize('fault',['short','zero','raised'])
def test_export_write_fault_keeps_partial_and_never_returns_success(tmp_path,monkeypatch,fault):
    from atlas_quant.market_source_bridge import archive
    real=archive.os.write
    def write(fd,raw):
        if fault=='raised':raise OSError('SIMULATED_WRITE_FAILURE')
        if fault=='zero':return 0
        assert len(raw)>7
        return real(fd,raw[:7])
    monkeypatch.setattr(archive.os,'write',write);out=tmp_path/'short'
    with pytest.raises((SourceError,OSError)):export_view(view(),out)
    assert (out/'source.json').stat().st_size==(7 if fault=='short' else 0)
    assert not (out/'view.json').exists()
