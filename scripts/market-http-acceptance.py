"""Run a predeclared fixture source and one pooled forecast over real loopback HTTP.

Never loads provider configuration. Existing job IDs are resumed/read; completed
research is never refit. Start scripts/preview-market.mjs first. The resulting
archives can be independently checked with audit-market-dataset.py.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

import requests
from atlas_quant import __version__
from atlas_quant.market_acquisition.service import MarketConsumer
from atlas_quant.runner import serve
from atlas_quant.runner_claims import claim_request
from fixtures.market_source import SyntheticMarketProvider


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_once(directory, name, route, body, *, api, save):
    result_path = directory / name
    if result_path.exists():
        return json.loads(result_path.read_text())
    intent_path = result_path.with_suffix(".intent.json")
    if intent_path.exists():
        raise RuntimeError(
            f"Unknown mutation outcome retained in {intent_path.name}; "
            "read the existing owner experiments/runs and reconcile the receipt. "
            "This harness will not repeat the POST or refit."
        )
    # These endpoints have no idempotency key. A durable sentinel precedes
    # the first POST, including failures before a response can be saved.
    raw = json.dumps(
        {"route": route, "body": body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    fd = os.open(intent_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    sync_directory(directory)
    return save(name, api(route, body))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--phase", choices=["source", "forecast", "all", "status"], default="all"
    )
    parser.add_argument(
        "--compute-lock",
        type=Path,
        help="Shared private lock; persisted for later phases",
    )
    args = parser.parse_args()
    session_path = args.session.resolve()
    if session_path.stat().st_mode & 0o077:
        raise ValueError("Session must be private")
    session = json.loads(session_path.read_text())
    u = urlsplit(session["baseUrl"])
    if (
        u.scheme != "http"
        or u.hostname not in {"localhost", "127.0.0.1"}
        or u.path != "/quant/api"
        or u.username
        or u.password
        or u.query
        or u.fragment
        or session.get("synthetic") is not True
    ):
        raise ValueError("Explicit loopback synthetic session required")
    directory = session_path.parent
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    http = requests.Session()
    http.trust_env = False
    http.headers.update({"Cookie": session["cookie"], "Accept-Encoding": "identity"})

    def save(name, value):
        target = directory / name
        raw = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        sync_directory(directory)
        return value

    def api(route, body=None):
        r = http.request(
            "GET" if body is None else "POST",
            session["baseUrl"] + route,
            json=body,
            timeout=(10, 180),
            allow_redirects=False,
        )
        if r.status_code not in (200, 201, 202):
            raise RuntimeError(f"HTTP {r.status_code} on {route}: {r.text[:500]}")
        return r.json()

    def download(route, name, max_bytes):
        target = directory / name
        if target.exists():
            return {
                "path": str(target),
                "sha256": hashlib.file_digest(target.open("rb"), "sha256").hexdigest(),
                "byteLength": target.stat().st_size,
            }
        h, size = hashlib.sha256(), 0
        with http.get(
            session["baseUrl"] + route,
            stream=True,
            timeout=(10, 180),
            allow_redirects=False,
        ) as r:
            r.raise_for_status()
            if r.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("Exact download encoding required")
            partial = target.with_suffix(target.suffix + ".incomplete")
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                for raw in r.iter_content(256 * 1024):
                    size += len(raw)
                    if size > max_bytes:
                        raise ValueError("Download budget exceeded")
                    out.write(raw)
                    h.update(raw)
                out.flush()
                os.fsync(out.fileno())
        os.replace(partial, target)
        return {"path": str(target), "sha256": h.hexdigest(), "byteLength": size}

    if args.phase == "status":
        for name, route in (
            ("source-start.json", "/market-preparation-jobs/"),
            ("forecast-start.json", "/runs/"),
        ):
            path = directory / name
            if path.exists():
                job_id = json.loads(path.read_text())["job"]["id"]
                value = api(route + job_id)["job"]
                print(
                    json.dumps(
                        {
                            k: value.get(k)
                            for k in ("id", "status", "phase", "updatedAt", "error")
                        }
                    ),
                    flush=True,
                )
        return 0
    prior_path = directory / "consumer-config.json"
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    slot = args.compute_lock or Path(
        prior.get("compute_lock_path", directory / "compute" / "slot.lock")
    )
    slot.parent.mkdir(exist_ok=True, parents=True, mode=0o700)
    from atlas_quant.compute_slot import validate_slot_path

    validate_slot_path(str(slot))
    config = {
        "api_base": session["baseUrl"],
        "runner_secret": session["runnerSecret"],
        "delivery_dir": str(directory / "research-delivery"),
        "market_delivery_dir": str(directory / "market-delivery"),
        "authorization_scope": session["authorizationScope"],
        "allow_market_fixtures": True,
        "market_acquisition_enabled": True,
        "market_dataset_research_enabled": True,
        "compute_lock_path": str(slot),
        "job_timeout": 900,
        "poll_seconds": 3,
    }
    save("consumer-config.json", config)
    if args.phase in {"source", "all"}:
        started_path = directory / "source-start.json"
        if started_path.exists():
            started = json.loads(started_path.read_text())
        else:
            intent_path = directory / "source-start-intent.json"
            intent = (
                json.loads(intent_path.read_text())
                if intent_path.exists()
                else save(
                    intent_path.name,
                    {"requestId": str(uuid.uuid4()), "planRoot": session["planRoot"]},
                )
            )
            started = save(
                started_path.name,
                api(
                    "/market-preparation-plans/" + session["planId"] + "/start", intent
                ),
            )
        jid = started["job"]["id"]
        status = api("/market-preparation-jobs/" + jid)
        begin = time.monotonic()
        if status["job"]["status"] in {"queued", "running"}:
            consumer = MarketConsumer(
                config,
                provider_factory=SyntheticMarketProvider,
                stop_requested=stop.is_set,
            )
            with consumer.spool.locked():
                consumer.once()
        status = save("source-result.json", api("/market-preparation-jobs/" + jid))
        save(
            "source-timing.json",
            {
                "elapsedSeconds": time.monotonic() - begin,
                "realProviderCalls": 0,
                "syntheticScheduling": True,
            },
        )
        if status["job"]["status"] != "completed":
            raise RuntimeError(
                "Source job terminal failure retained; inspect source-result.json"
            )
        print(
            json.dumps(
                {
                    "phase": "source",
                    "jobId": jid,
                    "status": "completed",
                    "realProviderCalls": 0,
                }
            ),
            flush=True,
        )

    source = json.loads((directory / "source-result.json").read_text())
    ref = source["job"]["result"]["marketDatasetRef"]
    source_download = download(
        f"/market-datasets/{ref['datasetId']}/download?datasetRoot={ref['datasetRoot']}",
        "market-source.tar",
        650 * 1024 * 1024,
    )
    save("source-download.json", source_download)
    if args.phase == "source":
        return 0
    # Record capabilities with the real transport before the server admits a run.
    runner_headers = {"Authorization": "Bearer " + config["runner_secret"]}
    capabilities = claim_request(str(uuid.uuid4()), market_datasets=True)
    capabilities.pop("requestId")
    http.post(
        session["baseUrl"] + "/runner/heartbeat",
        json={**capabilities, "state": "idle"},
        headers=runner_headers,
        timeout=30,
    ).raise_for_status()
    profile = (
        "pooled_asset_1000_auto_candidate_v1"
        if session["strategy"]["model"]["estimator"] == "auto"
        else "pooled_asset_1000_v1"
    )
    binding = {
        "marketDatasetRef": ref,
        "universeScopeRef": session["universeScopeRef"],
        "admissionProfile": profile,
    }
    experiment = create_once(
        directory,
        "experiment.json",
        "/statistical-quant/experiments",
        {"strategy": session["strategy"], **binding},
        api=api,
        save=save,
    )
    queued = create_once(
        directory,
        "forecast-start.json",
        f"/statistical-quant/experiments/{experiment['experiment']['id']}/run",
        {
            "version": experiment["experiment"]["version"],
            "dataSource": "ready_market",
            **binding,
        },
        api=api,
        save=save,
    )
    jid = queued["job"]["id"]
    status = api("/runs/" + jid)
    begin = time.monotonic()
    if status["job"]["status"] in {"queued", "running"}:
        serve(config, once=True)
    status = save("forecast-summary.json", api("/runs/" + jid + "/report"))
    save(
        "forecast-timing.json",
        {"elapsedSeconds": time.monotonic() - begin, "realProviderCalls": 0},
    )
    if status["job"]["status"] != "completed":
        raise RuntimeError(
            "Forecast outcome retained; inspect forecast-summary.json, do not silently refit"
        )
    bundle_id = status["transport"]["bundleId"]
    result_download = download(
        f"/runs/{jid}/report/bundle?bundleId={bundle_id}",
        "forecast-bundle.tar",
        260 * 1024 * 1024,
    )
    save(
        "acceptance.json",
        {
            "sourceKind": "fixture",
            "realProviderCalls": 0,
            "marketDatasetRef": ref,
            "forecastJobId": jid,
            "bundleId": bundle_id,
            "sourceDownload": source_download,
            "resultDownload": result_download,
        },
    )
    print(
        json.dumps(
            {
                "phase": "forecast",
                "jobId": jid,
                "status": "completed",
                "bundleId": bundle_id,
                "realProviderCalls": 0,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
