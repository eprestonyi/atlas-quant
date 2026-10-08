#!/usr/bin/env python3
"""Fresh-process result/source reconstruction; never fits F or contacts a provider."""
import argparse,json,os,resource,shutil,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'));sys.path.insert(0,str(ROOT/'scripts'))


def save(path,value):
    with path.open('x') as f:
        os.chmod(path,0o600);json.dump(value,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())


def child(a,out):
    def denied(*args,**kwargs):raise RuntimeError('NETWORK_FORBIDDEN')
    socket.create_connection=denied;socket.socket.connect=denied
    from atlas_quant.compute_slot import compute_slot
    from atlas_quant.financial_bundle_v2 import financial_graph_directory_reader
    from atlas_quant.research_dataset.graph_v3.archive import extract_graph_dataset_archive
    from atlas_quant.research_dataset.graph_v3.dataset import DirectoryGraphDatasetReader
    from atlas_quant.research_dataset.codec import require
    from dataset_audit import load_registry_pins
    started=time.monotonic()
    with compute_slot(a.compute_lock_path,deadline=started+900):
        reader=financial_graph_directory_reader(a.bundle)
        require(reader.bundle_id==a.expected_bundle_id,'IDENTITY','Pinned result bundle differs')
        extract_graph_dataset_archive(a.source_archive,out/'source',expected_root=a.expected_dataset_root)
        source=DirectoryGraphDatasetReader(out/'source',expected_root=a.expected_dataset_root)
        result=reader.restore_sources(source,load_registry_pins(a.registry_pins))
        save(out/'recomposition.json',{'status':'PASS_FRESH_PROCESS_RECOMPOSITION_NO_FIT','bundleId':reader.bundle_id,
            'datasetRoot':source.dataset_root,'forecastArtifactId':reader.manifest['forecastArtifactId'],
            'rows':len(result.data),'symbols':len(result.data.ts_code.unique()),'logicalJoined':result.logical_joined,
            'sourceValidation':result.validation_report,'snapshotExact':True,'modelsFitted':0,'providerCalls':0,
            'wallSeconds':time.monotonic()-started,
            'childPeakRssBytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('bundle','source-archive','registry-pins','expected-bundle-id','expected-dataset-root','compute-lock-path','output'):p.add_argument('--'+n,required=True)
    p.add_argument('--child',action='store_true');a=p.parse_args();out=Path(a.output).resolve()
    if a.child:
        try:child(a,out)
        except BaseException as e:save(out/'failure.json',{'status':'FAIL','code':getattr(e,'code',None),'type':type(e).__name__,'message':str(e),'modelsFitted':0,'providerCalls':0});raise
        return
    out.mkdir(mode=0o700);started=time.monotonic();peak=0;stop=None
    save(out/'predeclaration.json',{'bundleId':a.expected_bundle_id,'datasetRoot':a.expected_dataset_root,
        'wallSeconds':900,'rssBytes':3*1024**3,'freeDiskBytes':500*1024**2,'providerCalls':0,'modelsFitted':0,
        'sourceArchive':str(Path(a.source_archive).resolve()),'registryPins':str(Path(a.registry_pins).resolve()),'computeLockPath':a.compute_lock_path})
    cmd=[sys.executable,__file__,'--child']
    for k,v in vars(a).items():
        if k not in ('child',):cmd.extend(['--'+k.replace('_','-'),str(v)])
    with (out/'child.log').open('x') as log:
        os.chmod(out/'child.log',0o600);proc=subprocess.Popen(cmd,stdout=log,stderr=log)
        try:
            while proc.poll() is None:
                q=subprocess.run(['ps','-o','rss=','-p',str(proc.pid)],capture_output=True,text=True,check=False)
                peak=max(peak,int(q.stdout.strip() or '0')*1024)
                if peak>3*1024**3:stop='RSS_BUDGET'
                if time.monotonic()-started>900:stop='WALL_BUDGET'
                if shutil.disk_usage(out).free<500*1024**2:stop='DISK_RESERVE'
                if stop:proc.terminate();break
                time.sleep(.2)
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
    result={'exitCode':proc.returncode,'stopReason':stop,'wallSeconds':time.monotonic()-started,'sampledPeakRssBytes':peak}
    save(out/'supervisor.json',result);print(json.dumps(result));raise SystemExit(proc.returncode or int(bool(stop)))

if __name__=='__main__':main()
