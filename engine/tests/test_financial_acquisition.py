"""Explicit synthetic acquisition fixtures; never contact a provider or public API."""

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import uuid

import pytest
import requests

from atlas_quant.financial_acquisition.client import AcquisitionClient
from atlas_quant.financial_acquisition.normalize import (
    build_publication,
    calendar_from_receipt,
    normalize_statement,
    table,
)
from atlas_quant.financial_acquisition.planner import (
    create_plan,
    validate_execution,
    validate_request,
)
from atlas_quant.financial_acquisition.protocol import PROFILE, encode, sha
from atlas_quant.financial_acquisition.provider import (
    RawResponse,
    RawTushareAdapter,
    OutcomeUnknown,
)
from atlas_quant.financial_acquisition.service import AcquisitionConsumer, execute_one
from atlas_quant.financial_acquisition.spool import AcquisitionSpool
from atlas_quant.financial_statements.package import decode_package
from atlas_quant.financial_statements.package import prepare_package
from atlas_quant.runner import RunnerError

SCOPE = "test-fixture-entitlement-v1"
LIMITS = {
    "responseBytes": 4194304,
    "totalBytes": 16777216,
    "packageBytes": 25165824,
    "calendarBytes": 262144,
    "chunkBytes": 524288,
    "manifestBytes": 131072,
}


def config(tmp_path):
    return {
        "api_base": "https://queue.example.test/quant/api",
        "runner_secret": "x" * 40,
        "delivery_dir": str(tmp_path / "research"),
        "acquisition_delivery_dir": str(tmp_path / "acquire"),
        "authorization_scope": SCOPE,
        "acquisition_enabled": True,
        "allow_acquisition_fixtures": True,
        "counter_path": str(tmp_path / "calls.jsonl"),
    }


def input_value():
    return {
        "requestId": str(uuid.uuid4()),
        "profile": PROFILE["id"],
        "name": "Explicit SYNTHETIC acquisition",
        "symbols": ["600001.SH"],
        "period": "20241231",
        "announcementStart": "20250303",
        "start": "20250303",
        "end": "20250305",
        "selectedStateIds": ["model_fin_cash_asset_share"],
    }


def planned():
    p = create_plan(input_value(), SCOPE)
    p.pop("planRoot")
    p["requests"] = [{**r, "cache": {"status": "missing"}} for r in p["requests"]]
    p["budget"].update(cachedRequests=0, newRequests=len(p["requests"]))
    p["blockedReasons"] = []
    p["planRoot"] = sha(encode(p))
    execution = {
        **deepcopy(p),
        "executionPlanRoot": sha(
            encode({"planRoot": p["planRoot"], "requests": p["requests"]})
        ),
    }
    job = {
        "id": str(uuid.uuid4()),
        "kind": "financial_acquire",
        "planId": str(uuid.uuid4()),
        "leaseToken": str(uuid.uuid4()),
        "leaseUntil": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
        "deadline": (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat(),
    }
    job["inputUrl"] = "/quant/api/runner/financial-acquire/jobs/" + job["id"] + "/input"
    return job, {
        "job": {k: job[k] for k in ("id", "kind", "planId")},
        "reviewedPlan": p,
        "executionPlan": execution,
        "limits": dict(LIMITS),
    }


def raw_table(request):
    fields = request["fields"]
    if request["endpoint"] == "trade_cal":
        rows = [
            {
                "exchange": "SSE",
                "cal_date": f"2025030{n}",
                "is_open": 1,
                "pretrade_date": "20250228" if n == 3 else f"2025030{n-1}",
            }
            for n in (3, 4, 5)
        ]
    else:
        row = {f: None for f in fields}
        row.update(
            ts_code=request["params"]["ts_code"],
            ann_date="20250303",
            f_ann_date="20250303",
            end_date="20241231",
            report_type="1",
            comp_type="1",
            update_flag="0",
        )
        row.update(
            {
                f: 100.0
                for f in fields
                if f not in row
                or f
                not in {
                    "ts_code",
                    "ann_date",
                    "f_ann_date",
                    "end_date",
                    "report_type",
                    "comp_type",
                    "update_flag",
                }
            }
        )
        rows = [row]
    # Deliberately noncanonical whitespace: preserve raw bytes unchanged.
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {"fields": fields, "items": [[r[f] for f in fields] for r in rows]},
        },
        indent=2,
    ).encode()


def receipt(request, **changes):
    raw = raw_table(request)
    return {
        "receiptId": str(uuid.uuid4()),
        "requestKey": request["requestKey"],
        "raw": raw,
        "sha256": sha(raw),
        "byteLength": len(raw),
        "httpStatus": 200,
        "retrievedAt": "2026-10-08T01:00:00+00:00",
        "sourceKind": "fixture",
        **changes,
    }


class FixtureProvider:
    def __init__(self, cfg):
        self.cfg = cfg

    def call_once(self, request, *, deadline=None, maximum_bytes=None):
        with open(self.cfg["counter_path"], "a") as f:
            f.write(request["requestKey"] + "\n")
        if self.cfg.get("fixture_crash"):
            raise OutcomeUnknown()
        raw = raw_table(request)
        if self.cfg.get("fixture_invalid"):
            raw = b'{"code":-1,"msg":"SYNTHETIC failure"}'
        return RawResponse(raw, 200, "2026-10-08T01:00:00+00:00", "fixture")


class Monitor:
    def __init__(self, *args):
        self.phase = "checking_plan"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def check(self):
        pass


class FakeClient:
    """Control transport fixture, including committed-then-lost ACK failures."""

    def __init__(self, job, meta):
        self.job, self.meta = job, meta
        self.status = "running"
        self.request_id = None
        self.intents, self.receipts, self.parts = {}, {}, {}
        self.lost = set()
        self.fail_once = set()
        self.manifest = None
        self.inputs = 0

    def maybe_lose(self, operation):
        if operation in self.fail_once and operation not in self.lost:
            self.lost.add(operation)
            raise RunnerError("ACQUISITION_NETWORK", "Injected transport loss")

    def post(self, route, payload, **kwargs):
        if route == "claim":
            if self.request_id is None:
                self.request_id = payload["requestId"]
            assert payload["requestId"] == self.request_id
            self.maybe_lose("claim")
            return {
                "claim": {
                    "requestId": self.request_id,
                    "status": self.status,
                    "jobId": self.job["id"],
                },
                "job": (
                    self.job if self.status in ("running", "cancel_requested") else None
                ),
            }
        key = route.split("/")[-2]
        if route.endswith("/begin"):
            if key in self.receipts:
                return {
                    "state": "received",
                    "maySend": False,
                    "receiptId": self.receipts[key]["receiptId"],
                }
            new = key not in self.intents
            self.intents.setdefault(key, payload["attemptId"])
            self.maybe_lose("begin")
            return {"state": "intent", "attemptId": self.intents[key], "maySend": new}
        if route.endswith("/unknown"):
            self.intents[key] = "unknown"
            return {"state": "outcome_unknown", "manualReviewRequired": True}
        if route.endswith("/fail"):
            self.status = "cancelled" if self.status == "cancel_requested" else "failed"
            self.error = payload["error"]
            self.maybe_lose("fail")
            return {"job": {"id": self.job["id"], "status": self.status}}
        if route.endswith("/publication"):
            if self.manifest is not None:
                assert self.manifest == payload["manifest"]
            self.manifest = payload["manifest"]
            self.maybe_lose("publication")
            return {
                "manifestSha256": sha(encode(self.manifest)),
                "missing": {
                    name: [
                        x["ordinal"]
                        for x in self.manifest[name]["chunks"]
                        if (name, x["ordinal"]) not in self.parts
                    ]
                    for name in ("package", "calendar")
                },
            }
        if route.endswith("/complete"):
            self.status = "completed"
            self.maybe_lose("complete")
            return {
                "job": {"id": self.job["id"], "status": self.status},
                "result": {
                    "inputId": str(uuid.uuid4()),
                    "calendarRef": str(uuid.uuid4()),
                    "inputRoot": self.manifest["inputRoot"],
                    "packRoot": self.manifest["packRoot"],
                    "researchBinding": False,
                },
            }
        raise AssertionError(route)

    def input(self, job, **kwargs):
        self.inputs += 1
        return deepcopy(self.meta)

    def put_receipt(self, job, request, item, attempt, **kwargs):
        key = request["requestKey"]
        assert self.intents[key] == attempt
        if key not in self.receipts:
            self.receipts[key] = {**item, "receiptId": str(uuid.uuid4())}
        else:
            assert self.receipts[key]["raw"] == item["raw"]
        self.maybe_lose("receipt")
        return {k: v for k, v in self.receipts[key].items() if k != "raw"}

    def get_receipt(self, job, request, **kwargs):
        return deepcopy(self.receipts[request["requestKey"]])

    def put_chunk(self, job, root, name, ordinal, raw, **kwargs):
        assert root == sha(encode(self.manifest))
        self.parts[name, ordinal] = raw
        self.maybe_lose("chunk")
        return {"ok": True}


def consumer(tmp_path, **changes):
    cfg = config(tmp_path)
    cfg.update(changes)
    job, meta = planned()
    client = FakeClient(job, meta)
    c = AcquisitionConsumer(
        cfg, client, provider_factory=FixtureProvider, monitor_factory=Monitor
    )
    return c, client, cfg


def call_count(cfg):
    path = Path(cfg["counter_path"])
    return len(path.read_text().splitlines()) if path.exists() else 0


def test_real_spawn_core_publication_is_uploaded_without_units(tmp_path):
    c, client, cfg = consumer(tmp_path)
    with c.spool.locked():
        c.once()
    assert (
        call_count(cfg) == 4
        and client.status == "completed"
        and c.spool.state() is None
    )
    raw = b"".join(
        client.parts["package", d["ordinal"]]
        for d in client.manifest["package"]["chunks"]
    )
    package = decode_package(raw)
    assert package["packRoot"] == client.manifest["packRoot"]
    assert package["unitPolicy"] == "verified_only"
    result = prepare_package(package)
    assert (
        result.provenance["preparedRoot"]
        and result.panel["model_fin_cash_asset_share"].isna().all()
    )
    assert all(r["raw"].startswith(b"{\n") for r in client.receipts.values())


@pytest.mark.parametrize(
    "lost", ["claim", "receipt", "publication", "chunk", "complete"]
)
def test_lost_control_ack_resumes_without_repeating_provider(tmp_path, lost):
    c, client, cfg = consumer(tmp_path)
    client.fail_once.add(lost)
    with c.spool.locked():
        with pytest.raises(RunnerError, match="Injected"):
            c.once()
    restarted = AcquisitionConsumer(
        cfg, client, provider_factory=FixtureProvider, monitor_factory=Monitor
    )
    with restarted.spool.locked():
        restarted.once()
    assert (
        client.status == "completed"
        and call_count(cfg) == 4
        and restarted.spool.state() is None
    )
    assert client.inputs == 1


def test_lost_begin_ack_is_unknown_without_any_provider_call(tmp_path):
    c, client, cfg = consumer(tmp_path)
    client.fail_once.add("begin")
    with c.spool.locked():
        with pytest.raises(RunnerError):
            c.once()
        c.once()
    assert call_count(cfg) == 0 and client.status == "failed"
    assert "unknown" in client.intents.values()
    assert list(c.spool.root.glob("*-review.enc"))


def test_unknown_child_does_not_repeat_and_preserves_review(tmp_path):
    c, client, cfg = consumer(tmp_path, fixture_crash=True)
    with c.spool.locked():
        c.once()
    assert call_count(cfg) == 1 and client.status == "failed"
    assert client.error["code"] == "PROVIDER_OUTCOME_UNKNOWN"
    assert list(c.spool.root.glob("*-review.enc"))


def test_definite_provider_error_receipt_saved_before_failure_and_stops_plan(tmp_path):
    c, client, cfg = consumer(tmp_path, fixture_invalid=True)
    with c.spool.locked():
        c.once()
    assert call_count(cfg) == 1 and len(client.receipts) == 1
    assert (
        client.status == "failed" and client.error["code"] == "PROVIDER_RESPONSE_ERROR"
    )


def test_restart_calling_without_raw_never_calls_provider(tmp_path):
    c, client, cfg = consumer(tmp_path)
    state = c.claim(c.spool.current_or_create())
    key = client.meta["executionPlan"]["requests"][0]["requestKey"]
    state["requests"][key] = {"attemptId": str(uuid.uuid4()), "phase": "calling"}
    client.intents[key] = state["requests"][key]["attemptId"]
    c.spool.save(state)
    with c.spool.locked():
        c.once()
    assert call_count(cfg) == 0 and client.status == "failed"


def test_cache_reads_exact_responses_without_begin_or_provider(tmp_path):
    c, client, cfg = consumer(tmp_path)
    p = client.meta["reviewedPlan"]
    p.pop("planRoot")
    for r in p["requests"]:
        item = receipt(r)
        client.receipts[r["requestKey"]] = item
        r["cache"] = {
            "status": "frozen",
            **{
                k: item[k]
                for k in (
                    "receiptId",
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                )
            },
        }
    p["budget"].update(cachedRequests=4, newRequests=0)
    p["planRoot"] = sha(encode(p))
    client.meta["executionPlan"] = {
        **deepcopy(p),
        "executionPlanRoot": sha(
            encode({"planRoot": p["planRoot"], "requests": p["requests"]})
        ),
    }
    with c.spool.locked():
        c.once()
    assert call_count(cfg) == 0 and not client.intents and client.status == "completed"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: x["selection"]["symbols"].append("600002.SH"),
        lambda x: x["requests"][0]["params"].__setitem__("exchange", "SZSE"),
        lambda x: x["requests"][1]["fields"].append("arbitrary"),
        lambda x: x["requests"][1].__setitem__("authorizationScope", "other"),
        lambda x: x["budget"].__setitem__("maximumTotalBytes", 16777217),
    ],
)
def test_rehashed_plan_still_cannot_expand_fixed_scope(mutate):
    job, meta = planned()
    p = meta["reviewedPlan"]
    p.pop("planRoot")
    mutate(p)
    p["planRoot"] = sha(encode(p))
    meta["executionPlan"] = {
        **deepcopy(p),
        "executionPlanRoot": sha(
            encode({"planRoot": p["planRoot"], "requests": p["requests"]})
        ),
    }
    with pytest.raises((RunnerError, ValueError)):
        validate_execution(meta, job, SCOPE, allow_fixtures=True)


def test_wrong_auth_and_fixture_cache_rejected_before_io():
    job, meta = planned()
    with pytest.raises(RunnerError):
        validate_execution(meta, job, "wrong")
    r = meta["reviewedPlan"]["requests"][0]
    item = receipt(r)
    r["cache"] = {
        "status": "frozen",
        **{
            k: item[k]
            for k in (
                "receiptId",
                "sha256",
                "byteLength",
                "retrievedAt",
                "httpStatus",
                "sourceKind",
            )
        },
    }
    p = meta["reviewedPlan"]
    p.pop("planRoot")
    p["budget"].update(cachedRequests=1, newRequests=3)
    p["planRoot"] = sha(encode(p))
    meta["executionPlan"] = {
        **deepcopy(p),
        "executionPlanRoot": sha(
            encode({"planRoot": p["planRoot"], "requests": p["requests"]})
        ),
    }
    with pytest.raises(RunnerError) as e:
        validate_execution(meta, job, SCOPE)
    assert e.value.code == "ACQUISITION_SOURCE_KIND"


def test_independent_spool_auth_permissions_and_lease_state(tmp_path):
    cfg = config(tmp_path)
    spool = AcquisitionSpool(cfg)
    spool.current_or_create()
    assert (spool.root / "current.enc").stat().st_mode & 0o077 == 0
    assert b"requestId" not in (spool.root / "current.enc").read_bytes()
    with pytest.raises(RunnerError):
        AcquisitionSpool({**cfg, "authorization_scope": "other"}).state()
    with spool.locked():
        with pytest.raises(RunnerError):
            with AcquisitionSpool(cfg).locked():
                pass
    with pytest.raises(RunnerError):
        AcquisitionSpool({**cfg, "acquisition_delivery_dir": cfg["delivery_dir"]})


class Response:
    status_code = 200

    def __init__(self, raw=b"raw", headers=None):
        self.raw = raw
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, n):
        yield self.raw


class Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.response


def provider(session):
    return RawTushareAdapter(
        {"authorization_scope": SCOPE, "tushare_token": "secret-never-log" * 3}, session
    )


def test_adapter_one_http_attempt_no_redirect_and_raw_identity():
    session = Session(Response(b' { "x": 1 }\n'))
    request = create_plan(input_value(), SCOPE)["requests"][0]
    result = provider(session).call_once(request)
    assert result.raw == b' { "x": 1 }\n' and len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][0][1] == "https://api.tushare.pro"


@pytest.mark.parametrize(
    "response,error",
    [
        (None, requests.Timeout("SECRET")),
        (Response(headers={"Content-Encoding": "gzip"}), None),
        (Response(headers={"Content-Length": "4"}), None),
        (Response(headers={"Content-Length": str(5 * 1024 * 1024)}), None),
    ],
)
def test_unknown_adapter_outcomes_are_never_retried(response, error):
    session = Session(response, error)
    request = create_plan(input_value(), SCOPE)["requests"][0]
    with pytest.raises(OutcomeUnknown) as e:
        provider(session).call_once(request)
    assert len(session.calls) == 1 and "SECRET" not in str(e.value)


def test_adapter_remaining_parent_budget_is_hard_bound():
    session = Session(Response(b"1234"))
    with pytest.raises(OutcomeUnknown):
        provider(session).call_once(
            create_plan(input_value(), SCOPE)["requests"][0], maximum_bytes=3
        )
    assert len(session.calls) == 1


def test_calendar_missing_day_and_full_statement_response_fail_closed():
    job, meta = planned()
    request = meta["executionPlan"]["requests"][0]
    r = receipt(request)
    body = json.loads(r["raw"])
    body["data"]["items"].pop()
    raw = encode(body)
    with pytest.raises(RunnerError):
        calendar_from_receipt(
            request, {**r, "raw": raw, "sha256": sha(raw), "byteLength": len(raw)}
        )
    request = meta["executionPlan"]["requests"][1]
    r = receipt(request)
    body = json.loads(r["raw"])
    body["data"]["items"] *= 1000
    raw = encode(body)
    with pytest.raises(RunnerError) as e:
        normalize_statement(
            request, {**r, "raw": raw, "sha256": sha(raw), "byteLength": len(raw)}
        )
    assert e.value.code == "PROVIDER_EMPTY_OR_TRUNCATED"


def test_mixed_venue_profile_and_uncertain_calendar_not_inferred():
    v = input_value()
    v["symbols"] = ["600001.SH", "000001.SZ"]
    with pytest.raises(RunnerError) as e:
        create_plan(v, SCOPE)
    assert e.value.code == "MULTI_EXCHANGE_PROFILE_UNAVAILABLE"


def test_client_receipt_hash_cache_and_source_kind_pin(tmp_path):
    job, meta = planned()
    r = meta["executionPlan"]["requests"][0]
    item = receipt(r)
    descriptor = {k: v for k, v in item.items() if k != "raw"}
    header = base64.urlsafe_b64encode(encode(descriptor)).decode().rstrip("=")
    session = Session(Response(item["raw"], {"X-Acquisition-Receipt": header}))
    client = AcquisitionClient(config(tmp_path), session)
    assert client.get_receipt(job, r)["raw"] == item["raw"]
    session.response.raw += b" "
    with pytest.raises(RunnerError):
        client.get_receipt(job, r)
    session.response.raw = item["raw"]
    client.allow_fixtures = False
    with pytest.raises(RunnerError):
        client.get_receipt(job, r)


def test_python_matches_fixed_javascript_planner_fixture():
    fixture = json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("acquisition-plan-v1.json")
        .read_text()
    )
    assert (
        create_plan(fixture["input"], fixture["scope"], today=fixture["today"])
        == fixture["plan"]
    )


def test_restart_after_raw_commit_before_upload_does_not_repeat_call(tmp_path):
    c, client, cfg = consumer(tmp_path)
    state = c.claim(c.spool.current_or_create())
    request = client.meta["executionPlan"]["requests"][0]
    key = request["requestKey"]
    state["requests"][key] = {"attemptId": str(uuid.uuid4()), "phase": "calling"}
    client.intents[key] = state["requests"][key]["attemptId"]
    c.spool.save(state)
    item = receipt(request)
    item.pop("receiptId")
    c.spool.save_receipt(state["job"], key, item)
    with c.spool.locked():
        c.once()
    assert client.status == "completed" and call_count(cfg) == 3
    assert client.receipts[key]["raw"] == item["raw"]


def test_receipt_cache_hit_at_begin_uses_exact_get_and_no_provider(tmp_path):
    c, client, cfg = consumer(tmp_path)
    request = client.meta["executionPlan"]["requests"][0]
    client.receipts[request["requestKey"]] = receipt(request)
    with c.spool.locked():
        c.once()
    assert client.status == "completed" and call_count(cfg) == 3


class SlowFixtureProvider(FixtureProvider):
    def call_once(self, request, **kwargs):
        result = super().call_once(request, **kwargs)
        time.sleep(3)
        return result


def test_child_wallclock_deadline_kills_without_response_or_second_call(tmp_path):
    cfg = config(tmp_path)
    spool = AcquisitionSpool(cfg)
    job, meta = planned()
    request = {
        k: v for k, v in meta["executionPlan"]["requests"][0].items() if k != "cache"
    }
    with pytest.raises(OutcomeUnknown):
        execute_one(
            cfg,
            spool,
            job,
            request,
            4194304,
            time.monotonic() + 1,
            lambda: None,
            SlowFixtureProvider,
        )
    assert call_count(cfg) == 1 and spool.receipt(job, request["requestKey"]) is None
    import multiprocessing

    assert not multiprocessing.active_children()


def test_cancel_during_child_keeps_unknown_and_never_resends(tmp_path):
    cfg = config(tmp_path)
    spool = AcquisitionSpool(cfg)
    job, meta = planned()
    request = {
        k: v for k, v in meta["executionPlan"]["requests"][0].items() if k != "cache"
    }

    def cancelled():
        if call_count(cfg):
            raise RunnerError("ACQUISITION_CANCELLED", "fixture cancellation")

    with pytest.raises(RunnerError):
        execute_one(
            cfg,
            spool,
            job,
            request,
            4194304,
            time.monotonic() + 10,
            cancelled,
            SlowFixtureProvider,
        )
    assert call_count(cfg) == 1 and spool.receipt(job, request["requestKey"]) is None
    import multiprocessing

    assert not multiprocessing.active_children()


def test_production_consumer_rejects_fixture_cache_before_provider(tmp_path):
    c, client, cfg = consumer(tmp_path, allow_acquisition_fixtures=False)
    p = client.meta["reviewedPlan"]
    p.pop("planRoot")
    r = p["requests"][0]
    item = receipt(r)
    r["cache"] = {
        "status": "frozen",
        **{
            k: item[k]
            for k in (
                "receiptId",
                "sha256",
                "byteLength",
                "httpStatus",
                "retrievedAt",
                "sourceKind",
            )
        },
    }
    p["budget"].update(cachedRequests=1, newRequests=3)
    p["planRoot"] = sha(encode(p))
    client.meta["executionPlan"] = {
        **deepcopy(p),
        "executionPlanRoot": sha(
            encode({"planRoot": p["planRoot"], "requests": p["requests"]})
        ),
    }
    with c.spool.locked():
        c.once()
    assert (
        call_count(cfg) == 0
        and client.status == "failed"
        and client.error["code"] == "ACQUISITION_SOURCE_KIND"
    )


def test_fixed_request_adapter_rejects_rehashed_pagination_before_network():
    request = create_plan(input_value(), SCOPE)["requests"][1]
    request["params"]["offset"] = 100
    request["requestKey"] = sha(
        encode({k: v for k, v in request.items() if k != "requestKey"})
    )
    session = Session(Response())
    with pytest.raises(RunnerError):
        provider(session).call_once(request)
    assert not session.calls


def test_aggregate_limit_cannot_be_lifted_by_rehashed_server_plan():
    job, meta = planned()
    meta["limits"]["totalBytes"] *= 2
    with pytest.raises(RunnerError) as e:
        validate_execution(meta, job, SCOPE)
    assert e.value.code == "ACQUISITION_LIMITS"


def test_raw_spool_tampering_fails_before_normalization(tmp_path):
    spool = AcquisitionSpool(config(tmp_path))
    job, meta = planned()
    r = meta["executionPlan"]["requests"][0]
    spool.save_receipt(job, r["requestKey"], receipt(r))
    path = next(spool.root.glob("*.raw.enc"))
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    with pytest.raises(RunnerError) as e:
        spool.receipt(job, r["requestKey"])
    assert e.value.code == "ACQUISITION_SPOOL_INTEGRITY"
