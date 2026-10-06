#!/usr/bin/env python3
"""Reproduce a research run locally. Tushare credentials are environment-only."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from atlas_quant.runner import execute_bounded


def main():
    p = argparse.ArgumentParser()
    p.add_argument("strategy", type=Path)
    p.add_argument("--source", choices=("demo", "upload", "tushare"), required=True)
    p.add_argument("--dataset", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--timeout", type=int, default=600)
    args = p.parse_args()
    try:
        if args.strategy.stat().st_size > 256000:
            raise ValueError("strategy too large")
        strategy = json.loads(args.strategy.read_text())
        job = {"id": "local", "strategy": strategy, "dataSource": args.source}
        if args.source == "upload":
            if args.dataset is None or args.dataset.stat().st_size > 20*1024*1024:
                raise ValueError("missing or oversized dataset")
            job["dataset"] = json.loads(args.dataset.read_text())
        answer = execute_bounded(job, timeout=max(30, min(900, args.timeout)), token=os.environ.get("TUSHARE_TOKEN"))
    except (OSError, ValueError):
        answer = {"error": {"code": "LOCAL_INPUT", "message": "策略或上传文件无效。"}}
    content = json.dumps(answer, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    else:
        print(content, end="")
    return 1 if "error" in answer else 0


if __name__ == "__main__":
    raise SystemExit(main())
