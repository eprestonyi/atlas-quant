"""Fixed-origin, exact-lease downloads of a complete committed market source."""

from urllib.parse import urlsplit
import shutil
from ..dataset_runner.client import DatasetClient
from ..dataset_runner.protocol import keys, integer
from ..market_acquisition.protocol import (
    META_BYTES,
    MANIFEST_BYTES,
    RAW_CHUNK_BYTES,
    CHUNK_BYTES,
    decode,
    encode,
    require,
    digest,
    identifier,
    output_collections,
)
from ..market_acquisition.reader import MarketSourceReader
from ..capacity.profiles import FULL_FILTER_PROFILE_ID, AUTO_FILTER_CANDIDATE_ID
from .spool import MarketResearchSpool


class MarketResearchClient(DatasetClient):
    def __init__(self, config, session=None):
        super().__init__(config, session)
        self.base = config["api_base"].rstrip("/") + "/runner/research-markets"
        self.prefix = urlsplit(self.base).path + "/"

    def _request(self, method, route, **kwargs):
        require(
            method == "GET", "MARKET_SOURCE_ROUTE", "Frozen market access is read-only"
        )
        return super()._request(method, route, **kwargs)

    def prepare(self, job, spool, *, deadline, check):
        require(
            len(encode(job)) <= META_BYTES,
            "MARKET_SOURCE_BUDGET",
            "Claim metadata exceeds budget",
        )
        identifier(job.get("id"))
        identifier(job.get("leaseToken"))
        require(
            job.get("jobKind") == "forecast"
            and job.get("dataSource") == "ready_market"
            and job.get("dataset") is None
            and not any(
                k in job
                for k in ("providerAccess", "pcdAccess", "replay", "replayBundle")
            ),
            "MARKET_SOURCE_IDENTITY",
            "Whole-market research only accepts frozen provider-free input",
        )
        ref = job.get("marketDatasetRef")
        keys(ref, {"datasetId", "datasetRoot", "format", "version"})
        identifier(ref["datasetId"])
        digest(ref["datasetRoot"])
        require(
            ref["format"] == "atlas.quant.market_dataset"
            and type(ref["version"]) is int
            and ref["version"] == 1,
            "MARKET_SOURCE_FORMAT",
            "Unsupported frozen market source",
        )
        profile = job.get("admissionProfile")
        require(
            profile in {FULL_FILTER_PROFILE_ID, AUTO_FILTER_CANDIDATE_ID},
            "MARKET_RESEARCH_PROFILE",
            "Unknown approved profile",
        )
        evidence = {
            "marketDatasetRef": ref,
            "universeScopeRef": job.get("universeScopeRef"),
            "admissionProfile": profile,
            "rowValueRoot": digest(job.get("sourceEvidence", {}).get("rowValueRoot")),
        }
        require(
            job.get("sourceEvidence") == evidence,
            "MARKET_SOURCE_IDENTITY",
            "Incomplete source evidence",
        )
        route = job["id"] + "/"
        require(
            job.get("marketInputUrl") == self.prefix + route + "input",
            "MARKET_SOURCE_ROUTE",
            "Unexpected source URL",
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

        meta = decode(get("input", META_BYTES), limit=META_BYTES)
        keys(
            meta,
            {
                "job",
                "marketDatasetRef",
                "universeScopeRef",
                "admissionProfile",
                "sourceEvidence",
                "documents",
                "partUrlTemplate",
            },
        )
        require(
            meta["job"] == {"id": job["id"], "kind": "forecast"}
            and all(
                meta[k] == job[k]
                for k in (
                    "marketDatasetRef",
                    "universeScopeRef",
                    "admissionProfile",
                    "sourceEvidence",
                )
            ),
            "MARKET_SOURCE_IDENTITY",
            "Input belongs to another source or job",
        )
        keys(meta["documents"], {"manifest", "plan", "scope"})
        query = "?datasetRoot=" + ref["datasetRoot"]
        require(
            meta["partUrlTemplate"]
            == self.prefix + route + "parts/{collection}/{ordinal}" + query,
            "MARKET_SOURCE_ROUTE",
            "Unexpected source template",
        )
        store = MarketResearchSpool.from_spool(spool, job)

        def fetch(descriptor, suffix, limit):
            keys(descriptor, {"sha256", "byteLength", "url"})
            digest(descriptor["sha256"])
            integer(descriptor["byteLength"], 1, limit)
            require(
                descriptor["url"] == self.prefix + route + suffix,
                "MARKET_SOURCE_ROUTE",
                "Source redirect is not allowed",
            )
            return get(suffix, descriptor["byteLength"], descriptor)

        require(
            meta["documents"]["manifest"]["sha256"] == ref["datasetRoot"],
            "MARKET_SOURCE_IDENTITY",
            "Unpinned source manifest",
        )
        documents = {
            name: fetch(
                d, name + query, META_BYTES if name == "plan" else MANIFEST_BYTES
            )
            for name, d in meta["documents"].items()
        }
        reader = MarketSourceReader(
            documents["manifest"],
            documents["plan"],
            documents["scope"],
            lambda n, i: store.read(store.part_name(n, i)),
            expected_root=ref["datasetRoot"],
        )
        require(
            reader.manifest["universeScopeRef"] == job["universeScopeRef"],
            "MARKET_SOURCE_IDENTITY",
            "Original universe differs from admitted scope",
        )
        source_bytes = sum(
            c["byteLength"] for c in output_collections(reader.manifest).values()
        )
        require(
            shutil.disk_usage(store.root).free
            > source_bytes + (256 + 400 + 500) * 1024 * 1024,
            "MARKET_SOURCE_DISK",
            "Insufficient space for source, result, cache and reserve",
        )
        # The complete descriptor graph is validated before any raw body is read.
        for name, c in output_collections(reader.manifest).items():
            for p in c["chunks"]:
                suffix = f"parts/{name}/{p['ordinal']}" + query
                descriptor = {k: p[k] for k in ("sha256", "byteLength")}
                descriptor["url"] = self.prefix + route + suffix
                store.write(
                    store.part_name(name, p["ordinal"]),
                    fetch(
                        descriptor,
                        suffix,
                        RAW_CHUNK_BYTES if name == "raw" else CHUNK_BYTES,
                    ),
                )
        for name, raw in documents.items():
            store.write(name, raw)
        store.write("input", encode(meta))  # Final complete-input marker.
        return dict(job, _marketKey=store.key)
