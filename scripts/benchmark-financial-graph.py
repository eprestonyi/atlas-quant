#!/usr/bin/env python3
"""Supervised graph/column component acceptance from retained synthetic sources; no F."""
import argparse,json,os,resource,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))


def save(path,value):
    with path.open('x') as f:
        os.chmod(path,0o600);json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())


def child(source,out):
    from copy import deepcopy
    import pandas as pd
    from atlas_quant.provider import _validate_panel,_records
    from atlas_quant.financial_statements.package import decode_package
    from atlas_quant.financial_statements.prepare import _safe_rows
    from atlas_quant.financial_runner.trust import resolve_package_registry
    from atlas_quant.research_dataset import derive_market_snapshot_view
    from atlas_quant.research_dataset.codec import encode,sha,require
    from atlas_quant.research_dataset.graph_v3 import encode_graph,verify_graph,iter_panel_rows,encode_table,verify_table
    from atlas_quant.research_dataset.graph_v3.streams import stream_digest,canonical_chunks,LOGICAL_LIMIT
    def write(name,value):
        raw=encode(value)
        with (out/name).open('xb') as f:os.chmod(out/name,0o600);f.write(raw)
        return len(raw)
    def read(name,limit=64*1024**2):
        p=source/name;require(p.stat().st_size<=limit,'BYTES','Retained source exceeds bounded file input')
        return p.read_bytes()
    started=time.monotonic();declaration=json.loads(read('predeclaration.json'));scope=declaration['scope']
    registry_map=json.loads(read('registry-pins.json'))
    registry={k:Path(v).read_bytes() for k,v in registry_map.items()}
    require(len(registry)==1,'REGISTRY','Fixture requires its independently pinned calendar')
    for key,raw in registry.items():
        require(raw==read('downloaded-registry/'+key+'.json'),'REGISTRY','Independently pinned bytes differ')
    original_market=read('source-market-snapshot.json',24*1024**2)
    original_manifest=read('source-market-manifest.json',512*1024)
    view=derive_market_snapshot_view(original_market,original_manifest,
        {'kind':'snapshot_scope_view','version':1,'mode':'exact',**scope},
        expected_bundle_id=sha(original_manifest),expected_snapshot_sha256=sha(original_market))
    market=json.loads(view.market_bytes)
    inputs=[];source_bytes=0
    for index in range(declaration['packages']):
        raw=read(f'financial-source-{index}.json',24*1024**2);source_bytes+=len(raw)
        package=resolve_package_registry(raw,next(iter(registry.values())),[]).package
        inputs.append((package['packRoot'],index,package))
    inputs.sort()
    panels={};metadata=[];field_sources={};measurements=[];graph_bytes=0
    for _,index,package in inputs:
        raw=read(f'prepared-{index}.json');payload=json.loads(raw)
        graph=encode_graph(payload,package['selection'])
        report=verify_graph(graph,expected_prepared_root=payload['provenance']['preparedRoot'],expected_payload_sha256=sha(raw))
        reconstructed=iter_panel_rows(graph)
        require(stream_digest(canonical_chunks(payload['panel']),32*1024**2)['sha256']==
                stream_digest(canonical_chunks(list(reconstructed)),32*1024**2)['sha256'],'PANEL','Rebuilt daily panel differs')
        for row in iter_panel_rows(graph):
            key=(row['ts_code'],row['trade_date']);require(key not in panels,'SCOPE','Overlapping synthetic sources')
            panels[key]=row
        physical=write(f'graph-{index}.json',graph);graph_bytes+=physical
        measurements.append({'package':index,'graphBytes':physical,'logicalPreparedBytes':len(raw),
                             'preparedRoot':report['preparedRoot'],'payloadSha256':sha(raw),'panelExact':True})
        prov=payload['provenance'];selected=package['selection']['selectedStates'];symbols=package['selection']['universe']['symbols']
        metadata.append({'packRoot':package['packRoot'],'preparedRoot':prov['preparedRoot'],'calendarRoot':prov['calendar']['root'],
                         'unitPolicy':package['unitPolicy'],'selectedStateIds':selected})
        for state in selected:
            field_sources.setdefault(state,[]).append({'symbols':sorted(symbols),'packRoot':package['packRoot'],
                'preparedRoot':prov['preparedRoot'],'calendarRoot':prov['calendar']['root'],
                'unitPolicy':package['unitPolicy'],'evidence':prov['externalFields'][state]})
        del payload,raw,graph
    external=deepcopy(market['provenance'].get('externalFields',{}))
    for state,sources in field_sources.items():
        external[state]={'source':'FROZEN_NATIVE_STATEMENT_PREPARATION','path':'financial_state/'+state,'dataType':'number',
            'unit':'ratio','availabilityPolicy':'point_in_time_asof','availableDateColumn':state+'__available_date',
            'semanticKind':'native_statement_state','formulaId':state,'formulaVersion':sources[0]['evidence']['formulaVersion'],
            'preparedInputs':sources,'authentication':'proof_and_calendar_authenticity_is_trusted_caller_responsibility',
            'qualityFlags':sorted({x for source in sources for x in source['evidence']['qualityFlags']})}
    rows=[{**row,**panels[row['ts_code'],row['trade_date']]} for row in market['rows']]
    data=_validate_panel({'universe':scope},rows,external_fields=external)
    rows=_safe_rows(data)  # Match actual legacy dataset joining numeric normalization.
    # This reproduces existing logical metadata for exact size measurement only;
    # it does not call private model-admission registration or fit F.
    provenance=deepcopy(market['provenance'])
    provenance.update(source='COMPOSED_FINANCIAL_DATASET',marketRoot=sha(view.market_bytes),marketSource=market['provenance'].get('source'),
        financialCompositionVersion='financial_dataset_v1',financialInputs=metadata,externalFields=external,
        rows=len(rows),dataFingerprint=sha(encode(_records(data))),synthetic=True,
        financialSourceScope='selected_frozen_report_set_not_complete_filing_history')
    provenance['financialDatasetRoot']=sha(encode({'marketRoot':provenance['marketRoot'],'financialInputs':metadata,
        'rows':rows,'externalFields':external,'compositionVersion':'financial_dataset_v1'}))
    joined={'schemaVersion':1,'rows':rows,'provenance':provenance}
    joined_descriptor=stream_digest(canonical_chunks(joined),LOGICAL_LIMIT)
    kinds={k:'dictionary' if k in ('ts_code','trade_date') or k.endswith('__available_date') else 'number' for k in rows[0]}
    table=encode_table(rows,kinds);table_info=verify_table(table);table_bytes=write('numeric-columns.json',table)
    write('joined-provenance.json',provenance)
    physical=graph_bytes+table_bytes+source_bytes+len(view.origin_bytes)+len(view.market_bytes)+sum(map(len,registry.values()))+len(encode(provenance))+262144
    require(physical<=64*1024**2,'CLOSURE_BYTES','Measured components and full manifest reserve exceed 64 MiB')
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    save(out/'summary.json',{'status':'PASS_COMPONENTS_ONLY','synthetic':True,'providerCalls':0,'modelsFitted':0,
        'sourceDirectory':str(source),'symbols':len(scope['symbols']),'calendarDaysInclusive':366,'rows':len(rows),
        'financialStates':len(field_sources),'graphs':measurements,'graphBytes':graph_bytes,'numericTableBytes':table_bytes,
        'fullLogicalJoinedDocument':joined_descriptor,'logicalBudgetBytes':LOGICAL_LIMIT,'sourceDataFingerprint':provenance['dataFingerprint'],
        'financialDatasetRoot':provenance['financialDatasetRoot'],'measuredPhysicalComponentsIncludingManifestReserve':physical,
        'wholeDatasetManifestImplemented':False,'sourceAuthorityAdmissionRegistered':False,'financialSnapshotImplemented':False,
        'sourceAndResultArchivesAudited':False,'componentChildPeakRssBytes':peak,'componentWallSeconds':time.monotonic()-started,
        'tableLogicalRows':table_info['logicalRows'],
        'limitations':['Component acceptance only; not an admitted dataset/3, hosted transport or F run.',
          'The complete joined document remains under the unchanged 24 MiB guard.',
          'Source recomposition from raw package formulas and independent archive verifier remain next gates.']})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--child',action='store_true');a=p.parse_args()
    source=Path(a.source).resolve();out=Path(a.output).resolve()
    if a.child:
        try:child(source,out)
        except BaseException as e:save(out/'failure.json',{'status':'FAIL','code':getattr(e,'code',None),'type':type(e).__name__,'message':str(e),'modelsFitted':0,'providerCalls':0});raise
        return
    out.mkdir(mode=0o700)
    budgets={'wallSeconds':900,'rssBytes':3*1024**3,'minimumFreeDiskBytes':500*1024**2,'physicalClosureBytes':64*1024**2,'logicalJoinedBytes':24*1024**2}
    save(out/'predeclaration.json',{'sourceDirectory':str(source),'budgets':budgets,'modelFit':False,'providerCalls':0})
    started=time.monotonic();peak=0;stopped=None
    with (out/'child.log').open('x') as log:
        os.chmod(out/'child.log',0o600)
        process=subprocess.Popen([sys.executable,__file__,'--child','--source',str(source),'--output',str(out)],stdout=log,stderr=log)
        try:
            while process.poll() is None:
                raw=subprocess.run(['ps','-o','rss=','-p',str(process.pid)],capture_output=True,text=True,check=False)
                peak=max(peak,int(raw.stdout.strip() or '0')*1024)
                if peak>budgets['rssBytes']:stopped='RSS_BUDGET'
                if time.monotonic()-started>budgets['wallSeconds']:stopped='WALL_BUDGET'
                if shutil.disk_usage(out).free<budgets['minimumFreeDiskBytes']:stopped='DISK_RESERVE'
                if stopped:process.terminate();break
                time.sleep(.2)
            process.wait(timeout=5)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
    result={'exitCode':process.returncode,'supervisorWallSeconds':time.monotonic()-started,'sampledPeakRssBytes':peak,'stopReason':stopped}
    save(out/'supervisor.json',result);print(json.dumps(result));raise SystemExit(process.returncode or int(bool(stopped)))

if __name__=='__main__':main()
