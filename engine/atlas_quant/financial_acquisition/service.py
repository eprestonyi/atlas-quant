"""Single-writer, at-most-once provider consumer for financial-acquire/v1.

Only this opt-in process receives provider configuration. A lost provider outcome
is a durable manual-review condition; retryable control delivery never repeats it.
"""

import multiprocessing as mp
import threading
import time
import uuid

from .. import __version__
from ..runner import RunnerError
from .client import AcquisitionClient
from .normalize import build_publication, calendar_from_receipt, normalize_statement
from .planner import validate_execution
from .protocol import (
    CAPABILITY,
    KIND,
    REQUEST_SECONDS,
    PROFILE,
    encode,
    identifier,
    require,
    safe_error,
    sha,
    timestamp,
)
from .provider import OutcomeUnknown, RawTushareAdapter, preflight_provider_config
from .spool import AcquisitionSpool

TERMINAL = {"completed", "failed", "cancelled", "empty"}
CONTROL_ERRORS = {
    "ACQUISITION_HTTP",
    "ACQUISITION_NETWORK",
    "ACQUISITION_INTEGRITY",
    "ACQUISITION_SPOOL_INTEGRITY",
}


def _provider_child(config, context, job, request, remaining_bytes, deadline, factory):
    """The child commits bytes before exit; no response body crosses IPC."""
    spool = AcquisitionSpool.from_context(context)
    try:
        response = factory(config).call_once(
            request, deadline=deadline, maximum_bytes=remaining_bytes
        )
        require(
            response.source_kind == "provider"
            or config.get("allow_acquisition_fixtures") is True
            and response.source_kind == "fixture",
            "ACQUISITION_SOURCE_KIND",
            "Fixture response refused in production",
        )
        require(
            isinstance(response.raw, bytes)
            and len(response.raw) <= min(remaining_bytes, PROFILE["maxResponseBytes"]),
            "ACQUISITION_RESPONSE_BUDGET",
            "Raw response exceeded remaining parent budget",
        )
        receipt = {
            "requestKey": request["requestKey"],
            "raw": response.raw,
            "sha256": sha(response.raw),
            "byteLength": len(response.raw),
            "httpStatus": response.http_status,
            "retrievedAt": response.retrieved_at,
            "sourceKind": response.source_kind,
        }
        spool.save_receipt(job, request["requestKey"], receipt)
    except Exception:
        # The parent treats every absent durable receipt as unknown, including
        # process crashes or disk errors. Never serialize exception/config text.
        return


def execute_one(
    config,
    spool,
    job,
    request,
    remaining_bytes,
    deadline,
    check,
    factory=RawTushareAdapter,
):
    limit = min(deadline, time.monotonic() + REQUEST_SECONDS)
    process = mp.get_context("spawn").Process(
        target=_provider_child,
        args=(config, spool.context(), job, request, remaining_bytes, limit, factory),
    )
    check()
    process.start()
    try:
        while process.is_alive():
            process.join(timeout=0.1)
            check()
            if time.monotonic() >= limit:
                raise OutcomeUnknown()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        process.close()
    if spool.receipt(job, request["requestKey"]) is None:
        raise OutcomeUnknown()


class LeaseMonitor:
    def __init__(self, config, job, deadline, client_factory=AcquisitionClient):
        self.client = client_factory(config)
        self.job, self.deadline = job, deadline
        self.phase = "checking_plan"
        self.lease_deadline = min(
            deadline,
            time.monotonic() + max(0, timestamp(job["leaseUntil"]) - time.time()),
        )
        self.error = None
        self.stop = threading.Event()
        self.thread = None

    def check(self):
        require(
            time.monotonic() < self.deadline,
            "ACQUISITION_DEADLINE",
            "Acquisition deadline elapsed",
        )
        require(
            time.monotonic() < self.lease_deadline,
            "ACQUISITION_LEASE",
            "Last confirmed lease expired",
        )
        if self.error:
            raise self.error

    def beat(self):
        result = self.client.post(
            "heartbeat",
            {
                "capability": CAPABILITY,
                "engineVersion": __version__,
                "state": "busy",
                "jobId": self.job["id"],
                "leaseToken": self.job["leaseToken"],
                "phase": self.phase,
            },
            deadline=min(self.deadline, self.lease_deadline),
        )
        require(
            result.get("leaseValid") is True,
            "ACQUISITION_LEASE",
            "Acquisition lease no longer valid",
        )
        require(
            result.get("cancelRequested") is not True,
            "ACQUISITION_CANCELLED",
            "Acquisition was cancelled",
        )

        # Only an explicit successful receipt can extend this monotonic limit.
        until = timestamp(result.get("leaseUntil"))
        remaining = until - time.time()
        require(
            0 < remaining <= 125, "ACQUISITION_LEASE", "Invalid renewed lease duration"
        )
        self.lease_deadline = min(self.deadline, time.monotonic() + remaining)

    def attempt(self):
        try:
            self.check()
            self.beat()
            return True
        except RunnerError as error:
            transient = error.code == "ACQUISITION_NETWORK" or (
                error.code == "ACQUISITION_HTTP"
                and error.http_status in {408, 425, 429, 500, 502, 503, 504}
            )
            if transient and time.monotonic() < min(self.deadline, self.lease_deadline):
                return False
            raise

    def __enter__(self):
        healthy = self.attempt()

        def loop():
            interval = 20 if healthy else 3
            while not self.stop.wait(interval):
                try:
                    interval = 20 if self.attempt() else 3
                except Exception as error:
                    self.error = error
                    return

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)


class AcquisitionConsumer:
    def __init__(
        self,
        config,
        client=None,
        *,
        provider_factory=RawTushareAdapter,
        executor=execute_one,
        monitor_factory=LeaseMonitor,
        stop_requested=None,
    ):
        require(
            config.get("acquisition_enabled") is True,
            "ACQUISITION_DISABLED",
            "Separate provider process requires explicit enablement",
        )
        if provider_factory is RawTushareAdapter:
            preflight_provider_config(config)
        self.config = config
        self.stop_requested = stop_requested or (lambda: False)
        self.client = client or AcquisitionClient(config)
        self.spool = AcquisitionSpool(config)
        self.provider_factory, self.executor, self.monitor_factory = (
            provider_factory,
            executor,
            monitor_factory,
        )

    def check(self, monitor):
        require(
            not self.stop_requested(),
            "ACQUISITION_STOPPED",
            "Acquisition process was stopped",
        )
        monitor.check()

    def claim(self, state):
        answer = self.client.post(
            "claim",
            {
                "requestId": state["requestId"],
                "capability": CAPABILITY,
                "engineVersion": __version__,
            },
        )
        receipt = answer.get("claim", {})
        require(
            receipt.get("requestId") == state["requestId"]
            and receipt.get("status") in TERMINAL | {"running", "cancel_requested"},
            "ACQUISITION_CLAIM",
            "Claim receipt does not match durable identity",
        )
        job = answer.get("job")
        if receipt["status"] in TERMINAL:
            require(
                job is None
                and (
                    receipt["status"] == "empty"
                    and receipt.get("jobId") is None
                    or receipt["status"] != "empty"
                    and identifier(receipt.get("jobId"))
                ),
                "ACQUISITION_CLAIM",
                "Terminal claim receipt is incomplete",
            )
            if state.get("job"):
                require(
                    receipt.get("jobId") == state["job"]["id"],
                    "ACQUISITION_CLAIM",
                    "Terminal job identity changed",
                )
            retain = receipt["status"] in {"failed", "cancelled"} or bool(
                state.get("unknown")
            )
            self.spool.terminal(state, receipt["status"], retain=retain)
            return None
        require(
            isinstance(job, dict)
            and set(job)
            == {
                "id",
                "kind",
                "planId",
                "leaseToken",
                "leaseUntil",
                "deadline",
                "inputUrl",
            }
            and job["kind"] == KIND
            and receipt.get("jobId") == job["id"],
            "ACQUISITION_CLAIM",
            "Invalid acquisition job",
        )
        for key in ("id", "planId", "leaseToken"):
            identifier(job[key])
        for key in ("leaseUntil", "deadline"):
            timestamp(job[key])
        if state.get("job"):
            require(
                all(
                    job[k] == state["job"][k]
                    for k in (
                        "id",
                        "kind",
                        "planId",
                        "leaseToken",
                        "deadline",
                        "inputUrl",
                    )
                ),
                "ACQUISITION_CLAIM",
                "Existing claim was reassigned",
            )
        return self.spool.save(
            {
                **state,
                "job": job,
                "phase": state["phase"] if state.get("job") else "claimed",
            }
        )

    def _unknown(self, state, request, attempt_id):
        state["unknown"] = True
        state["phase"] = "failing"
        state["error"] = safe_error(OutcomeUnknown())
        state["requests"][request["requestKey"]] = {
            "attemptId": attempt_id,
            "phase": "unknown",
        }
        self.spool.save(state)
        self.client.post(
            f"jobs/{state['job']['id']}/requests/{request['requestKey']}/unknown",
            {
                "leaseToken": state["job"]["leaseToken"],
                "attemptId": attempt_id,
                "reason": "Provider outcome unknown; automatic retry prohibited",
            },
        )
        raise OutcomeUnknown()

    def _receipt(self, state, request, remaining, deadline, monitor):
        job, key = state["job"], request["requestKey"]
        receipt = self.spool.receipt(job, key)
        entry = state["requests"].get(key)
        if receipt and "receiptId" in receipt:
            return receipt
        if not receipt and request["cache"]["status"] == "frozen":
            receipt = self.client.get_receipt(job, request, deadline=deadline)
            require(
                receipt["byteLength"] <= remaining,
                "ACQUISITION_RESPONSE_BUDGET",
                "Cached response exceeds remaining raw budget",
            )
            self.spool.save_receipt(job, key, receipt)
            return receipt
        if receipt is None:
            if entry and entry["phase"] in {"calling", "unknown"}:
                self._unknown(state, request, entry["attemptId"])
            if entry is None:
                entry = {"attemptId": str(uuid.uuid4()), "phase": "begin_pending"}
                state["requests"][key] = entry
                self.spool.save(state)
            self.check(monitor)
            ack = self.client.post(
                f"jobs/{job['id']}/requests/{key}/begin",
                {"leaseToken": job["leaseToken"], "attemptId": entry["attemptId"]},
                deadline=deadline,
            )
            if ack.get("state") == "received" and ack.get("maySend") is False:
                receipt = self.client.get_receipt(job, request, deadline=deadline)
                require(
                    ack.get("receiptId") == receipt["receiptId"],
                    "ACQUISITION_RECEIPT",
                    "Received identity changed",
                )
                self.spool.save_receipt(job, key, receipt)
                return receipt
            require(
                ack.get("state") == "intent"
                and ack.get("attemptId") == entry["attemptId"]
                and type(ack.get("maySend")) is bool,
                "ACQUISITION_BEGIN",
                "Provider intent acknowledgement differs",
            )
            if ack["maySend"] is not True:
                self._unknown(state, request, entry["attemptId"])
            self.check(monitor)
            # Persist before child creation; even a crash before its first byte
            # is conservatively unknown rather than assumed safe to resend.
            entry["phase"] = "calling"
            self.spool.save(state)
            try:
                self.executor(
                    self.config,
                    self.spool,
                    job,
                    {k: v for k, v in request.items() if k != "cache"},
                    min(remaining, PROFILE["maxResponseBytes"]),
                    deadline,
                    lambda: self.check(monitor),
                    self.provider_factory,
                )
            except Exception:
                receipt = self.spool.receipt(job, key)
                if receipt is None:
                    self._unknown(state, request, entry["attemptId"])
            receipt = self.spool.receipt(job, key)
            if receipt is None:
                self._unknown(state, request, entry["attemptId"])
        require(
            entry is not None and receipt["byteLength"] <= remaining,
            "ACQUISITION_RESPONSE_BUDGET",
            "Raw receipt exceeds remaining parent budget",
        )
        # PUT may repeat with these exact durable bytes; provider may not.
        ack = self.client.put_receipt(
            job, request, receipt, entry["attemptId"], deadline=deadline
        )
        receipt = {**receipt, **{k: v for k, v in ack.items() if k != "idempotent"}}
        self.spool.save_receipt(job, key, receipt)
        entry["phase"] = "received"
        self.spool.save(state)
        return receipt

    def _publish(self, state, manifest, deadline, monitor):
        job = state["job"]
        expected = sha(encode(manifest))
        answer = self.client.post(
            f"jobs/{job['id']}/publication",
            {"leaseToken": job["leaseToken"], "manifest": manifest},
            deadline=deadline,
        )
        require(
            answer.get("manifestSha256") == expected
            and isinstance(answer.get("missing"), dict)
            and set(answer["missing"]) == {"package", "calendar"},
            "ACQUISITION_PUBLICATION",
            "Publication acknowledgement differs",
        )
        for name in ("package", "calendar"):
            missing = answer["missing"][name]
            require(
                isinstance(missing, list)
                and all(type(i) is int for i in missing)
                and len(missing) == len(set(missing))
                and set(missing) <= set(range(len(manifest[name]["chunks"]))),
                "ACQUISITION_PUBLICATION",
                "Invalid missing-part receipt",
            )
            for ordinal in missing:
                self.check(monitor)
                raw = self.spool.chunk(job, name, manifest[name]["chunks"][ordinal])
                ack = self.client.put_chunk(
                    job, expected, name, ordinal, raw, deadline=deadline
                )
                require(
                    ack.get("ok") is True,
                    "ACQUISITION_PUBLICATION",
                    "Part write was not acknowledged",
                )
        self.check(monitor)
        result = self.client.post(
            f"jobs/{job['id']}/complete",
            {"leaseToken": job["leaseToken"], "manifestSha256": expected},
            deadline=deadline,
        )
        completed = result.get("job", {})
        roots = result.get("result", {})
        require(
            completed.get("id") == job["id"]
            and completed.get("status") == "completed"
            and roots.get("researchBinding") is False
            and all(roots.get(k) == manifest[k] for k in ("inputRoot", "packRoot")),
            "ACQUISITION_COMPLETION",
            "Completion identity or frozen roots differ",
        )
        identifier(roots.get("inputId"))
        identifier(roots.get("calendarRef"))
        state["phase"] = "awaiting_terminal"
        self.spool.save(state)

    def once(self):
        """One durable claim cycle. Call under spool.locked(); no provider retry."""
        state = self.spool.current_or_create()
        if state.get("phase") == "terminal":
            self.spool.terminal(
                state, state["terminalStatus"], retain=state.get("retain", False)
            )
            return False
        state = self.claim(state)
        if state is None:
            return False
        job = state["job"]
        deadline = time.monotonic() + min(
            600, max(0, timestamp(job["deadline"]) - time.time())
        )
        try:
            with self.monitor_factory(self.config, job, deadline) as monitor:
                if state["phase"] == "failing":
                    raise RunnerError(state["error"]["code"], state["error"]["message"])
                metadata = self.spool.metadata(job)
                if metadata is None:
                    metadata = self.client.input(job, deadline=deadline)
                    self.spool.metadata(job, metadata)
                plan = validate_execution(
                    metadata,
                    job,
                    self.config["authorization_scope"],
                    allow_fixtures=self.config.get("allow_acquisition_fixtures")
                    is True,
                )
                manifest = self.spool.publication(job)
                if manifest is None:
                    monitor.phase = "fetching_sources"
                    receipts, remaining = {}, PROFILE["maxTotalBytes"]
                    for request in plan["requests"]:
                        self.check(monitor)
                        require(
                            remaining > 0,
                            "ACQUISITION_RESPONSE_BUDGET",
                            "Raw parent budget exhausted before next request",
                        )
                        receipt = self._receipt(
                            state, request, remaining, deadline, monitor
                        )
                        remaining -= receipt["byteLength"]
                        require(
                            remaining >= 0,
                            "ACQUISITION_RESPONSE_BUDGET",
                            "Aggregate raw response budget exceeded",
                        )
                        receipts[request["requestKey"]] = receipt
                        # Validate each received response before spending another
                        # provider request, including calendar completeness first.
                        if request["endpoint"] == "trade_cal":
                            calendar_from_receipt(request, receipt)
                        else:
                            normalize_statement(request, receipt)
                    monitor.phase = "normalizing"
                    self.check(monitor)
                    manifest, chunks = build_publication(job, plan, receipts)
                    self.spool.save_publication(job, manifest, chunks)
                    state["phase"] = "publishing"
                    self.spool.save(state)
                monitor.phase = "writing_evidence"
                self._publish(state, manifest, deadline, monitor)
        except Exception as error:
            if getattr(error, "code", "") in CONTROL_ERRORS:
                raise
            state["phase"], state["error"] = "failing", safe_error(error)
            self.spool.save(state)
            ack = self.client.post(
                f"jobs/{job['id']}/fail",
                {"leaseToken": job["leaseToken"], "error": state["error"]},
            )
            require(
                ack.get("job", {}).get("id") == job["id"]
                and ack["job"].get("status") in {"failed", "cancelled"},
                "ACQUISITION_TERMINAL",
                "Failure was not acknowledged",
            )
        # Terminal readback, including lost complete ACK, always reuses UUID.
        self.claim(state)
        return True


def serve(config, *, once=False, stop_requested=None, client_factory=AcquisitionClient):
    stop_requested = stop_requested or (lambda: False)
    consumer = AcquisitionConsumer(
        config, client_factory(config), stop_requested=stop_requested
    )
    with consumer.spool.locked():
        while not stop_requested():
            try:
                ready = consumer.client.post(
                    "heartbeat",
                    {
                        "capability": CAPABILITY,
                        "engineVersion": __version__,
                        "state": "idle",
                    },
                )
                pending = consumer.spool.state() is not None
                require(
                    isinstance(ready, dict)
                    and ready.get("ok") is True
                    and type(ready.get("canClaim")) is bool,
                    "ACQUISITION_PROTOCOL",
                    "Idle heartbeat must include an explicit queue gate",
                )
                if not stop_requested() and (pending or ready["canClaim"]):
                    consumer.once()
            except RunnerError as error:
                # No exception repr, request bodies, URLs or credentials in logs.
                print(encode(safe_error(error)).decode(), flush=True)
                if error.code not in CONTROL_ERRORS:
                    raise
            if once:
                return
            until = time.monotonic() + config.get("poll_seconds", 10)
            while not stop_requested() and time.monotonic() < until:
                time.sleep(0.1)
