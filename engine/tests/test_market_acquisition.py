"""Pure market preparation and recovery fixtures; never opens provider sockets."""

import copy
from datetime import timedelta
import json
from pathlib import Path
import time
import uuid
import pytest
from atlas_quant.market_acquisition.protocol import *
from atlas_quant.market_acquisition.normalize import table, calendar, build_publication
from atlas_quant.market_acquisition.spool import MarketSpool
from atlas_quant.market_acquisition.provider import RawMarketAdapter, OutcomeUnknown
from atlas_quant.market_acquisition.service import MarketConsumer
from atlas_quant.runner import RunnerError

ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((ROOT / "contracts/fixtures/market-plan-v1.json").read_text())
JOB = {
    "id": str(uuid.uuid4()),
    "kind": "market_acquire",
    "planId": str(uuid.uuid4()),
    "planRoot": PLAN["planRoot"],
    "leaseToken": str(uuid.uuid4()),
    "leaseUntil": "2099-01-01T00:00:00Z",
    "deadline": "2099-01-01T00:00:00Z",
    "inputUrl": "",
}
JOB["inputUrl"] = f"/quant/api/runner/market-acquire/jobs/{JOB['id']}/input"


def metadata():
    return {
        "job": {k: JOB[k] for k in ["id", "kind", "planId"]},
        "plan": copy.deepcopy(PLAN),
        "publicationLimits": {
            "manifestBytes": MANIFEST_BYTES,
            "chunkBytes": CHUNK_BYTES,
            "chunks": MAX_CHUNKS,
            "totalBytes": DATA_BYTES,
        },
    }


def response(request):
    start, end = date(request["params"]["start_date"]), date(
        request["params"]["end_date"]
    )
    all_dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows = []
    for i, d in enumerate(all_dates):
        day = d.strftime("%Y%m%d")
        if request["apiName"] == "trade_cal":
            row = {
                "exchange": request["params"]["exchange"],
                "cal_date": day,
                "is_open": int(d.weekday() < 5),
                "pretrade_date": None,
            }
        elif d.weekday() >= 5:
            continue
        else:
            # Genuine missing session remains absent for one fixture security.
            if (
                request["params"]["ts_code"] == "600000.SH"
                and day == "20240103"
                and request["apiName"] == "daily"
            ):
                continue
            row = {
                "ts_code": request["params"]["ts_code"],
                "trade_date": day,
                "open": 10 + i,
                "high": 12 + i,
                "low": 9 + i,
                "close": 11 + i,
                "vol": 100,
                "amount": 110,
                "adj_factor": 2 if i < 5 else 4,
                "pb": None if i == 2 else 1.2,
            }
        rows.append([row[k] for k in request["fields"].split(",")])
    raw = encode(
        {"code": 0, "data": {"fields": request["fields"].split(","), "items": rows}}
    )
    return {
        "requestKey": request["requestKey"],
        "receiptId": str(uuid.uuid4()),
        "raw": raw,
        "sha256": sha(raw),
        "byteLength": len(raw),
        "httpStatus": 200,
        "retrievedAt": "2026-10-08T00:00:00Z",
        "sourceKind": "fixture",
    }


def sources():
    return {r["requestKey"]: response(r) for r in PLAN["requests"]}


def test_node_plan_contract_hash_and_complete_request_validation():
    assert validate_plan(metadata(), JOB, "SYNTHETIC_MARKET_TEST") == PLAN
    for mutate in [
        lambda p: p["requests"].pop(),
        lambda p: p["scope"]["symbols"].pop(),
        lambda p: p["requests"][2].update(responseBytes=4 * 1024 * 1024),
        lambda p: p["budget"].update(maxActualAttemptsPerRequest=2),
    ]:
        value = metadata()
        mutate(value["plan"])
        value["plan"]["planRoot"] = sha(
            encode({k: v for k, v in value["plan"].items() if k != "planRoot"})
        )
        job = {**JOB, "planRoot": value["plan"]["planRoot"]}
        with pytest.raises(RunnerError):
            validate_plan(value, job, "SYNTHETIC_MARKET_TEST")


def test_real_normalization_first_anchor_missing_masks_and_full_receipt_closure():
    receipts = sources()
    chunks = {}
    m = build_publication(
        JOB,
        PLAN,
        lambda r: receipts[r["requestKey"]],
        lambda n, i, b: chunks.__setitem__((n, i), b),
    )
    rows = [
        r
        for p in m["collections"]["rows"]["chunks"]
        for r in json.loads(chunks["rows", p["ordinal"]])
    ]
    assert (
        m["rowCount"] == 15
        and len(m["calendar"]) == 8
        and {r["ts_code"] for r in rows} == set(PLAN["scope"]["symbols"])
    )
    assert not any(
        r["ts_code"] == "600000.SH" and r["trade_date"] == "20240103" for r in rows
    )
    first = next(
        r for r in rows if r["ts_code"] == "000001.SZ" and r["trade_date"] == "20240101"
    )
    last = next(
        r for r in rows if r["ts_code"] == "000001.SZ" and r["trade_date"] == "20240110"
    )
    assert first["close"] == 11 and last["close"] == 40 and last["raw_close"] == 20
    assert (
        next(
            r
            for r in rows
            if r["ts_code"] == "000001.SZ" and r["trade_date"] == "20240103"
        )["pb"]
        is None
    )
    assert (
        m["sourceKind"] == "fixture" and m["collections"]["receipts"]["rowCount"] == 8
    )
    p = json.loads(chunks["provenance", 0])[0]
    assert p["synthetic"] is True and p["originalProviderWireAvailable"] is False


def mutate_raw(receipt, fn):
    value = json.loads(receipt["raw"])
    fn(value)
    raw = encode(value)
    return {**receipt, "raw": raw, "sha256": sha(raw), "byteLength": len(raw)}


@pytest.mark.parametrize(
    "case",
    [
        "missing_symbol",
        "missing_adjustment",
        "duplicate",
        "future",
        "wrong_exchange",
        "divergent_calendar",
        "nan",
        "wrong_fields",
        "http429",
    ],
)
def test_source_gaps_and_provider_failures_reject_entire_scope(case):
    rec = sources()
    r = next(
        r
        for r in PLAN["requests"]
        if r["apiName"]
        == (
            "trade_cal"
            if case in {"wrong_exchange", "divergent_calendar"}
            else "adj_factor" if case == "missing_adjustment" else "daily"
        )
    )
    key = r["requestKey"]

    def edit(v):
        rows = v["data"]["items"]
        f = v["data"]["fields"]
        if case == "missing_symbol":
            rows.clear()
        elif case == "missing_adjustment":
            rows.pop(0)
        elif case == "duplicate":
            rows.append(rows[0])
        elif case == "future":
            rows[0][f.index("trade_date")] = "20250101"
        elif case == "wrong_exchange":
            rows[0][f.index("exchange")] = "OTHER"
        elif case == "divergent_calendar":
            rows[0][f.index("is_open")] = 0
        elif case == "nan":
            rows[0][f.index("close")] = "NaN"
        elif case == "wrong_fields":
            f[0] = "unexpected"

    rec[key] = mutate_raw(rec[key], edit)
    if case == "http429":
        rec[key]["httpStatus"] = 429
    with pytest.raises(RunnerError):
        build_publication(JOB, PLAN, lambda r: rec[r["requestKey"]], lambda *a: None)


def config(tmp_path):
    return {
        "api_base": "http://localhost:1234/quant/api",
        "runner_secret": "s" * 32,
        "authorization_scope": "SYNTHETIC_MARKET_TEST",
        "delivery_dir": str(tmp_path / "research"),
        "market_delivery_dir": str(tmp_path / "market"),
        "market_acquisition_enabled": True,
        "allow_market_fixtures": True,
    }


def test_spool_independent_encryption_integrity_and_durable_manifest(tmp_path):
    c = config(tmp_path)
    s = MarketSpool(c)
    state = s.current_or_create()
    r = PLAN["requests"][0]
    s.save_receipt(JOB, r["requestKey"], response(r))
    assert s.receipt(JOB, r["requestKey"])["sourceKind"] == "fixture"
    assert b"SYNTHETIC" not in next(s.root.glob("*.raw.enc")).read_bytes()
    with pytest.raises(RunnerError):
        MarketSpool({**c, "authorization_scope": "another"}).state()
    data = sources()
    m = build_publication(
        JOB,
        PLAN,
        lambda r: data[r["requestKey"]],
        lambda n, i, b: s.write_chunk(JOB, n, i, b),
    )
    s.save_manifest(JOB, m)
    assert s.publication(JOB) == m
    p = m["collections"]["rows"]["chunks"][0]
    s._write(s._part(JOB, "rows", 0), b"[]", CHUNK_BYTES)
    with pytest.raises(RunnerError):
        s.chunk(JOB, "rows", p)


class Monitor:
    phase = "checking_plan"

    def __init__(self, *a):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def check(self):
        pass


class Client:
    def __init__(self):
        self.receipts = {}
        self.intents = {}
        self.status = "running"
        self.lost = None
        self.input_calls = 0

    def post(self, path, value, **kwargs):
        if path == "claim":
            return {
                "claim": {
                    "requestId": value["requestId"],
                    "status": self.status,
                    "jobId": JOB["id"],
                },
                "job": copy.deepcopy(JOB) if self.status == "running" else None,
            }
        if path.endswith("/begin"):
            key = path.split("/")[-2]
            old = self.intents.get(key)
            self.intents[key] = value["attemptId"]
            return {
                "state": "intent",
                "attemptId": value["attemptId"],
                "maySend": old is None,
            }
        if path.endswith("/unknown"):
            return {"state": "outcome_unknown", "manualReviewRequired": True}
        if path.endswith("/fail"):
            self.status = "failed"
            return {"job": {"id": JOB["id"], "status": "failed"}}
        if path.endswith("/publication"):
            m = value["manifest"]
            self.manifest = m
            return {
                "manifestSha256": sha(encode(m)),
                "missing": {
                    n: list(range(len(c["chunks"])))
                    for n, c in m["collections"].items()
                },
            }
        if path.endswith("/complete"):
            self.status = "completed"
            answer = {
                "job": {"id": JOB["id"], "status": "completed"},
                "result": {
                    "marketDatasetRef": {
                        "datasetId": str(uuid.uuid4()),
                        "datasetRoot": value["manifestSha256"],
                        "format": "atlas.quant.market_dataset",
                        "version": 1,
                    },
                    "universeScopeRef": PLAN["universeScopeRef"],
                },
            }
            if self.lost == "complete":
                self.lost = None
                raise RunnerError("ACQUISITION_NETWORK", "lost complete ack")
            return answer
        raise AssertionError(path)

    def input(self, *a, **kw):
        self.input_calls += 1
        return metadata()

    def put_receipt(self, job, r, receipt, attempt_id, **kw):
        self.receipts[r["requestKey"]] = receipt
        value = {k: v for k, v in receipt.items() if k != "raw"}
        value["receiptId"] = str(uuid.uuid4())
        if self.lost == "receipt":
            self.lost = None
            raise RunnerError("ACQUISITION_NETWORK", "lost receipt ack")
        return value

    def put_chunk(self, job, root, name, ordinal, raw, **kw):
        return {
            "ok": True,
            "collection": name,
            "ordinal": ordinal,
            "sha256": sha(raw),
            "byteLength": len(raw),
        }


def fake_executor(c, spool, job, request, remaining, deadline, check, factory):
    check()
    factory.append(request["requestKey"])
    spool.save_receipt(
        job,
        request["requestKey"],
        {k: v for k, v in response(request).items() if k != "receiptId"},
    )


@pytest.mark.parametrize("lost", [None, "receipt", "complete"])
def test_durable_ack_recovery_never_replays_provider(tmp_path, monkeypatch, lost):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    # Use a advancing wall clock only for deterministic pacing; no lease bypass.
    clock = [time.time()]

    def now():
        clock[0] += 2
        return clock[0]

    monkeypatch.setattr(time, "time", now)
    client = Client()
    client.lost = lost
    calls = []
    c = config(tmp_path)

    def consumer():
        return MarketConsumer(
            c,
            client,
            provider_factory=calls,
            executor=fake_executor,
            monitor_factory=Monitor,
        )

    if lost:
        with pytest.raises(RunnerError):
            consumer().once()
    consumer().once()
    assert (
        len(calls) == len(set(calls)) == len(PLAN["requests"])
        and client.status == "completed"
        and MarketSpool(c).state() is None
        and client.input_calls == 1
    )


def test_crash_after_begin_without_raw_is_sticky_unknown(tmp_path, monkeypatch):
    client = Client()
    calls = []
    c = config(tmp_path)
    a = MarketConsumer(
        c,
        client,
        provider_factory=calls,
        executor=fake_executor,
        monitor_factory=Monitor,
    )
    state = a.spool.current_or_create()
    state = a.claim(state)
    r = PLAN["requests"][0]
    state["requests"][r["requestKey"]] = {
        "attemptId": str(uuid.uuid4()),
        "phase": "calling",
    }
    a.spool.save(state)
    a.once()
    assert (
        not calls
        and client.status == "failed"
        and list(a.spool.root.glob("*-review.enc"))
    )


class SpawnFixtureProvider:
    """Separate-process fixture implements the real provider interface without I/O."""

    def __init__(self, _config):
        pass

    def call_once(self, request, *, deadline=None, maximum_bytes=None):
        from atlas_quant.market_acquisition.provider import RawResponse

        value = response(request)
        assert len(value["raw"]) <= maximum_bytes
        return RawResponse(value["raw"], 200, value["retrievedAt"], "fixture")


def test_real_spawn_writes_raw_to_market_encryption_domain_before_parent_parse(
    tmp_path,
):
    from atlas_quant.financial_acquisition.service import execute_one

    c = {**config(tmp_path), "allow_acquisition_fixtures": True}
    s = MarketSpool(c)
    request = PLAN["requests"][0]
    execute_one(
        c,
        s,
        JOB,
        request,
        request["responseBytes"],
        time.monotonic() + 20,
        lambda: None,
        SpawnFixtureProvider,
    )
    receipt = s.receipt(JOB, request["requestKey"])
    assert receipt["raw"] == response(request)["raw"]
    assert calendar(request, receipt) == [
        "20240101",
        "20240102",
        "20240103",
        "20240104",
        "20240105",
        "20240108",
        "20240109",
        "20240110",
    ]


def test_raw_market_adapter_sends_once_and_never_retries_unknown_or_redirect(tmp_path):
    import requests

    class Session:
        trust_env = True

        def __init__(self):
            self.calls = []

        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise requests.Timeout("no response")

    session = Session()
    adapter = RawMarketAdapter(
        {
            "authorization_scope": "SYNTHETIC_MARKET_TEST",
            "tushare_token": "test-secret-never-sent",
        },
        session=session,
    )
    with pytest.raises(OutcomeUnknown):
        adapter.call_once(PLAN["requests"][0])
    assert len(session.calls) == 1
    _, call = session.calls[0]
    assert call["allow_redirects"] is False and call["stream"] is True
    assert session.trust_env is False
