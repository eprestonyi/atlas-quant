"""Same-origin immutable source reads with parent budgets and lease fencing."""

import time
from urllib.parse import urlsplit

import requests

from .. import bundle
from ..runner import RunnerError
from .protocol import (
    CAPABILITY,
    LIMITS,
    PROFILE,
    decode,
    digest,
    encode,
    fail,
    identifier,
    integer,
    keys,
    require,
    sha,
)
from .spool import COMPONENT


class DatasetClient:
    def __init__(self, config, session=None):
        self.base = config["api_base"].rstrip("/") + "/runner/datasets"
        self.prefix = urlsplit(self.base).path + "/"
        self.secret = config["runner_secret"]
        self.session = session or requests.Session()
        self.session.trust_env = False

    def _request(
        self,
        method,
        route,
        *,
        payload=None,
        raw=None,
        lease=None,
        limit=LIMITS["inputMetadataBytes"],
        deadline=None,
        heartbeat=None,
        descriptor=None
    ):
        require(
            isinstance(route, str)
            and route
            and not route.startswith("/")
            and not any(s in route for s in ("..", "#", "%", "\\", "://")),
            "DATASET_ROUTE",
        )
        deadline = min(
            deadline if deadline is not None else time.monotonic() + 60,
            time.monotonic() + 60,
        )
        content = encode(payload) if payload is not None else raw
        if content is not None:
            maximum = (
                2 * LIMITS["manifestBytes"] + 4096
                if payload is not None
                else LIMITS["partBytes"]
            )
            require(
                isinstance(content, bytes) and len(content) <= maximum,
                "DATASET_BYTE_BUDGET",
            )
        headers = {
            "Authorization": "Bearer " + self.secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }
        if lease is not None:
            headers["X-Dataset-Lease"] = identifier(lease)
        try:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "DATASET_DEADLINE")
            if heartbeat:
                heartbeat()
            with self.session.request(
                method,
                self.base + "/" + route,
                data=content,
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=(min(10, remaining), min(20, remaining)),
            ) as response:
                if response.status_code != 200:
                    raise RunnerError(
                        "DATASET_HTTP",
                        "数据集服务尚未成功响应。",
                        http_status=response.status_code,
                    )
                require(
                    response.headers.get("Content-Encoding", "identity").lower()
                    == "identity",
                    "DATASET_ENCODING",
                )
                length = response.headers.get("Content-Length")
                if length is not None:
                    require(
                        length.isdecimal() and int(length) <= limit,
                        "DATASET_BYTE_BUDGET",
                    )
                if descriptor is not None:
                    require(
                        length is not None
                        and int(length) == descriptor["byteLength"]
                        and response.headers.get("x-content-sha256")
                        == descriptor["sha256"],
                        "DATASET_INTEGRITY",
                    )
                blocks, size = [], 0
                for block in response.iter_content(65536):
                    size += len(block)
                    require(size <= limit, "DATASET_BYTE_BUDGET")
                    require(time.monotonic() < deadline, "DATASET_DEADLINE")
                    if heartbeat:
                        heartbeat()
                    blocks.append(block)
                require(length is None or size == int(length), "DATASET_INTEGRITY")
                result = b"".join(blocks)
                if descriptor is not None:
                    require(
                        len(result) == descriptor["byteLength"]
                        and sha(result) == descriptor["sha256"],
                        "DATASET_INTEGRITY",
                    )
                return result
        except RunnerError:
            raise
        except requests.RequestException:
            raise RunnerError(
                "DATASET_NETWORK", "数据集连接中断，原任务身份与产物已保留。"
            ) from None

    def post(self, route, payload, *, deadline=None):
        result = decode(
            self._request("POST", route, payload=payload, deadline=deadline),
            limit=LIMITS["inputMetadataBytes"],
        )
        require(isinstance(result, dict))
        return result

    def get(self, route, lease, *, deadline=None):
        result = decode(
            self._request("GET", route, lease=lease, deadline=deadline),
            limit=LIMITS["inputMetadataBytes"],
        )
        require(isinstance(result, dict))
        return result

    def descriptor(self, value, job, suffix, maximum):
        require(isinstance(value, dict))
        integer(value.get("byteLength"), 1, maximum)
        digest(value.get("sha256"))
        route = "jobs/" + identifier(job["id"]) + "/" + suffix
        require(value.get("url") == self.prefix + route, "DATASET_ROUTE")
        return route

    def download(self, value, job, suffix, maximum, *, deadline, heartbeat):
        route = self.descriptor(value, job, suffix, maximum)
        return self._request(
            "GET",
            route,
            lease=job["leaseToken"],
            limit=value["byteLength"],
            deadline=deadline,
            heartbeat=heartbeat,
            descriptor=value,
        )

    def source_plan(self, meta, job):
        """Admit all declared source counts/bytes before opening payloads."""
        keys(meta, {"job", "planRoot", "plan", "sources", "registry", "limits"})
        require(
            meta["job"] == {k: job[k] for k in ("id", "kind", "planId", "deadline")},
            "DATASET_INPUT_IDENTITY",
        )
        digest(meta["planRoot"])
        require(meta["limits"] == LIMITS, "DATASET_LIMITS")
        keys(
            meta["plan"],
            {
                "profile",
                "marketSource",
                "financialInputs",
                "marketCalendarRef",
                "scope",
            },
        )
        plan = meta["plan"]
        require(plan["profile"] == PROFILE)
        identifier(plan["marketCalendarRef"])
        selection = plan["marketSource"]
        keys(
            selection,
            {
                "kind",
                "runId",
                "expectedBundleId",
                "expectedSnapshotSha256",
                "transform",
            },
        )
        require(selection["kind"] == "forecast_snapshot_view")
        identifier(selection["runId"])
        digest(selection["expectedBundleId"])
        digest(selection["expectedSnapshotSha256"])
        keys(meta["sources"], {"market", "financial"})
        market = meta["sources"]["market"]
        keys(market, {"runId", "bundleId", "originalScope", "manifest", "snapshot"})
        require(
            market["runId"] == selection["runId"]
            and market["bundleId"] == selection["expectedBundleId"],
            "DATASET_SOURCE_IDENTITY",
        )
        self.descriptor(
            market["manifest"],
            job,
            "sources/market/manifest",
            LIMITS["sourceManifestBytes"],
        )
        require(
            market["manifest"]["sha256"] == market["bundleId"],
            "DATASET_SOURCE_IDENTITY",
        )
        snap = market["snapshot"]
        keys(snap, {"sha256", "byteLength", "rowCount", "parts"})
        digest(snap["sha256"])
        integer(snap["byteLength"], 1, LIMITS["sourceSnapshotBytes"])
        integer(snap["rowCount"], 1, LIMITS["marketRows"])
        require(
            snap["sha256"] == selection["expectedSnapshotSha256"],
            "DATASET_SOURCE_IDENTITY",
        )
        require(
            isinstance(snap["parts"], list)
            and 1 <= len(snap["parts"]) <= LIMITS["parts"],
            "DATASET_INPUT_BUDGET",
        )
        row_count = chunk_bytes = 0
        for ordinal, part in enumerate(snap["parts"]):
            keys(part, {"ordinal", "start", "count", "sha256", "byteLength", "url"})
            require(
                type(part["ordinal"]) is int
                and part["ordinal"] == ordinal
                and type(part["start"]) is int
                and part["start"] == row_count
            )
            integer(part["count"], 1, 10000)
            self.descriptor(
                part,
                job,
                "sources/market/parts/" + str(ordinal),
                LIMITS["sourceChunkBytes"],
            )
            row_count += part["count"]
            chunk_bytes += part["byteLength"]
        require(
            row_count == snap["rowCount"]
            and chunk_bytes <= snap["byteLength"] + 2 * len(snap["parts"]),
            "DATASET_SOURCE_IDENTITY",
        )
        financial = meta["sources"]["financial"]
        require(
            isinstance(financial, list)
            and 1 <= len(financial) <= LIMITS["financialInputs"]
            and isinstance(plan["financialInputs"], list)
            and len(plan["financialInputs"]) == len(financial),
            "DATASET_INPUT_BUDGET",
        )
        expected = {}
        for item in plan["financialInputs"]:
            keys(
                item,
                {
                    "inputId",
                    "preparationId",
                    "inputRoot",
                    "packRoot",
                    "preparedRoot",
                    "calendarRoot",
                },
            )
            identifier(item["inputId"])
            identifier(item["preparationId"])
            require(item["inputId"] not in expected, "DATASET_SOURCE_IDENTITY")
            for key in ("inputRoot", "packRoot", "preparedRoot", "calendarRoot"):
                digest(item[key])
            expected[item["inputId"]] = item
        refs = {plan["marketCalendarRef"]}
        total_packages = 0
        ids = set()
        observed = set()
        part_count = 0
        for index, source in enumerate(financial):
            keys(
                source,
                {
                    "sourceId",
                    "inputId",
                    "preparationId",
                    "roots",
                    "calendarRef",
                    "proofRefs",
                    "package",
                },
            )
            require(
                source["sourceId"] == "financial" + str(index)
                and source["sourceId"] not in ids
                and source["inputId"] in expected
                and source["inputId"] not in observed,
                "DATASET_SOURCE_IDENTITY",
            )
            ids.add(source["sourceId"])
            observed.add(source["inputId"])
            item = expected[source["inputId"]]
            require(
                source["preparationId"] == item["preparationId"]
                and source["roots"]
                == {
                    k: item[k]
                    for k in ("inputRoot", "packRoot", "preparedRoot", "calendarRoot")
                },
                "DATASET_SOURCE_IDENTITY",
            )
            identifier(source["calendarRef"])
            refs.add(source["calendarRef"])
            proof = source["proofRefs"]
            require(
                isinstance(proof, list)
                and len(proof) <= 256
                and all(isinstance(v, str) for v in proof)
                and proof == sorted(set(proof)),
                "DATASET_INPUT_BUDGET",
            )
            for ref in proof:
                identifier(ref)
                refs.add(ref)
            package = source["package"]
            keys(package, {"sha256", "byteLength", "parts"})
            digest(package["sha256"])
            integer(package["byteLength"], 1, LIMITS["packageBytes"])
            require(
                isinstance(package["parts"], list) and package["parts"],
                "DATASET_INPUT_BUDGET",
            )
            size = 0
            for ordinal, part in enumerate(package["parts"]):
                keys(
                    part,
                    {"ordinal", "startRow", "rowCount", "sha256", "byteLength", "url"},
                )
                require(
                    type(part["ordinal"]) is int
                    and part["ordinal"] == ordinal
                    and part["startRow"] is None
                    and part["rowCount"] is None
                )
                self.descriptor(
                    part,
                    job,
                    "sources/" + source["sourceId"] + "/parts/" + str(ordinal),
                    LIMITS["partBytes"],
                )
                size += part["byteLength"]
                part_count += 1
                require(part_count <= LIMITS["parts"], "DATASET_INPUT_BUDGET")
            require(size == package["byteLength"], "DATASET_SOURCE_IDENTITY")
            total_packages += size
        require(total_packages <= LIMITS["packageBytes"], "DATASET_INPUT_BUDGET")
        registry = meta["registry"]
        keys(registry, {"count", "totalBytes", "listUrl"})
        integer(registry["count"], 1, LIMITS["registryEntries"])
        integer(registry["totalBytes"], 1, LIMITS["registryTotalBytes"])
        require(
            registry["count"] == len(refs)
            and registry["listUrl"] == self.prefix + "jobs/" + job["id"] + "/registry",
            "DATASET_SOURCE_IDENTITY",
        )
        require(
            market["manifest"]["byteLength"]
            + snap["byteLength"]
            + total_packages
            + registry["totalBytes"]
            + len(encode(meta))
            < LIMITS["closureBytes"],
            "DATASET_INPUT_BUDGET",
        )
        return refs

    def registry_plan(self, meta, job, refs, *, deadline):
        entries = []
        offset = 0
        seen = set()
        size = 0
        while offset < meta["registry"]["count"]:
            page = self.get(
                "jobs/" + job["id"] + "/registry?offset=" + str(offset),
                job["leaseToken"],
                deadline=deadline,
            )
            keys(page, {"items", "total", "offset", "nextOffset"})
            require(
                type(page["total"]) is int
                and page["total"] == meta["registry"]["count"]
                and type(page["offset"]) is int
                and page["offset"] == offset
                and isinstance(page["items"], list)
                and 1 <= len(page["items"]) <= 64,
                "DATASET_REGISTRY",
            )
            for entry in page["items"]:
                keys(entry, {"ref", "kind", "sha256", "byteLength", "url"})
                ref = identifier(entry["ref"])
                require(
                    ref in refs
                    and ref not in seen
                    and entry["kind"] in {"calendar", "unit_proof"},
                    "DATASET_REGISTRY",
                )
                self.descriptor(
                    entry, job, "registry/" + ref, LIMITS["registryEntryBytes"]
                )
                seen.add(ref)
                size += entry["byteLength"]
                entries.append(entry)
                require(size <= meta["registry"]["totalBytes"], "DATASET_INPUT_BUDGET")
            offset += len(page["items"])
            require(
                offset <= meta["registry"]["count"]
                and (page["nextOffset"] is None or type(page["nextOffset"]) is int)
                and page["nextOffset"]
                == (offset if offset < meta["registry"]["count"] else None),
                "DATASET_REGISTRY",
            )
        require(
            seen == refs and size == meta["registry"]["totalBytes"], "DATASET_REGISTRY"
        )
        return entries

    def inputs(self, job, *, deadline, heartbeat, remember_input=None):
        heartbeat()
        meta = self.get(
            "jobs/" + job["id"] + "/input", job["leaseToken"], deadline=deadline
        )
        refs = self.source_plan(meta, job)
        if remember_input:
            remember_input(job, meta)
        registry_descriptors = self.registry_plan(meta, job, refs, deadline=deadline)
        source = meta["sources"]["market"]
        manifest_raw = self.download(
            source["manifest"],
            job,
            "sources/market/manifest",
            LIMITS["sourceManifestBytes"],
            deadline=deadline,
            heartbeat=heartbeat,
        )
        manifest = bundle.validate_manifest(manifest_raw, source["bundleId"])
        require(manifest["kind"] == "forecast", "DATASET_SOURCE_IDENTITY")
        collection = next(
            c for c in manifest["collections"] if c["id"] == "snapshotRows"
        )
        require(
            collection["rowCount"] == source["snapshot"]["rowCount"]
            and collection["chunks"]
            == [
                {k: v for k, v in p.items() if k != "url"}
                for p in source["snapshot"]["parts"]
            ],
            "DATASET_SOURCE_IDENTITY",
        )
        require(
            manifest["documents"]["snapshot"]["sha256"] == source["snapshot"]["sha256"]
            and manifest["documents"]["snapshot"]["byteLength"]
            == source["snapshot"]["byteLength"],
            "DATASET_SOURCE_IDENTITY",
        )
        parts = source["snapshot"]["parts"]

        def read_source(collection, ordinal):
            require(
                collection == "snapshotRows"
                and type(ordinal) is int
                and 0 <= ordinal < len(parts),
                "DATASET_SOURCE_IDENTITY",
            )
            return self.download(
                parts[ordinal],
                job,
                "sources/market/parts/" + str(ordinal),
                LIMITS["sourceChunkBytes"],
                deadline=deadline,
                heartbeat=heartbeat,
            )

        buffers = []
        size = 0
        for raw in bundle.iter_document_bytes(manifest, "snapshot", read_source):
            size += len(raw)
            require(size <= source["snapshot"]["byteLength"], "DATASET_INPUT_BUDGET")
            buffers.append(raw)
        snapshot_raw = b"".join(buffers)
        require(
            len(snapshot_raw) == source["snapshot"]["byteLength"]
            and sha(snapshot_raw) == source["snapshot"]["sha256"],
            "DATASET_INTEGRITY",
        )
        packages = {}
        for source in meta["sources"]["financial"]:
            raw = b"".join(
                self.download(
                    part,
                    job,
                    "sources/" + source["sourceId"] + "/parts/" + str(part["ordinal"]),
                    LIMITS["partBytes"],
                    deadline=deadline,
                    heartbeat=heartbeat,
                )
                for part in source["package"]["parts"]
            )
            require(
                len(raw) == source["package"]["byteLength"]
                and sha(raw) == source["package"]["sha256"],
                "DATASET_INTEGRITY",
            )
            packages[source["sourceId"]] = raw
        registry = {
            entry["ref"]: self.download(
                entry,
                job,
                "registry/" + entry["ref"],
                LIMITS["registryEntryBytes"],
                deadline=deadline,
                heartbeat=heartbeat,
            )
            for entry in registry_descriptors
        }
        heartbeat()
        return meta, manifest_raw, snapshot_raw, packages, registry

    def upload(
        self, job, publication_id, dataset_root, component, part, raw, *, deadline
    ):
        require(isinstance(component, str) and COMPONENT.fullmatch(component))
        integer(part["ordinal"], 0, LIMITS["parts"] - 1)
        route = (
            "jobs/"
            + identifier(job["id"])
            + "/publication/"
            + identifier(publication_id)
            + "/parts/"
            + component
            + "/"
            + str(part["ordinal"])
            + "?datasetRoot="
            + digest(dataset_root)
        )
        value = decode(
            self._request(
                "PUT", route, raw=raw, lease=job["leaseToken"], deadline=deadline
            ),
            limit=LIMITS["inputMetadataBytes"],
        )
        require(
            isinstance(value, dict)
            and value.get("ok") is True
            and value.get("componentId") == component
            and value.get("ordinal") == part["ordinal"]
            and value.get("sha256") == sha(raw)
            and value.get("byteLength") == len(raw),
            "DATASET_PART_ACK",
        )
        return value
