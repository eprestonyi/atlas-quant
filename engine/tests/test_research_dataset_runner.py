"""Real child F from synthetic dataset/2 and adversarial source transport."""

from copy import deepcopy
import json
import multiprocessing
import time

import pytest

from atlas_quant import runner
from atlas_quant.dataset_runner.protocol import LIMITS, PROFILE, encode, sha
from atlas_quant.financial_bundle_spool import FinancialBundleSpool
from atlas_quant.research_dataset_runner.client import ResearchDatasetClient
from atlas_quant.research_dataset_runner.spool import ResearchDatasetSpool
from atlas_quant.runner_claims import ClaimIntent, claim_request
from dataset_runner_support import JOB, LEASE, DATASET, config
from test_dataset_client import Response, Session
from test_research_dataset_components import sources, long_sources, strategy
from test_snapshot_market_view import legacy_source, derive, publication
from test_financial_bundle_spool import Client


@pytest.fixture(scope="module")
def frozen(long_sources, legacy_source):
    dataset, parts, reader = publication(
        derive(legacy_source, long_sources["scope"]), long_sources
    )
    settings = strategy(long_sources)
    settings["model"].update(trainWindow=120, refitDays=60)
    settings["validation"] = {"minTrainDates": 40, "innerFolds": 2, "outerFolds": 2}
    ref = {
        "datasetId": DATASET,
        "datasetRoot": dataset.dataset_root,
        "format": "atlas.quant.research_dataset",
        "version": 2,
    }
    prefix = "/quant/api/runner/research-datasets/" + JOB + "/"
    query = "?datasetRoot=" + dataset.dataset_root
    evidence = {"datasetRef": ref, "admissionProfile": PROFILE}
    task = {
        "id": JOB,
        "leaseToken": LEASE,
        "jobKind": "forecast",
        "dataSource": "ready_dataset",
        "dataset": None,
        "strategy": settings,
        **evidence,
        "sourceEvidence": evidence,
        "datasetInputUrl": prefix + "input",
        "resultTransport": {"format": "atlas.quant.financial_bundle", "version": 1},
    }
    registry = long_sources["registry"]
    entries = [
        {
            "ref": key,
            "kind": json.loads(raw)["kind"],
            "sha256": sha(raw),
            "byteLength": len(raw),
            "url": prefix + "registry/" + key + query,
        }
        for key, raw in registry.items()
    ]
    meta = {
        "job": {"id": JOB, "kind": "forecast"},
        **evidence,
        "sourceEvidence": evidence,
        "manifest": {
            "sha256": dataset.dataset_root,
            "byteLength": len(dataset.manifest_bytes),
            "url": prefix + "manifest" + query,
        },
        "partUrlTemplate": prefix + "parts/{componentId}/{ordinal}" + query,
        "registry": {
            "count": len(entries),
            "totalBytes": sum(map(len, registry.values())),
            "listUrl": prefix + "registry" + query + "&offset=0",
        },
        "limits": dict(LIMITS),
    }
    responses = {
        prefix + "input": encode(meta),
        prefix + "manifest" + query: dataset.manifest_bytes,
        prefix
        + "registry"
        + query
        + "&offset=0": encode(
            {"items": entries, "total": len(entries), "offset": 0, "nextOffset": None}
        ),
    }
    responses.update(
        {prefix + "registry/" + ref + query: raw for ref, raw in registry.items()}
    )
    responses.update(
        {
            prefix + "parts/" + c + "/" + str(n) + query: raw
            for (c, n), raw in parts.items()
        }
    )
    return task, meta, responses, reader, registry


def prepare(tmp_path, frozen, mutate=None):
    task, meta, raw, _, _ = deepcopy(frozen[:3]) + frozen[3:]
    if mutate:
        mutate(task, meta, raw)
    raw[task["datasetInputUrl"]] = encode(meta)
    session = Session({url: Response(value) for url, value in raw.items()})
    spool = runner.CompletionSpool(config(tmp_path))
    client = ResearchDatasetClient(config(tmp_path), session)
    job = client.prepare(
        task, spool, deadline=time.monotonic() + 30, check=lambda: None
    )
    return job, spool, session


def test_exact_encrypted_input_fits_in_spawned_child_without_provider(tmp_path, frozen):
    job, spool, session = prepare(tmp_path, frozen)
    context = FinancialBundleSpool.context_for(spool, job)
    before = {p.pid for p in multiprocessing.active_children()}
    answer = runner.execute_bounded(job, timeout=30, bundle_context=context)
    assert "error" not in answer, answer
    assert answer["_bundleFormat"] == "atlas.quant.financial_bundle/1"
    result = FinancialBundleSpool(context).reader(answer["bundleId"])
    assert result.verify_integrity()["transportVerified"]
    joined = result.restore_sources(frozen[3], frozen[4])
    assert encode(joined.to_dataset()) == frozen[3].payload("researchRows")
    assert result.document("report")["forecasts"]["rows"]
    assert result.manifest["sourceEvidence"] == job["sourceEvidence"]
    assert all(
        "provider" not in url and kwargs["headers"]["X-Dataset-Lease"] == LEASE
        for _, url, kwargs in session.calls
    )
    assert {p.pid for p in multiprocessing.active_children()} == before
    encrypted = ResearchDatasetSpool(context)
    assert all(
        p.suffix == ".enc" and b"cash_asset_share" not in p.read_bytes()
        for p in encrypted.root.iterdir()
    )


@pytest.mark.parametrize(
    "attack",
    [
        "root",
        "profile",
        "redirect",
        "count",
        "page_offset",
        "registry_bytes",
        "part_bytes",
    ],
)
def test_hostile_metadata_and_changed_source_bytes_never_fit(tmp_path, frozen, attack):
    def mutate(job, meta, raw):
        if attack == "root":
            meta["datasetRef"]["datasetRoot"] = "e" * 64
        elif attack == "profile":
            meta["limits"]["closureBytes"] *= 2
        elif attack == "redirect":
            meta["manifest"]["url"] = "https://untrusted.test/manifest"
        elif attack == "count":
            meta["registry"]["count"] = LIMITS["registryEntries"] + 1
        elif attack == "page_offset":
            path = meta["registry"]["listUrl"]
            value = json.loads(raw[path])
            value["nextOffset"] = 0
            raw[path] = encode(value)
        else:
            path = next(
                p
                for p in raw
                if ("/registry/" if attack == "registry_bytes" else "/parts/") in p
            )
            raw[path] += b" "

    with pytest.raises((runner.RunnerError, ValueError)):
        prepare(tmp_path, frozen, mutate)
    result_dir = tmp_path / "research" / "financial-bundles"
    assert not result_dir.exists()


def test_same_child_rejects_rehashed_external_registry_without_trusting_sidecar(
    tmp_path, frozen
):
    job, spool, _ = prepare(tmp_path, frozen)
    store = ResearchDatasetSpool.from_spool(spool, job)
    index = json.loads(store.read("registry-index"))
    item = index[0]
    # Simulate an internally consistent new server pin after original sidecar freeze.
    value = json.loads(store.read("registry-" + item["ref"]))
    value["evidenceWitness"] = "changed"
    raw = encode(value)
    item.update(byteLength=len(raw), sha256=sha(raw))
    store.write("registry-" + item["ref"], raw)
    store.write("registry-index", encode(index))
    meta = json.loads(store.read("input"))
    meta["registry"]["totalBytes"] = sum(x["byteLength"] for x in index)
    store.write("input", encode(meta))
    answer = runner.execute_bounded(job, timeout=30, bundle_context=store.context)
    assert "error" in answer and "bundleId" not in answer
    assert not (FinancialBundleSpool(store.context).root / "manifest.enc").exists()


def test_capabilities_are_opt_in_and_no_provider_configuration_reaches_dataset_job(
    tmp_path, frozen
):
    assert "datasetFormats" not in claim_request(JOB)
    assert claim_request(JOB, financial_datasets=True)["datasetFormats"] == [
        "atlas.quant.research_dataset/2"
    ]
    task = dict(
        frozen[0],
        providerAccess={"secret": "forbidden"},
        pcdAccess={"token": "forbidden"},
    )
    actual = runner.prepare_job(
        task, dict(config(tmp_path), provider_access={"secret": "also forbidden"})
    )
    assert "providerAccess" not in actual and "pcdAccess" not in actual
    with pytest.raises(runner.RunnerError) as error:
        runner.run_job(actual)
    assert error.value.code == "DATA_SOURCE"  # Direct bypass has no fresh admission.


class Delivery(Client):
    def __init__(self, request):
        super().__init__()
        self.request = request
        self.unknown = True
        self.terminal = False

    def post(self, route, body, **kwargs):
        if route == "financial-bundles/complete":
            self.calls.append(route)
            assert set(body) == {"id", "leaseToken", "stageId", "bundleId"}
            self.terminal = True
            if self.unknown:
                raise runner.RunnerError("QUEUE_NETWORK", "Lost completion ACK")
            return {"ok": True}
        if route == "claim":
            if self.unknown:
                raise runner.RunnerError("QUEUE_NETWORK", "Unknown terminal receipt")
            assert self.terminal and body["requestId"] == self.request
            return {
                "job": None,
                "claim": {
                    "requestId": self.request,
                    "jobId": JOB,
                    "status": "completed",
                },
            }
        if route == "heartbeat" and self.terminal:
            return {"leaseValid": False}
        return super().post(route, body, **kwargs)


def test_unknown_terminal_retains_both_inputs_and_outputs_then_cleans_without_fit(
    tmp_path, frozen, monkeypatch
):
    job, spool, _ = prepare(tmp_path, frozen)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    claims.executing(intent, job)
    context = FinancialBundleSpool.context_for(spool, job)
    answer = runner.execute_bounded(job, timeout=30, bundle_context=context)
    assert "error" not in answer, answer
    spool.write(
        {
            "id": JOB,
            "leaseToken": LEASE,
            "_claimRequestId": intent["requestId"],
            "_datasetKey": job["_datasetKey"],
            **answer,
        }
    )
    queue = Delivery(intent["requestId"])
    monkeypatch.setattr(runner, "_wait", lambda _: None)
    with pytest.raises(runner.RunnerError) as error:
        runner.flush_completions(queue, spool)
    assert error.value.code == "COMPLETION_UNCONFIRMED"
    input_dir = ResearchDatasetSpool(context).root
    output_dir = FinancialBundleSpool(context).root
    assert input_dir.exists() and output_dir.exists() and list(spool.pending())

    def forbidden(*args, **kwargs):
        pytest.fail("A delivery retry must never refit F")

    monkeypatch.setattr(runner, "execute_bounded", forbidden)
    queue.unknown = False
    uploads = len(queue.uploads)
    runner.flush_completions(queue, runner.CompletionSpool(config(tmp_path)))
    assert (
        len(queue.uploads) == uploads
        and not input_dir.exists()
        and not output_dir.exists()
    )
    assert not list(spool.pending()) and claims.read() is None


class HostedQueue(Delivery):
    def __init__(self, task):
        super().__init__(None)
        self.task, self.claims = task, []
        self.unknown = False
        self.errors = []

    def post(self, route, body, **kwargs):
        if route == "claim" and not self.terminal:
            self.claims.append(deepcopy(body))
            self.request = body["requestId"]
            return {
                "job": deepcopy(self.task),
                "claim": {"requestId": self.request, "jobId": JOB, "status": "running"},
            }
        if route == "complete":
            assert set(body) == {"id", "leaseToken", "error"}
            self.errors.append(body["error"])
            self.terminal = True
            return {"ok": True}
        return super().post(route, body, **kwargs)


def test_full_claim_download_child_publish_cycle_and_private_format_never_leaks(
    tmp_path, frozen, monkeypatch
):
    task, meta, raw = deepcopy(frozen[:3])
    session = Session({url: Response(value) for url, value in raw.items()})
    queue = HostedQueue(task)
    cfg = dict(
        config(tmp_path), financial_dataset_research_enabled=True, job_timeout=30
    )
    monkeypatch.setattr(runner, "QueueClient", lambda _: queue)
    monkeypatch.setenv("TUSHARE_TOKEN", "never-forward-to-financial-child")
    monkeypatch.setattr(
        ResearchDatasetClient,
        "__init__",
        lambda self, cfg: init_client(self, cfg, session),
    )
    monkeypatch.setattr(runner, "STOP", False)
    assert runner.serve(cfg, once=True) == 0
    assert not queue.errors and "financial-bundles/complete" in queue.calls
    assert queue.claims[0]["snapshotFormats"] == ["financial_json_v1"]
    assert not list(runner.CompletionSpool(cfg).pending())


def init_client(self, cfg, session):
    # Bypass only socket construction; exercise the real bounded source reader.
    from atlas_quant.dataset_runner.client import DatasetClient
    from urllib.parse import urlsplit

    DatasetClient.__init__(self, cfg, session)
    self.base = cfg["api_base"].rstrip("/") + "/runner/research-datasets"
    self.prefix = urlsplit(self.base).path + "/"


@pytest.mark.parametrize("finished", [False, True])
def test_restart_after_spawn_only_delivers_finished_manifest_or_fails_same_lease(
    tmp_path, frozen, monkeypatch, finished
):
    job, spool, session = prepare(tmp_path, frozen)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    claims.executing(intent, job)
    context = FinancialBundleSpool.context_for(spool, job)
    if finished:
        answer = runner.execute_bounded(job, timeout=30, bundle_context=context)
        assert "error" not in answer, answer
    # A parent died before creating the completion envelope. Sources must not be fetched again.
    queue = HostedQueue(frozen[0])

    def forbidden(*args, **kwargs):
        pytest.fail("Restart must not download or refit")

    monkeypatch.setattr(runner, "QueueClient", lambda _: queue)
    monkeypatch.setattr(runner, "execute_bounded", forbidden)
    monkeypatch.setattr(ResearchDatasetClient, "prepare", forbidden)
    monkeypatch.setattr(runner, "STOP", False)
    assert (
        runner.serve(
            dict(config(tmp_path), financial_dataset_research_enabled=True), once=True
        )
        == 0
    )
    assert (
        len(queue.claims) == 1 and queue.claims[0]["requestId"] == intent["requestId"]
    )
    assert (not queue.errors) == finished
    if not finished:
        assert queue.errors[0]["code"] == "RUNNER_INTERRUPTED"
    assert not ResearchDatasetSpool(context).root.joinpath("input.enc").exists()
    assert not list(spool.pending()) and claims.read() is None


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_terminal_while_offline_cleans_exact_claim_sources_and_partial_outputs(
    tmp_path, frozen, monkeypatch, status
):
    job, spool, _ = prepare(tmp_path, frozen)
    claims = ClaimIntent(spool)
    intent = claims.current_or_create()
    claims.executing(intent, job)
    output = FinancialBundleSpool.from_spool(spool, job)
    output.write("partial", b"private partial")
    inputs = ResearchDatasetSpool.from_spool(spool, job)
    other = ResearchDatasetSpool.from_spool(
        spool, {"id": "other", "leaseToken": "other"}
    )
    other.write("input", b"unrelated evidence")

    class Expired:
        def post(self, route, body, **kwargs):
            assert route == "claim" and body["requestId"] == intent["requestId"]
            return {
                "job": None,
                "claim": {
                    "requestId": intent["requestId"],
                    "jobId": JOB,
                    "status": status,
                },
            }

    monkeypatch.setattr(runner, "QueueClient", lambda _: Expired())
    monkeypatch.setattr(runner, "STOP", False)
    assert runner.serve(config(tmp_path), once=True) == 0
    assert not inputs.root.exists() and not output.root.exists()
    assert other.read("input") == b"unrelated evidence"
    assert not list(spool.pending()) and claims.read() is None
