"""Recompute frozen market input, then fit one pooled F in the same child."""

import copy
import time
import pandas as pd
from threadpoolctl import threadpool_limits
from ..bundle_spool import BundleSpool
from ..capacity.core import run_capacity_research
from ..capacity.panel_store import private_dir
from ..compute_slot import compute_slot
from ..market_acquisition.protocol import require, encode
from ..provider import canonical_hash, _records
from ..statistical_quant.schema import validate
from .spool import MarketResearchSpool


def compute(job, context, *, slot_path, deadline):
    require(
        job.get("jobKind") == "forecast"
        and job.get("dataSource") == "ready_market"
        and not any(
            k in job for k in ("providerAccess", "pcdAccess", "replay", "replayBundle")
        ),
        "MARKET_SOURCE_IDENTITY",
        "Whole-market forecasts require immutable provider-free input",
    )
    require(
        slot_path is not None,
        "CONFIG_COMPUTE_SLOT",
        "Whole-market research requires a shared private compute slot",
    )
    store = MarketResearchSpool(context)
    reader = store.inputs(job)
    profile = job["admissionProfile"]
    strategy = validate(job["strategy"], capacity_profile=profile)
    require(
        all(
            strategy["universe"][k] == reader.manifest["scope"][k]
            for k in ("symbols", "start", "end")
        ),
        "MARKET_SOURCE_SCOPE",
        "Research must use every resolved symbol and exact dates",
    )
    with threadpool_limits(limits=2), compute_slot(slot_path, deadline=deadline):
        require(
            time.monotonic() < deadline, "MARKET_DEADLINE", "Compute budget exhausted"
        )
        frame, provenance = reader.research_input()
        provenance["marketSource"] = copy.deepcopy(job["sourceEvidence"])
        provenance["dataFingerprint"] = canonical_hash(_records(frame))
        plans = []
        cache = private_dir(store.root / "cache")
        try:
            result = run_capacity_research(
                strategy,
                frame,
                provenance,
                profile_id=profile,
                cache_dir=cache,
                plan_sink=plans.append,
                progress=lambda event: store.write("progress", encode(event)),
            )
            require(
                len(plans) == 1, "MARKET_COVERAGE", "Independent pre-fit plan missing"
            )
            # The capacity library's old standalone marker does not describe
            # this explicitly admitted queue route. Keep deployment state out
            # of the numeric report; record the actual immutable entry instead.
            result["capacity"].pop("hostedApiEnabled", None)
            result["capacity"]["entryPoint"] = "ready_market"
            result["capacity"]["sourceAdmission"] = copy.deepcopy(job["sourceEvidence"])
            require(
                time.monotonic() < deadline,
                "MARKET_DEADLINE",
                "Compute budget exhausted",
            )
            snapshot = {
                "schemaVersion": 1,
                "rows": frame.astype(object)
                .where(pd.notna(frame), None)
                .to_dict(orient="records"),
                "provenance": provenance,
                "sourceDataFingerprint": provenance["dataFingerprint"],
                "dataFingerprint": result["forecasts"]["dataFingerprint"],
                "fingerprintVersion": "research_input_v1",
            }
            return BundleSpool(context).build(result, snapshot, plans[0])
        finally:
            # Only our private per-lease cache; immutable source bytes remain until ACK.
            import shutil

            shutil.rmtree(cache)
