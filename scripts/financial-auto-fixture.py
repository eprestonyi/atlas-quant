#!/usr/bin/env python3
"""Explicit artificial 50-security financial source for capacity, never market evidence."""
from dataclasses import asdict
from datetime import date,timedelta
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from atlas_quant import bundle
from atlas_quant.connectors import EXTRA_DATASETS
from atlas_quant.financial_runner.trust import calendar_scope
from atlas_quant.financial_statements import FIELDS, RECIPES, UnitEvidence, TradingCalendar
from atlas_quant.financial_statements.package import freeze_package,prepare_package,raw_input,_encoded
from atlas_quant.financial_statements.results import canonical_hash
from atlas_quant.financial_statements.unit_bindings import DeclaredUnitBinding
from atlas_quant.fixtures import make_demo_data
from atlas_quant.research_dataset import (FinancialSource,DatasetReader,compose_snapshot_dataset_components,
    derive_market_snapshot_view,export_dataset_archive,extract_dataset_archive)
from atlas_quant.research_dataset.codec import encode,sha
from atlas_quant.research_dataset.compose import prepared_payload
from atlas_quant.runner_artifacts import freeze_input
from atlas_quant.statistical_quant.schema import validate,digest,prediction_config

PROVIDER='ATLAS_SYNTHETIC_FINANCIAL_CAPACITY_V1'
SCOPE={'symbols':[f'{100000+i:06d}.SZ' for i in range(50)],'start':'20240101','end':'20241231'}

def write(path,raw):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with path.open('xb') as stream:
        os.chmod(path,0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())

def calendar():
    day,end=date(2021,1,1),date(2025,12,31)
    days=[]
    while day<=end:
        if day.weekday()<5:days.append(day.strftime('%Y%m%d'))
        day+=timedelta(days=1)
    return TradingCalendar(tuple(days),'20210101','20251231',True,
        'explicit-synthetic-weekdays-not-an-exchange-calendar','fixture')

def snapshots(symbol,index):
    tables={key:[] for key in ('income','balancesheet','cashflow')}
    for year in range(2021,2025):
        cumul={k:0. for k in ('revenue','operate_profit','n_income','n_income_attr_p','n_cashflow_act','c_pay_acq_const_fiolta')}
        for q,suffix in enumerate(('0331','0630','0930','1231'),1):
            announced=str(year+1)+'0328' if q==4 else str(year)+{1:'0425',2:'0825',3:'1025'}[q]
            if announced>'20241231':continue
            revenue=100.+index*1.5+(year-2021)*(5+index%4)+q*3
            cumul['revenue']+=revenue
            cumul['operate_profit']+=revenue*(0.10+(index%9)*0.004+q*0.001)
            cumul['n_income']+=revenue*(0.07+(index%7)*0.003)
            cumul['n_income_attr_p']+=revenue*(0.06+(index%5)*0.002)
            cumul['n_cashflow_act']+=revenue*(0.12+(index%11)*0.002+q*0.001)
            cumul['c_pay_acq_const_fiolta']+=revenue*(0.03+(index%6)*0.001)
            assets=1000.+index*20+(year-2021)*80+q*14
            values={
                'income':{k:cumul[k] for k in ('revenue','operate_profit','n_income','n_income_attr_p')},
                'cashflow':{k:cumul[k] for k in ('n_cashflow_act','c_pay_acq_const_fiolta')},
                'balancesheet':{'total_assets':assets,'total_liab':assets*(0.35+index%6*0.01),
                    'total_cur_assets':assets*0.3,'total_cur_liab':assets*0.1,
                    'money_cap':assets*(0.10+index%5*0.01),'accounts_receiv':assets*0.05,
                    'goodwill':assets*(0.01+index%7*0.002),'st_borr':assets*0.05,'lt_borr':assets*0.05}}
            for endpoint in tables:
                fields=EXTRA_DATASETS[endpoint].split(',')
                row={k:None for k in fields}
                row.update(ts_code=symbol,ann_date=announced,f_ann_date=None,end_date=str(year)+suffix,
                           report_type='1',comp_type='1',update_flag='0',**values[endpoint])
                tables[endpoint].append(row)
    result=[]
    for endpoint,rows in tables.items():
        body={'endpoint':endpoint,'params':{'ts_code':symbol,'start_date':'20210101','end_date':'20241231'},
              'fields':EXTRA_DATASETS[endpoint].split(','),'rows':rows,'retrievedAt':'2026-10-08T00:00:00Z',
              'sourceKind':'fixture','sourceProvider':PROVIDER,'representation':'normalized_provider_table_snapshot',
              'wireBytesAvailable':False,'wireNumericLexemesAvailable':False}
        result.append({**body,'id':canonical_hash(body),'rowCount':len(rows),'byteLength':len(_encoded(body))})
    return result

def market_view(output):
    strategy=validate({'schemaVersion':2,'name':'SYNTHETIC capacity source only NO MODEL FIT','universe':SCOPE,
        'research':{'mode':'statistical_quant'},'target':{'kind':'asset_price','horizonSessions':1},
        'model':{'family':'mean_reversion','estimator':'ridge'},'execution':{'enabled':False},
        'factors':[{'id':'one','expression':'returns(close,1)','role':'predictor'}]})
    frame,provenance=make_demo_data(strategy)
    provenance['symbolIdentities']='50 artificial identifiers; not exchange listings or historical constituents'
    snapshot=freeze_input(strategy,frame,provenance)
    forecast={'schemaVersion':1,'totalRows':0,'truncated':False,'sourceStrategy':strategy,
        'dataFingerprint':snapshot['dataFingerprint'],'predictionConfigHash':digest(prediction_config(strategy)),
        'rows':[],'targetDefinitions':[],'modelFits':[],'hedgeFits':[],'diagnostics':{}}
    forecast['artifactId']=bundle.sha(bundle.encode(forecast))
    report={'schemaVersion':2,'status':'completed','strategy':strategy,'provenance':snapshot['provenance'],
        'selection':{'reason':'SYNTHETIC_TRANSPORT_FIXTURE_NO_FIT'},'research':{'executionOnly':False,'mode':'statistical_quant'},
        'forecasts':forecast,'equity':[],'trades':[],'execution':{'ledger':[],'decisions':[]}}
    chunks={}
    manifest=bundle.build_bundle(report,snapshot,{'schemaVersion':1,'source':'legacy_artifact_derived',
        'baselineRequired':False,'origins':[]},lambda c,n,b:chunks.__setitem__((c,n),b),lambda c,n:chunks[c,n])
    raw=b''.join(bundle.iter_document_bytes(json.loads(manifest),'snapshot',lambda c,n:chunks[c,n]))
    write(output/'source-market-manifest.json',manifest);write(output/'source-market-snapshot.json',raw)
    return derive_market_snapshot_view(raw,manifest,{'kind':'snapshot_scope_view','version':1,'mode':'exact',**SCOPE},
        expected_bundle_id=sha(manifest),expected_snapshot_sha256=sha(raw))

def create(output):
    output.mkdir(mode=0o700)
    write(output/'predeclaration.json',encode({'scope':SCOPE,'calendarDaysInclusive':366,
        'selectedStates':list(RECIPES),'sourceProvider':PROVIDER,'synthetic':True,'providerCalls':0,
        'packages':2,'symbolsPerPackage':25,'financialStatements':'artificial deterministic CNY formulas',
        'limitsUnchanged':True,'modelsFittedDuringSourceCreation':False}))
    cal=calendar();ref=str(uuid.uuid4())
    registry_raw=encode({'kind':'calendar','registryVersion':1,'evidenceLevel':'EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE',
        'payload':asdict(cal),'scope':calendar_scope(asdict(cal))})
    # The separately frozen authorized pin predates composition; archive bytes do not create trust.
    write(output/'authorized-registry'/f'{ref}.json',registry_raw)
    write(output/'downloaded-registry'/f'{ref}.json',registry_raw)
    write(output/'registry-pins.json',encode({ref:str(output/'authorized-registry'/f'{ref}.json')}))
    sources=[];sizes=[]
    for group in range(2):
        symbols=SCOPE['symbols'][group*25:(group+1)*25]
        items=[item for i,s in enumerate(symbols,group*25) for item in snapshots(s,i)]
        _,root=raw_input(items,cal,source_kind='fixture',source_provider=PROVIDER)
        units={field:DeclaredUnitBinding(UnitEvidence('CNY','CNY',False,'user_declared_assumption',
            'SYNTHETIC_CAPACITY_DECLARATION',True),field,PROVIDER,root,'synthetic-fixture-author',
            '2026-10-08T00:00:00Z','Artificial deterministic fixture uses CNY; not authentic issuer statements.') for field in FIELDS}
        package=freeze_package(items,cal,{'universe':{**SCOPE,'symbols':symbols}},list(RECIPES),units,
            announcement_start='20210101',source_kind='fixture',source_provider=PROVIDER,
            unit_policy='allow_declared',trusted_unit_proofs=False)
        raw=_encoded(package);write(output/f'financial-source-{group}.json',raw)
        prepared=prepare_package(package)
        # Preserve the actual bounded prepared bytes even if total composition fails.
        from types import SimpleNamespace
        payload=prepared_payload(SimpleNamespace(prepared=prepared))
        serialized=encode(payload);write(output/f'prepared-{group}.json',serialized)
        sizes.append({'package':group,'inputBytes':len(raw),'preparedBytes':len(serialized),
                      'parts':{k:len(encode(v)) for k,v in payload.items()}})
        sources.append(FinancialSource(raw,prepared.provenance['preparedRoot'],ref))
    write(output/'measured-component-sizes.json',encode({'measurements':sizes,
        'preparedByteSum':sum(x['preparedBytes'] for x in sizes),
        'sourceClosedProfileBytes':64*1024**2,'providerCalls':0,'modelFitted':False}))
    view=market_view(output)
    parts={}
    def part(c,n,b):
        write(output/'composed-parts'/c/f'{n}.bin',b);parts[c,n]=b
    pub=compose_snapshot_dataset_components(view,sources,{ref:registry_raw},part,market_calendar_ref=ref)
    reader=DatasetReader(pub.manifest_bytes,lambda c,n:parts[c,n],expected_root=pub.dataset_root)
    export_dataset_archive(reader,output/'dataset.tar')
    extract_dataset_archive(output/'dataset.tar',output/'dataset',expected_root=pub.dataset_root)
    result={'datasetId':str(uuid.uuid4()),'datasetRoot':pub.dataset_root,'scope':SCOPE,'synthetic':True,
            'providerCalls':0,'modelFitted':False,'calendarDaysInclusive':366,'tradingSessions':262,
            'marketRows':len(pub.result.data),'financialStates':len(RECIPES),'sourcePackages':len(sources)}
    write(output/'summary.json',encode(result));print(json.dumps(result))

if __name__=='__main__':
    output=Path(sys.argv[1]).resolve()
    try:create(output)
    except Exception as error:
        if output.is_dir():write(output/'failure.json',encode({'status':'FAIL','code':getattr(error,'code',None),
            'type':type(error).__name__,'message':str(error),'providerCalls':0,'modelFitted':False}))
        raise
