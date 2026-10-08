"""Graph source reconstruction, full F and validated output inside one child."""
import time

from ..compute_slot import compute_slot
from ..dataset_runner.protocol import require
from ..financial_graph_bundle_spool import FinancialGraphBundleSpool
from ..research_dataset.codec import encode
from ..research_dataset.graph_v3.research import run_graph_research
from .client import GraphResearchClient
from .spool import GraphResearchSpool


def compute(job, context, *, slot_path, deadline):
    require(job.get("jobKind") == "forecast" and job.get("dataSource") == "ready_dataset"
            and job.get("dataset") is None, "DATASET_INPUT_IDENTITY")
    # Revalidate even when bypassing the download helper or recovering a child.
    GraphResearchClient.source_contract(job)
    require(time.monotonic() < deadline, "CAPACITY_TIMEOUT")
    require(isinstance(slot_path, str) and bool(slot_path), "COMPUTE_SLOT_CONFIG")
    inputs = GraphResearchSpool(context)
    source, registry = inputs.inputs(job)
    output = FinancialGraphBundleSpool(context)
    completed = []

    def finalize(report, snapshot, coverage, result, check):
        require(time.monotonic() < deadline, "CAPACITY_TIMEOUT")
        completed.append(output.build(report, encode(snapshot), coverage, job["sourceEvidence"],
            source_result=result, source_scope=source.manifest["scope"], precommit_check=check))

    with compute_slot(slot_path, deadline=deadline):
        require(time.monotonic() < deadline, "CAPACITY_TIMEOUT")
        run_graph_research(job["strategy"], source, registry, job["datasetRef"],
            research_profile=job["admissionProfile"], work_dir=inputs.root, finalize=finalize, deadline=deadline,
            progress=lambda event: inputs.write("progress", encode(event)))
        require(len(completed) == 1, "DATASET_COVERAGE")
        require(time.monotonic() < deadline, "CAPACITY_TIMEOUT")
    return completed[0]
