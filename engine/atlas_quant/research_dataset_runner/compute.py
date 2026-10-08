"""Recompose authenticated source bytes and fit F in this very process."""

import contextlib
import time

from ..compute_slot import compute_slot
from ..dataset_runner.protocol import require
from ..engine import run_research
from ..financial_bundle_spool import FinancialBundleSpool
from ..research_dataset import restore_dataset_for_research, freeze_financial_input
from ..research_dataset.codec import encode
from .spool import ResearchDatasetSpool


def compute(job, context, *, slot_path, deadline):
    require(
        job.get("jobKind") == "forecast" and job.get("dataSource") == "ready_dataset"
    )
    require(
        not any(
            key in job
            for key in ("providerAccess", "pcdAccess", "replay", "replayBundle")
        ),
        "DATASET_INPUT_IDENTITY",
    )
    inputs = ResearchDatasetSpool(context)
    reader, registry = inputs.inputs(job)
    try:
        from threadpoolctl import threadpool_limits

        scope = threadpool_limits(limits=2)
    except ImportError:
        scope = contextlib.nullcontext()
    with scope, compute_slot(slot_path, deadline=deadline):
        require(time.monotonic() < deadline, "DATASET_DEADLINE")
        # Recomposition creates process-local admission, never a pickled trusted object.
        joined = restore_dataset_for_research(job["strategy"], reader, registry, research_profile=job["admissionProfile"])
        snapshot = encode(
            freeze_financial_input(
                job["strategy"],
                joined,
                job["datasetRef"],
                manifest_bytes=reader.manifest_bytes,
                research_profile=job["admissionProfile"],
            )
        )
        plans = []
        result = run_research(
            job["strategy"],
            joined.data,
            joined.provenance,
            forecast_plan_sink=plans.append,
        )
        require(len(plans) == 1, "DATASET_COVERAGE")
        require(time.monotonic() < deadline, "DATASET_DEADLINE")
        return FinancialBundleSpool(context).build(
            result, snapshot, plans[0], job["sourceEvidence"]
        )
