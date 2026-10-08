"""Resume exact output bytes; trusted same-lease terminal readback precedes deletion."""

from ..financial_runner.spool import TERMINAL
from ..runner import RunnerError
from .compute import safe_error
from .protocol import digest, encode, identifier, keys, require, sha


def dataset_ref(value, expected_root=None, *, dataset_version=2):
    keys(value, {"datasetId", "datasetRoot", "format", "version"})
    identifier(value["datasetId"])
    digest(value["datasetRoot"])
    require(
        value["format"] == "atlas.quant.research_dataset"
        and type(value["version"]) is int
        and value["version"] == dataset_version
        and (expected_root is None or value["datasetRoot"] == expected_root),
        "DATASET_COMPLETION_IDENTITY",
    )
    return value


def terminal_status(client, state, *, dataset_version=2):
    job = state["job"]
    response = client.get("jobs/" + job["id"] + "/status", job["leaseToken"])
    row = response.get("job")
    require(
        isinstance(row, dict)
        and row.get("id") == job["id"]
        and row.get("status")
        in {"running", "cancel_requested", "completed", "failed", "cancelled"},
        "DATASET_STATUS_IDENTITY",
    )
    ref = response.get("datasetRef")
    if row["status"] == "completed":
        expected = state.get("publication", {}).get("datasetRoot")
        dataset_ref(ref, expected, dataset_version=dataset_version)
    else:
        require(ref is None, "DATASET_STATUS_IDENTITY")
    return row["status"], ref


def settle_failure(client, spool, state, *, dataset_version=2):
    status, _ = terminal_status(client, state, dataset_version=dataset_version)
    if status not in TERMINAL:
        error = state["error"]
        if status == "cancel_requested" and error["code"] != "CANCELLED":
            error = safe_error(RunnerError("CANCELLED", "取消。"))
            state = spool.save(dict(state, error=error))
        job = state["job"]
        client.post(
            "jobs/" + job["id"] + "/fail",
            {"leaseToken": job["leaseToken"], "error": error},
        )
        status, _ = terminal_status(client, state, dataset_version=dataset_version)
    require(status in TERMINAL, "DATASET_TERMINAL_ACK")
    spool.acknowledge(state, status)


def missing_parts(response, manifest):
    allowed = {
        (c["componentId"], p["ordinal"]): p
        for c in manifest["components"]
        for p in c["parts"]
    }
    missing = response.get("missing")
    require(
        isinstance(missing, list) and len(missing) <= len(manifest["components"]),
        "DATASET_PART_ACK",
    )
    seen, names = set(), set()
    for item in missing:
        keys(item, {"componentId", "ordinals"})
        name, ordinals = item["componentId"], item["ordinals"]
        require(
            isinstance(name, str)
            and name not in names
            and isinstance(ordinals, list)
            and 1 <= len(ordinals) <= len(allowed),
            "DATASET_PART_ACK",
        )
        names.add(name)
        for ordinal in ordinals:
            require(
                type(ordinal) is int
                and (name, ordinal) in allowed
                and (name, ordinal) not in seen,
                "DATASET_PART_ACK",
            )
            seen.add((name, ordinal))
    return [(name, allowed[name, ordinal]) for name, ordinal in sorted(seen)]


def deliver(client, spool, state, monitor, *, dataset_version=2):
    job, old = state["job"], state.get("publication")
    publication = spool.publication(job)
    manifest = publication.manifest()
    require(manifest is not None, "DATASET_SPOOL_INTEGRITY")
    raw = encode(manifest)
    root = sha(raw)
    monitor.phase = "writing_evidence"
    monitor.check()
    route = "jobs/" + job["id"] + "/publication"
    if old:
        require(old["datasetRoot"] == root, "DATASET_SPOOL_INTEGRITY")
        response = client.get(
            route + "?datasetRoot=" + root, job["leaseToken"], deadline=monitor.deadline
        )
    else:
        response = client.post(
            route,
            {
                "leaseToken": job["leaseToken"],
                "datasetRoot": root,
                "manifestText": raw.decode(),
            },
            deadline=monitor.deadline,
        )
    identity = {
        "publicationId": identifier(response.get("publicationId")),
        "datasetRoot": digest(response.get("datasetRoot")),
    }
    require(
        identity["datasetRoot"] == root and (old is None or old == identity),
        "DATASET_PUBLICATION_IDENTITY",
    )
    require(response.get("status") in {"staging", "committed"}, "DATASET_PART_ACK")
    parts = missing_parts(response, manifest)
    state = spool.save(dict(state, publication=identity))
    for name, part in parts:
        monitor.check()
        client.upload(
            job,
            identity["publicationId"],
            root,
            name,
            part,
            publication.read_chunk(name, part),
            deadline=monitor.deadline,
        )
    monitor.check()
    response = client.post(
        "jobs/" + job["id"] + "/complete",
        {"leaseToken": job["leaseToken"], **identity},
        deadline=monitor.deadline,
    )
    require(
        response.get("ok") is True and response.get("status") == "completed",
        "DATASET_TERMINAL_ACK",
    )
    completed_ref = dataset_ref(response.get("datasetRef"), root, dataset_version=dataset_version)
    status, readback_ref = terminal_status(client, state, dataset_version=dataset_version)
    require(
        status == "completed" and readback_ref == completed_ref, "DATASET_TERMINAL_ACK"
    )
    spool.acknowledge(state, status)
