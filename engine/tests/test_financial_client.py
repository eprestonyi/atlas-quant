"""Transport adversaries: no external destination, trust shortcut or oversized read."""

import json
import uuid

import pytest

from atlas_quant.financial_runner.client import FinancialClient
from atlas_quant.financial_runner.protocol import (
    REGISTRY_BYTES,
    RunnerError,
    decode,
    encode,
    sha,
)

JOB = {
    "id": str(uuid.UUID(int=1)),
    "kind": "financial_validate",
    "inputId": str(uuid.UUID(int=2)),
    "leaseToken": str(uuid.UUID(int=3)),
}
PREFIX = "/quant/api/runner/financial/jobs/" + JOB["id"] + "/"
CONFIG = {
    "api_base": "https://example.test/quant/api",
    "runner_secret": "private-operator-token",
}


class Response:
    def __init__(self, raw, *, status=200, length=None, blocks=None):
        self.raw, self.status_code = raw, status
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.blocks = blocks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def iter_content(self, _):
        yield from self.blocks if self.blocks is not None else [self.raw]


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def inputs(source=b'{"retained":1,"retained":2}', calendar=b'{"kind":"calendar"}'):
    ref = str(uuid.UUID(int=4))
    meta = {
        "job": {k: JOB[k] for k in ("id", "kind", "inputId")},
        "source": {
            "url": PREFIX + "source",
            "sha256": sha(source),
            "byteLength": len(source),
        },
        "calendar": {
            "url": PREFIX + "registry/" + ref,
            "ref": ref,
            "sha256": sha(calendar),
            "byteLength": len(calendar),
        },
        "proofs": [],
        "operation": {},
    }
    return meta, source, calendar


def test_source_duplicate_keys_are_preserved_for_real_validator_and_lease_stays_in_header():
    meta, source, calendar = inputs()
    responses = [
        Response(encode(meta)),
        Response(source, length=len(source)),
        Response(calendar),
    ]
    session = Session(responses)
    actual = FinancialClient(CONFIG, session).inputs(JOB)
    assert actual == (meta, source, {meta["calendar"]["ref"]: calendar})
    assert all(r.closed for r in responses)
    assert len(session.calls) == 3
    for method, url, options in session.calls:
        assert method == "GET" and url.startswith(
            CONFIG["api_base"] + "/runner/financial/"
        )
        assert options["allow_redirects"] is False
        assert options["headers"]["X-Financial-Lease"] == JOB["leaseToken"]
        assert options["data"] is None
        assert CONFIG["runner_secret"] not in url


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":1e999}',
        b'{"a":-1e999}',
        b'"\xff"',
    ],
)
def test_metadata_decoder_rejects_duplicate_nonfinite_and_invalid_utf8(raw):
    with pytest.raises(RunnerError, match="JSON"):
        decode(raw, limit=100)


@pytest.mark.parametrize(
    "url",
    [
        "https://external.test/secrets",
        "//external.test/source",
        PREFIX + "source?token=private",
        PREFIX + "../source",
        PREFIX.replace(JOB["id"], str(uuid.UUID(int=19))) + "source",
    ],
)
def test_server_reference_cannot_move_operator_credentials_or_read_another_job(url):
    meta, _, _ = inputs()
    meta["source"]["url"] = url
    session = Session([Response(encode(meta))])
    with pytest.raises(RunnerError) as err:
        FinancialClient(CONFIG, session).inputs(JOB)
    assert err.value.code == "FINANCIAL_ROUTE"
    assert len(session.calls) == 1


@pytest.mark.parametrize("tamper", ["duplicate", "oversized", "aggregate", "identity"])
def test_all_reference_budgets_checked_before_source_download(tamper):
    meta, _, _ = inputs()
    if tamper == "duplicate":
        meta["proofs"] = [dict(meta["calendar"])]
    elif tamper == "oversized":
        meta["calendar"]["byteLength"] = REGISTRY_BYTES + 1
    elif tamper == "identity":
        meta["job"]["inputId"] = str(uuid.UUID(int=29))
    else:
        meta["calendar"]["byteLength"] = REGISTRY_BYTES
        for i in range(128):
            ref = str(uuid.UUID(int=i + 100))
            meta["proofs"].append(
                {
                    "ref": ref,
                    "url": PREFIX + "registry/" + ref,
                    "byteLength": REGISTRY_BYTES,
                    "sha256": "a" * 64,
                }
            )
    session = Session([Response(encode(meta))])
    with pytest.raises(RunnerError):
        FinancialClient(CONFIG, session).inputs(JOB)
    assert len(session.calls) == 1


@pytest.mark.parametrize("case", ["hash", "short", "long_header", "stream_overflow"])
def test_corrupt_or_truncated_source_never_reaches_computation(case):
    meta, source, _ = inputs()
    if case == "hash":
        response = Response(b"x" * len(source))
    elif case == "short":
        response = Response(source[:-1], length=len(source))
    elif case == "long_header":
        response = Response(source, length=len(source) + 1)
    else:
        response = Response(b"", blocks=[source, b"x"])
    session = Session([Response(encode(meta)), response])
    with pytest.raises(RunnerError):
        FinancialClient(CONFIG, session).inputs(JOB)
    assert len(session.calls) == 2 and response.closed


def test_redirect_never_followed_or_retried_and_error_body_is_not_exposed():
    response = Response(b"upstream-secret", status=302)
    session = Session([response])
    with pytest.raises(RunnerError) as err:
        FinancialClient(CONFIG, session).post("claim", {"requestId": JOB["id"]})
    assert err.value.http_status == 302
    assert "upstream-secret" not in str(err.value)
    assert len(session.calls) == 1 and response.closed


def test_expired_deadline_performs_no_request():
    session = Session([])
    with pytest.raises(RunnerError) as err:
        FinancialClient(CONFIG, session).post("claim", {}, deadline=0)
    assert err.value.code == "FINANCIAL_DEADLINE"
    assert session.calls == []


def test_upload_sends_exact_bytes_and_no_implicit_retry():
    raw = b'[{"value":null}]'
    session = Session([Response(b'{"ok":true}')])
    result = FinancialClient(CONFIG, session).upload(
        str(uuid.UUID(int=8)), "f" * 64, "events", 0, raw, JOB
    )
    assert result == {"ok": True}
    method, url, options = session.calls[0]
    assert method == "PUT" and options["data"] == raw
    assert url.endswith("/chunks/events/0?manifestSha256=" + "f" * 64)
    assert options["headers"]["X-Financial-Lease"] == JOB["leaseToken"]
