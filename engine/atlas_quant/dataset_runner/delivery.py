"""Resume exact output bytes; trusted same-lease terminal readback precedes deletion."""

from contextlib import contextmanager

from ..financial_runner.spool import TERMINAL
from ..runner import RunnerError
from .compute import safe_error
from .protocol import digest, encode, identifier, keys, require, sha


TERMINAL_REJECTIONS = frozenset({400, 403, 404, 409, 413, 422})


class PublicationRejected(RunnerError):
    """Explicit rejection of begin/part/complete, not a heartbeat or a GET."""


def terminal_rejection(error):
    """Only an explicit write rejection can quarantine a publication."""
    return isinstance(error, PublicationRejected) and error.http_status in TERMINAL_REJECTIONS


def uncertain():
    return RunnerError(
        "DATASET_PUBLICATION_UNCERTAIN",
        "交付回执尚未核实；已保留原任务及完整来源、产物，等待同身份读回。",
    )


def remote_call(operation, *args, readback=False, **kwargs):
    """A malformed successful response is not a rejection of the stored bytes.

    Keep write HTTP statuses for the service's explicit rejection policy. A GET
    error, including 404/409, cannot prove that a prior write was rejected.
    Decoder errors stay local to this delivery boundary, not the source parser.
    """
    try:
        return operation(*args, **kwargs)
    except RunnerError as error:
        if error.code == "DATASET_HTTP" and not readback and error.http_status in TERMINAL_REJECTIONS:
            raise PublicationRejected(
                "DATASET_HTTP", "发布被明确拒绝。", http_status=error.http_status
            ) from None
        if error.code == "DATASET_NETWORK" or (
            error.code == "DATASET_HTTP" and not readback
        ):
            raise
        raise uncertain() from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise uncertain() from None


@contextmanager
def receipt_validation():
    try:
        yield
    except (RunnerError, ValueError, TypeError, KeyError, AttributeError):
        raise uncertain() from None


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
    response = remote_call(
        client.get, "jobs/" + job["id"] + "/status", job["leaseToken"], readback=True
    )
    with receipt_validation():
        require(isinstance(response, dict), "DATASET_STATUS_IDENTITY")
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
        require(
            isinstance(old, dict) and old.get("datasetRoot") == root,
            "DATASET_SPOOL_INTEGRITY",
        )
        response = remote_call(
            client.get, route + "?datasetRoot=" + root, job["leaseToken"],
            deadline=monitor.deadline, readback=True,
        )
    else:
        # Write intent precedes the first network side effect. Even a missing or
        # undecodable 200 ACK must resume via GET, never by guessing and re-POSTing.
        state = spool.save(dict(state, publication={"datasetRoot": root}))
        response = remote_call(
            client.post, route,
            {
                "leaseToken": job["leaseToken"],
                "datasetRoot": root,
                "manifestText": raw.decode(),
            },
            deadline=monitor.deadline,
        )
    with receipt_validation():
        require(isinstance(response, dict), "DATASET_PUBLICATION_IDENTITY")
        identity = {
            "publicationId": identifier(response.get("publicationId")),
            "datasetRoot": digest(response.get("datasetRoot")),
        }
        require(
            identity["datasetRoot"] == root
            and (not old or "publicationId" not in old or old == identity),
            "DATASET_PUBLICATION_IDENTITY",
        )
        require(response.get("status") in {"staging", "committed"}, "DATASET_PART_ACK")
        parts = missing_parts(response, manifest)
        require(response["status"] != "committed" or not parts, "DATASET_PART_ACK")
    state = spool.save(dict(state, publication=identity))
    for name, part in parts:
        monitor.check()
        remote_call(
            client.upload,
            job,
            identity["publicationId"],
            root,
            name,
            part,
            publication.read_chunk(name, part),
            deadline=monitor.deadline,
        )
    monitor.check()
    response = remote_call(
        client.post, "jobs/" + job["id"] + "/complete",
        {"leaseToken": job["leaseToken"], **identity},
        deadline=monitor.deadline,
    )
    with receipt_validation():
        require(
            isinstance(response, dict)
            and response.get("ok") is True and response.get("status") == "completed",
            "DATASET_TERMINAL_ACK",
        )
        completed_ref = dataset_ref(response.get("datasetRef"), root, dataset_version=dataset_version)
    status, readback_ref = terminal_status(client, state, dataset_version=dataset_version)
    require(
        status == "completed" and readback_ref == completed_ref, "DATASET_TERMINAL_ACK"
    )
    spool.acknowledge(state, status)
