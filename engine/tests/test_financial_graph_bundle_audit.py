"""Independent bundle/2 hostile fixtures; no model/provider call is needed."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_research_dataset_components import sources, long_sources
from test_snapshot_market_view import legacy_source, derive
from test_financial_graph_dataset import build as graph_build
from test_graph_dataset_audit import rebuild as source_directory

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import financial_graph_bundle_audit as audit
import graph_dataset_audit as graph
import financial_bundle_audit as old


def asset_construction_fits(dates, targets, *, start=61, observation=1, refit=20):
    """Asset definitions exist before holdout, even without a fitted predictor.

    This explicit transport fixture follows the declared observation/refit clock;
    it does not call the auditor's expected-domain helper or fit any model.
    """
    fits=[]
    for position in range(start,len(dates),observation):
        if fits and position-fits[-1][0]<refit:
            continue
        fits.append((position,{
            'date':dates[position],
            'informationCutoff':dates[position-1],
            'targetIds':[target['id'] for target in targets],
            'status':'valid',
        }))
    return [row for _,row in fits]


@pytest.fixture(scope='module')
def fixture(legacy_source,long_sources):
    sources=long_sources
    pub,_,reader=graph_build(derive(legacy_source,sources['scope']),sources)
    values={c['componentId']:json.loads(reader.payload(c['componentId'])) for c in reader.manifest['components']}
    envelope=values['researchColumns'];p=envelope['provenance']
    strategy={'schemaVersion':2,'name':'SYNTHETIC_TRANSPORT_FIXTURE_NO_FIT','universe':sources['scope'],
        'research':{'mode':'statistical_quant','observationDays':1},'target':{'kind':'asset_price','horizonSessions':5},
        'model':{'family':'fundamental','estimator':'auto','refitDays':20},'execution':{'enabled':False},
        'validation':{'innerFolds':2,'outerFolds':2,'holdoutFraction':0.2},
        'factors':[{'id':'one','role':'predictor','expression':'model_fin_revenue_quarter_yoy'}]}
    prediction={k:v for k,v in strategy.items() if k not in {'execution','portfolio','costs','name','graph'}}
    phash=audit.sha(audit.canonical(prediction));fhash='1'*64
    ref={'datasetId':'00000000-0000-0000-0000-000000000083','datasetRoot':pub.dataset_root,
         'format':'atlas.quant.research_dataset','version':3}
    commitment={k:p[k] for k in ('financialCompositionVersion','marketRoot','financialDatasetRoot','financialInputs')}
    snapshot={'schemaVersion':3,'fingerprintVersion':'research_input_financial_column_v1','datasetRef':ref,
        'sourceEvidenceClosure':'separate_research_dataset_v3','dataFingerprint':fhash,
        'sourceDataFingerprint':p['dataFingerprint'],'financialSourceCommitment':commitment,
        'provenance':p,'numericInput':envelope['numericInput']}
    # Construct the entire declared grid, with explicitly unavailable predictions.
    # No fixture performs a model fit; source/coverage validity is separate from F.
    dates=values['schema']['calendarSessions'];start=61
    holdout=dates[start+int((len(dates)-start)*0.8)]
    interval={'holdoutStart':holdout,'holdoutEnd':dates[-1]}
    targets=[]
    for symbol in sources['scope']['symbols']:
        content={'kind':'asset_price','symbols':[symbol],'quantities':[1],
            'unit':'CNY_adjusted_research_price','construction':'single_asset',
            'formationStart':None,'formationEnd':None,'hedgeAudit':{}}
        targets.append({'id':'target_'+audit.sha(audit.canonical(content))[:24],**content})
    origins=[];rows=[]
    for t in range(start,len(dates)):
        if dates[t]<holdout:continue
        for target in targets:
            plan={'date':dates[t],'targetId':target['id'],
                'entryDate':dates[t+1] if t+1<len(dates) else None,
                'targetDate':dates[t+6] if t+6<len(dates) else None,'inputValid':False}
            origins.append(plan)
            rows.append({**{k:v for k,v in plan.items() if k!='inputValid'},
                'forecastId':'fixture_'+str(len(rows)), 'modelFitId':None,
                'informationCutoff':dates[t]+'_AFTER_CLOSE','horizonSessions':5,
                'status':'invalid','invalidReason':'SYNTHETIC_NO_FIT'})
    forecast={'schemaVersion':1,'totalRows':len(rows),'truncated':False,'sourceStrategy':strategy,'rows':rows,
        'targetDefinitions':targets,'modelFits':[],
        'hedgeFits':asset_construction_fits(dates,targets),
        'diagnostics':{**interval,'factorIncrement':{'baselineRows':deepcopy(rows),
            'baselineModelFits':[],'baselineValidation':deepcopy(interval)}},
        'dataFingerprint':fhash,'predictionConfigHash':phash}
    report={'schemaVersion':2,'status':'SYNTHETIC_TRANSPORT_FIXTURE_NO_FIT','strategy':strategy,
        'provenance':{'dataSha256':fhash,'financialSourceCommitment':commitment},
        'research':{'executionOnly':False,'observationDays':1},'validation':deepcopy(interval),
        'metrics':None,'equity':[],'trades':[], 'execution':{'enabled':False,'ledger':[],'decisions':[]}}
    docs={'forecast':forecast,'report':report,'coverage':{'schemaVersion':1,'source':'samples_before_model_fitting',
        'baselineRequired':True,'holdoutStart':holdout,'origins':origins},'snapshot':snapshot}
    return pub,values,sources['registry'],docs,ref


def build(fixture,tmp_path,mutate=lambda *_:None):
    docs=deepcopy(fixture[3]);mutate(docs)
    forecast=docs['forecast'];fid=audit.sha(audit.canonical(forecast))
    docs['report']['forecasts']={'artifactId':fid,**forecast}
    docs['report']['execution']['forecastArtifactId']=fid
    manifest={'format':'atlas.quant.financial_bundle','version':2,'kind':'forecast',
        'forecastArtifactId':fid,'predictionConfigHash':forecast['predictionConfigHash'],
        'dataFingerprint':forecast['dataFingerprint'],'sourceEvidence':{'datasetRef':deepcopy(docs['snapshot']['datasetRef']),'admissionProfile':audit.RESEARCH_PROFILE},
        'documents':{},'collections':[],'totals':{'chunkCount':0,'chunkBytes':0}}
    folder=tmp_path/'result';folder.mkdir()
    lookup={pair:name for name,pair in audit.GRAPH_PATHS.items()}
    for doc,value in docs.items():
        encoder=audit.financial_json if doc=='snapshot' else audit.canonical
        parts=[]
        def literal(raw):
            text=raw.decode()
            if parts and 'literal' in parts[-1]:parts[-1]['literal']+=text
            else:parts.append({'literal':text})
        def walk(value,path=''):
            if doc=='report' and path=='/forecasts':
                parts.append({'document':'forecast','wrapArtifactId':fid})
            elif (doc,path) in lookup:
                name=lookup[doc,path];target=folder/'chunks'/name;target.mkdir(parents=True)
                chunks=[]
                if value:
                    raw=encoder(value);(target/'0.json').write_bytes(raw)
                    chunks=[{'ordinal':0,'start':0,'count':len(value),'byteLength':len(raw),'sha256':audit.sha(raw)}]
                    manifest['totals']['chunkCount']+=1;manifest['totals']['chunkBytes']+=len(raw)
                manifest['collections'].append({'id':name,'document':doc,'path':path,'rowCount':len(value),'chunks':chunks})
                parts.append({'collection':name})
            elif isinstance(value,dict):
                literal(b'{')
                for i,key in enumerate(sorted(value)):
                    if i:literal(b',')
                    literal(encoder(key)+b':');walk(value[key],path+'/'+key)
                literal(b'}')
            else:literal(encoder(value))
        walk(value);raw=encoder(value)
        manifest['documents'][doc]={'codec':audit.CODECS[doc],'parts':parts,'sha256':audit.sha(raw),'byteLength':len(raw)}
    (folder/'manifest.json').write_bytes(audit.canonical(manifest))
    return folder


def tar(folder,path):
    from bundle_archive import _expected_header
    manifest=json.loads((folder/'manifest.json').read_bytes())
    names=['manifest.json']+[f"chunks/{c['id']}/{d['ordinal']}.json" for c in manifest['collections'] for d in c['chunks']]
    with path.open('wb') as stream:
        for name in names:
            raw=(folder/name).read_bytes();stream.write(_expected_header(name,len(raw)));stream.write(raw);stream.write(bytes((-len(raw))%512))
        stream.write(bytes(1024))
    return path


def source(fixture,tmp_path):
    return source_directory(fixture,tmp_path)


def test_transport_pair_pin_and_legacy_rejection_without_model_fit(fixture,tmp_path):
    result=build(fixture,tmp_path);dataset=source(fixture,tmp_path)
    bundle_id=audit.sha((result/'manifest.json').read_bytes())
    incomplete=audit.audit_financial_graph_bundle(result)
    assert incomplete['status']=='INCOMPLETE_SOURCE' and incomplete['sourceAuthorityVerified'] is False
    report=audit.audit_financial_graph_bundle(tar(result,tmp_path/'result.tar'),source_dataset=dataset,
        registry_pins=fixture[2],expected_bundle_id=bundle_id,expected_dataset_root=fixture[0].dataset_root)
    assert report['status']=='PASS' and report['sourceEvidenceClosed'] and report['bundleIdPinned']
    assert report['datasetRootPinned'] and report['externalRegistryBytesMatched']
    assert report['modelFitted'] is False and report['providerCalls']==0
    assert report['financialFormulasRecomputed'] is False and report['modelAdmissionRegistered'] is False
    assert report['sourceForecastDomainVerified'] is True and report['inputValidityRecomputed'] is False
    assert report['sourceCoverage']['expectedOriginDates']==41
    assert report['sourceCoverage']['expectedForecastRows']==len(fixture[1]['coverage']['observedSymbols'])*41
    assert report['sourceCoverage']['expectedHedgeFits']==11
    assert report['sourceCoverage']['fullHedgeTargetReferencesVerified'] is True
    with pytest.raises(ValueError):old.audit_financial_bundle(result)
    for kw in [{'expected_bundle_id':'0'*64},{'expected_dataset_root':'0'*64}]:
        with pytest.raises(ValueError):audit.audit_financial_graph_bundle(result,source_dataset=dataset,**kw)


@pytest.mark.parametrize('attack',['numeric','provenance','dataset','commitment','schema','execution','profile'])
def test_rehashed_result_cannot_change_paired_source_or_contract(fixture,tmp_path,attack):
    def mutate(docs):
        snapshot=docs['snapshot']
        if attack=='numeric':
            table=snapshot['numericInput'];column=next(c for c in table['columns'] if c['kind']=='number');column['values'][0]=-0.0
            check=graph.Checks();table['logicalRows']=graph.digest_stream(graph.array_stream(graph.table_rows(table),check),graph.LOGICAL,check)
        elif attack=='provenance':snapshot['provenance']['synthetic']=False
        elif attack=='dataset':snapshot['datasetRef']['datasetRoot']='0'*64
        elif attack=='commitment':snapshot['financialSourceCommitment']['marketRoot']='0'*64
        elif attack=='schema':snapshot['schemaVersion']=True
        elif attack=='execution':docs['report']['execution']['enabled']=True
        elif attack=='profile':docs['forecast']['sourceStrategy']['model']['refitDays']=1
    result=build(fixture,tmp_path,mutate);dataset=source(fixture,tmp_path)
    with pytest.raises(ValueError):audit.audit_financial_graph_bundle(result,source_dataset=dataset,registry_pins=fixture[2])


@pytest.mark.parametrize('attack',['tail','extra','part','codec','version','unknown_collection'])
def test_transport_and_version_fail_closed(fixture,tmp_path,attack):
    result=build(fixture,tmp_path)
    if attack=='tail':
        result=tar(result,tmp_path/'result.tar');result.write_bytes(result.read_bytes()+b'x')
    elif attack=='extra':(result/'extra').write_text('x')
    elif attack=='part':
        target=result/'chunks/snapshotColumns/0.json';target.write_bytes(target.read_bytes()+b' ')
    else:
        path=result/'manifest.json';m=json.loads(path.read_bytes())
        if attack=='codec':m['documents']['snapshot']['codec']='financial_json_v1'
        elif attack=='version':m['version']=1
        else:m['collections'][0]['id']='snapshotRows'
        path.write_bytes(audit.canonical(m))
    with pytest.raises(ValueError):audit.audit_financial_graph_bundle(result)


def test_stdlib_cli_incomplete_then_pair_without_engine(fixture,tmp_path):
    result=build(fixture,tmp_path);dataset=source(fixture,tmp_path)
    command=[sys.executable,'-S',str(ROOT/'scripts/audit-financial-graph-bundle.py'),str(result)]
    incomplete=subprocess.run(command,capture_output=True,text=True)
    assert incomplete.returncode==2 and json.loads(incomplete.stdout)['status']=='INCOMPLETE_SOURCE'
    paired=subprocess.run(command+['--source-dataset',str(dataset)],capture_output=True,text=True)
    assert paired.returncode==0,paired.stdout+paired.stderr
    assert json.loads(paired.stdout)['engineImports'] is False


@pytest.mark.parametrize('attack',['asset','day','tail','empty','tail_endpoint','horizon','holdout','target_quantity','baseline'])
def test_coordinated_rehashed_domains_cannot_hide_source_origins(fixture,tmp_path,attack):
    def mutate(docs):
        f=docs['forecast'];coverage=docs['coverage'];inc=f['diagnostics']['factorIncrement']
        first_date=f['rows'][0]['date'];first_target=f['targetDefinitions'][0]['id']
        def keep(row):
            if attack=='asset':return row['targetId']!=first_target
            if attack=='day':return row['date']!=first_date
            if attack=='tail':return row['targetDate'] is not None
            return attack!='empty'
        if attack in {'asset','day','tail','empty'}:
            f['rows']=[x for x in f['rows'] if keep(x)]
            inc['baselineRows']=[x for x in inc['baselineRows'] if keep(x)]
            coverage['origins']=[x for x in coverage['origins'] if keep(x)]
            f['totalRows']=len(f['rows'])
            if attack=='asset':f['targetDefinitions']=[x for x in f['targetDefinitions'] if x['id']!=first_target]
        elif attack=='tail_endpoint':
            for rows in (f['rows'],inc['baselineRows'],coverage['origins']):
                rows[-1]['entryDate']='20250101';rows[-1]['targetDate']='20250108'
        elif attack=='horizon':
            for rows in (f['rows'],inc['baselineRows']):rows[0]['horizonSessions']=6
        elif attack=='holdout':
            coverage['holdoutStart']='20240101'
            for d in (f['diagnostics'],inc['baselineValidation'],docs['report']['validation']):d['holdoutStart']='20240101'
        elif attack=='target_quantity':f['targetDefinitions'][0]['quantities']=[2]
        elif attack=='baseline':
            coverage['baselineRequired']=False
            del inc['baselineRows'];del inc['baselineModelFits']
    result=build(fixture,tmp_path,mutate);dataset=source(fixture,tmp_path)
    # All inner hashes, recipes and totals have been rebuilt. The predecessor's
    # self-consistency checks still accept; the frozen source domain must reject.
    assert audit.audit_financial_graph_bundle(result)['status']=='INCOMPLETE_SOURCE'
    with pytest.raises(ValueError,match='asset grid|Targets|Target definition|clock|baseline|Origin order'):
        audit.audit_financial_graph_bundle(result,source_dataset=dataset)


@pytest.mark.parametrize('observation,expression,start',[(3,'model_fin_revenue_quarter_yoy',61),
    (5,'lag(model_fin_revenue_quarter_yoy,80)',81)])
def test_declared_stride_is_anchored_before_terminal_boundary(fixture,tmp_path,observation,expression,start):
    docs=deepcopy(fixture[3]);dates=fixture[1]['schema']['calendarSessions']
    strategy=docs['forecast']['sourceStrategy']
    strategy['research']['observationDays']=observation
    strategy['factors'][0]['expression']=expression
    docs['forecast']['hedgeFits']=asset_construction_fits(
        dates,docs['forecast']['targetDefinitions'],start=start,
        observation=observation,refit=strategy['model']['refitDays'])
    docs['report']['strategy']=strategy;docs['report']['research']['observationDays']=observation
    prediction={k:v for k,v in strategy.items() if k not in {'execution','portfolio','costs','name','graph'}}
    docs['forecast']['predictionConfigHash']=audit.sha(audit.canonical(prediction))
    holdout=dates[start+int((len(dates)-start)*0.8)]
    wanted={dates[t] for t in range(start,len(dates),observation) if dates[t]>=holdout}
    def select_dates(documents,selected):
        f=documents['forecast'];inc=f['diagnostics']['factorIncrement'];coverage=documents['coverage']
        for d in (f['diagnostics'],inc['baselineValidation'],documents['report']['validation']):d['holdoutStart']=holdout
        coverage['holdoutStart']=holdout
        f['rows']=[r for r in f['rows'] if r['date'] in selected]
        inc['baselineRows']=[r for r in inc['baselineRows'] if r['date'] in selected]
        coverage['origins']=[r for r in coverage['origins'] if r['date'] in selected]
        f['totalRows']=len(f['rows'])
    altered=(*fixture[:3],docs,fixture[4]);good=tmp_path/'good';good.mkdir()
    result=build(altered,good,lambda d:select_dates(d,wanted));dataset=source(fixture,tmp_path)
    report=audit.audit_financial_graph_bundle(result,source_dataset=dataset)
    assert report['sourceCoverage']['sampleStartIndex']==start
    assert report['sourceCoverage']['expectedOriginDates']==len(wanted)
    # Resetting the step at holdout is a coherent, differently selected clock.
    reset=set(dates[dates.index(holdout)::observation]);assert reset!=wanted
    bad=tmp_path/'bad';bad.mkdir()
    result=build(altered,bad,lambda d:select_dates(d,reset))
    assert audit.audit_financial_graph_bundle(result)['status']=='INCOMPLETE_SOURCE'
    with pytest.raises(ValueError,match='asset grid|Origin order'):
        audit.audit_financial_graph_bundle(result,source_dataset=dataset)


@pytest.mark.parametrize('attack',['missing_prefit','missing_target','future_cutoff'])
def test_rehashed_asset_construction_cannot_omit_source_refits(fixture,tmp_path,attack):
    def mutate(docs):
        fits=docs['forecast']['hedgeFits']
        if attack=='missing_prefit':fits.pop(0)
        elif attack=='missing_target':fits[0]['targetIds'].pop()
        else:fits[0]['informationCutoff']=fits[0]['date']
    result=build(fixture,tmp_path,mutate);dataset=source(fixture,tmp_path)
    assert audit.audit_financial_graph_bundle(result)['status']=='INCOMPLETE_SOURCE'
    with pytest.raises(ValueError) as caught:
        audit.audit_financial_graph_bundle(result,source_dataset=dataset)
    assert caught.value.code=='RESULT_HEDGE_REFERENCES'
