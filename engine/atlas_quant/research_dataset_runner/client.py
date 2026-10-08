"""Lease-fenced reads from one fixed dataset root; no provider credentials."""

from urllib.parse import urlsplit

from ..dataset_runner.client import DatasetClient
from ..dataset_runner.protocol import (
    LIMITS,
    PROFILE,
    decode,
    encode,
    digest,
    identifier,
    integer,
    keys,
    require,
)
from ..research_dataset.snapshot import dataset_reference
from ..research_dataset.research_profile import admit_profile
from ..research_dataset import DatasetReader
from .spool import ResearchDatasetSpool


class ResearchDatasetClient(DatasetClient):
    def __init__(self, config, session=None):
        super().__init__(config, session)
        self.base = config["api_base"].rstrip("/") + "/runner/research-datasets"
        self.prefix = urlsplit(self.base).path + "/"

    def _request(self, method, route, **kwargs):
        require(method == "GET", "DATASET_ROUTE")
        return super()._request(method, route, **kwargs)

    def source_contract(self, job):
        """Legacy dataset/2 stays explicit; graph/3 uses a separate subclass."""
        reference = dataset_reference(job.get("datasetRef"))
        require(reference["version"] == 2)
        profile = admit_profile(job.get("admissionProfile"), 2)
        require(job.get("admissionProfile") == profile, "DATASET_INPUT_IDENTITY")
        require(job.get("resultTransport") == {"format": "atlas.quant.financial_bundle", "version": 1},
                "DATASET_INPUT_IDENTITY")
        return reference, profile

    def source_store(self, spool, job):
        return ResearchDatasetSpool.from_spool(spool, job)

    def source_reader(self, manifest, store, reference, job):
        reader = DatasetReader(manifest, lambda c, n: store.read(store.part_name(c, n)),
                               expected_root=reference["datasetRoot"])
        require(reader.manifest["version"] == 2 and reader.manifest["profile"] == PROFILE)
        return reader

    def prepare(self, job, spool, *, deadline, check):
        # Only a small control envelope is ever passed to multiprocessing.spawn.
        require(len(encode(job)) <= LIMITS["inputMetadataBytes"], "DATASET_BYTE_BUDGET")
        identifier(job.get("id"))
        identifier(job.get("leaseToken"))
        require(
            job.get("jobKind") == "forecast"
            and job.get("dataSource") == "ready_dataset"
            and job.get("dataset") is None,
            "DATASET_INPUT_IDENTITY",
        )
        reference, research_profile = self.source_contract(job)
        evidence = {"datasetRef": reference, "admissionProfile": research_profile}
        require(job.get("sourceEvidence") == evidence, "DATASET_INPUT_IDENTITY")
        route = job["id"] + "/"
        require(
            job.get("datasetInputUrl") == self.prefix + route + "input", "DATASET_ROUTE"
        )

        def get(suffix, limit, descriptor=None):
            return self._request(
                "GET",
                route + suffix,
                lease=job["leaseToken"],
                limit=limit,
                deadline=deadline,
                heartbeat=check,
                descriptor=descriptor,
            )

        raw = get("input", LIMITS["inputMetadataBytes"])
        meta = decode(raw, limit=LIMITS["inputMetadataBytes"])
        keys(
            meta,
            {
                "job",
                "datasetRef",
                "admissionProfile",
                "sourceEvidence",
                "manifest",
                "partUrlTemplate",
                "registry",
                "limits",
            },
        )
        require(
            meta["job"] == {"id": job["id"], "kind": "forecast"}
            and meta["datasetRef"] == reference
            and meta["sourceEvidence"] == evidence
            and meta["admissionProfile"] == research_profile,
            "DATASET_INPUT_IDENTITY",
        )
        require(meta["limits"] == LIMITS, "DATASET_LIMITS")
        query = "?datasetRoot=" + reference["datasetRoot"]

        def descriptor(value, suffix, maximum):
            keys(value, {"sha256", "byteLength", "url"})
            digest(value["sha256"])
            integer(value["byteLength"], 1, maximum)
            require(value["url"] == self.prefix + route + suffix, "DATASET_ROUTE")
            return get(suffix, value["byteLength"], value)

        require(
            meta["manifest"]["sha256"] == reference["datasetRoot"],
            "DATASET_INPUT_IDENTITY",
        )
        require(
            meta["partUrlTemplate"]
            == self.prefix + route + "parts/{componentId}/{ordinal}" + query,
            "DATASET_ROUTE",
        )
        keys(meta["registry"], {"count", "totalBytes", "listUrl"})
        count = integer(meta["registry"]["count"], 1, LIMITS["registryEntries"])
        registry_bytes = integer(
            meta["registry"]["totalBytes"], 1, LIMITS["registryTotalBytes"]
        )
        require(
            meta["registry"]["listUrl"]
            == self.prefix + route + "registry" + query + "&offset=0",
            "DATASET_ROUTE",
        )
        manifest = descriptor(
            meta["manifest"], "manifest" + query, LIMITS["manifestBytes"]
        )
        store = self.source_store(spool, job)
        reader = self.source_reader(manifest, store, reference, job)
        # Complete registry page admission precedes any source payload download.
        index, refs, total, offset = [], set(), 0, 0
        while offset < count:
            page = decode(
                get(
                    "registry" + query + "&offset=" + str(offset),
                    LIMITS["inputMetadataBytes"],
                ),
                limit=LIMITS["inputMetadataBytes"],
            )
            keys(page, {"items", "total", "offset", "nextOffset"})
            require(
                type(page["total"]) is int
                and page["total"] == count
                and type(page["offset"]) is int
                and page["offset"] == offset
            )
            require(
                isinstance(page["items"], list)
                and 1 <= len(page["items"]) <= 64
                and offset + len(page["items"]) <= count
            )
            for item in page["items"]:
                keys(item, {"ref", "kind", "sha256", "byteLength", "url"})
                identifier(item["ref"])
                digest(item["sha256"])
                integer(item["byteLength"], 1, LIMITS["registryEntryBytes"])
                require(
                    item["ref"] not in refs and isinstance(item["kind"], str),
                    "DATASET_INPUT_IDENTITY",
                )
                require(
                    item["url"]
                    == self.prefix + route + "registry/" + item["ref"] + query,
                    "DATASET_ROUTE",
                )
                refs.add(item["ref"])
                total += item["byteLength"]
                require(total <= registry_bytes, "DATASET_BYTE_BUDGET")
                index.append(item)
            offset += len(page["items"])
            require(page["nextOffset"] == (offset if offset < count else None))
        require(total == registry_bytes, "DATASET_BYTE_BUDGET")
        stored_index = [
            {k: item[k] for k in ("ref", "sha256", "byteLength")} for item in index
        ]
        require(len(encode(stored_index)) <= LIMITS["partBytes"], "DATASET_BYTE_BUDGET")
        for item in index:
            value = {k: item[k] for k in ("sha256", "byteLength", "url")}
            raw = descriptor(
                value, "registry/" + item["ref"] + query, LIMITS["registryEntryBytes"]
            )
            store.write("registry-" + item["ref"], raw)
        for component in reader.manifest["components"]:
            for part in component["parts"]:
                suffix = f"parts/{component['componentId']}/{part['ordinal']}" + query
                value = {
                    "sha256": part["sha256"],
                    "byteLength": part["byteLength"],
                    "url": self.prefix + route + suffix,
                }
                store.write(
                    store.part_name(component["componentId"], part["ordinal"]),
                    descriptor(value, suffix, LIMITS["partBytes"]),
                )
        store.write("registry-index", encode(stored_index))
        store.write("manifest", manifest)
        store.write(
            "input", encode(meta)
        )  # Final admission marker; no model fitting here.
        return dict(job, _datasetKey=store.key)
