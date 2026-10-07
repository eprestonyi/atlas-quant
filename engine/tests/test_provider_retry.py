"""Bounded retries of idempotent provider reads, never whole research jobs."""
import json

import pytest
import requests

from atlas_quant.provider import DATASETS, ProviderError, TushareClient
from atlas_quant.runner import _safe_error


PARAMS = {"exchange": "SSE", "start_date": "20250929", "end_date": "20250930"}
SECRET = "do-not-echo-private-provider-credential-or-body"
SUCCESS = {"code": 0, "data": {"fields": DATASETS["trade_cal"].split(","),
                                 "items": [["SSE", "20250930", 1, "20250929"]]}}


class Response:
    def __init__(self, status=200, body=SUCCESS, stream_error=None):
        self.status_code = status
        self.body = body
        self.stream_error = stream_error
        self.closed = False
        self.body_reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def iter_content(self, _):
        self.body_reads += 1
        if self.stream_error:
            yield b'{"code":'
            raise self.stream_error
        yield self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()


class ScriptedSession:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        response = self.responses[len(self.requests) - 1]
        if isinstance(response, Exception):
            raise response
        return response


def client_for(responses):
    waits = []
    session = ScriptedSession(responses)
    client = TushareClient(None, proxy_url="https://atlas-aletheia.com/internal/provider",
                          service_token=SECRET, session=session, wait=waits.append)
    return client, session, waits


def test_transient_http_retries_close_responses_and_preserve_identical_read():
    responses = [Response(502, {"msg": SECRET}), Response(503, {"msg": SECRET}), Response()]
    client, session, waits = client_for(responses)
    frame = client.call("trade_cal", PARAMS)
    assert len(frame) == 1
    assert client.calls == len(session.requests) == 3 and waits == [1., 2.]
    assert all(r.closed for r in responses)
    assert [r.body_reads for r in responses] == [0, 0, 1]
    assert all(request == session.requests[0] for request in session.requests)
    assert session.requests[0][1]["allow_redirects"] is False
    assert "token" not in session.requests[0][1]["json"]


def test_exhausted_retries_keep_safe_final_http_status_and_attempt_count():
    client, session, waits = client_for([Response(502, SECRET), Response(503, SECRET), Response(504, SECRET)])
    with pytest.raises(ProviderError) as caught:
        client.call("trade_cal", PARAMS)
    error = caught.value
    assert error.code == "TUSHARE_HTTP_ERROR" and error.http_status == 504 and error.attempts == 3
    safe = json.dumps(_safe_error(error), ensure_ascii=False)
    assert "HTTP 504" in safe and "3 次" in safe
    assert SECRET not in safe and "https://" not in safe
    assert client.calls == len(session.requests) == 3 and waits == [1., 2.]


@pytest.mark.parametrize("status,code", [(401, "TUSHARE_PERMISSION"), (403, "TUSHARE_PERMISSION"),
                                        (429, "TUSHARE_RATE_LIMIT"), (400, "TUSHARE_HTTP_ERROR"),
                                        (302, "TUSHARE_HTTP_ERROR"), (500, "TUSHARE_HTTP_ERROR")])
def test_permission_rate_limit_redirect_and_other_http_errors_do_not_retry(status, code):
    client, session, waits = client_for([Response(status, {"msg": SECRET}), Response()])
    with pytest.raises(ProviderError) as caught:
        client.call("trade_cal", PARAMS)
    assert caught.value.code == code and caught.value.http_status == status
    assert client.calls == len(session.requests) == 1 and waits == []
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("failure", [requests.Timeout(SECRET), requests.ConnectionError(SECRET),
                                     Response(stream_error=requests.exceptions.ChunkedEncodingError(SECRET))])
def test_transient_network_failure_retries_only_the_same_read(failure):
    client, session, waits = client_for([failure, Response()])
    assert len(client.call("trade_cal", PARAMS)) == 1
    assert client.calls == len(session.requests) == 2 and waits == [1.]
    if isinstance(failure, Response):
        assert failure.closed


def test_exhausted_network_failure_is_bounded_and_never_echoes_exception():
    client, session, waits = client_for([requests.Timeout(SECRET)] * 3)
    with pytest.raises(ProviderError) as caught:
        client.call("trade_cal", PARAMS)
    assert caught.value.code == "TUSHARE_NETWORK" and caught.value.attempts == 3
    assert SECRET not in json.dumps(_safe_error(caught.value))
    assert client.calls == len(session.requests) == 3 and waits == [1., 2.]


@pytest.mark.parametrize("response", [Response(body=b'invalid ' + SECRET.encode()),
                                      Response(body={"code": -1, "msg": "每分钟限流 " + SECRET}),
                                      Response(body={"code": -2, "msg": SECRET}),
                                      Response(body={"code": 0, "data": {"fields": [], "items": []}})])
def test_malformed_success_provider_rate_and_contract_failure_do_not_retry(response):
    client, session, waits = client_for([response, Response()])
    with pytest.raises(ProviderError) as caught:
        client.call("trade_cal", PARAMS)
    assert client.calls == len(session.requests) == 1 and waits == []
    assert SECRET not in str(caught.value)


def test_attempts_count_against_existing_job_budget_even_when_retry_fails():
    client, session, waits = client_for([Response(502, SECRET), Response()])
    client.calls = 511
    with pytest.raises(ProviderError) as caught:
        client.call("trade_cal", PARAMS)
    # No sleep or request beyond the hard cap; retain final HTTP diagnostics.
    assert caught.value.http_status == 502 and caught.value.attempts == 1
    assert client.calls == 512 and len(session.requests) == 1 and waits == []
    with pytest.raises(ProviderError) as caught:
        client.call("trade_cal", PARAMS)
    assert caught.value.code == "PROVIDER_BUDGET" and len(session.requests) == 1


def test_unregistered_read_never_enters_retry_loop():
    client, session, waits = client_for([])
    with pytest.raises(ProviderError) as caught:
        client.call("unregistered_write", PARAMS)
    assert caught.value.code == "DATASET_FORBIDDEN"
    assert client.calls == 0 and session.requests == [] and waits == []
