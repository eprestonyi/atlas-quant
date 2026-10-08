#!/usr/bin/env python3
"""One declared local graph/3 automatic F job with immutable result/source exports."""
import argparse,json,os,resource,shutil,socket,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'));sys.path.insert(0,str(ROOT/'scripts'))


def save(path,value):
    with path.open('x') as f:
        os.chmod(path,0o600);json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())


def progress(out,value):
    with (out/'progress.jsonl').open('a') as f:
        os.chmod(out/'progress.jsonl',0o600);f.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n');f.flush()
    temp=out/'current-progress.tmp';temp.write_text(json.dumps(value,allow_nan=False));temp.chmod(0o600);temp.replace(out/'current-progress.json')


def child(args,out):
    def denied(*a,**kw):raise RuntimeError('PROVIDER_NETWORK_FORBIDDEN')
    socket.create_connection=denied;socket.socket.connect=denied
    from atlas_quant.research_dataset.graph_v3.dataset import DirectoryGraphDatasetReader
    from atlas_quant.research_dataset.graph_v3.snapshot import RESEARCH_PROFILE,validate_snapshot
    from atlas_quant.research_dataset.graph_v3.research import run_graph_research
    from atlas_quant.research_dataset.graph_v3.archive import export_graph_dataset_archive
    from atlas_quant.financial_bundle_v2 import (build_financial_graph_bundle,FinancialGraphBundleReader,
        export_financial_graph_archive,financial_graph_directory_reader)
    from atlas_quant.research_dataset.reader import _read_file
    from atlas_quant.research_dataset.codec import encode,sha,require
    from dataset_audit import load_registry_pins
    pre=json.loads(_read_file(out/'predeclaration.json',262144));source=DirectoryGraphDatasetReader(args.dataset,expected_root=args.expected_root)
    pins=load_registry_pins(args.registry_pins);plans=[];started=time.monotonic()
    report,snapshot,fits=run_graph_research(pre['strategy'],source,pins,pre['sourceEvidence']['datasetRef'],
        research_profile=RESEARCH_PROFILE,work_dir=out,plan_sink=plans.append,progress=lambda v:progress(out,v))
    # Write immutable chunks directly; a valid manifest is published last. No
    # legacy spool/delivery capability is silently reused for this new codec.
    directory=out/'bundle';directory.mkdir(mode=0o700);(directory/'chunks').mkdir(mode=0o700)
    def write(c,n,raw):
        folder=directory/'chunks'/c;folder.mkdir(mode=0o700,exist_ok=True)
        with (folder/f'{n}.json').open('xb') as f:os.chmod(folder/f'{n}.json',0o600);f.write(raw)
    def read(c,n):return _read_file(directory/'chunks'/c/f'{n}.json',8*1024**2)
    manifest=build_financial_graph_bundle(report,encode(snapshot),plans[0],pre['sourceEvidence'],write,read)
    for item in json.loads(manifest)['collections']:(directory/'chunks'/item['id']).mkdir(mode=0o700,exist_ok=True)
    with (directory/'manifest.json').open('xb') as f:os.chmod(directory/'manifest.json',0o600);f.write(manifest)
    reader=financial_graph_directory_reader(directory);reader.verify_integrity()
    export_financial_graph_archive(reader,out/'financial.tar');export_graph_dataset_archive(source,out/'dataset.tar')
    forecast=report['forecasts'];d=forecast['diagnostics'];baseline=d['factorIncrement']['baselineRows']
    stats=forecast['factorResearch']['diagnostics'];features=len(stats['features']);joints=stats['dependence']['jointDistributions']
    require(len(forecast['rows'])==len(baseline)==len(plans[0]['origins']),'COVERAGE','Forecast or baseline coverage differs')
    require(stats['dependence']['omittedPairs']==0 and len(joints)==features*(features-1)//2,'DIAGNOSTICS','Complete pair diagnostics missing')
    require(d['selectionAudit']['candidateCount']==8 and d['selectionAudit']['researchFitBudget']['branches']==2,'SELECTION','Predeclared selection changed')
    require(report['trades']==[] and report['metrics'] is None,'EXECUTION','Financial graph research cannot execute')
    save(out/'fit-events.json',list(fits))
    save(out/'snapshot-measurements.json',validate_snapshot(snapshot))
    save(out/'summary.json',{'status':'PASS_LOCAL_F_PENDING_INDEPENDENT_PAIRED_AUDIT','jobId':pre['jobId'],
        'providerCalls':0,'synthetic':True,'hosted':False,'production':False,'bundleId':reader.bundle_id,
        'datasetRoot':source.dataset_root,'forecastArtifactId':forecast['artifactId'],'symbols':len(pre['strategy']['universe']['symbols']),
        'factorCount':len(pre['strategy']['factors']),'forecastRows':len(forecast['rows']),'baselineRows':len(baseline),
        'plannedOrigins':len(plans[0]['origins']),'modelFits':len(forecast['modelFits']),'fitCount':len(fits),
        'factorFeatures':features,'jointDistributions':len(joints),'functionArtifacts':len(forecast['factorResearch']['modelFunctions']),
        'selectionAudit':d['selectionAudit'],'selectedModel':d['selectedModel'],'evidenceStatus':report['selection']['evidenceStatus'],
        'trades':0,'snapshot':validate_snapshot(snapshot),'wallSeconds':time.monotonic()-started,
        'childPeakRssBytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
        'archives':{n:{'byteLength':(out/n).stat().st_size,'sha256':sha((out/n).read_bytes())} for n in ('financial.tar','dataset.tar')},
        'limitations':['Artificial source and local explicit profile only; no market advantage claimed.',
            'Independent full paired archive audit and separate source restoration remain required.',
            'No hosted or production capability registered.']})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','expected-root','registry-pins','output','compute-lock-path'):p.add_argument('--'+name,required=True)
    p.add_argument('--child',action='store_true');args=p.parse_args();out=Path(args.output).resolve()
    if args.child:
        try:
            from atlas_quant.compute_slot import compute_slot
            with compute_slot(args.compute_lock_path,deadline=time.monotonic()+900):child(args,out)
        except BaseException as e:save(out/'failure.json',{'status':'FAIL','code':getattr(e,'code',None),'type':type(e).__name__,'message':str(e),'providerCalls':0});raise
        return
    from atlas_quant.research_dataset.graph_v3.dataset import DirectoryGraphDatasetReader
    from atlas_quant.research_dataset.graph_v3.snapshot import RESEARCH_PROFILE,validate_research_profile
    from atlas_quant.financial_statements.recipes import RECIPES
    from atlas_quant.statistical_quant.models import candidates
    from atlas_quant.compute_slot import validate_slot_path
    from atlas_quant.research_dataset.codec import decode,require
    validate_slot_path(args.compute_lock_path)
    source=DirectoryGraphDatasetReader(args.dataset,expected_root=args.expected_root)
    envelope=decode(source.payload('researchColumns'),24*1024**2)
    require(envelope['provenance'].get('synthetic') is True,'SYNTHETIC_REQUIRED','This benchmark only accepts explicit artificial source')
    del envelope
    scope=source.manifest['scope']
    strategy=validate_research_profile({'schemaVersion':2,'name':'Predeclared synthetic full-scope graph fundamental auto F',
        'universe':scope,'research':{'mode':'statistical_quant'},'target':{'kind':'asset_price','horizonSessions':5},
        'model':{'family':'fundamental','estimator':'auto','trainWindow':120,'refitDays':20},
        'validation':{'minTrainDates':40,'innerFolds':2,'outerFolds':2},'execution':{'enabled':False},
        'factors':[{'id':key,'expression':key,'role':'predictor'} for key in RECIPES]},scope,research_profile=RESEARCH_PROFILE)
    out.mkdir(mode=0o700)
    limits={'wallSeconds':900,'perFitWallSeconds':300,'rssBytes':3*1024**3,'minimumFreeDiskBytes':500*1024**2,
            'sourcePhysicalBytes':64*1024**2,'logicalJoinedBytes':24*1024**2,'snapshotPhysicalAndLogicalBytes':24*1024**2}
    save(out/'predeclaration.json',{'jobId':str(uuid.uuid4()),'strategy':strategy,'sourceEvidence':{
        'datasetRef':{'datasetId':str(uuid.uuid4()),'datasetRoot':source.dataset_root,'format':'atlas.quant.research_dataset','version':3},
        'admissionProfile':RESEARCH_PROFILE},'candidates':candidates('auto'),'factorFreeBaselineRequired':True,
        'jointPairs':'all_declared_features','limits':limits,'providerCalls':0,'registryPins':str(Path(args.registry_pins).resolve()),
        'sourceDirectory':str(Path(args.dataset).resolve()),'sourceRestoreAndFInSameChild':True,'computeLockPath':args.compute_lock_path})
    started=time.monotonic();peak=0;stop=None
    with (out/'child.log').open('x') as log:
        os.chmod(out/'child.log',0o600)
        proc=subprocess.Popen([sys.executable,__file__,'--child','--dataset',str(Path(args.dataset).resolve()),
            '--expected-root',args.expected_root,'--compute-lock-path',args.compute_lock_path,'--registry-pins',str(Path(args.registry_pins).resolve()),'--output',str(out)],stdout=log,stderr=log)
        try:
            while proc.poll() is None:
                info=subprocess.run(['ps','-o','rss=','-p',str(proc.pid)],capture_output=True,text=True,check=False)
                peak=max(peak,int(info.stdout.strip() or '0')*1024)
                if peak>limits['rssBytes']:stop='RSS_BUDGET'
                if time.monotonic()-started>limits['wallSeconds']:stop='WALL_BUDGET'
                if shutil.disk_usage(out).free<limits['minimumFreeDiskBytes']:stop='DISK_RESERVE'
                try:
                    phase=json.loads((out/'current-progress.json').read_text())
                    if phase.get('phase')=='fit_started' and time.monotonic()-phase['startedMonotonic']>limits['perFitWallSeconds']:stop='FIT_WALL_BUDGET'
                except FileNotFoundError:pass
                if stop:proc.terminate();break
                time.sleep(.2)
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
    result={'exitCode':proc.returncode,'stopReason':stop,'wallSeconds':time.monotonic()-started,'sampledPeakRssBytes':peak}
    save(out/'supervisor.json',result);print(json.dumps(result));raise SystemExit(proc.returncode or int(bool(stop)))

if __name__=='__main__':main()
