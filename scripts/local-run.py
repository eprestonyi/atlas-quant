#!/usr/bin/env python3
"""Reproduce research with environment credentials or an external private config."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from atlas_quant.runner import execute_bounded, load_config, prepare_job


def main():
    p = argparse.ArgumentParser()
    p.add_argument("strategy", type=Path)
    p.add_argument("--source", choices=("demo", "upload", "tushare"), required=True)
    p.add_argument("--dataset", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--config", type=Path, help="Optional external private 0600 runner config for authorized provider/PCD reads")
    args = p.parse_args()
    try:
        if args.strategy.stat().st_size > 256000:
            raise ValueError("strategy too large")
        strategy = json.loads(args.strategy.read_text())
        job = {"id": "local", "strategy": strategy, "dataSource": args.source}
        if args.source == "upload":
            if args.dataset is None or args.dataset.stat().st_size > 24*1024*1024:
                raise ValueError("missing or oversized dataset")
            job["dataset"] = json.loads(args.dataset.read_text())
        cfg = load_config(args.config) if args.config else {}
        job = prepare_job(job, cfg)
        answer = execute_bounded(job, timeout=max(30, min(900, args.timeout)), token=os.environ.get("TUSHARE_TOKEN"),
                                 cache_dir=cfg.get("cache_dir"), allowed_proxy_hosts=cfg.get("allowed_proxy_hosts"))
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
