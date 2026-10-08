"""Provider-free graph composition preserves independently authorized raw inputs."""
from ..dataset_runner.publication import source_inputs
from ..dataset_runner.protocol import LIMITS,require
from ..research_dataset.codec import decode
from ..research_dataset.graph_v3.dataset import compose_graph_dataset_components
from .protocol import PROFILE,JOB_KIND


def compute_publication(job, inputs, write_part):
    view, sources, registry, plan, expected = source_inputs(job, inputs, profile=PROFILE, job_kind=JOB_KIND)
    publication = compose_graph_dataset_components(view, sources, registry, write_part,
        market_calendar_ref=plan["marketCalendarRef"])
    actual = {a["packRoot"]: a for a in publication.result.financial_summaries}
    require(set(actual) == {v["packRoot"] for v in expected.values()} and all(
        actual[v["packRoot"]]["calendarRoot"] == v["calendarRoot"]
        and actual[v["packRoot"]]["preparedRoot"] == v["preparedRoot"] for v in expected.values()),
        "DATASET_SOURCE_IDENTITY")
    return decode(publication.manifest_bytes, LIMITS["manifestBytes"])
