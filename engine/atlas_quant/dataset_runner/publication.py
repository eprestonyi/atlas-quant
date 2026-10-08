"""Pure calculation entry for the provider-free isolated child."""

from ..research_dataset import (
    FinancialSource,
    derive_market_snapshot_view,
    compose_snapshot_dataset_components,
)
from ..research_dataset.codec import decode
from .protocol import LIMITS, PROFILE, keys, require


def source_inputs(job, inputs, *, profile=PROFILE, job_kind="dataset_compose"):
    metadata, manifest_raw, snapshot_raw, packages, registry = inputs
    require(
        metadata["job"] == {k: job[k] for k in ("id", "kind", "planId", "deadline")},
        "DATASET_INPUT_IDENTITY",
    )
    plan, source = metadata["plan"], metadata["sources"]["market"]
    require(plan["profile"] == profile and job["kind"] == job_kind)
    selection = plan["marketSource"]
    require(
        source["runId"] == selection["runId"]
        and source["bundleId"] == selection["expectedBundleId"]
        and source["snapshot"]["sha256"] == selection["expectedSnapshotSha256"],
        "DATASET_SOURCE_IDENTITY",
    )
    view = derive_market_snapshot_view(
        snapshot_raw,
        manifest_raw,
        selection["transform"],
        expected_bundle_id=selection["expectedBundleId"],
        expected_snapshot_sha256=selection["expectedSnapshotSha256"],
    )
    require(
        view.receipt["targetScope"] == plan["scope"]
        and view.receipt["sourceScope"] == source["originalScope"],
        "DATASET_SOURCE_SCOPE",
    )
    expected = {v["inputId"]: v for v in plan["financialInputs"]}
    require(
        len(expected)
        == len(plan["financialInputs"])
        == len(metadata["sources"]["financial"]),
        "DATASET_SOURCE_IDENTITY",
    )
    sources = []
    for entry in metadata["sources"]["financial"]:
        fixed = expected.get(entry["inputId"])
        require(
            fixed is not None and entry["preparationId"] == fixed["preparationId"],
            "DATASET_SOURCE_IDENTITY",
        )
        require(
            entry["roots"]
            == {
                k: fixed[k]
                for k in ("inputRoot", "packRoot", "preparedRoot", "calendarRoot")
            },
            "DATASET_SOURCE_IDENTITY",
        )
        raw = packages[entry["sourceId"]]
        value = decode(raw, LIMITS["packageBytes"])
        require(
            value["inputRoot"] == fixed["inputRoot"]
            and value["packRoot"] == fixed["packRoot"],
            "DATASET_SOURCE_IDENTITY",
        )
        sources.append(
            FinancialSource(
                raw,
                fixed["preparedRoot"],
                entry["calendarRef"],
                tuple(entry["proofRefs"]),
            )
        )
    return view, sources, registry, plan, expected


def compute_publication(job, inputs, write_part):
    view, sources, registry, plan, expected = source_inputs(job, inputs)
    publication = compose_snapshot_dataset_components(
        view,
        sources,
        registry,
        write_part,
        market_calendar_ref=plan["marketCalendarRef"],
    )
    # The complete output retains original packages and exact registry authority.
    actual = {
        a.summary["packRoot"]: a.summary for a in publication.result.financial_artifacts
    }
    require(
        all(
            actual[v["packRoot"]]["calendarRoot"] == v["calendarRoot"]
            and actual[v["packRoot"]]["preparedRoot"] == v["preparedRoot"]
            for v in expected.values()
        ),
        "DATASET_SOURCE_IDENTITY",
    )
    return decode(publication.manifest_bytes, LIMITS["manifestBytes"])
