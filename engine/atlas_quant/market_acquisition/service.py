"""At-most-once whole-universe acquisition; restart resumes durable control only."""

import time
from .. import __version__
from ..runner import RunnerError
from ..financial_acquisition.service import (
    AcquisitionConsumer,
    LeaseMonitor,
    execute_one,
    TERMINAL,
    CONTROL_ERRORS,
)
from .client import MarketClient
from .spool import MarketSpool
from .provider import RawMarketAdapter, preflight_provider_config
from .normalize import table, calendar, build_publication
from .protocol import *


def safe_error(error):
    return {
        "code": str(getattr(error, "code", "MARKET_PREPARATION_FAILED"))[:80],
        "message": "完整市场准备未完成；已保留原始回执，未知请求不可自动重读。",
    }


class MarketLeaseMonitor(LeaseMonitor):
    def __init__(self, config, job, deadline, client_factory=MarketClient):
        super().__init__(config, job, deadline, client_factory)

    def beat(self):
        value = self.client.post(
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
            value.get("leaseValid") is True, "ACQUISITION_LEASE", "Market lease invalid"
        )
        require(
            value.get("cancelRequested") is not True,
            "ACQUISITION_CANCELLED",
            "Market preparation cancelled",
        )
        remaining = timestamp(value.get("leaseUntil")) - time.time()
        require(0 < remaining <= 125, "ACQUISITION_LEASE", "Invalid renewed lease")
        self.lease_deadline = min(self.deadline, time.monotonic() + remaining)


class MarketConsumer(AcquisitionConsumer):
    # Reuse the audited intent -> one bounded child -> durable raw -> retryable
    # receipt gate, not the financial claim, normalization, limits or namespace.
    def __init__(
        self,
        config,
        client=None,
        *,
        provider_factory=RawMarketAdapter,
        executor=execute_one,
        monitor_factory=MarketLeaseMonitor,
        stop_requested=None,
    ):
        require(
            config.get("market_acquisition_enabled") is True,
            "MARKET_DISABLED",
            "Separate market process requires explicit enablement",
        )
        if provider_factory is RawMarketAdapter:
            preflight_provider_config(config)
        self.config = {
            **config,
            "allow_acquisition_fixtures": config.get("allow_market_fixtures") is True,
        }
        self.client = client or MarketClient(config)
        self.spool = MarketSpool(config)
        self.provider_factory = provider_factory
        self.executor = executor
        self.monitor_factory = monitor_factory
        self.stop_requested = stop_requested or (lambda: False)

    def claim(self, state):
        result = self.client.post(
            "claim",
            {
                "requestId": state["requestId"],
                "capability": CAPABILITY,
                "engineVersion": __version__,
            },
        )
        r = result.get("claim", {})
        job = result.get("job")
        require(
            r.get("requestId") == state["requestId"]
            and r.get("status") in TERMINAL | {"running", "cancel_requested"},
            "MARKET_CLAIM",
            "Durable claim identity differs",
        )
        if r["status"] in TERMINAL:
            require(
                job is None
                and (
                    r["status"] == "empty"
                    and r.get("jobId") is None
                    or r["status"] != "empty"
                    and identifier(r.get("jobId"))
                ),
                "MARKET_CLAIM",
                "Invalid terminal receipt",
            )
            if state.get("job"):
                require(
                    r.get("jobId") == state["job"]["id"],
                    "MARKET_CLAIM",
                    "Terminal job changed",
                )
            self.spool.terminal(
                state,
                r["status"],
                retain=r["status"] in {"failed", "cancelled"}
                or bool(state.get("unknown")),
            )
            return None
        require(
            isinstance(job, dict)
            and set(job)
            == {
                "id",
                "kind",
                "planId",
                "planRoot",
                "leaseToken",
                "leaseUntil",
                "deadline",
                "inputUrl",
            }
            and job["id"] == r.get("jobId")
            and job["kind"] == "market_acquire",
            "MARKET_CLAIM",
            "Invalid market claim",
        )
        for key in ["id", "planId", "leaseToken"]:
            identifier(job[key])
        digest(job["planRoot"])
        timestamp(job["deadline"])
        timestamp(job["leaseUntil"])
        if state.get("job"):
            require(
                all(
                    job[k] == state["job"][k]
                    for k in [
                        "id",
                        "kind",
                        "planId",
                        "planRoot",
                        "leaseToken",
                        "deadline",
                        "inputUrl",
                    ]
                ),
                "MARKET_CLAIM",
                "Recovered job identity changed",
            )
        return self.spool.save(
            {
                **state,
                "job": job,
                "phase": state["phase"] if state.get("job") else "claimed",
            }
        )

    def _publish(self, state, m, deadline, monitor):
        job = state["job"]
        root = sha(encode(m))
        ack = self.client.post(
            f"jobs/{job['id']}/publication",
            {"leaseToken": job["leaseToken"], "manifest": m},
            deadline=deadline,
        )
        require(
            ack.get("manifestSha256") == root
            and isinstance(ack.get("missing"), dict)
            and set(ack["missing"]) == set(m["collections"]),
            "MARKET_PUBLICATION",
            "Publication identity differs",
        )
        for name, c in m["collections"].items():
            missing = ack["missing"][name]
            require(
                isinstance(missing, list)
                and all(type(i) is int for i in missing)
                and len(set(missing)) == len(missing)
                and set(missing) <= set(range(len(c["chunks"]))),
                "MARKET_PUBLICATION",
                "Invalid missing chunks",
            )
            for i in missing:
                self.check(monitor)
                p = c["chunks"][i]
                raw = self.spool.chunk(job, name, p)
                answer = self.client.put_chunk(
                    job, root, name, i, raw, deadline=deadline
                )
                require(
                    answer
                    == {
                        "ok": True,
                        "collection": name,
                        "ordinal": i,
                        "sha256": p["sha256"],
                        "byteLength": p["byteLength"],
                    },
                    "MARKET_PUBLICATION",
                    "Chunk ACK differs",
                )
        self.check(monitor)
        answer = self.client.post(
            f"jobs/{job['id']}/complete",
            {"leaseToken": job["leaseToken"], "manifestSha256": root},
            deadline=deadline,
        )
        r = answer.get("result", {})
        ref = r.get("marketDatasetRef", {})
        require(
            answer.get("job", {}).get("id") == job["id"]
            and answer["job"]["status"] == "completed"
            and ref.get("datasetRoot") == root
            and ref.get("format") == "atlas.quant.market_dataset"
            and ref.get("version") == 1
            and r.get("universeScopeRef") == m["universeScopeRef"],
            "MARKET_COMPLETION",
            "Dataset completion identity differs",
        )
        identifier(ref.get("datasetId"))
        state["phase"] = "awaiting_terminal"
        self.spool.save(state)

    def once(self):
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
            7200, max(0, timestamp(job["deadline"]) - time.time())
        )
        try:
            with self.monitor_factory(self.config, job, deadline) as monitor:
                if state["phase"] == "failing":
                    raise RunnerError(state["error"]["code"], state["error"]["message"])
                meta = self.spool.metadata(job)
                if meta is None:
                    meta = self.client.input(job, deadline=deadline)
                    self.spool.metadata(job, meta)
                plan = validate_plan(meta, job, self.config["authorization_scope"])
                m = self.spool.publication(job)
                if m is None:
                    monitor.phase = "fetching_sources"
                    remaining = RAW_BYTES
                    for request in plan["requests"]:
                        self.check(monitor)
                        # Fixed serial admission: restarts retain last send spacing.
                        if self.spool.receipt(job, request["requestKey"]) is None:
                            while time.time() < state.get("nextRequestAt", 0):
                                self.check(monitor)
                                time.sleep(0.1)
                            state["nextRequestAt"] = time.time() + 1
                            self.spool.save(state)
                        receipt = self._receipt(
                            state,
                            {**request, "cache": {"status": "missing"}},
                            remaining,
                            deadline,
                            monitor,
                        )
                        require(
                            receipt["byteLength"] <= request["responseBytes"],
                            "MARKET_RAW_BUDGET",
                            "Per-request byte budget exceeded",
                        )
                        remaining -= receipt["byteLength"]
                        require(
                            remaining >= 0,
                            "MARKET_RAW_BUDGET",
                            "Raw parent budget exceeded",
                        )
                        table(request, receipt)
                        if request["apiName"] == "trade_cal":
                            calendar(request, receipt)
                    monitor.phase = "normalizing"
                    self.check(monitor)
                    from ..compute_slot import compute_slot

                    with compute_slot(
                        self.config.get("compute_lock_path"),
                        deadline=deadline,
                        check=lambda: self.check(monitor),
                    ):
                        m = build_publication(
                            job,
                            plan,
                            lambda r: self.spool.receipt(job, r["requestKey"]),
                            lambda c, i, raw: (
                                self.check(monitor),
                                self.spool.write_chunk(job, c, i, raw),
                            ),
                        )
                    self.spool.save_manifest(job, m)
                    state["phase"] = "publishing"
                    self.spool.save(state)
                monitor.phase = "writing_evidence"
                self._publish(state, m, deadline, monitor)
        except Exception as error:
            if getattr(error, "code", "") in CONTROL_ERRORS:
                raise
            state["phase"] = "failing"
            state["error"] = safe_error(error)
            self.spool.save(state)
            ack = self.client.post(
                f"jobs/{job['id']}/fail",
                {"leaseToken": job["leaseToken"], "error": state["error"]},
            )
            require(
                ack.get("job", {}).get("id") == job["id"]
                and ack["job"]["status"] in {"failed", "cancelled"},
                "MARKET_TERMINAL",
                "Failure ACK differs",
            )
        self.claim(state)
        return True


def serve(config, *, once=False, stop_requested=None, client_factory=MarketClient):
    stop_requested = stop_requested or (lambda: False)
    consumer = MarketConsumer(
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
                require(
                    ready.get("ok") is True and type(ready.get("canClaim")) is bool,
                    "MARKET_HEARTBEAT",
                    "Explicit queue gate required",
                )
                if not stop_requested() and (
                    consumer.spool.state() or ready["canClaim"]
                ):
                    consumer.once()
            except RunnerError as error:
                print(encode(safe_error(error)).decode(), flush=True)
                if error.code not in CONTROL_ERRORS:
                    raise
            if once:
                return
            until = time.monotonic() + config.get("poll_seconds", 10)
            while not stop_requested() and time.monotonic() < until:
                time.sleep(0.1)
