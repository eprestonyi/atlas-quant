"""Independent bundle/2 hostile fixtures; no model/provider call is needed."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_dataset import graph_source
from test_graph_dataset_audit import rebuild as source_directory

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import financial_graph_bundle_audit as audit
import graph_dataset_audit as graph
import financial_bundle_audit as old


@pytest.fixture(scope='module')
def fixture(graph_source,sources):
    pub,_,reader=graph_source[1]
    values={c['componentId']:json.loads(reader.payload(c['componentId'])) for c in reader.manifest['components']}
    envelope=values['researchColumns'];p=envelope['provenance']
    strategy={'schemaVersion':2,'name':'SYNTHETIC_TRANSPORT_FIXTURE_NO_FIT','universe':sources['scope'],
        'research':{'mode':'statistical_quant'},'target':{'kind':'asset_price'},
        'model':{'family':'fundamental','estimator':'auto','refitDays':20},'execution':{'enabled':False},
        'validation':{'innerFolds':2,'outerFolds':2},'factors':[{'id':'one','role':'predictor'}]}
    prediction={k:v for k,v in strategy.items() if k not in {'execution','portfolio','costs','name','graph'}}
    phash=audit.sha(audit.canonical(prediction));fhash='1'*64
    ref={'datasetId':'00000000-0000-0000-0000-000000000083','datasetRoot':pub.dataset_root,
         'format':'atlas.quant.research_dataset','version':3}
    commitment={k:p[k] for k in ('financialCompositionVersion','marketRoot','financialDatasetRoot','financialInputs')}
    snapshot={'schemaVersion':3,'fingerprintVersion':'research_input_financial_column_v1','datasetRef':ref,
        'sourceEvidenceClosure':'separate_research_dataset_v3','dataFingerprint':fhash,
        'sourceDataFingerprint':p['dataFingerprint'],'financialSourceCommitment':commitment,
        'provenance':p,'numericInput':envelope['numericInput']}
    forecast={'schemaVersion':1,'totalRows':0,'truncated':False,'sourceStrategy':strategy,'rows':[],
        'targetDefinitions':[],'modelFits':[],'hedgeFits':[],'diagnostics':{},
        'dataFingerprint':fhash,'predictionConfigHash':phash}
    report={'schemaVersion':2,'status':'SYNTHETIC_TRANSPORT_FIXTURE_NO_FIT','strategy':strategy,
        'provenance':{'dataSha256':fhash,'financialSourceCommitment':commitment},'research':{'executionOnly':False},
        'metrics':None,'equity':[],'trades':[], 'execution':{'enabled':False,'ledger':[],'decisions':[]}}
    docs={'forecast':forecast,'report':report,'coverage':{'schemaVersion':1,'source':'legacy_artifact_derived','baselineRequired':False,'origins':[]},'snapshot':snapshot}
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
