"""Dedicated financial consumer: at-most-once computation, resumable delivery."""

from __future__ import annotations

import argparse
from datetime import datetime
import multiprocessing
import re
import signal
import sys
import threading
import time

from .. import __version__
from ..runner import RunnerError, load_config
from .client import FinancialClient
from .protocol import CAPABILITY, KINDS, digest, encode, fail, identifier
from .spool import FinancialSpool, TERMINAL


def _timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
        return date.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        fail("FINANCIAL_PROTOCOL", "财务租约须包含明确时区与固定截止时间。")


def _identity(job):
    return {"jobId": job["id"], "leaseToken": job["leaseToken"]}


def safe_error(error):
    code = str(getattr(error, "code", "FINANCIAL_COMPUTE_FAILED"))
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code):
        code = "FINANCIAL_COMPUTE_FAILED"
    # Never forward exception values, provider rows, registry contents or repr.
    return {"code": code, "message": "财务任务未完成；请按错误代码检查任务与输入证据。"}


def validate_claim(response, state, base):
    receipt = response.get("claim") if isinstance(response, dict) else None
    if not isinstance(receipt, dict) or receipt.get("requestId") != state["requestId"]:
        fail("FINANCIAL_CLAIM_PROTOCOL", "领取回执没有匹配原持久请求。")
    status, job = receipt.get("status"), response.get("job")
    previous = state.get("job")
    if status not in TERMINAL | {"running", "cancel_requested"}:
        fail("FINANCIAL_CLAIM_PROTOCOL", "领取状态无效；保留原请求。")
    if status == "empty":
        if previous or job is not None or receipt.get("jobId") is not None:
            fail("FINANCIAL_CLAIM_PROTOCOL", "空领取回执与原任务冲突。")
        return status, None
    identifier(receipt.get("jobId"))
    if previous and receipt["jobId"] != previous["id"]:
        fail("FINANCIAL_CLAIM_PROTOCOL", "重试领取更换了任务。")
    if status in TERMINAL:
        if job is not None:
            fail("FINANCIAL_CLAIM_PROTOCOL", "终态领取不应返回可运行任务。")
        return status, None
    if (
        not isinstance(job, dict)
        or job.get("id") != receipt["jobId"]
        or job.get("kind") not in KINDS
    ):
        fail("FINANCIAL_CLAIM_PROTOCOL", "财务任务种类或身份无效。")
    for key in ("id", "leaseToken", "inputId"):
        identifier(job.get(key))
    from urllib.parse import urlsplit

    expected = (
        urlsplit(base).path.rstrip("/")
        + "/runner/financial/jobs/"
        + job["id"]
        + "/input"
    )
    if job.get("inputUrl") != expected:
        fail("FINANCIAL_CLAIM_PROTOCOL", "财务输入地址不属于原任务。")
    if _timestamp(job.get("leaseUntil")) > _timestamp(job.get("deadline")):
        fail("FINANCIAL_CLAIM_PROTOCOL", "租约不能超过固定任务截止时间。")
    if previous and any(
        previous[k] != job[k]
        for k in ("id", "kind", "inputId", "leaseToken", "deadline")
    ):
        fail("FINANCIAL_CLAIM_PROTOCOL", "恢复任务的身份、租约或固定截止时间变化。")
    return status, job


class LeaseMonitor:
    """Separate HTTP session keeps leases alive during input and result I/O."""

    def __init__(self, client, job, *, stop_requested=None, interval=20):
        self.client, self.job = client, dict(job)
        self.stop_requested = stop_requested or (lambda: False)
        self.interval = interval
        now = time.monotonic()
        self.deadline = now + min(
            KINDS[job["kind"]], _timestamp(job["deadline"]) - time.time()
        )
        self.lease_until = now + min(120, _timestamp(job["leaseUntil"]) - time.time())
        self.phase, self.error = "checking_inputs", None
        self.last_transient = None
        self.closed = threading.Event()
        self.thread = None

    def check(self):
        if self.stop_requested():
            fail("RUNNER_STOPPED", "财务服务停止，原任务不会重新计算。")
        if self.error is not None:
            raise self.error
        if time.monotonic() >= self.deadline:
            fail("FINANCIAL_DEADLINE", "财务任务超过固定截止时间。")
        if time.monotonic() >= self.lease_until:
            fail("LEASE_EXPIRED", "财务任务租约已失效。")

    def pulse(self):
        self.check()
        try:
            response = self.client.post(
                "heartbeat",
                {
                    "capability": CAPABILITY,
                    "engineVersion": __version__,
                    "state": "busy",
                    **_identity(self.job),
                    "phase": self.phase,
                },
                deadline=min(self.deadline, time.monotonic() + 20),
            )
        except RunnerError as error:
            transient = error.code == "FINANCIAL_NETWORK" or (
                error.code == "FINANCIAL_HTTP"
                and isinstance(error.http_status, int)
                and 500 <= error.http_status <= 599
            )
            if not transient:
                raise
            # A lost renewal response cannot extend the last acknowledged lease.
            # Retry on the ordinary 20-second cadence only while that lease and
            # the original fixed deadline still permit computation.
            self.last_transient = error.code
            self.check()
            return
        if not isinstance(response, dict) or response.get("ok") is not True:
            fail("FINANCIAL_PROTOCOL", "财务心跳没有可信回执。")
        if response.get("cancelRequested") is True:
            fail("CANCELLED", "财务任务已请求取消。")
        if response.get("leaseValid") is not True:
            fail("LEASE_EXPIRED", "财务任务租约已失效。")
        until = _timestamp(response.get("leaseUntil"))
        if until > _timestamp(self.job["deadline"]):
            fail("FINANCIAL_PROTOCOL", "心跳试图延长固定任务截止时间。")
        self.lease_until = time.monotonic() + min(120, until - time.time())
        self.last_transient = None
        self.check()

    def _loop(self):
        while not self.closed.wait(self.interval):
            try:
                self.pulse()
            except RunnerError as error:
                self.error = error
                return
            except Exception:
                self.error = RunnerError("FINANCIAL_HEARTBEAT", "财务心跳中断。")
                return

    def __enter__(self):
        self.pulse()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=1)


def _child_entry(connection, context, meta, source, registries, computer, compute_lock_path=None, deadline=None):
    try:
        if computer is None:
            from .publication import compute_publication

            computer = compute_publication
        publication = FinancialSpool.from_context(context).publication(context["job"])
        from ..compute_slot import compute_slot

        with compute_slot(compute_lock_path, deadline=deadline):
            manifest = computer(
                context["job"], meta, source, registries, publication.write_chunk
            )
            publication.finalize(manifest)  # Manifest is the final durable commit marker.
        connection.send({"complete": True})
    except BaseException as error:
        connection.send({"error": safe_error(error)})
    finally:
        connection.close()


def execute_bounded(spool, job, inputs, monitor, *, computer=None, compute_lock_path=None):
    """Child gets no queue/provider credentials and cannot outlive its deadline."""
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child_entry,
        args=(
            child,
            spool.context(job),
            *inputs,
            computer,
        ) + ((compute_lock_path, monitor.deadline) if compute_lock_path is not None else ()),
        daemon=True,
    )
    try:
        monitor.check()
        process.start()
        child.close()
        while True:
            monitor.check()
            if parent.poll(min(0.1, max(0, monitor.deadline - time.monotonic()))):
                try:
                    answer = parent.recv()
                except EOFError:
                    if spool.publication(job).manifest() is not None:
                        monitor.check()
                        return
                    fail("FINANCIAL_CHILD_EXIT", "财务子进程意外退出。")
                if answer.get("error"):
                    raise RunnerError(
                        answer["error"]["code"], answer["error"]["message"]
                    )
                monitor.check()
                if (
                    answer != {"complete": True}
                    or spool.publication(job).manifest() is None
                ):
                    fail("FINANCIAL_SPOOL_INTEGRITY", "计算没有生成完整持久产物。")
                return
            if not process.is_alive():
                # A child can die after the manifest's fsync but before its pipe
                # acknowledgment. Durable completion, not the pipe, is authority.
                if spool.publication(job).manifest() is not None:
                    monitor.check()
                    return
                fail("FINANCIAL_CHILD_EXIT", "财务子进程未完成产物。")
    finally:
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        parent.close()


def terminal_status(client, job):
    value = client.get("jobs/" + job["id"] + "/status", job["leaseToken"])
    row = value.get("job")
    if (
        not isinstance(row, dict)
        or row.get("id") != job["id"]
        or row.get("inputId") != job["inputId"]
        or row.get("status")
        not in {"running", "cancel_requested", "completed", "failed", "cancelled"}
    ):
        fail("FINANCIAL_PROTOCOL", "终态读取与原任务身份不一致。")
    return row["status"]


def _settle_failure(client, spool, state):
    job = state["job"]
    # Read first so a lost successful-completion ACK never becomes a failed job.
    status = terminal_status(client, job)
    if status not in TERMINAL:
        error = state["error"]
        if status == "cancel_requested" and error["code"] != "CANCELLED":
            error = safe_error(RunnerError("CANCELLED", "任务已取消。"))
            state = spool.save(dict(state, error=error))
        client.post("fail", {**_identity(job), "error": error})
        status = terminal_status(client, job)
    if status not in TERMINAL:
        fail("FINANCIAL_PROTOCOL", "失败投递尚未确认终态，保留原件。")
    spool.acknowledge(state, status)


def deliver(client, spool, state, monitor):
    job = state["job"]
    publication = spool.publication(job)
    manifest = publication.manifest()
    if manifest is None:
        fail("FINANCIAL_SPOOL_INTEGRITY", "缺少完整财务 manifest。")
    monitor.phase = "writing_evidence"
    monitor.check()
    old = state.get("publication")
    if old:
        response = client.get(
            "publications/"
            + old["publicationId"]
            + "?manifestSha256="
            + old["manifestSha256"],
            job["leaseToken"],
            deadline=monitor.deadline,
        )
    else:
        response = client.post(
            "publications/begin",
            {**_identity(job), "manifest": manifest},
            deadline=monitor.deadline,
        )
    identifier(response.get("publicationId"))
    digest(response.get("manifestSha256"))
    identity = {k: response[k] for k in ("publicationId", "manifestSha256")}
    if old and old != identity:
        fail("FINANCIAL_PROTOCOL", "财务恢复发布身份发生变化。")
    if response.get("status") not in {"staging", "committed"}:
        fail("FINANCIAL_PROTOCOL", "财务发布状态无效。")
    state = spool.save(dict(state, publication=identity))
    allowed = {
        (name, part["ordinal"]): part
        for name, collection in manifest["collections"].items()
        for part in collection["chunks"]
    }
    missing = response.get("missing")
    if not isinstance(missing, list) or len(missing) > len(allowed):
        fail("FINANCIAL_PROTOCOL", "服务器缺片回执超出原清单。")
    seen = set()
    for item in missing:
        if (
            not isinstance(item, dict)
            or set(item) != {"collection", "ordinal"}
            or type(item["ordinal"]) is not int
            or not isinstance(item["collection"], str)
        ):
            fail("FINANCIAL_PROTOCOL", "服务器缺片回执无效。")
        key = (item["collection"], item["ordinal"])
        if key not in allowed or key in seen:
            fail("FINANCIAL_PROTOCOL", "服务器要求未声明或重复分片。")
        seen.add(key)
    for name, ordinal in sorted(seen):
        monitor.check()
        raw = publication.read_chunk(name, allowed[(name, ordinal)])
        result = client.upload(
            identity["publicationId"],
            identity["manifestSha256"],
            name,
            ordinal,
            raw,
            job,
            deadline=monitor.deadline,
        )
        if result.get("ok") is not True:
            fail("FINANCIAL_PROTOCOL", "财务分片尚未确认接收。")
    monitor.check()
    response = client.post(
        "complete", {**_identity(job), **identity}, deadline=monitor.deadline
    )
    if (
        response.get("ok") is not True
        or response.get("status") != "completed"
        or response.get("inputId") != job["inputId"]
    ):
        fail("FINANCIAL_PROTOCOL", "财务完成回执不匹配，保留原件。")
    # Original-lease readback is the final authority, not merely HTTP 200.
    status = terminal_status(client, job)
    if status not in TERMINAL:
        fail("FINANCIAL_PROTOCOL", "财务完成尚未确认终态。")
    spool.acknowledge(state, status)


def run_once(
    config,
    spool,
    client,
    heartbeat_client,
    *,
    stop_requested=None,
    bounded_compute=execute_bounded,
):
    state = spool.current_or_create()
    if state["phase"] == "terminal":
        spool.cleanup(state)
        return
    response = client.post(
        "claim",
        {
            "requestId": state["requestId"],
            "capability": CAPABILITY,
            "engineVersion": __version__,
        },
    )
    status, job = validate_claim(response, state, config["api_base"])
    if status in TERMINAL:
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
        _settle_failure(client, spool, state)
        return
    try:
        with LeaseMonitor(
            heartbeat_client, job, stop_requested=stop_requested
        ) as monitor:
            if state["phase"] == "computing":
                if spool.publication(job).manifest() is None:
                    fail("RUNNER_INTERRUPTED", "计算中断且无完整产物；不会重新计算。")
                state = spool.save(dict(state, phase="publishing"))
            if state["phase"] == "claimed":
                inputs = client.inputs(
                    job, deadline=monitor.deadline, heartbeat=monitor.check
                )
                monitor.check()
                state = spool.save(dict(state, phase="computing"))
                monitor.phase = "preparing_states"
                options = (
                    {"compute_lock_path": config["compute_lock_path"]}
                    if config.get("compute_lock_path") is not None
                    else {}
                )
                bounded_compute(spool, job, inputs, monitor, **options)
                state = spool.save(dict(state, phase="publishing"))
            deliver(client, spool, state, monitor)
    except RunnerError as error:
        current = spool.read()
        if current is None:
            raise
        if error.code == "FINANCIAL_HTTP" and current["phase"] == "publishing":
            if error.http_status in {400, 413}:
                rejected = safe_error(
                    RunnerError("FINANCIAL_RESULT_REJECTED", "产物被明确拒绝。")
                )
                current = spool.save(dict(current, phase="failing", error=rejected))
                _settle_failure(client, spool, current)
                return
            if error.http_status == 409:
                status = terminal_status(client, current["job"])
                if status in TERMINAL:
                    spool.acknowledge(current, status)
                    return
                code = (
                    "CANCELLED"
                    if status == "cancel_requested"
                    else "FINANCIAL_PUBLICATION_CONFLICT"
                )
                current = spool.save(
                    dict(
                        current,
                        phase="failing",
                        error=safe_error(RunnerError(code, "发布身份冲突。")),
                    )
                )
                _settle_failure(client, spool, current)
                return
        # Ambiguous publication transport always keeps the exact manifest and
        # publication identity. No new UUID, result or computation is created.
        if error.code in {
            "FINANCIAL_NETWORK",
            "FINANCIAL_HTTP",
            "FINANCIAL_PROTOCOL",
            "FINANCIAL_HEARTBEAT",
        }:
            raise
        if (
            error.code.startswith("FINANCIAL_SPOOL")
            or error.code == "FINANCIAL_MANIFEST"
        ):
            raise
        current = spool.save(dict(current, phase="failing", error=safe_error(error)))
        _settle_failure(client, spool, current)


def serve(config, *, once=False, stop_requested=None, client_factory=FinancialClient):
    stop_requested = stop_requested or (lambda: False)
    spool = FinancialSpool(config)
    with spool.locked():
        client = client_factory(config)
        while not stop_requested():
            try:
                ready = client.post(
                    "heartbeat",
                    {
                        "capability": CAPABILITY,
                        "engineVersion": __version__,
                        "state": "ready",
                    },
                )
                pending = spool.read()
                if pending is not None:
                    # Queue availability is only advisory: an ambiguous claim
                    # or publication must resume with its original identity.
                    should_claim = True
                elif (
                    isinstance(ready, dict)
                    and ready.get("ok") is True
                    and type(ready.get("canClaim")) is bool
                ):
                    should_claim = ready["canClaim"]
                else:
                    fail("FINANCIAL_PROTOCOL", "财务队列未返回明确的领取状态。")
                if should_claim:
                    run_once(
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
                if error.code.startswith("FINANCIAL_SPOOL"):
                    return code
            if once:
                return code
            until = time.monotonic() + config.get("poll_seconds", 10)
            while not stop_requested() and time.monotonic() < until:
                time.sleep(0.1)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Atlas financial preparation consumer (no provider acquisition)"
    )
    parser.add_argument("--config", required=True, help="Private 0600 JSON config")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    stopped = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopped.set())
    try:
        return serve(
            load_config(args.config), once=args.once, stop_requested=stopped.is_set
        )
    except (RunnerError, OSError) as error:
        print(encode({"error": safe_error(error)}).decode(), file=sys.stderr)
        return 1
