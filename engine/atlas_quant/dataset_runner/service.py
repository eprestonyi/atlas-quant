"""Separate dataset queue: durable claims, one computation, retryable publication."""

import sys
import time
from urllib.parse import urlsplit

from .. import __version__
from ..financial_runner.spool import TERMINAL
from ..runner import RunnerError
from .client import DatasetClient
from .compute import execute_bounded, safe_error
from .delivery import deliver, settle_failure, terminal_status
from .lease import LeaseMonitor, timestamp
from .protocol import CAPABILITY, encode, identifier, require
from .spool import DatasetSpool


def validate_claim(response, state, base, *, namespace="datasets", job_kind="dataset_compose"):
    receipt = response.get("claim") if isinstance(response, dict) else None
    require(
        isinstance(receipt, dict) and receipt.get("requestId") == state["requestId"],
        "DATASET_CLAIM_IDENTITY",
    )
    status, job = receipt.get("status"), response.get("job")
    previous = state.get("job")
    require(
        status in TERMINAL | {"running", "cancel_requested"}, "DATASET_CLAIM_IDENTITY"
    )
    if status == "empty":
        require(
            previous is None and job is None and receipt.get("jobId") is None,
            "DATASET_CLAIM_IDENTITY",
        )
        return status, None
    identifier(receipt.get("jobId"))
    require(
        previous is None or receipt["jobId"] == previous["id"], "DATASET_CLAIM_IDENTITY"
    )
    if status in TERMINAL:
        require(job is None, "DATASET_CLAIM_IDENTITY")
        return status, None
    require(
        isinstance(job, dict)
        and job.get("id") == receipt["jobId"]
        and job.get("kind") == job_kind,
        "DATASET_CLAIM_IDENTITY",
    )
    for key in ("id", "planId", "leaseToken"):
        identifier(job.get(key))
    expected = (
        urlsplit(base).path.rstrip("/")
        + "/runner/" + namespace + "/jobs/"
        + job["id"]
        + "/input"
    )
    require(job.get("inputUrl") == expected, "DATASET_CLAIM_IDENTITY")
    require(
        timestamp(job.get("leaseUntil")) <= timestamp(job.get("deadline")),
        "DATASET_LEASE",
    )
    if previous:
        require(
            all(
                previous[k] == job[k]
                for k in ("id", "kind", "planId", "leaseToken", "deadline", "inputUrl")
            ),
            "DATASET_CLAIM_IDENTITY",
        )
    return status, job


def _retain(error):
    """Transport ambiguity and corrupt private evidence cannot clear a claim."""
    code = error.code
    return (
        "SPOOL" in code
        or code
        in {
            "DATASET_MANIFEST",
            "DATASET_NETWORK",
            "DATASET_HEARTBEAT",
            "DATASET_CLAIM_IDENTITY",
            "DATASET_PUBLICATION_IDENTITY",
            "DATASET_STATUS_IDENTITY",
            "DATASET_COMPLETION_IDENTITY",
            "DATASET_TERMINAL_ACK",
            "DATASET_PART_ACK",
        }
        or (
            code == "DATASET_HTTP"
            and error.http_status not in {400, 403, 404, 409, 413, 422}
        )
    )


def run_once(
    config,
    spool,
    client,
    heartbeat_client,
    *,
    stop_requested=None,
    bounded_compute=execute_bounded,
    capability=CAPABILITY,
    claim_validator=validate_claim,
    terminal_reader=terminal_status,
    failure_settler=settle_failure,
    delivery=deliver,
    monitor_class=LeaseMonitor,
    rejection_handler=None,
):
    state = spool.current_or_create()
    if state["phase"] == "terminal":
        spool.cleanup(state)
        return
    response = client.post(
        "claim",
        {
            "requestId": state["requestId"],
            "capability": capability,
            "engineVersion": __version__,
        },
    )
    status, job = claim_validator(response, state, config["api_base"])
    if status in TERMINAL:
        if state.get("job"):
            # A lost completion response may be confirmed by the original claim.
            # Still check the same-lease dataset identity before removing evidence.
            if state["phase"] in {"computing", "publishing"}:
                manifest = spool.publication(state["job"]).manifest()
                if manifest is not None and not state.get("publication"):
                    from .protocol import sha

                    state = dict(
                        state, publication={"datasetRoot": sha(encode(manifest))}
                    )
            observed, _ = terminal_reader(client, state)
            require(observed == status, "DATASET_TERMINAL_ACK")
        spool.acknowledge(state, status)
        return
    if state["phase"] == "claiming":
        state = spool.save(dict(state, phase="claimed", job=job))
    if status == "cancel_requested":
        state = spool.save(
            dict(
                state,
                phase="failing",
                error=safe_error(RunnerError("CANCELLED", "取消。")),
            )
        )
    if state["phase"] == "failing":
        failure_settler(client, spool, state)
        return
    try:
        with monitor_class(
            heartbeat_client, job, stop_requested=stop_requested
        ) as monitor:
            if state["phase"] == "computing":
                require(
                    spool.publication(job).manifest() is not None, "RUNNER_INTERRUPTED"
                )
                state = spool.save(dict(state, phase="publishing"))
            if state["phase"] == "claimed":
                inputs = client.inputs(
                    job,
                    deadline=monitor.deadline,
                    heartbeat=monitor.check,
                    remember_input=spool.remember_input,
                )
                monitor.check()
                state = spool.save(dict(state, phase="computing"))
                monitor.phase = "composing_states"
                bounded_compute(
                    spool,
                    job,
                    inputs,
                    monitor,
                    slot_path=config.get("compute_lock_path"),
                )
                state = spool.save(dict(state, phase="publishing"))
            delivery(client, spool, state, monitor)
    except Exception as original:
        error = (
            original
            if isinstance(original, RunnerError)
            else RunnerError(**safe_error(original))
        )
        if _retain(error):
            raise error from None
        current = spool.read()
        require(current is not None, "DATASET_SPOOL_INTEGRITY")
        rejected = safe_error(error)
        if error.code == "DATASET_HTTP" and current["phase"] == "publishing":
            if rejection_handler is not None:
                rejection_handler(spool, current, error)
            rejected = safe_error(
                RunnerError("DATASET_RESULT_REJECTED", "发布被明确拒绝。")
            )
        current = spool.save(dict(current, phase="failing", error=rejected))
        failure_settler(client, spool, current)


def serve(config, *, once=False, stop_requested=None, client_factory=DatasetClient,
          enabled_key="dataset_enabled", spool_type=DatasetSpool, capability=CAPABILITY, iteration=None):
    require(config.get(enabled_key) is True, "DATASET_DISABLED")
    stop_requested = stop_requested or (lambda: False)
    spool = spool_type(config)
    with spool.locked():
        client = client_factory(config)
        while not stop_requested():
            try:
                pending = spool.read()
                # Recovery must work even when new claims are maintenance-gated.
                if pending is not None:
                    should_claim = True
                else:
                    ready = client.post(
                        "heartbeat",
                        {
                            "capability": capability,
                            "engineVersion": __version__,
                            "state": "ready",
                        },
                    )
                    require(
                        isinstance(ready, dict)
                        and ready.get("ok") is True
                        and type(ready.get("canClaim")) is bool
                    )
                    should_claim = ready["canClaim"]
                if should_claim and not stop_requested():
                    (iteration or run_once)(
                        config,
                        spool,
                        client,
                        client_factory(config),
                        stop_requested=stop_requested,
                    )
                code = 0
            except RunnerError as error:
                print(encode({"error": safe_error(error)}).decode(), file=sys.stderr)
                code = 1
                if "SPOOL" in error.code:
                    return code
            if once:
                return code
            until = time.monotonic() + config.get("poll_seconds", 10)
            while not stop_requested() and time.monotonic() < until:
                time.sleep(0.1)
    return 0
