#!/usr/bin/env python3
"""Replay execution from frozen forecasts and data; never contact a provider."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from atlas_quant.runner import CompletionSpool, execute_bounded, _validate_result


def read_json(path, limit):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Input missing or oversized")
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forecast", type=Path, nargs="?", help="Original report, or its complete forecasts artifact")
    parser.add_argument("snapshot", type=Path, nargs="?", help="Private frozen input from local-run --snapshot-output")
    parser.add_argument("--source-bundle", type=Path, help="Private manifest/chunks export; replaces both positional files")
    parser.add_argument("--bundle-output", type=Path, help="New private directory for execution manifest/chunks")
    parser.add_argument("--overrides", type=Path, help="JSON containing only execution, portfolio and/or costs")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    try:
        if bool(args.source_bundle) == bool(args.forecast or args.snapshot):
            raise ValueError("Choose a source bundle or both legacy inputs")
        inputs = {p.resolve() for p in (args.forecast,args.snapshot) if p}
        if args.output and (args.output.resolve() in inputs or (args.source_bundle and args.output.resolve().is_relative_to(args.source_bundle.resolve()))):
            raise ValueError("Output must not overwrite the original forecast or frozen dataset")
        if args.bundle_output and (args.bundle_output.exists() or (args.output and args.output.resolve().is_relative_to(args.bundle_output.resolve()))):
            raise ValueError("Bundle export requires a new directory and a separate receipt path")
        source = None
        if args.source_bundle:
            from atlas_quant.bundle import directory_reader, document_skeleton
            source = directory_reader(args.source_bundle)
            source.verify_integrity()
            if "snapshot" not in source.manifest["documents"]:
                raise ValueError("Execution requires the original forecast bundle with its frozen snapshot")
            metadata = document_skeleton(source.manifest,"forecast")
            strategy = copy.deepcopy(metadata["sourceStrategy"])
            artifact_id = source.manifest["forecastArtifactId"]
        else:
            report = read_json(args.forecast,24*1024*1024)
            body = report.get("result",report)
            artifact = body.get("forecasts",body.get("artifact",body))
            strategy = copy.deepcopy(artifact["sourceStrategy"])
            artifact_id = artifact["artifactId"]
        strategy["execution"] = {**strategy.get("execution",{}),"enabled":True}
        if args.overrides:
            overrides = read_json(args.overrides,65536)
            if not isinstance(overrides,dict) or set(overrides)-{"execution","portfolio","costs"}:
                raise ValueError("Execution-only override keys required")
            for key, values in overrides.items():
                if not isinstance(values,dict):
                    raise ValueError("Each override must be an object")
                strategy[key] = {**strategy.get(key,{}),**values}
        job = {"id":"local-replay","jobKind":"execution","strategy":strategy,"forecastArtifactId":artifact_id}
        if source is None:
            job["replay"] = {"artifact":artifact,"snapshot":read_json(args.snapshot,24*1024*1024)}
        if args.bundle_output or source is not None:
            from atlas_quant.bundle import export_bundle
            from atlas_quant.bundle_spool import BundleSpool
            with tempfile.TemporaryDirectory(prefix="atlas-quant-replay-") as temporary:
                spool = CompletionSpool({"api_base":"https://local-bundle.invalid","runner_secret":os.urandom(32).hex(),
                                         "delivery_dir":str(Path(temporary)/"delivery")})
                identity = {"id":"local-replay","leaseToken":os.urandom(16).hex()}
                context = BundleSpool.context_for(spool,identity)
                store = BundleSpool(context)
                if source is not None:
                    for info in source.manifest["collections"]:
                        for descriptor in info["chunks"]:
                            store.write_chunk(info["id"],descriptor["ordinal"],source.read_chunk(info["id"],descriptor["ordinal"]))
                    store.import_manifest(source.manifest_raw,source.bundle_id)
                    job["replayBundle"] = {"bundleId":source.bundle_id,"_bundleKey":store.key}
                answer = execute_bounded(job,timeout=max(30,min(900,args.timeout)),bundle_context=context)
                if "error" not in answer:
                    reader = store.reader(answer["bundleId"])
                    reader.verify_integrity()
                    if args.bundle_output:
                        export_bundle(reader,args.bundle_output)
                        answer = {"bundle":{"bundleId":reader.bundle_id,"forecastArtifactId":reader.manifest["forecastArtifactId"],
                                  "manifest":str(args.bundle_output/"manifest.json"),"complete":True}}
                    else:
                        answer = {"result":_validate_result(reader.document("report"))}
        else:
            answer = execute_bounded(job,timeout=max(30,min(900,args.timeout)))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        answer = {"error":{"code":"REPLAY_INPUT","message":"预测、冻结行情或执行覆盖配置无效；超过单包预算时请使用 --bundle-output。"}}
    content = json.dumps(answer,ensure_ascii=False,indent=2,allow_nan=False)+"\n"
    if args.output:
        # Validate again outside the input-error path before writing anything.
        forbidden = {p.resolve() for p in (args.forecast,args.snapshot) if p}
        if args.output.resolve() in forbidden or (args.source_bundle and args.output.resolve().is_relative_to(args.source_bundle.resolve())) or (args.bundle_output and args.output.resolve().is_relative_to(args.bundle_output.resolve())):
            raise SystemExit("Output must not overwrite source or exported bundle files")
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(content)
    else:
        print(content,end="")
    return 1 if "error" in answer else 0


if __name__ == "__main__":
    raise SystemExit(main())
