"""Bounded context transport and durable restoration; zero provider/F calls."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from atlas_quant import bundle
from atlas_quant.bundle_spool import BundleSpool
from atlas_quant.context_sources import summarize_context_provenance
from atlas_quant.runner import CompletionSpool, RunnerError


@pytest.fixture(scope='module')
def synthetic_transport():
    # Reuse the cross-language hand-authored transport fixture; it does not fit
    # any model and its forecast rows are deliberately labelled synthetic.
    root = Path(__file__).resolve().parents[2]
    script = "import {contextBundleFixture} from './tests/fixtures/context-bundle-fixture.mjs'; const f=contextBundleFixture({sourceCount:16,dateCount:2200});console.log(JSON.stringify([f.report,f.snapshot,f.coverage]));"
    result = subprocess.run(['node','--input-type=module','-e',script],cwd=root,text=True,capture_output=True,check=True)
    report, snapshot, coverage = json.loads(result.stdout)
    report['research']['executionOnly'] = False
    report['forecasts']['diagnostics']['holdoutStart'] = coverage['holdoutStart']
    report['forecasts']['artifactId'] = bundle.sha(bundle.encode({k:v for k,v in report['forecasts'].items() if k!='artifactId'}))
    report['execution']['forecastArtifactId'] = report['forecasts']['artifactId']
    return report,snapshot,coverage


def packed(values):
    chunks={}
    raw=bundle.build_bundle(*values,lambda c,n,b:chunks.__setitem__((c,n),b),lambda c,n:chunks[c,n],chunk_target=256*1024)
    return raw,chunks,bundle.BundleReader(raw,lambda c,n:chunks[c,n])


def test_large_source_grid_is_chunked_without_changing_full_snapshot(synthetic_transport):
    before=copy.deepcopy(synthetic_transport)
    raw,chunks,reader=packed(synthetic_transport)
    assert len(bundle.encode(synthetic_transport[1]['provenance']['contextSources']))>512*1024
    assert len(raw)<32*1024
    assert reader.collections['snapshotContextSources']['rowCount']==16
    assert reader.verify_integrity()['verified']
    assert reader.document('snapshot')==synthetic_transport[1]
    assert reader.document('report')==synthetic_transport[0]
    assert all(len(value)<=bundle.CHUNK_LIMIT for value in chunks.values())
    assert synthetic_transport==before


def test_private_encrypted_context_spool_can_resume_and_export(synthetic_transport,tmp_path):
    completion=CompletionSpool({'delivery_dir':str(tmp_path/'delivery'),'runner_secret':'s'*48,'api_base':'https://offline.test'})
    identity={'id':'test-only','leaseToken':'private-test-lease'}
    store=BundleSpool.from_spool(completion,identity)
    result=store.build(*synthetic_transport)
    assert all(b'PARSED_PROVIDER_RESPONSE' not in path.read_bytes() for path in store.root.iterdir())
    reopened=BundleSpool.from_spool(completion,identity).reader(result['bundleId'])
    assert reopened.verify_integrity()['verified']
    bundle.export_bundle(reopened,tmp_path/'archive')
    restored=bundle.directory_reader(tmp_path/'archive')
    assert restored.document('snapshot')==synthetic_transport[1]
    assert restored.verify_integrity()['verified']


@pytest.mark.parametrize('mutate',[
    lambda r,s:r['provenance']['contextSources'][0].update(rowCount=1),
    lambda r,s:r['provenance'].update(contextSourceRoot='0'*64),
    lambda r,s:s['provenance']['contextSources'][0]['records'][0].update(close=9000),
    lambda r,s:s.update(fingerprintVersion='research_input_v1'),
])
def test_rehashed_envelope_does_not_hide_source_drift(synthetic_transport,mutate):
    values=copy.deepcopy(synthetic_transport);mutate(values[0],values[1])
    with pytest.raises(RunnerError): packed(values)[2].verify_hashes()


def test_report_summarization_keeps_original_source_and_legacy_identity(synthetic_transport,monkeypatch):
    report,snapshot,coverage=copy.deepcopy(synthetic_transport)
    original=copy.deepcopy(snapshot['provenance'])
    compact=summarize_context_provenance(snapshot['provenance'])
    assert all(set(s)=={'api','params','fields','sha256','rowCount'} for s in compact['contextSources'])
    assert snapshot['provenance']==original
    for p in (report['provenance'],snapshot['provenance']):
        for key in list(p):
            if key.startswith('context'): del p[key]
    snapshot['fingerprintVersion']='research_input_v1'
    values=report,snapshot,coverage
    current=packed(values)
    monkeypatch.setattr(bundle,'OPTIONAL_COLLECTIONS',{})
    legacy=packed(values)
    assert current[:2]==legacy[:2]


def test_real_queue_client_routes_context_chunks_only_in_ordinary_bundle_namespace():
    from atlas_quant.runner import QueueClient
    calls = []
    raw = b'[{"context":"EXPLICIT_OFFLINE_ROUTE_FIXTURE"}]'
    class Response:
        status_code = 200
        def __init__(self, content): self.content = content
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield self.content
    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method,url,kwargs))
            return Response(raw if method == 'GET' else b'{"ok":true}')
    client=QueueClient({'api_base':'https://offline.test/quant/api','runner_secret':'offline-test-only'},Session())
    identity={'id':'test-job','leaseToken':'test-lease'}
    assert client.bundle_chunk('PUT','a'*64,'snapshotContextSources',0,identity,
                               raw=raw,stage_id='test-stage') == {'ok':True}
    assert client.bundle_chunk('GET','a'*64,'snapshotContextSources',0,identity) == raw
    assert len(calls)==2
    assert all(call[1].endswith('/runner/bundles/'+'a'*64+'/chunks/snapshotContextSources/0') for call in calls)
    assert calls[0][2]['data'] == raw and calls[0][2]['allow_redirects'] is False
    for namespace in ('financial-bundles','financial-graph-bundles','unknown'):
        with pytest.raises(RunnerError) as error:
            client.bundle_chunk('PUT','a'*64,'snapshotContextSources',0,identity,
                                raw=raw,stage_id='test-stage',namespace=namespace)
        assert error.value.code == 'QUEUE_ROUTE'
    with pytest.raises(RunnerError):
        client.bundle_chunk('PUT','a'*64,'unknownSource',0,identity,raw=raw,stage_id='test-stage')
    assert len(calls)==2  # rejected locally, without a network call


def test_foreign_cross_language_archive_retains_raw_adjustments():
    root = Path(__file__).resolve().parents[2]
    script = "import {foreignContextBundleFixture} from './tests/fixtures/context-bundle-fixture.mjs'; const f=foreignContextBundleFixture();console.log(JSON.stringify([f.report,f.snapshot,f.coverage]));"
    result = subprocess.run(['node','--input-type=module','-e',script],cwd=root,text=True,capture_output=True,check=True)
    report,snapshot,coverage = json.loads(result.stdout)
    report['research']['executionOnly'] = False
    report['forecasts']['diagnostics']['holdoutStart'] = coverage['holdoutStart']
    report['forecasts']['artifactId'] = bundle.sha(bundle.encode({k:v for k,v in report['forecasts'].items() if k!='artifactId'}))
    report['execution']['forecastArtifactId'] = report['forecasts']['artifactId']
    raw,chunks,reader = packed((report,snapshot,coverage))
    assert reader.verify_integrity()['verified']
    assert reader.document('snapshot') == snapshot
    source = reader.document('snapshot')['provenance']['contextSources'][0]
    assert source['api'] == 'us_daily_adj' and source['records'][0]['adj_factor'] == 0.5


def test_yahoo_cross_language_archive_retains_frozen_source_summary():
    root = Path(__file__).resolve().parents[2]
    script = "import {yahooContextBundleFixture} from './tests/fixtures/context-bundle-fixture.mjs'; const f=yahooContextBundleFixture();console.log(JSON.stringify([f.report,f.snapshot,f.coverage]));"
    result = subprocess.run(['node','--input-type=module','-e',script],cwd=root,text=True,capture_output=True,check=True)
    report,snapshot,coverage = json.loads(result.stdout)
    report['research']['executionOnly'] = False
    report['forecasts']['diagnostics']['holdoutStart'] = coverage['holdoutStart']
    report['forecasts']['artifactId'] = bundle.sha(bundle.encode({k:v for k,v in report['forecasts'].items() if k!='artifactId'}))
    report['execution']['forecastArtifactId'] = report['forecasts']['artifactId']
    raw,chunks,reader = packed((report,snapshot,coverage))
    assert reader.verify_integrity()['verified']
    source = reader.document('report')['provenance']['contextSources'][0]
    assert source['providerDetails']['libraryVersion'] == '1.7.0'
    assert source['observedRange'] == {'start':'20150101','end':'20150103'}
    assert source['historicalRevisionVerified'] is False
    values=copy.deepcopy((report,snapshot,coverage))
    values[0]['provenance']['contextSources'][0]['observedRange']['end']='20150102'
    with pytest.raises(RunnerError): packed(values)[2].verify_hashes()
