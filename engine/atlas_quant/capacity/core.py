"""Explicit experimental profile; same pooled fitter, never fifty-stock submodels."""

import copy
import os
from pathlib import Path
import resource
import shutil
import sys
import time
import uuid
import numpy as np
import pandas as pd
import sklearn

from ..statistical_quant.schema import validate, digest, fail, prediction_config
from ..statistical_quant.core import _research_from_samples
from ..statistical_quant.validation import forecast_origins
from ..statistical_quant.models import candidates
from .profiles import get_profile
from .panel_store import PanelStore, private_dir, write_json, file_hash
from .features import FeatureGraph
from .asset_samples import build_asset_samples


def disk_bytes(root):
    return sum(
        p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()
    )


def peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def implementation_root(engine_root):
    """Bind executable sources and packaged semantic contracts, never caches."""
    paths = sorted([*engine_root.rglob("*.py"), *engine_root.rglob("*.json")])
    return digest(
        {str(path.relative_to(engine_root)): file_hash(path) for path in paths}
    )


class FitRuntime:
    def __init__(self, check, maximum, progress):
        self.check, self.maximum, self.progress = check, maximum, progress
        self.events = []
        self.active = None

    def before_fit(self, spec, cutoff, rows):
        self.check()
        if len(self.events) >= self.maximum:
            fail("CAPACITY_FITS", "Predeclared pooled fit attempt budget exhausted")
        self.active = {
            "ordinal": len(self.events),
            "specId": spec["id"],
            "cutoff": cutoff,
            "trainRows": rows,
            "startedMonotonic": time.monotonic(),
        }
        if self.progress:
            self.progress({"phase": "fit_started", **self.active})

    def after_fit(self):
        event = {
            **self.active,
            "elapsedSeconds": time.monotonic() - self.active["startedMonotonic"],
        }
        self.events.append(event)
        if self.progress:
            self.progress({"phase": "fit_finished", **event})
        if event["elapsedSeconds"] > 300:
            fail(
                "CAPACITY_FIT_TIMEOUT",
                "One fit exceeded the explicit 300-second budget",
            )
        self.active = None
        self.check()


def run_capacity_research(
    strategy, data, provenance, *, profile_id, cache_dir, plan_sink=None, progress=None
):
    """In-process numeric API. Production queue/50-stock routes do not call this.

    Admission/phase boundaries enforce cache/RSS limits. A supervising isolated
    process must impose a hard wall/RSS deadline while sklearn itself is running.
    """
    profile = get_profile(profile_id)
    s = validate(strategy, capacity_profile=profile_id)
    if not isinstance(provenance, dict):
        fail("INVALID_PROVENANCE", "Explicit provenance required")
    p = copy.deepcopy(provenance)
    start = time.monotonic()
    root = private_dir(cache_dir)
    graph = FeatureGraph.compile(s["factors"])
    calendar = p.get("tradingDates")
    t = len(calendar) if isinstance(calendar, list) else data.trade_date.nunique()
    n = len(s["universe"]["symbols"])
    f = len(s["factors"])
    # Conservative dense-array bytes, staging headroom and Python metadata are
    # separate from the on-disk report/snapshot transport budget.
    estimate = int(
        t * n * 8 * (len(data.columns) + len(graph.nodes) + f + f + 9)
        + 32 * 1024 * 1024
    )
    if estimate > profile.max_cache_bytes:
        fail("CAPACITY_DISK", "Declared feature/sample cache exceeds profile budget")
    if shutil.disk_usage(root).free < estimate + 500 * 1024 * 1024:
        fail(
            "CAPACITY_DISK",
            "Insufficient free space for estimated cache plus 500 MiB system reserve",
        )

    def check():
        if disk_bytes(root) > profile.max_cache_bytes:
            fail("CAPACITY_DISK", "Complete cache exceeds explicit profile budget")
        if peak_rss_bytes() > profile.max_rss_bytes:
            fail("CAPACITY_MEMORY", "Process peak RSS exceeded profile limit")
        if time.monotonic() - start > profile.max_wall_seconds:
            fail("CAPACITY_TIMEOUT", "Profile elapsed budget exceeded")

    store, audit = PanelStore.prepare(
        data, s, p, root / "panels", profile_id=profile_id
    )
    check()
    features = graph.evaluate(
        store, root / "features", max_bytes=profile.max_cache_bytes
    )
    check()
    samples = build_asset_samples(
        store,
        features,
        s,
        root / ("samples_" + uuid.uuid4().hex),
        max_samples=profile.max_samples,
    )
    check()
    holdout, indices = forecast_origins(samples, s, max_forecasts=profile.max_forecasts)
    origin_dates = sorted(samples.meta.loc[indices, "date"].unique())
    last = -100000
    refits = 0
    for date in origin_dates:
        index = store.dates.index(date)
        if index - last >= s["model"]["refitDays"]:
            refits += 1
            last = index
    baseline = any(name.startswith("factor:") for name in samples.X)
    engine_root = Path(__file__).resolve().parents[1]
    candidate_set = candidates(s["model"]["estimator"])
    candidate_count = len(candidate_set)
    inner, outer = s["validation"]["innerFolds"], s["validation"]["outerFolds"]
    nested_fits = (outer+1)*inner*candidate_count+outer
    plan = {
        "profile": profile.to_dict(),
        "modelScope": "pooled_all_symbols",
        "targetKind": "asset_price",
        "implementationRoot": implementation_root(engine_root),
        "numericalRuntime": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikitLearn": sklearn.__version__,
        },
        "predictionConfigHash": digest(prediction_config(s)),
        "panelRoot": store.identity,
        "featureRoot": features.identity,
        "dataFingerprint": audit["dataSha256"],
        "calendarHash": audit["calendarSha256"],
        "symbols": store.symbols,
        "inputRows": audit["observedRows"],
        "completeGridRows": audit["expectedRows"],
        "sampleRows": len(samples.meta),
        "forecastRows": len(indices),
        "baselineRequired": baseline,
        "holdoutStart": holdout,
        "candidateConfigurations": candidate_count,
        "candidateSet": candidate_set,
        "candidateSetHash": digest(candidate_set),
        "innerFolds": inner,
        "outerFolds": outer,
        "scheduledTerminalFits": refits,
        "scheduledFits": (2 if baseline else 1) * (nested_fits + refits),
        "fitAttemptsUpperBound": (2 if baseline else 1) * (nested_fits + len(origin_dates)),
        "terminalFailureRetryPolicy": "existing_model_unavailable_retries_each_observation",
        "featureNodes": len(graph.nodes),
        "estimatedCacheBytes": estimate,
        "trainingWeighting": "equal_weight_sample_rows",
        "scoringWeighting": "equal_weight_daily_average",
        "hardInFlightLimitsRequireIsolatedSupervisor": True,
    }
    plan["resourcePlanId"] = digest(plan)
    plan_path = root / ("plan_" + plan["resourcePlanId"] + ".json")
    if not plan_path.exists():
        write_json(plan_path, plan)
    # Forecast-only admission makes panel unnecessary for the existing envelope.
    runtime = FitRuntime(check, plan["fitAttemptsUpperBound"], progress)
    if progress:
        progress(
            {
                "phase": "plan_frozen",
                "resourcePlanId": plan["resourcePlanId"],
                "scheduledFits": plan["scheduledFits"],
            }
        )
    result = _research_from_samples(
        s,
        None,
        store.dates,
        audit,
        p,
        samples,
        plan_sink=plan_sink,
        max_forecasts=profile.max_forecasts,
        runtime=runtime,
    )
    check()
    result["capacity"] = {
        **plan,
        "elapsedSeconds": time.monotonic() - start,
        "peakRssBytes": peak_rss_bytes(),
        "actualCacheBytes": disk_bytes(root),
        "hostedApiEnabled": False,
        "actualFitAttempts": len(runtime.events),
        "fitTimings": runtime.events,
    }
    return result
