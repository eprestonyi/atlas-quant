#!/usr/bin/env python3
"""Independent stdlib source-only audit. No engine, pandas, provider, or model import.

Matching external content pins NEVER proves owner authorization or vendor origin.
Only directory exports of frozen_market_view/1 are supported in this tranche.
"""
from datetime import datetime
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys

TOTAL=64*1024**2; DOCUMENT=24*1024**2; DESCRIPTOR=256*1024
BASE=set('ts_code trade_date open high low close raw_close vol amount adj_factor'.split())
OPTIONAL=set('turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv'.split())
LEGACY_KEYS=set('source classification synthetic transport retrievedAt symbols start end tradingDates rows dataFingerprint cacheHit providerCalls datasets adjustment calendar warnings observedColumns derivedColumns optionalFieldCoverage'.split())
SCOPE_KEYS=set('format version membershipPolicy symbols symbolCount start end snapshotHash resolutionHash selection catalogSnapshot sourceUniverses steps algorithmVersion historicalMembershipVerified'.split())
ADJUSTMENT='OHLC multiplied by adj_factor / first observed adj_factor per symbol'

class AuditError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

def check(ok,message,code='SOURCE_AUDIT'):
    if not ok:raise AuditError(code,message)
def keys(v,names):check(type(v) is dict and set(v)==set(names),'Unexpected/missing fields','SCHEMA')
def encode(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(s):check(type(s) is str and re.fullmatch('[a-f0-9]{64}',s),'SHA256 required','PIN');return s

def decode(raw,maximum,canonical=False):
    check(type(raw) is bytes and 0<len(raw)<=maximum,'Bounded complete JSON required','BUDGET')
    quoted=False;escape=False;depth=0
    for c in raw:
        if quoted:
            if escape:escape=False
            elif c==92:escape=True
            elif c==34:quoted=False
        elif c==34:quoted=True
        elif c in (91,123):
            depth+=1;check(depth<=40,'Nesting limit','JSON')
        elif c in (93,125):depth-=1
    def pairs(items):
        out={}
        for k,v in items:check(k not in out,'Duplicate JSON key','JSON');out[k]=v
        return out
    def fp(x):
        n=float(x);check(math.isfinite(n),'Nonfinite JSON','JSON');return n
    def invalid(x):raise AuditError('JSON','Nonfinite token')
    try:v=json.loads(raw.decode(),object_pairs_hook=pairs,parse_float=fp,parse_constant=invalid)
    except (ValueError,UnicodeError,RecursionError) as e:
        if isinstance(e,AuditError):raise
        raise AuditError('JSON','Invalid JSON') from e
    if canonical:check(encode(v)==raw,'Descriptor is not canonical','JSON')
    return v

def day(s):
    check(type(s) is str and re.fullmatch('[0-9]{8}',s),'Date string required','DATE')
    try:return datetime.strptime(s,'%Y%m%d')
    except ValueError as e:raise AuditError('DATE','Invalid date') from e

def scope(u,maximum=1000):
    keys(u,{'symbols','start','end'});ss=u['symbols']
    check(type(ss) is list and 0<len(ss)<=maximum and all(type(s) is str and re.fullmatch('[0-9]{6}\\.(SH|SZ)',s) for s in ss) and ss==sorted(set(ss)),'Complete sorted scope required','SCOPE')
    check(day(u['start'])<=day(u['end']),'Reversed interval','SCOPE')

def read(path,maximum):
    check(not any(p.is_symlink() for p in [path,*path.parents]),'Symlink forbidden','FILE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno());check(stat.S_ISREG(info.st_mode) and 0<info.st_size<=maximum,'Regular bounded file required','FILE')
        raw=f.read(maximum+1);check(len(raw)==info.st_size,'File changed/oversized','FILE');return raw

def finite_number(v):
    if type(v) not in (int,float):return False
    try:return math.isfinite(v)
    except OverflowError:return False

def full_rows(rows,meta,u):
    scope(u);check(type(meta) is dict and type(meta.get('synthetic')) is bool,'Original synthetic state missing','EVIDENCE')
    check(type(rows) is list and 0<len(rows)<=300000 and type(rows[0]) is dict,'Complete original rows required','ROWS')
    dates=meta.get('tradingDates');check(type(dates) is list and dates and all(type(d) is str for d in dates) and dates==sorted(set(dates)),'Complete original calendar required','CALENDAR')
    for d in dates:day(d);check(u['start']<=d<=u['end'],'Calendar outside original scope','CALENDAR')
    columns=set(rows[0]);check(BASE<=columns<=BASE|OPTIONAL,'Unregistered/financial columns','COLUMNS')
    seen=set();present=set();first={};date_set=set(dates)
    for i,r in enumerate(rows):
        keys(r,columns);s,d=r['ts_code'],r['trade_date']
        check(type(s) is str and s in u['symbols'] and type(d) is str and d in date_set,'Out-of-scope original row','SCOPE')
        check((s,d) not in seen,'Duplicate row','ROWS');seen.add((s,d));present.add(s)
        for k in columns-{'ts_code','trade_date'}:
            v=r[k];check(finite_number(v) or k in OPTIONAL and v is None,'Finite non-bool original number required','NUMBER')
        check(all(r[k]>0 for k in ('open','high','low','close','raw_close','adj_factor')) and r['vol']>=0 and r['amount']>=0,'Invalid original price/volume','NUMBER')
        check(r['high']+1e-9>=max(r['open'],r['close'],r['low']) and r['low']-1e-9<=min(r['open'],r['close'],r['high']),'Original OHLC inconsistent','NUMBER')
        if s not in first or d<first[s][1]['trade_date']:first[s]=(i,r)
    check(present==set(u['symbols']),'Missing original member','SCOPE')
    check(meta.get('adjustment')==ADJUSTMENT,'Unregistered adjustment convention','ADJUSTMENT')
    for r in rows:
        try:expected=r['raw_close']*r['adj_factor']/first[r['ts_code']][1]['adj_factor']
        except OverflowError as exc:raise AuditError('ADJUSTMENT','Adjustment arithmetic overflow') from exc
        check(math.isfinite(expected) and math.isclose(r['close'],expected,rel_tol=2e-10,abs_tol=2e-10),'Original adjustment basis changed','ADJUSTMENT')
    return [{'symbol':s,'date':first[s][1]['trade_date'],'rowOrdinal':first[s][0],'factorF64':struct.pack('>d',float(first[s][1]['adj_factor'])).hex()} for s in sorted(first)]

def native_original(directory,origin):
    # Existing source-only stdlib auditor verifies every raw receipt and the
    # complete normalized source. No source-selection shortcut is accepted.
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    if str(scripts) not in sys.path:sys.path.insert(0,str(scripts))
    from market_dataset_audit import audit_market_dataset
    report=audit_market_dataset(directory,expected_root=origin['datasetRoot'])
    check(report['status']=='PASS' and report['modelFitted'] is False,'Native whole source failed','NATIVE')
    m=decode(read(directory/'manifest.json',DESCRIPTOR),DESCRIPTOR,True)
    rows=[]
    for p in m['collections']['rows']['chunks']:rows.extend(decode(read(directory/f'parts/rows/{p["ordinal"]}.bin',512*1024),512*1024,True))
    ps=m['collections']['provenance']['chunks'];check(len(ps)==1,'One provenance part required','NATIVE')
    meta=decode(read(directory/'parts/provenance/0.bin',512*1024),512*1024,True)[0]
    return rows,meta,{k:m['scope'][k] for k in ('symbols','start','end')},m['sourceKind'],report['checks']

def audit(directory,*,pins,expected_view_root):
    root=Path(directory).absolute();check(root.is_dir() and not any(p.is_symlink() for p in [root,*root.parents]),'Real source directory required','FILE')
    keys(pins,{'sourceRoot','filterScopeRoot','originals'});digest(pins['sourceRoot']);digest(pins['filterScopeRoot']);digest(expected_view_root)
    source_raw=read(root/'source.json',DESCRIPTOR);s=decode(source_raw,DESCRIPTOR,True)
    check(sha(source_raw)==pins['sourceRoot'],'Source differs from independent pin','PIN')
    keys(s,{'format','version','originKind','origin','originalScope','originalCalendarSha256','originalRows','originalRowsSha256','adjustment','declaredSource','declaredClassification','artifacts','evidence'})
    check(s['format']=='atlas.quant.frozen_market_source' and type(s['version']) is int and s['version']==1,'Unsupported source version','FORMAT')
    kind=s['originKind'];check(kind in ('legacy_cache','market_dataset'),'Origin not implemented','FORMAT')
    artifacts=s['artifacts'];check(type(artifacts) is list and 0<len(artifacts)<=580,'Original artifact budget','BUDGET')
    names=[];total=len(source_raw)
    for d in artifacts:
        keys(d,{'name','sha256','byteLength'});name=d['name'];check(type(name) is str and re.fullmatch(r'cache.json|manifest.json|plan.json|scope.json|parts/(rows|receipts|provenance|raw)/[0-9]+\.bin',name),'Unregistered original path','FILE')
        check(name not in names and type(d['byteLength']) is int and 0<d['byteLength']<=DOCUMENT,'Original size/identity invalid','BUDGET')
        digest(d['sha256']);names.append(name);total+=d['byteLength']
    check(names==sorted(names) and total<=TOTAL,'Complete original closure too large','BUDGET')
    check(type(pins['originals']) is dict and set(pins['originals'])==set(names),'Exact full original external pin set required','PIN')
    all_files=[];all_dirs=[]
    for path in root.rglob('*'):
        check(not path.is_symlink(),'Symlink inside source archive','FILE')
        if path.is_file():all_files.append(path.relative_to(root).as_posix())
        else:
            check(path.is_dir(),'Special archive member','FILE');all_dirs.append(path.relative_to(root).as_posix())
    expected={'source.json','view.json','filter-scope.json','market.json',*['originals/'+n for n in names]}
    expected_dirs={str(p) for n in expected for p in Path(n).parents if str(p)!='.'}
    check(set(all_files)==expected and set(all_dirs)==expected_dirs,'Missing or extra archive files/directories','FILE')
    originals={}
    for d in artifacts:
        n=d['name'];keys(pins['originals'][n],{'sha256','byteLength'})
        check(type(pins['originals'][n]['byteLength']) is int and pins['originals'][n]=={k:d[k] for k in ('sha256','byteLength')},'Original descriptor differs from independent pin','PIN')
        raw=read(root/'originals'/n,d['byteLength']);check(len(raw)==d['byteLength'] and sha(raw)==d['sha256'],'Original bytes changed','PIN');originals[n]=raw
    native_checks=0
    if kind=='legacy_cache':
        check(names==['cache.json'],'Legacy original set differs','SCHEMA');keys(s['origin'],{'decoder','originalSha256'})
        check(s['origin']['decoder']=='provider_cache_rows_provenance_v1' and s['origin']['originalSha256']==sha(originals['cache.json']),'Legacy decoder/pin invalid','FORMAT')
        v=decode(originals['cache.json'],DOCUMENT);keys(v,{'rows','provenance'});rows=v['rows'];meta=v['provenance'];keys(meta,LEGACY_KEYS)
        for k in ('source','classification','transport','retrievedAt','calendar'):check(type(meta[k]) is str and 0<len(meta[k])<=1000,'Invalid legacy string','SCHEMA')
        declared=(meta['source'],meta['classification'],meta['synthetic'],meta['transport'],meta['calendar'])
        known_real=declared in [('TUSHARE_PRO','PROVIDER_DATA',False,t,'Tushare SSE official trading calendar; SH/SZ/BJ session alignment assumed') for t in ('private_proxy','official_https_rest')]
        known_fixture=declared==('SYNTHETIC_CACHE_FIXTURE','SYNTHETIC_FIXTURE',True,'offline_fixture','SYNTHETIC_FIXTURE_CALENDAR_DECLARATION')
        check(type(meta['synthetic']) is bool and (known_real or known_fixture),'Unsupported legacy origin dialect','FORMAT')
        check(type(meta['cacheHit']) is bool and type(meta['providerCalls']) is int and 0<=meta['providerCalls']<=512,'Invalid legacy counters','SCHEMA')
        for k in ('datasets','warnings','observedColumns'):check(type(meta[k]) is list and all(type(x) is str for x in meta[k]),'Legacy provenance list','SCHEMA')
        for k in ('derivedColumns','optionalFieldCoverage'):check(type(meta[k]) is dict,'Legacy provenance object','SCHEMA')
        check(type(rows) is list and type(meta['rows']) is int and len(rows)==meta['rows']<=110000,'Legacy full row count','ROWS')
        check(sha(encode(rows))==digest(meta['dataFingerprint']),'Legacy row fingerprint differs','FINGERPRINT')
        u={k:meta[k] for k in ('symbols','start','end')};receipts=False
    else:
        keys(s['origin'],{'format','version','datasetRoot','sourceKind'})
        check(s['origin']['format']=='atlas.quant.market_dataset' and type(s['origin']['version']) is int and s['origin']['version']==1,'Native origin version','FORMAT')
        rows,meta,u,source_kind,native_checks=native_original(root/'originals',s['origin']);check(source_kind==s['origin']['sourceKind'],'Native original kind changed','EVIDENCE');receipts=True
    bases=full_rows(rows,meta,u)
    evidence={'originalBytesVerified':True,'ownerGrantVerified':False,'filterResolutionVerified':False,'providerReceiptBytesRetained':receipts,'providerOriginIndependentlyAttested':False,'pointInTimeRevisionsVerified':False,'synthetic':meta['synthetic'],'numericReconstruction':'original_responses_recomputed' if kind=='market_dataset' else 'legacy_cache_rows_fingerprint_and_adjustment_checked'}
    check(encode(s['evidence'])==encode(evidence),'Source evidence was upgraded/changed','EVIDENCE')
    check(s['originalScope']==u and type(s['originalRows']) is int and s['originalRows']==len(rows) and s['originalRowsSha256']==sha(encode(rows)) and s['originalCalendarSha256']==sha(encode(meta['tradingDates'])),'Complete original logical identity differs','SOURCE')
    check(encode(s['adjustment'])==encode({'method':'original_base_preserved','declaration':meta['adjustment'],'bases':bases}) and s['declaredSource']==meta.get('source') and s['declaredClassification']==meta.get('classification'),'Original metadata/basis changed','SOURCE')
    scope_raw=read(root/'filter-scope.json',DESCRIPTOR);f=decode(scope_raw,DESCRIPTOR);keys(f,SCOPE_KEYS)
    check(sha(scope_raw)==pins['filterScopeRoot'],'Frozen whole-filter pin differs','PIN')
    check(f['format']=='atlas.quant.universe_scope' and type(f['version']) is int and f['version']==1 and f['membershipPolicy']=='complete_filtered_set' and f['historicalMembershipVerified'] is False,'Whole frozen filter required','SCOPE')
    target={k:f[k] for k in ('symbols','start','end')};scope(target,50)
    check(type(f['symbolCount']) is int and f['symbolCount']==len(f['symbols']) and (day(f['end'])-day(f['start'])).days+1<=366,'Target scope capacity','SCOPE')
    for k in ('snapshotHash','resolutionHash'):digest(f[k])
    keys(f['selection'],{'version','includeGroups','excludeGroups','includeSymbols','excludeSymbols'})
    check(type(f['selection']['version']) is int and f['selection']['version']==1 and all(type(f['selection'][k]) is list for k in ('includeGroups','excludeGroups','includeSymbols','excludeSymbols')),'Filter schema','SCOPE')
    check(type(f['catalogSnapshot']) is dict and f['catalogSnapshot'].get('hash')==f['snapshotHash'] and f['catalogSnapshot'].get('historicalMembershipVerified') is False and type(f['sourceUniverses']) is list and type(f['steps']) is list and type(f['algorithmVersion']) is str and 0<len(f['algorithmVersion'])<=200,'Frozen filter provenance','SCOPE')
    check(set(target['symbols'])<=set(u['symbols']) and u['start']<=target['start']<=target['end']<=u['end'],'Whole-filter view escapes original','SCOPE')
    vr=read(root/'view.json',DESCRIPTOR);v=decode(vr,DESCRIPTOR,True);check(sha(vr)==expected_view_root,'View pin mismatch','PIN')
    t=v.get('transform');keys(t,{'kind','version','mode','universeScopeRef'});ref=t['universeScopeRef'];keys(ref,{'scopeId','scopeRoot','format','version'})
    check(type(ref['scopeId']) is str and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',ref['scopeId']) and ref['scopeRoot']==sha(scope_raw) and ref['format']=='atlas.quant.universe_scope' and type(ref['version']) is int and ref['version']==1,'Filter reference differs','SCOPE')
    check(t['kind']=='frozen_filter_scope_view' and type(t['version']) is int and t['version']==1 and t['mode'] in ('exact','explicit_subset') and (t['mode']=='exact')==(target==u),'Projection mode differs','SCOPE')
    selected=[r for r in rows if r['ts_code'] in target['symbols'] and target['start']<=r['trade_date']<=target['end']]
    check(0<len(selected)<=110000 and {r['ts_code'] for r in selected}==set(target['symbols']),'Complete selected member coverage','SCOPE')
    sessions=[d for d in meta['tradingDates'] if target['start']<=d<=target['end']]
    provenance={'source':'FROZEN_MARKET_SOURCE_VIEW','classification':'LEGACY_CACHE_ORIGIN_UNVERIFIED' if kind=='legacy_cache' else 'FROZEN_MARKET_DATASET_ORIGIN','synthetic':meta['synthetic'],**target,'tradingDates':sessions,'rows':len(selected),'sourceRoot':sha(source_raw),'originKind':kind,'evidence':evidence,'originalAdjustment':s['adjustment'],'projection':t,'rowFingerprintVersion':'exact_json_rows_v1','rowFingerprint':sha(encode(selected))}
    mr=read(root/'market.json',DOCUMENT);decode(mr,DOCUMENT,True)
    check(mr==encode({'schemaVersion':1,'rows':selected,'provenance':provenance}),'Projected values, provenance or relative order differ','PROJECTION')
    expected_view={'format':'atlas.quant.frozen_market_view','version':1,'sourceRoot':sha(source_raw),'transform':t,'targetScope':target,'sourceRows':len(rows),'selectedRows':len(selected),'removedRows':len(rows)-len(selected),'sourceCalendarSha256':sha(encode(meta['tradingDates'])),'selectedCalendarSha256':sha(encode(sessions)),'marketSha256':sha(mr),'marketByteLength':len(mr),'preservesRelativeRowOrder':True,'imputation':'none','warmupExtension':'none','adjustmentRebased':False,'ownerGrantVerified':False,'filterResolutionVerified':False}
    check(vr==encode(expected_view),'View receipt not independently reproduced','PROJECTION')
    total+=len(scope_raw)+len(vr)+len(mr);check(total<=TOTAL,'Full original plus view closure exceeds budget','BUDGET')
    return {'status':'PASS','viewRoot':sha(vr),'sourceRoot':sha(source_raw),'originKind':kind,'originalRows':len(rows),'selectedRows':len(selected),'calendarSessions':len(sessions),'originalBytes':sum(map(len,originals.values())),'closureBytes':total,'allOriginalsVerifiedBeforeProjection':True,'nativeSourceChecks':native_checks,'externalContentPinsMatched':True,'ownerGrantVerified':False,'filterResolutionVerified':False,'providerOriginIndependentlyAttested':False,'pointInTimeRevisionsVerified':False,'providerCalls':0,'researchFits':0,'modelAdmissionRegistered':False,'financialCompositionPerformed':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--pins',type=Path,required=True);p.add_argument('--expected-view-root',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:
        check(a.output!=a.pins and not a.output.exists(),'Output must be new','FILE')
        check(not a.output.resolve().is_relative_to(a.directory.resolve()),'Audit output must remain outside the immutable source directory','FILE')
        check(a.output.parent.is_dir() and not any(x.is_symlink() for x in [a.output,*a.output.parents]),'Real output path required','FILE')
        pins=decode(read(a.pins,DESCRIPTOR),DESCRIPTOR)
        result=audit(a.directory,pins=pins,expected_view_root=a.expected_view_root)
        fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:out.write(encode(result)+b'\n')
        print(json.dumps(result));return 0
    except (ValueError,OSError,KeyError,TypeError,OverflowError) as e:
        print(json.dumps({'status':'FAIL','code':getattr(e,'code',type(e).__name__),'providerCalls':0,'researchFits':0,'ownerGrantVerified':False}));return 2

if __name__=='__main__':raise SystemExit(main())
