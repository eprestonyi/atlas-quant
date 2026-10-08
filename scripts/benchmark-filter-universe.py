#!/usr/bin/env python3
"""Predeclared 1000-symbol whole-filter research with a hard local subprocess supervisor."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))


def benchmark_strategy():
    from atlas_quant.capacity.benchmark import benchmark_strategy as original

    strategy = original()
    strategy["name"] = "Predeclared 1000 synthetic whole-filter pooled Ridge capacity"
    strategy["universe"] = {
        "symbols": [f"{100000+i:06d}.SZ" for i in range(1000)],
        "start": "20240101",
        "end": "20241231",
    }
    strategy["model"].update(
        family=os.environ.get("ATLAS_CAPACITY_FAMILY", "mean_reversion"),
        trainWindow=120,
        refitDays=20,
    )
    from atlas_quant.capacity.profiles import AUTO_FILTER_PROFILES, TREND_AUTO_PROFILE_ID
    if os.environ.get("ATLAS_CAPACITY_PROFILE") in AUTO_FILTER_PROFILES:
        strategy["name"] = "Predeclared 1000 synthetic whole-filter pooled auto candidate capacity"
        strategy["model"]["estimator"] = "auto"
        if os.environ.get("ATLAS_CAPACITY_PROFILE") == TREND_AUTO_PROFILE_ID:
            strategy["name"] = "Predeclared 1000 synthetic whole-filter pooled trend auto capacity"
    strategy["validation"]["minTrainDates"] = 40
    return strategy


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        json.dump(
            value, stream, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2
        )
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def child(output):
    from atlas_quant.capacity import run_capacity_research
    from atlas_quant.capacity.profiles import FULL_FILTER_PROFILE_ID
    PROFILE_ID = os.environ.get("ATLAS_CAPACITY_PROFILE", FULL_FILTER_PROFILE_ID)
    from atlas_quant.capacity.benchmark import make_benchmark_data
    from atlas_quant.capacity.core import disk_bytes, peak_rss_bytes
    from atlas_quant.bundle import build_bundle, BundleReader, sha

    strategy = benchmark_strategy()
    data, provenance = make_benchmark_data(strategy)
    provenance["symbolIdentities"] = (
        "1000 synthetic codes; not historical CSI1000 constituents"
    )
    plans = []
    result = run_capacity_research(
        strategy,
        data,
        provenance,
        profile_id=PROFILE_ID,
        cache_dir=output / "cache",
        plan_sink=plans.append,
        progress=lambda state: save(output / "progress.json", state),
    )
    save(
        output / "progress.json",
        {"phase": "bundle_writing", "startedMonotonic": time.monotonic()},
    )
    # Same exact snapshot representation, with an explicitly expanded research
    # profile. No old provider/runner size validator is patched or bypassed.
    snapshot = {
        "schemaVersion": 1,
        "rows": data.astype(object).where(data.notna(), None).to_dict(orient="records"),
        "provenance": provenance,
        "sourceDataFingerprint": provenance["dataFingerprint"],
        "dataFingerprint": result["forecasts"]["dataFingerprint"],
        "fingerprintVersion": "research_input_v1",
    }
    bundle = output / "bundle"
    bundle.mkdir(mode=0o700)
    (bundle / "chunks").mkdir(mode=0o700)

    def path(collection, ordinal):
        return bundle / "chunks" / collection / (str(ordinal) + ".json")

    def write(collection, ordinal, raw):
        p = path(collection, ordinal)
        p.parent.mkdir(mode=0o700, exist_ok=True)
        with p.open("xb") as stream:
            os.chmod(p, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def read(collection, ordinal):
        return path(collection, ordinal).read_bytes()

    manifest = build_bundle(result, snapshot, plans[0], write, read)
    reader = BundleReader(manifest, read)
    verification = reader.verify_integrity()
    with (bundle / "manifest.json").open("xb") as stream:
        os.chmod(bundle / "manifest.json", 0o600)
        stream.write(manifest)
        stream.flush()
        os.fsync(stream.fileno())
    diag = result["forecasts"]["diagnostics"]
    summary = {
        "status": "completed",
        "synthetic": True,
        "providerCalls": 0,
        "strategy": strategy,
        "capacity": result["capacity"],
        "bundleId": sha(manifest),
        "verification": verification,
        "peakRssBytes": peak_rss_bytes(),
        "outputBytes": disk_bytes(output),
        "documents": {
            k: {j: v[j] for j in ("byteLength", "sha256")}
            for k, v in reader.manifest["documents"].items()
        },
        "metrics": diag["metrics"],
        "factorIncrement": {
            k: v
            for k, v in diag["factorIncrement"].items()
            if k
            not in {
                "baselineRows",
                "baselineModelFits",
                "baselineValidation",
                "dailyLosses",
            }
        },
        "selectedModel": diag["selectedModel"],
        "selectionAudit": diag["selectionAudit"],
        "factorDiagnosticFeatures": len(result["forecasts"]["factorResearch"]["diagnostics"]["features"]),
        "factorJointDistributions": len(result["forecasts"]["factorResearch"]["diagnostics"]["dependence"]["jointDistributions"]),
        "portableFunctionCount": len(result["forecasts"]["factorResearch"]["modelFunctions"]),
        "invalidTrials": [t for t in diag["finalTrials"] if t["status"] != "valid"],
        "predictionCount": result["forecasts"]["totalRows"],
        "sourceFiles": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(
                [
                    *(ROOT / "engine" / "atlas_quant").rglob("*.py"),
                    *(ROOT / "scripts").glob("benchmark-filter*.py"),
                    ROOT / "engine" / "requirements.lock.txt",
                ]
            )
        },
    }
    save(output / "summary.json", summary)
    save(output / "progress.json", {"phase": "completed"})
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    from atlas_quant.capacity.profiles import FULL_FILTER_PROFILE_ID, AUTO_FILTER_PROFILES, FULL_FILTER_PROFILES
    parser.add_argument("--profile", choices=sorted(FULL_FILTER_PROFILES),
        default=os.environ.get("ATLAS_CAPACITY_PROFILE", FULL_FILTER_PROFILE_ID))
    parser.add_argument(
        "--family",
        choices=["mean_reversion", "trend"],
        default=os.environ.get("ATLAS_CAPACITY_FAMILY", "mean_reversion"),
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.environ["ATLAS_CAPACITY_FAMILY"] = args.family
    os.environ["ATLAS_CAPACITY_PROFILE"] = args.profile
    output = args.output.absolute()

    from atlas_quant.capacity import FeatureGraph
    from atlas_quant.capacity.profiles import FULL_FILTER_PROFILE_ID
    PROFILE_ID = os.environ.get("ATLAS_CAPACITY_PROFILE", FULL_FILTER_PROFILE_ID)
    import pandas as pd

    strategy = benchmark_strategy()
    from atlas_quant.statistical_quant.schema import validate
    from atlas_quant.statistical_quant.models import candidates
    validate(strategy, capacity_profile=PROFILE_ID)
    candidate_set = candidates(strategy["model"]["estimator"])
    nodes = len(FeatureGraph.compile(strategy["factors"]).nodes)
    dates = len(
        pd.bdate_range(strategy["universe"]["start"], strategy["universe"]["end"])
    )
    estimated_cache = dates * 1000 * 8 * (10 + nodes + 16 + 16 + 9) + 32 * 1024 * 1024
    admission = {
        "profile": PROFILE_ID,
        "experimentalCandidateNotProductionAdmission": PROFILE_ID in AUTO_FILTER_PROFILES,
        "candidateSet": candidate_set,
        "candidateConfigurations": len(candidate_set),
        "pooledAllSymbols": True,
        "independentFactorFreeBaselineRequired": True,
        "strategy": strategy,
        "calendarSessions": dates,
        "inputRows": dates * 1000,
        "featureNodes": nodes,
        "estimatedCacheBytes": estimated_cache,
        "reservedTotalOutputBytes": 400 * 1024 * 1024,
        "requiredFreeBytes": 900 * 1024 * 1024,
        "supervisor": {
            "wallSeconds": 900,
            "singleFitSeconds": 300,
            "rssBytes": 3 * 1024**3,
            "outputBytes": 400 * 1024 * 1024,
            "systemFreeReserveBytes": 500 * 1024 * 1024,
        },
    }
    if args.plan_only:
        print(json.dumps(admission, ensure_ascii=False))
        return 0
    if args.child:
        try:
            return child(output)
        except Exception as exc:
            save(
                output / "summary.json",
                {
                    "status": "failed",
                    "synthetic": True,
                    "providerCalls": 0,
                    "failureCode": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                },
            )
            raise
    if output.exists():
        raise SystemExit(
            "Output must be a new directory; existing evidence is preserved"
        )
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < admission["requiredFreeBytes"]:
        raise SystemExit(
            "Insufficient disk: require 400 MiB output plus 500 MiB system reserve"
        )
    output.mkdir(mode=0o700, parents=True)
    save(output / "admission.json", admission)
    started = time.monotonic()
    reason = None
    peak = 0
    with (output / "worker.log").open("xb") as log:
        os.chmod(output / "worker.log", 0o600)
        process = subprocess.Popen(
            [sys.executable, __file__, "--output", str(output), "--child"],
            stdout=log,
            stderr=log,
        )
        while process.poll() is None:
            now = time.monotonic()
            stat = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(process.pid)],
                text=True,
                capture_output=True,
            )
            rss = int(stat.stdout.strip() or "0") * 1024
            peak = max(peak, rss)
            progress = {}
            try:
                progress = json.loads((output / "progress.json").read_text())
            except (OSError, ValueError):
                pass
            size = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
            if now - started > 900:
                reason = "CAPACITY_TIMEOUT"
            elif rss > 3 * 1024**3:
                reason = "CAPACITY_MEMORY"
            elif (
                progress.get("phase") == "fit_started"
                and now - progress["startedMonotonic"] > 300
            ):
                reason = "CAPACITY_FIT_TIMEOUT"
            elif size > 400 * 1024 * 1024:
                reason = "CAPACITY_DISK"
            elif shutil.disk_usage(output).free < 500 * 1024 * 1024:
                reason = "CAPACITY_DISK_RESERVE"
            if reason:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                break
            time.sleep(0.5)
    if reason is None and process.returncode:
        try:
            reason = json.loads((output / "summary.json").read_text()).get(
                "failureCode"
            )
        except (OSError, ValueError):
            reason = "CHILD_PROCESS_FAILED"
    result = {
        "exitCode": process.returncode,
        "failureCode": reason,
        "wallSeconds": time.monotonic() - started,
        "supervisedPeakRssBytes": peak,
        "complete": process.returncode == 0 and (output / "summary.json").exists(),
    }
    save(output / "supervisor.json", result)
    print(json.dumps(result))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
