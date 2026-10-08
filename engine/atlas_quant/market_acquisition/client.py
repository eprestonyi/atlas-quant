"""Reuse bounded control transport, with an isolated fixed market route."""

from urllib.parse import urlsplit
from ..financial_acquisition.client import AcquisitionClient
from .protocol import *


class MarketClient(AcquisitionClient):
    def __init__(self, config, session=None):
        super().__init__(
            {
                **config,
                "allow_acquisition_fixtures": config.get("allow_market_fixtures")
                is True,
            },
            session,
        )
        self.base = config["api_base"].rstrip("/") + "/runner/market-acquire"
        self.prefix = urlsplit(self.base).path + "/"

    def input(self, job, *, deadline=None):
        route = "jobs/" + identifier(job["id"]) + "/input"
        require(
            job["inputUrl"] == self.prefix + route,
            "MARKET_ROUTE",
            "Claim input URL differs",
        )
        raw, _ = self.request(
            "GET", route, lease=job["leaseToken"], limit=META_BYTES, deadline=deadline
        )
        return decode(raw, limit=META_BYTES)

    def put_chunk(self, job, manifest_hash, collection, ordinal, raw, *, deadline=None):
        require(
            collection in {"rows", "receipts", "provenance"}
            and type(ordinal) is int
            and 0 <= ordinal < MAX_CHUNKS
            and len(raw) <= CHUNK_BYTES,
            "MARKET_PART",
            "Invalid market output part",
        )
        answer, _ = self.request(
            "PUT",
            f"jobs/{identifier(job['id'])}/publication/{collection}/{ordinal}?manifestSha256={digest(manifest_hash)}",
            raw=raw,
            lease=job["leaseToken"],
            deadline=deadline,
        )
        return decode(answer, limit=META_BYTES)
