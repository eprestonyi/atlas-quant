#!/usr/bin/env python3
"""Fresh raw source build and separate-process graph recomposition. No provider/F."""
import argparse,json,os,resource,shutil,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))


def save(path,value):
    with path.open('x') as f:
        os.chmod(path,0o600);json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())


def child(source,oracle,out,mode):
    # Fixture work is intentionally unable to open network connections.
    def denied(*args,**kwargs):raise RuntimeError('PROVIDER_NETWORK_FORBIDDEN')
    socket.create_connection=denied;socket.socket.connect=denied
    from atlas_quant.research_dataset.codec import decode,encode,sha,require
    from atlas_quant.research_dataset import derive_market_snapshot_view,FinancialSource
    from atlas_quant.research_dataset.graph_v3.dataset import (compose_graph_dataset_components,
        DirectoryGraphDatasetReader,restore_graph_dataset)
    from atlas_quant.research_dataset.reader import _read_file
    from atlas_quant.research_dataset.graph_v3.streams import stream_digest,canonical_chunks
    from atlas_quant.research_dataset.graph_v3.columns import iter_rows
    from atlas_quant.provider import _validate_panel
    from atlas_quant.financial_statements.prepare import _safe_rows
    started=time.monotonic();declaration=json.loads(_read_file(source/'predeclaration.json',262144))
    expected=json.loads(_read_file(oracle/'summary.json',262144))
    registry_map=json.loads(_read_file(source/'registry-pins.json',262144))
    registry={k:_read_file(Path(v),262144) for k,v in registry_map.items()}
    require(len(registry)==1,'REGISTRY','Explicit synthetic calendar registry required')
    for k,raw in registry.items():
        require(raw==_read_file(source/'downloaded-registry'/f'{k}.json',262144),'REGISTRY','Independent registry pins differ')
    # The earlier component prototype measured rows before the existing v2
    # numeric panel normalization. Preserve that evidence, then derive a new
    # explicitly labelled legacy-semantics oracle from those frozen values.
    table=decode(_read_file(oracle/'numeric-columns.json',24*1024**2),24*1024**2)
    provenance=decode(_read_file(oracle/'joined-provenance.json',24*1024**2),24*1024**2)
    original_rows=list(iter_rows(table))
    frame=_validate_panel({'universe':declaration['scope']},original_rows,external_fields=provenance['externalFields'])
    rows=_safe_rows(frame)
    type_changes=sum(type(a[k]) is not type(b[k]) for a,b in zip(original_rows,rows) for k in a)
    provenance['financialDatasetRoot']=sha(encode({'marketRoot':provenance['marketRoot'],
        'financialInputs':provenance['financialInputs'],'rows':rows,'externalFields':provenance['externalFields'],
        'compositionVersion':'financial_dataset_v1'}))
    joined=stream_digest(canonical_chunks({'schemaVersion':1,'rows':rows,'provenance':provenance}),24*1024**2)
    normalized={'label':'legacy_v2_numeric_panel_semantics_reconstructed_from_retained_components',
        'successfulLegacy50DatasetExists':False,'sourceRowsChanged':False,'normalizedLogicalTypeChanges':type_changes,
        'fullLogicalJoinedDocument':joined,'financialDatasetRoot':provenance['financialDatasetRoot'],
        'sourceDataFingerprint':provenance['dataFingerprint'],'prototypeOracleSha256':sha(_read_file(oracle/'summary.json',262144))}
    if mode=='build':save(out/'normalized-oracle.json',normalized)
    else:require(json.loads(_read_file(out/'normalized-oracle.json',262144))==normalized,'ORACLE','Pinned normalized oracle changed')
    expected={**expected,**normalized}
    del table,provenance,original_rows,frame,rows
    directory=out/'dataset' 
    if mode=='build':
        snapshot=_read_file(source/'source-market-snapshot.json',24*1024**2)
        manifest=_read_file(source/'source-market-manifest.json',512*1024)
        view=derive_market_snapshot_view(snapshot,manifest,{'kind':'snapshot_scope_view','version':1,'mode':'exact',**declaration['scope']},
            expected_bundle_id=sha(manifest),expected_snapshot_sha256=sha(snapshot))
        roots={row['package']:row for row in expected['graphs']}
        sources=[FinancialSource(_read_file(source/f'financial-source-{i}.json',24*1024**2),roots[i]['preparedRoot'],next(iter(registry)))
                 for i in range(declaration['packages'])]
        directory.mkdir(mode=0o700);(directory/'parts').mkdir(mode=0o700)
        def write(c,n,raw):
            folder=directory/'parts'/c;folder.mkdir(mode=0o700,exist_ok=True)
            with (folder/f'{n}.bin').open('xb') as f:os.chmod(folder/f'{n}.bin',0o600);f.write(raw)
        pub=compose_graph_dataset_components(view,sources,registry,write,market_calendar_ref=next(iter(registry)))
        with (directory/'manifest.json').open('xb') as f:os.chmod(directory/'manifest.json',0o600);f.write(pub.manifest_bytes)
        result=pub.result;reader=DirectoryGraphDatasetReader(directory,expected_root=pub.dataset_root)
        transport=reader.verify_integrity()
    else:
        pinned=json.loads(_read_file(out/'build.json',262144))['datasetRoot']
        reader=DirectoryGraphDatasetReader(directory,expected_root=pinned)
        result=restore_graph_dataset(reader,registry);transport={'transportVerified':True}
    require(result.logical_joined==expected['fullLogicalJoinedDocument'],'JOINED','Complete old/new logical joined identity differs')
    require(result.provenance['financialDatasetRoot']==expected['financialDatasetRoot'] and
            result.provenance['dataFingerprint']==expected['sourceDataFingerprint'],'SOURCE_ROOT','Original financial/data roots differ')
    require(len(result.data)==13100 and len(result.data.ts_code.unique())==50,'SCOPE','Full predeclared scope was not retained')
    for item in reader.manifest['components']:
        if item['type']=='financial_prepared_graph':
            roots=item['semanticRoots'];candidate=next(x for x in expected['graphs'] if x['preparedRoot']==roots['preparedRoot'])
            require(roots['preparedPayloadSha256']==candidate['payloadSha256'],'PREPARED','Original exact prepared bytes differ')
    physical=len(reader.manifest_bytes)+sum(x['byteLength'] for x in reader.manifest['components'])
    require(physical<=64*1024**2,'BYTES','Complete graph source exceeds original physical guard')
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    summary={'status':'PASS_SOURCE_CLOSURE_ONLY','phase':mode,'providerCalls':0,'modelsFitted':0,
        'sourceDirectory':str(source),'oracleDirectory':str(oracle),'datasetRoot':reader.dataset_root,
        'symbols':50,'rows':len(result.data),'states':16,'calendarDaysInclusive':366,'physicalClosureBytes':physical,
        'manifestBytes':len(reader.manifest_bytes),'parts':sum(len(x['parts']) for x in reader.manifest['components']),
        'components':len(reader.manifest['components']),'logicalJoined':result.logical_joined,
        'financialDatasetRoot':result.provenance['financialDatasetRoot'],'dataFingerprint':result.provenance['dataFingerprint'],
        'sourceValidation':result.validation_report,'transport':transport,'childWallSeconds':time.monotonic()-started,
        'childPeakRssBytes':peak,'modelAdmissionRegistered':False,'thinSnapshotImplemented':False,
        'twoArchiveAuditorImplemented':False,'hosted':False,'production':False}
    save(out/f'{mode}.json',summary)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--oracle',required=True)
    p.add_argument('--output',required=True);p.add_argument('--child',choices=('build','recompose'));a=p.parse_args()
    source=Path(a.source).resolve();oracle=Path(a.oracle).resolve();out=Path(a.output).resolve()
    if a.child:
        try:child(source,oracle,out,a.child)
        except BaseException as e:save(out/f'{a.child}-failure.json',{'status':'FAIL','code':getattr(e,'code',None),'type':type(e).__name__,'message':str(e),'providerCalls':0,'modelsFitted':0});raise
        return
    out.mkdir(mode=0o700)
    budgets={'totalWallSeconds':900,'rssBytes':3*1024**3,'minimumFreeDiskBytes':500*1024**2,'physicalBytes':64*1024**2,'joinedBytes':24*1024**2}
    save(out/'predeclaration.json',{'sourceDirectory':str(source),'oracleDirectory':str(oracle),'budgets':budgets,
        'scope':{'symbols':50,'calendarDaysInclusive':366,'states':16},'providerCalls':0,'modelsFitted':0,
        'phases':['fresh_raw_package_build','separate_process_fresh_raw_package_recomposition']})
    started=time.monotonic();phases=[]
    for mode in ['build','recompose']:
        peak=0;stopped=None;phase_start=time.monotonic()
        with (out/f'{mode}.log').open('x') as log:
            os.chmod(out/f'{mode}.log',0o600)
            process=subprocess.Popen([sys.executable,__file__,'--source',str(source),'--oracle',str(oracle),'--output',str(out),'--child',mode],stdout=log,stderr=log)
            try:
                while process.poll() is None:
                    raw=subprocess.run(['ps','-o','rss=','-p',str(process.pid)],capture_output=True,text=True,check=False)
                    peak=max(peak,int(raw.stdout.strip() or '0')*1024)
                    if peak>budgets['rssBytes']:stopped='RSS_BUDGET'
                    if time.monotonic()-started>budgets['totalWallSeconds']:stopped='WALL_BUDGET'
                    if shutil.disk_usage(out).free<budgets['minimumFreeDiskBytes']:stopped='DISK_RESERVE'
                    if stopped:process.terminate();break
                    time.sleep(.2)
                process.wait(timeout=5)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
        result={'phase':mode,'exitCode':process.returncode,'sampledPeakRssBytes':peak,'wallSeconds':time.monotonic()-phase_start,'stopReason':stopped}
        save(out/f'{mode}-supervisor.json',result);phases.append(result)
        if process.returncode or stopped:break
    result={'phases':phases,'totalWallSeconds':time.monotonic()-started,'complete':len(phases)==2 and all(x['exitCode']==0 and x['stopReason'] is None for x in phases)}
    save(out/'supervisor.json',result);print(json.dumps(result));raise SystemExit(0 if result['complete'] else 1)

if __name__=='__main__':main()
