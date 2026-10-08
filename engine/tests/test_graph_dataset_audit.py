"""Engine creates only synthetic no-fit fixtures; auditor runs with stdlib only."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_dataset import graph_source
from atlas_quant.research_dataset.codec import encode, sha
from atlas_quant.research_dataset.graph_v3.columns import encode_table

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import graph_dataset_audit as audit
import dataset_audit as legacy


@pytest.fixture(scope='module')
def fixture(graph_source, sources, legacy_source):
    pub, parts, reader = graph_source[1]
    values = {c['componentId']:json.loads(reader.payload(c['componentId'])) for c in reader.manifest['components']}
    pins = {'sourceManifest':legacy_source['manifest'], 'sourceSnapshot':legacy_source['raw'], 'financialInput0':sources['source'].package_bytes}
    return pub, values, sources['registry'], pins, graph_source[2][2]


def rebuild(fixture, tmp_path, mutate=lambda *_:None):
    pub, original, *_ = fixture
    manifest, values = json.loads(pub.manifest_bytes), deepcopy(original)
    mutate(manifest, values)
    folder = tmp_path/'dataset'; folder.mkdir()
    changed = {}
    for component in manifest['components']:
        name, old = component['componentId'], component['componentRoot']
        raw = encode(values[name]); component['dependencies'] = sorted(changed.get(d,d) for d in component['dependencies'])
        pieces = [raw[n:n+audit.PART] for n in range(0,len(raw),audit.PART)]
        component.update(payloadSha256=sha(raw), byteLength=len(raw), parts=[{'ordinal':i,'byteLength':len(p),'sha256':sha(p)} for i,p in enumerate(pieces)])
        component['componentRoot'] = audit.root(audit.without(component,'componentRoot')); changed[old] = component['componentRoot']
        target = folder/'parts'/name; target.mkdir(parents=True)
        for i,piece in enumerate(pieces):
            (target/f'{i}.bin').write_bytes(piece)
    (folder/'manifest.json').write_bytes(encode(manifest))
    return folder


def archive(folder, target):
    manifest = json.loads((folder/'manifest.json').read_bytes())
    paths = ['manifest.json']+[f"parts/{c['componentId']}/{p['ordinal']}.bin" for c in manifest['components'] for p in c['parts']]
    with target.open('wb') as stream:
        for name in paths:
            raw=(folder/name).read_bytes(); stream.write(legacy.tar_header(name,len(raw))); stream.write(raw);stream.write(bytes((-len(raw))%512))
        stream.write(bytes(1024))
    return target


def test_independent_exact_old_v2_oracle_and_external_bytes(fixture, tmp_path):
    folder = rebuild(fixture,tmp_path)
    report=audit.audit_graph_dataset(folder, expected_root=fixture[0].dataset_root,registry_pins=fixture[2],source_pins=fixture[3])
    assert report['status']=='PASS' and report['externalSourceBytesMatched'] and report['externalRegistryBytesMatched']
    assert report['sourceAuthorityVerified'] is False and report['financialFormulasRecomputed'] is False
    assert report['providerCalls']==0 and report['modelFitted'] is False
    old=fixture[4].payload('researchRows')
    assert report['logicalJoined']=={'sha256':sha(old),'byteLength':len(old)}
    assert report['roots']['financialDatasetRoot']==json.loads(old)['provenance']['financialDatasetRoot']
    assert audit.audit_graph_dataset(archive(folder,tmp_path/'dataset.tar'))['datasetRoot']==report['datasetRoot']
    with pytest.raises(legacy.AuditError,match='Unknown format'):
        legacy.audit_dataset(folder)


@pytest.mark.parametrize('attack',['dependency','dangling','duplicate','unused','gap','overlap','future','lineage','prepared_root','column','column_length','int_float','signed_zero','joined_provenance','coverage','registry'])
def test_self_rehashed_transport_rejects_semantic_damage(fixture,tmp_path,attack):
    def mutation(m,v):
        graph=v['financialGraph0']; table=v['researchColumns']['numericInput']
        numeric=next(c for c in table['columns'] if c['kind']=='number')
        if attack=='dependency':graph['dependencies'][0]['value']['raw_decimal']='12345'
        elif attack=='dangling':graph['events'][0]['dependencyRefs']=['0'*64]
        elif attack=='duplicate':graph['dependencies'].append(deepcopy(graph['dependencies'][0]))
        elif attack=='unused':
            extra=deepcopy(graph['dependencies'][0]);extra['value']['raw_decimal']='12345';extra['id']=audit.root(extra['value']);graph['dependencies'].append(extra);graph['dependencies'].sort(key=lambda x:x['id'])
        elif attack=='gap':graph['assignments'][0]['from']=graph['panel']['sessions'][1]
        elif attack=='overlap':graph['assignments'].append(deepcopy(graph['assignments'][0]))
        elif attack=='future':graph['events'][0]['result']['availableDate']='20990101'
        elif attack=='lineage':graph['events'][0]['result']['lineageHash']='0'*64
        elif attack=='prepared_root':graph['logicalPrepared']['preparedRoot']='0'*64
        elif attack=='column':graph['panel']['columnNames'].append('x')
        elif attack=='column_length':numeric['values'].pop()
        elif attack=='int_float':numeric['values'][0]=1
        elif attack=='signed_zero':numeric['values'][0]=-0.0
        elif attack=='joined_provenance':v['researchColumns']['provenance']['synthetic']=False
        elif attack=='coverage':v['coverage']['financial'][0]['originalAsPublishedVerified']=True
        elif attack=='registry':
            e=v['registryEvidence']['entries'][0];r=json.loads(e['rawText']);r['registryVersion']+=1;raw=encode(r);e.update(rawText=raw.decode(),sha256=sha(raw),byteLength=len(raw))
    with pytest.raises(audit.AuditError):
        audit.audit_graph_dataset(rebuild(fixture,tmp_path,mutation),registry_pins=fixture[2])


@pytest.mark.parametrize('attack',['root','source','registry'])
def test_only_out_of_band_pins_establish_matching_bytes(fixture,tmp_path,attack):
    folder=rebuild(fixture,tmp_path)
    kw={'expected_root':fixture[0].dataset_root,'registry_pins':fixture[2],'source_pins':fixture[3]}
    if attack=='root':kw['expected_root']='0'*64
    elif attack=='source':kw['source_pins']={**fixture[3],'financialInput0':b'{}'}
    else:kw['registry_pins']={key:b'{}' for key in fixture[2]}
    with pytest.raises(audit.AuditError):audit.audit_graph_dataset(folder,**kw)


def test_numeric_tokens_dictionary_and_underflow_are_not_normalized():
    table=encode_table([{'x':1,'s':'z','v':-0.0},{'x':1.0,'s':None,'v':0.0},{'x':None,'s':'a','v':-0.0}],{'x':'number','s':'dictionary','v':'number'})
    assert audit.verify_table(table)==table['logicalRows']
    assert encode(list(audit.table_rows(table)))==b'[{"s":"z","v":-0.0,"x":1},{"s":null,"v":0.0,"x":1.0},{"s":"a","v":-0.0,"x":null}]'
    for x in ['1e-9999','1e9999','NaN','Infinity']:
        with pytest.raises(audit.AuditError):audit.graph_number({'status':'ok','reasonCodes':[],'decimalValue':x},audit.Checks())
    for change in ('float','bool','unused','order'):
        bad=deepcopy(table)
        if change=='float':bad['columns'][0]['values'][0]=1.0
        elif change=='bool':bad['columns'][0]['values'][0]=True
        elif change=='unused':bad['columns'][1]['dictionary'].append('unused')
        else:bad['columns'][1]['indices']=[1,0,2]
        with pytest.raises(audit.AuditError):audit.verify_table(bad)


def test_stdlib_isolated_cli_no_engine_import_and_preserves_output(fixture,tmp_path):
    folder=rebuild(fixture,tmp_path); output=tmp_path/'audit.json'
    script=ROOT/'scripts/audit-graph-dataset.py'
    result=subprocess.run([sys.executable,'-S',str(script),str(folder),'--output',str(output)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['sourceAuthorityVerified'] is False
    old=output.read_bytes()
    second=subprocess.run([sys.executable,'-S',str(script),str(folder),'--output',str(output)],capture_output=True,text=True)
    assert second.returncode==2 and output.read_bytes()==old


@pytest.mark.parametrize('attack',['trailer','padding','symlink','extra','missing'])
def test_archive_directory_fail_closed(fixture,tmp_path,attack):
    folder=rebuild(fixture,tmp_path)
    if attack in ('trailer','padding'):
        path=archive(folder,tmp_path/'dataset.tar');raw=path.read_bytes()
        if attack=='trailer':raw+=b'x'
        else:
            end=512+len((folder/'manifest.json').read_bytes());raw=raw[:end]+b'x'+raw[end+1:]
        path.write_bytes(raw)
    elif attack=='extra':(folder/'extra').write_bytes(b'x');path=folder
    elif attack=='missing':next((folder/'parts/financialInput0').iterdir()).unlink();path=folder
    else:
        path=tmp_path/'link';path.symlink_to(folder,target_is_directory=True)
    with pytest.raises(audit.AuditError):audit.audit_graph_dataset(path)


def test_rehashed_integer_token_cannot_impersonate_normalized_market_float(fixture,tmp_path):
    def mutation(m,v):
        envelope=v['researchColumns'];table=envelope['numericInput']
        column=next(c for c in table['columns'] if c['name']=='adj_factor')
        assert column['values'][0]==1.0
        column['values'][0]=1
        check=audit.Checks()
        table['logicalRows']=audit.digest_stream(audit.array_stream(audit.table_rows(table),check),audit.LOGICAL,check)
        p=envelope['provenance']
        p['financialDatasetRoot']=audit.digest_stream(audit.object_stream({
            'marketRoot':audit.literal(p['marketRoot']),'financialInputs':audit.literal(p['financialInputs']),
            'rows':lambda:audit.array_stream(audit.table_rows(table),check),'externalFields':audit.literal(p['externalFields']),
            'compositionVersion':audit.literal('financial_dataset_v1')}),audit.TOTAL,check)['sha256']
        envelope['logicalJoined']=audit.digest_stream(audit.object_stream({'schemaVersion':audit.literal(1),
            'provenance':audit.literal(p),'rows':lambda:audit.array_stream(audit.table_rows(table),check)}),audit.LOGICAL,check)
        m['roots']['financialDatasetRoot']=p['financialDatasetRoot']
        descriptor=next(c for c in m['components'] if c['componentId']=='researchColumns')
        descriptor['semanticRoots']={'financialDatasetRoot':p['financialDatasetRoot'],'logicalJoinedSha256':envelope['logicalJoined']['sha256']}
    with pytest.raises(audit.AuditError,match='prescribed float conversion'):
        audit.audit_graph_dataset(rebuild(fixture,tmp_path,mutation))


def test_self_consistent_registry_requires_independently_authorized_pins(fixture,tmp_path):
    def mutation(m,v):
        e=v['registryEvidence']['entries'][0];record=json.loads(e['rawText']);record['registryVersion']+=1
        raw=encode(record);e.update(rawText=raw.decode(),sha256=sha(raw),byteLength=len(raw))
    folder=rebuild(fixture,tmp_path,mutation)
    result=audit.audit_graph_dataset(folder)
    assert result['trustStatus']=='unverified' and result['externalRegistryBytesMatched'] is False
    with pytest.raises(audit.AuditError,match='separately supplied'):
        audit.audit_graph_dataset(folder,registry_pins=fixture[2])
