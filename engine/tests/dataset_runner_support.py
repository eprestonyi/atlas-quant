"""Public synthetic transport fixtures; all prices/statements are generated offline."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import time

from atlas_quant import bundle
from atlas_quant.dataset_runner.protocol import LIMITS, PROFILE, encode, sha
from atlas_quant.runner import RunnerError
from atlas_quant.research_dataset.codec import decode

JOB = "11111111-1111-4111-8111-111111111111"
PLAN = "22222222-2222-4222-8222-222222222222"
LEASE = "33333333-3333-4333-8333-333333333333"
PUBLICATION = "44444444-4444-4444-8444-444444444444"
INPUT = "55555555-5555-4555-8555-555555555555"
PREP = "66666666-6666-4666-8666-666666666666"
RUN = "77777777-7777-4777-8777-777777777777"
DATASET = "88888888-8888-4888-8888-888888888888"
PREFIX = "/quant/api/runner/datasets/jobs/" + JOB + "/"


def iso(offset):
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).isoformat()


def job():
    return {
        "id": JOB,
        "kind": "dataset_compose",
        "planId": PLAN,
        "leaseToken": LEASE,
        "leaseUntil": iso(120),
        "deadline": iso(600),
        "inputUrl": PREFIX + "input",
    }


def config(path):
    return {
        "api_base": "https://example.test/quant/api",
        "runner_secret": "q" * 48,
        "delivery_dir": str(path / "research"),
        "dataset_delivery_dir": str(path / "dataset"),
        "dataset_enabled": True,
        "poll_seconds": 0,
    }


def inputs(sources, original, task):
    package_raw = sources["source"].package_bytes
    package = json.loads(package_raw)
    source_manifest = json.loads(original["manifest"])
    collection = next(
        c for c in source_manifest["collections"] if c["id"] == "snapshotRows"
    )
    from atlas_quant.financial_runner.trust import calendar_scope

    roots = {
        "inputRoot": package["inputRoot"],
        "packRoot": package["packRoot"],
        "preparedRoot": sources["source"].prepared_root,
        "calendarRoot": calendar_scope(package["raw"]["calendar"])["calendarRoot"],
    }
    selected = {
        "kind": "forecast_snapshot_view",
        "runId": RUN,
        "expectedBundleId": sha(original["manifest"]),
        "expectedSnapshotSha256": sha(original["raw"]),
        "transform": {
            "kind": "snapshot_scope_view",
            "version": 1,
            "mode": "explicit_subset",
            **sources["scope"],
        },
    }
    financial = {
        "sourceId": "financial0",
        "inputId": INPUT,
        "preparationId": PREP,
        "roots": roots,
        "calendarRef": sources["calendar"],
        "proofRefs": [],
        "package": {
            "sha256": sha(package_raw),
            "byteLength": len(package_raw),
            "parts": [
                {
                    "ordinal": 0,
                    "startRow": None,
                    "rowCount": None,
                    "sha256": sha(package_raw),
                    "byteLength": len(package_raw),
                    "url": PREFIX + "sources/financial0/parts/0",
                }
            ],
        },
    }
    meta = {
        "job": {k: task[k] for k in ("id", "kind", "planId", "deadline")},
        "planRoot": "a" * 64,
        "plan": {
            "profile": PROFILE,
            "marketSource": selected,
            "financialInputs": [{"inputId": INPUT, "preparationId": PREP, **roots}],
            "marketCalendarRef": sources["calendar"],
            "scope": sources["scope"],
        },
        "sources": {
            "market": {
                "runId": RUN,
                "bundleId": sha(original["manifest"]),
                "originalScope": {
                    k: original["strategy"]["universe"][k]
                    for k in ("symbols", "start", "end")
                },
                "manifest": {
                    "sha256": sha(original["manifest"]),
                    "byteLength": len(original["manifest"]),
                    "url": PREFIX + "sources/market/manifest",
                },
                "snapshot": {
                    **{
                        k: source_manifest["documents"]["snapshot"][k]
                        for k in ("sha256", "byteLength")
                    },
                    "rowCount": collection["rowCount"],
                    "parts": [
                        {
                            **p,
                            "url": PREFIX + "sources/market/parts/" + str(p["ordinal"]),
                        }
                        for p in collection["chunks"]
                    ],
                },
            },
            "financial": [financial],
        },
        "registry": {
            "count": len(sources["registry"]),
            "totalBytes": sum(map(len, sources["registry"].values())),
            "listUrl": PREFIX + "registry",
        },
        "limits": dict(LIMITS),
    }
    return (
        deepcopy(meta),
        original["manifest"],
        original["raw"],
        {"financial0": package_raw},
        dict(sources["registry"]),
    )


class Queue:
    """A protocol double only; actual numerical bytes come from the core fixture."""

    def __init__(self, fixture, task):
        self.task, self.fixture = task, fixture
        self.status, self.claims, self.input_calls, self.parts = "running", [], 0, {}
        self.manifest, self.root, self.error = None, None, None
        self.lost = set()
        self.failed_once = set()

    def ambiguous(self, key):
        if key in self.lost and key not in self.failed_once:
            self.failed_once.add(key)
            raise RunnerError("DATASET_NETWORK", "Lost fixture response")

    def ref(self):
        return {
            "datasetId": DATASET,
            "datasetRoot": self.root,
            "format": "atlas.quant.research_dataset",
            "version": 2,
        }

    def post(self, route, payload, **kwargs):
        if route == "heartbeat":
            if payload["state"] == "ready":
                return {"ok": True, "canClaim": self.status == "running"}
            return {
                "ok": True,
                "leaseValid": True,
                "cancelRequested": self.status == "cancel_requested",
                "leaseUntil": self.task["leaseUntil"],
            }
        if route == "claim":
            self.claims.append(payload["requestId"])
            self.ambiguous("claim")
            return {
                "claim": {
                    "requestId": payload["requestId"],
                    "jobId": JOB,
                    "status": self.status,
                },
                "job": (
                    deepcopy(self.task)
                    if self.status in {"running", "cancel_requested"}
                    else None
                ),
            }
        if route.endswith("/publication"):
            raw = payload["manifestText"].encode()
            assert sha(raw) == payload["datasetRoot"]
            if self.root:
                assert self.root == payload["datasetRoot"]
            self.root, self.manifest = payload["datasetRoot"], json.loads(raw)
            self.ambiguous("begin")
            return self.publication()
        if route.endswith("/complete"):
            assert (
                payload["datasetRoot"] == self.root
                and payload["publicationId"] == PUBLICATION
            )
            assert not self.publication()["missing"]
            self.status = "completed"
            self.ambiguous("complete")
            return {"ok": True, "status": "completed", "datasetRef": self.ref()}
        if route.endswith("/fail"):
            self.status = "cancelled" if self.status == "cancel_requested" else "failed"
            self.error = payload["error"]
            self.ambiguous("fail")
            return {"ok": True}
        raise AssertionError(route)

    def publication(self):
        return {
            "publicationId": PUBLICATION,
            "datasetRoot": self.root,
            "status": "committed" if self.status == "completed" else "staging",
            "missing": [
                {
                    "componentId": c["componentId"],
                    "ordinals": [
                        p["ordinal"]
                        for p in c["parts"]
                        if (c["componentId"], p["ordinal"]) not in self.parts
                    ],
                }
                for c in self.manifest["components"]
                if any(
                    (c["componentId"], p["ordinal"]) not in self.parts
                    for p in c["parts"]
                )
            ],
        }

    def get(self, route, lease, **kwargs):
        assert lease == LEASE
        if route.endswith("/status"):
            return {
                "job": {"id": JOB, "status": self.status, "error": self.error},
                "datasetRef": self.ref() if self.status == "completed" else None,
            }
        if "/publication?" in route:
            return self.publication()
        raise AssertionError(route)

    def inputs(self, task, *, remember_input, **kwargs):
        self.input_calls += 1
        remember_input(task, self.fixture[0])
        return self.fixture

    def upload(self, task, publication, root, name, part, raw, **kwargs):
        assert publication == PUBLICATION and root == self.root
        assert sha(raw) == part["sha256"] and len(raw) == part["byteLength"]
        self.parts[name, part["ordinal"]] = raw
        self.ambiguous("part")
        return {
            "ok": True,
            "componentId": name,
            "ordinal": part["ordinal"],
            "sha256": sha(raw),
            "byteLength": len(raw),
        }
