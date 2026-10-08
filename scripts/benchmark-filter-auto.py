#!/usr/bin/env python3
"""Isolated 1000-stock auto candidate; identical hard supervisor and no sampling."""
import runpy
import sys
from pathlib import Path

# This selects a named experimental profile through the ordinary admission path.
# The existing production profile, candidate list and guards are never patched.
sys.argv += ["--profile", "pooled_asset_1000_auto_candidate_v1", "--family", "mean_reversion"]
runpy.run_path(str(Path(__file__).with_name("benchmark-filter-universe.py")), run_name="__main__")
