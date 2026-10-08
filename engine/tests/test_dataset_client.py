"""Actual client source reconstruction against frozen synthetic HTTP responses."""

from copy import deepcopy
import json
import time
from urllib.parse import urlsplit

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from atlas_quant import bundle
from atlas_quant.dataset_runner.client import DatasetClient
from atlas_quant.dataset_runner.protocol import LIMITS, encode, sha
from atlas_quant.runner import RunnerError
from dataset_runner_support import PREFIX, config, inputs, job
from test_research_dataset_components import sources
from test_snapshot_market_view import legacy_source


class Response:
    def __init__(self, raw, status=200, headers=None):
        self.raw, self.status_code = raw, status
        self.headers = CaseInsensitiveDict(
            {
                "Content-Length": str(len(raw)),
                "Content-Encoding": "identity",
                "x-content-sha256": sha(raw),
                **(headers or {}),
            }
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        for i in range(0, len(self.raw), size):
            yield self.raw[i : i + size]


class Session:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        path = urlsplit(url).path
        if urlsplit(url).query:
            path += "?" + urlsplit(url).query
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def network(sources, legacy_source, tmp_path):
    task = job()
    data = inputs(sources, legacy_source, task)
    meta, manifest_raw, snapshot_raw, packages, registry = data
    responses = {
        PREFIX + "input": Response(encode(meta)),
        PREFIX + "sources/market/manifest": Response(manifest_raw),
    }
    manifest, snapshot = json.loads(manifest_raw), json.loads(snapshot_raw)
    collection = next(c for c in manifest["collections"] if c["id"] == "snapshotRows")
    for p in collection["chunks"]:
        responses[PREFIX + "sources/market/parts/" + str(p["ordinal"])] = Response(
            bundle.encode(snapshot["rows"][p["start"] : p["start"] + p["count"]])
        )
    responses[PREFIX + "sources/financial0/parts/0"] = Response(packages["financial0"])
    entries = []
    for ref, raw in registry.items():
        path = PREFIX + "registry/" + ref
        responses[path] = Response(raw)
        entries.append(
            {
                "ref": ref,
                "kind": json.loads(raw)["kind"],
                "sha256": sha(raw),
                "byteLength": len(raw),
                "url": path,
            }
        )
    responses[PREFIX + "registry?offset=0"] = Response(
        encode(
            {"items": entries, "total": len(entries), "offset": 0, "nextOffset": None}
        )
    )
    session = Session(responses)
    return task, data, DatasetClient(config(tmp_path), session), session


def test_sources_reconstruct_exact_bytes_and_read_no_other_report_collections(network):
    task, data, client, session = network
    remembered = []
    actual = client.inputs(
        task,
        deadline=time.monotonic() + 30,
        heartbeat=lambda: None,
        remember_input=lambda j, m: remembered.append(m),
    )
    assert actual == data and remembered == [data[0]]
    assert all(
        "forecasts" not in url and "report" not in url for _, url, _ in session.calls
    )
    assert len(session.calls) == 6


@pytest.mark.parametrize(
    "mutation",
    [
        "budget",
        "cross_job",
        "source_hash",
        "registry_count",
        "limits",
        "duplicate_part",
        "financial_root",
    ],
)
def test_parent_plan_rejected_before_opening_source_payloads(network, mutation):
    task, data, client, session = network
    meta = deepcopy(data[0])
    if mutation == "budget":
        meta["sources"]["market"]["snapshot"]["byteLength"] = (
            LIMITS["sourceSnapshotBytes"] + 1
        )
    elif mutation == "cross_job":
        meta["sources"]["market"]["manifest"]["url"] = meta["sources"]["market"][
            "manifest"
        ]["url"].replace(task["id"], "0" * 36)
    elif mutation == "source_hash":
        meta["sources"]["market"]["bundleId"] = "d" * 64
    elif mutation == "registry_count":
        meta["registry"]["count"] += 1
    elif mutation == "limits":
        meta["limits"]["symbols"] = 300
    elif mutation == "duplicate_part":
        meta["sources"]["market"]["snapshot"]["parts"] *= 2
    else:
        meta["sources"]["financial"][0]["roots"]["preparedRoot"] = "e" * 64
    session.responses[PREFIX + "input"] = Response(encode(meta))
    with pytest.raises(RunnerError):
        client.inputs(task, deadline=time.monotonic() + 30, heartbeat=lambda: None)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "mutation", ["count", "url", "duplicate", "size", "kind", "cursor"]
)
def test_complete_registry_plan_admitted_before_payload_reads(network, mutation):
    task, _, client, session = network
    route = PREFIX + "registry?offset=0"
    value = json.loads(session.responses[route].raw)
    if mutation == "count":
        value["total"] = True
    elif mutation == "url":
        value["items"][0]["url"] = "https://other.example/secret"
    elif mutation == "duplicate":
        value["items"] *= 2
    elif mutation == "size":
        value["items"][0]["byteLength"] = LIMITS["registryEntryBytes"] + 1
    elif mutation == "kind":
        value["items"][0]["kind"] = "operator_override"
    else:
        value["nextOffset"] = 0
    session.responses[route] = Response(encode(value))
    with pytest.raises(RunnerError):
        client.inputs(task, deadline=time.monotonic() + 30, heartbeat=lambda: None)
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "mutation", ["sha", "length", "body", "gzip", "redirect", "network"]
)
def test_raw_get_identity_and_no_retry(network, mutation):
    task, _, client, session = network
    route = PREFIX + "sources/market/manifest"
    raw = session.responses[route].raw
    if mutation == "sha":
        session.responses[route].headers["x-content-sha256"] = "f" * 64
    elif mutation == "length":
        session.responses[route].headers["content-length"] = str(len(raw) - 1)
    elif mutation == "body":
        session.responses[route] = Response(
            raw[:-1] + b" ", headers={"x-content-sha256": sha(raw)}
        )
    elif mutation == "gzip":
        session.responses[route].headers["content-encoding"] = "gzip"
    elif mutation == "redirect":
        session.responses[route] = Response(b"", status=302)
    else:
        session.responses[route] = requests.ConnectionError("secret must not leak")
    with pytest.raises(RunnerError) as caught:
        client.inputs(task, deadline=time.monotonic() + 30, heartbeat=lambda: None)
    assert "secret must not leak" not in str(caught.value)
    assert sum(urlsplit(url).path == route for _, url, _ in session.calls) == 1
