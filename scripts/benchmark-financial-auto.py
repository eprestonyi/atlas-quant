#!/usr/bin/env python3
"""New local auto F from existing synthetic dataset/2; never contacts providers.

This measures exactly the supplied frozen scope, not the 50-security upper bound.
The result archive and unchanged source archive are audited separately by the
stdlib-only audit-financial-bundle.py. All outputs require a new directory.
"""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
sys.path.insert(0, str(ROOT / 'scripts'))

# The spawn child imports this module, so the same prohibition applies inside F.
import requests
import urllib.request

def no_network(*args, **kwargs):
    raise RuntimeError('Financial frozen-input benchmark forbids network access')

requests.sessions.Session.request = no_network
urllib.request.urlopen = no_network


def save(path, value):
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def export_result_archive(reader, path):
    from atlas_quant.research_dataset.archive import BLOCK, header
    reader.verify_integrity()
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        def member(name, raw):
            stream.write(header(name, len(raw)))
            stream.write(raw)
            stream.write(bytes((-len(raw)) % BLOCK))
        member('manifest.json', reader.manifest_raw)
        for collection in reader.manifest['collections']:
            for part in collection['chunks']:
                member(f"chunks/{collection['id']}/{part['ordinal']}.json",
                       reader.read_chunk(collection['id'], part['ordinal']))
        stream.write(bytes(2 * BLOCK))
        stream.flush()
        os.fsync(stream.fileno())


def execute(args, output):
    from atlas_quant import runner
    from atlas_quant.financial_bundle import export_financial_bundle
    from atlas_quant.financial_bundle_spool import FinancialBundleSpool
    from atlas_quant.financial_statements.recipes import RECIPES
    from atlas_quant.research_dataset import DirectoryDatasetReader, export_dataset_archive
    from atlas_quant.research_dataset.codec import encode, sha
    from atlas_quant.research_dataset.research_profile import AUTO_PROFILE, validate_research_profile
    from atlas_quant.research_dataset_runner.spool import ResearchDatasetSpool
    from atlas_quant.statistical_quant.models import candidates
    from dataset_audit import load_registry_pins

    source = DirectoryDatasetReader(args.dataset, expected_root=args.expected_root)
    source.verify_integrity()
    registry = load_registry_pins(args.registry_pins)
    # Pins predate this job and must match separately acquired/downloaded bytes.
    for ref, pinned in registry.items():
        if (Path(args.registry_dir) / (ref + '.json')).read_bytes() != pinned:
            raise ValueError('Frozen registry differs from independently pinned source')
    scope = source.manifest['scope']
    strategy = validate_research_profile({
        'schemaVersion': 2, 'name': 'Predeclared frozen synthetic fundamental auto acceptance',
        'universe': scope, 'research': {'mode': 'statistical_quant'},
        'target': {'kind': 'asset_price', 'horizonSessions': 5},
        'model': {'family': 'fundamental', 'estimator': 'auto', 'trainWindow': 120, 'refitDays': 60},
        'validation': {'minTrainDates': 40, 'innerFolds': 2, 'outerFolds': 2},
        'execution': {'enabled': False},
        'factors': [{'id': key, 'expression': key, 'role': 'predictor'} for key in RECIPES],
    }, scope, research_profile=AUTO_PROFILE, dataset_version=2)
    joined_payload = json.loads(source.payload('researchRows'))
    if not joined_payload['provenance'].get('synthetic'):
        raise ValueError('This acceptance script requires explicitly synthetic frozen inputs')
    reference = {'datasetId': str(uuid.UUID(args.dataset_id)), 'datasetRoot': source.dataset_root,
                 'format': 'atlas.quant.research_dataset', 'version': 2}
    evidence = {'datasetRef': reference, 'admissionProfile': AUTO_PROFILE}
    job = {'id': str(uuid.uuid4()), 'leaseToken': str(uuid.uuid4()),
           'jobKind': 'forecast', 'dataSource': 'ready_dataset', 'dataset': None,
           'strategy': strategy, **evidence, 'sourceEvidence': evidence}
    budget = {'wallSeconds': 900, 'rssBytes': 3*1024**3, 'minimumFreeDiskBytes': 500*1024**2,
              'perFitTimeout': None, 'rssSamplingSeconds': 0.2}
    predeclared = {'jobId': job['id'], 'strategy': strategy, 'sourceEvidence': evidence,
                  'compositionProfile': source.manifest['profile'], 'candidates': candidates('auto'),
                  'factorFreeBaselineRequired': True, 'limits': budget, 'providerCalls': 0,
                  'transport': 'local_authenticated_encrypted_spool_not_http',
                  'inputRows': len(joined_payload['rows']), 'registryPins': str(Path(args.registry_pins).resolve())}
    save(output / 'predeclaration.json', predeclared)
    spool = runner.CompletionSpool({'api_base': 'https://local-only.invalid/quant/api',
        'runner_secret': 'local-synthetic-profile-acceptance-' + uuid.uuid4().hex,
        'delivery_dir': str(output / 'delivery'), 'poll_seconds': 0})
    context = FinancialBundleSpool.context_for(spool, job)
    inputs = ResearchDatasetSpool(context)
    inputs.write('manifest', source.manifest_bytes)
    for component in source.manifest['components']:
        for part in component['parts']:
            inputs.write(inputs.part_name(component['componentId'], part['ordinal']),
                         source.part(component['componentId'], part['ordinal']))
    index = []
    for ref, raw in registry.items():
        inputs.write('registry-' + ref, raw)
        index.append({'ref': ref, 'byteLength': len(raw), 'sha256': sha(raw)})
    inputs.write('registry-index', encode(index))
    inputs.write('input', encode({**evidence, 'sourceEvidence': evidence,
        'registry': {'count': len(index), 'totalBytes': sum(x['byteLength'] for x in index)}}))
    job['_datasetKey'] = inputs.key
    peak_sampled, termination = 0, None
    def guard():
        nonlocal peak_sampled, termination
        for child in multiprocessing.active_children():
            raw = subprocess.run(['ps','-o','rss=','-p',str(child.pid)], capture_output=True, text=True, check=False)
            peak_sampled = max(peak_sampled, int(raw.stdout.strip() or '0')*1024)
        if peak_sampled > budget['rssBytes']:
            termination = 'RSS_BUDGET'
        if shutil.disk_usage(output).free < budget['minimumFreeDiskBytes']:
            termination = 'DISK_RESERVE'
        return termination is not None
    started = time.monotonic()
    answer = runner.execute_bounded(job, timeout=900, bundle_context=context,
                                    compute_lock_path=str(output / 'compute.lock'), stop_requested=guard)
    wall = time.monotonic()-started
    child_peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if sys.platform != 'darwin': child_peak *= 1024
    measured = {'wallSeconds': wall, 'sampledPeakRssBytes': peak_sampled, 'childPeakRssBytes': child_peak,
                'termination': termination, 'providerCalls': 0}
    save(output / 'compute.json', measured)
    if 'error' in answer:
        save(output / 'failure.json', {'error': answer['error'], **measured})
        return 1
    result = FinancialBundleSpool(context).reader(answer['bundleId'])
    result.verify_integrity()
    rejoined = result.restore_sources(source, registry)
    if encode(rejoined.to_dataset()) != source.payload('researchRows'):
        raise ValueError('Independent source recomposition changed frozen research rows')
    export_financial_bundle(result, output / 'bundle')
    export_result_archive(result, output / 'financial.tar')
    export_dataset_archive(source, output / 'dataset.tar')
    report = result.document('report')
    forecast = report['forecasts']
    diagnostics = forecast['diagnostics']
    baseline = diagnostics['factorIncrement']['baselineRows']
    assert len(forecast['rows']) == len(baseline)
    assert diagnostics['selectionAudit']['candidateCount'] == 8
    assert diagnostics['selectionAudit']['researchFitBudget']['branches'] == 2
    assert report['trades'] == [] and report['metrics'] is None
    final = {'status':'PASS_LOCAL_COMPUTE_PENDING_INDEPENDENT_ARCHIVE_AUDIT', 'jobId':job['id'],
        'bundleId':result.bundle_id, 'forecastArtifactId':forecast['artifactId'], **measured,
        'symbols':len(scope['symbols']), 'inputRows':len(joined_payload['rows']),
        'factorCount':len(strategy['factors']), 'forecastRows':len(forecast['rows']), 'baselineRows':len(baseline),
        'modelFitCount':len(forecast['modelFits']), 'selectedModel':diagnostics['selectedModel'],
        'selectionAudit':diagnostics['selectionAudit'], 'evidenceStatus':report['selection']['evidenceStatus'],
        'factorFeatures':len(forecast['factorResearch']['diagnostics']['features']),
        'jointDistributions':len(forecast['factorResearch']['diagnostics']['dependence']['jointDistributions']),
        'functionArtifacts':len(forecast['factorResearch']['modelFunctions']), 'sourceRecompositionEqual':True,
        'sourceEvidence':evidence, 'trades':0,
        'archives':{name:{'byteLength':(output/name).stat().st_size,'sha256':sha((output/name).read_bytes())}
                    for name in ('financial.tar','dataset.tar')},
        'limitations':['Synthetic local compute, not production or provider acceptance.',
          'Resource proof is only for the supplied frozen scope.', 'No claim of predictive edge, unbiased selection or zero overfit.',
          'RSS is supervised; per-fit wall time is not separately limited.']}
    save(output / 'summary.json', final)
    print(json.dumps({k:final[k] for k in ('status','jobId','bundleId','symbols','forecastRows','baselineRows','wallSeconds','childPeakRssBytes','evidenceStatus')}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('dataset','expected-root','dataset-id','registry-pins','registry-dir','output'):
        parser.add_argument('--'+option, required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(mode=0o700)
    try:
        return execute(args, output)
    except Exception as error:
        save(output / 'exception.json', {'status':'FAIL','type':type(error).__name__,'message':str(error)})
        raise

if __name__ == '__main__':
    raise SystemExit(main())
