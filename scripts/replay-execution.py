#!/usr/bin/env python3
"""Replay execution from frozen forecasts and data; never contact a provider."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from atlas_quant.runner import execute_bounded


def read_json(path, limit):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Input missing or oversized")
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forecast", type=Path, help="Original report, or its complete forecasts artifact")
    parser.add_argument("snapshot", type=Path, help="Private frozen input from local-run --snapshot-output")
    parser.add_argument("--overrides", type=Path, help="JSON containing only execution, portfolio and/or costs")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    try:
        report = read_json(args.forecast, 24 * 1024 * 1024)
        body = report.get("result", report)
        artifact = body.get("forecasts", body.get("artifact", body))
        strategy = copy.deepcopy(artifact["sourceStrategy"])
        # A forecast-only source is the normal input to a separate execution.
        strategy["execution"] = {**strategy.get("execution", {}), "enabled": True}
        if args.overrides:
            overrides = read_json(args.overrides, 65536)
            if not isinstance(overrides, dict) or set(overrides) - {"execution", "portfolio", "costs"}:
                raise ValueError("Execution-only override keys required")
            for key, values in overrides.items():
                if not isinstance(values, dict):
                    raise ValueError("Each override must be an object")
                strategy[key] = {**strategy.get(key, {}), **values}
        snapshot = read_json(args.snapshot, 24 * 1024 * 1024)
        job = {"id": "local-replay", "jobKind": "execution", "strategy": strategy,
               "forecastArtifactId": artifact["artifactId"],
               "replay": {"artifact": artifact, "snapshot": snapshot}}
        answer = execute_bounded(job, timeout=max(30, min(900, args.timeout)))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        answer = {"error": {"code": "REPLAY_INPUT", "message": "预测、冻结行情或执行覆盖配置无效。"}}
    content = json.dumps(answer, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        if args.output.resolve() in {args.forecast.resolve(), args.snapshot.resolve()}:
            raise SystemExit("Output must not overwrite the original forecast or frozen dataset")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    else:
        print(content, end="")
    return 1 if "error" in answer else 0


if __name__ == "__main__":
    raise SystemExit(main())
