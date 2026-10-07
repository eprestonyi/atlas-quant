"""The documented private export and replay commands are runnable workflows."""
import json
import os
from pathlib import Path
import subprocess
import sys

from atlas_quant.bundle import directory_reader

ROOT = Path(__file__).resolve().parents[2]


def command(*args):
    return subprocess.run([sys.executable,*args],cwd=ROOT,capture_output=True,text=True,timeout=60,
                          env={**os.environ,'PYTHONPATH':str(ROOT/'engine')})


def test_cli_bundle_export_execution_and_independent_audit(tmp_path):
    forecast = tmp_path/'forecast'
    receipt = tmp_path/'forecast-receipt.json'
    first = command('scripts/local-run.py','engine/examples/statistical-quant.json','--source','demo',
                    '--bundle-output',str(forecast),'--output',str(receipt))
    assert first.returncode == 0, first.stderr + first.stdout
    record = json.loads(receipt.read_text())
    assert record['bundle']['complete'] is True and 'result' not in record
    source = directory_reader(forecast)
    original_id = source.manifest['forecastArtifactId']
    execution = tmp_path/'execution'
    second = command('scripts/replay-execution.py','--source-bundle',str(forecast),'--bundle-output',str(execution))
    assert second.returncode == 0, second.stderr + second.stdout
    replay = directory_reader(execution)
    assert replay.manifest['forecastArtifactId'] == original_id
    assert replay.document('report')['research']['predictionRefitPerformed'] is False
    audit = command('scripts/audit-bundle.py',str(execution),'--source-bundle',str(forecast))
    assert audit.returncode == 0, audit.stderr + audit.stdout
    proof = json.loads(audit.stdout)
    assert proof['sourceForecastAndPlanIdentical'] is True and proof['trades'] > 0
    before = (forecast/'manifest.json').read_bytes()
    rejected = command('scripts/replay-execution.py','--source-bundle',str(forecast),'--output',str(forecast/'manifest.json'))
    assert rejected.returncode != 0 and (forecast/'manifest.json').read_bytes() == before
