"""Independent authenticated source files; small references cross spawn IPC."""

from ..bundle_spool import BundleSpool
from ..market_acquisition.protocol import META_BYTES, decode, require
from ..market_acquisition.reader import MarketSourceReader


class MarketResearchSpool(BundleSpool):
    DIRECTORY = "research-market-inputs"
    AAD_TAG = b":research-market-input-v1:"
    MAGIC = b"AQM1"

    @staticmethod
    def part_name(collection, ordinal):
        require(
            collection in {"rows", "receipts", "provenance", "raw"}
            and type(ordinal) is int
            and 0 <= ordinal < 320,
            "MARKET_SOURCE_PART",
            "Unregistered source part",
        )
        return f"part-{collection}-{ordinal}"

    def reader(self, expected_root):
        return MarketSourceReader(
            self.read("manifest"),
            self.read("plan"),
            self.read("scope"),
            lambda n, i: self.read(self.part_name(n, i)),
            expected_root=expected_root,
        )

    def inputs(self, job):
        require(
            job.get("_marketKey") == self.key,
            "MARKET_SOURCE_IDENTITY",
            "Wrong encrypted source identity",
        )
        meta = decode(self.read("input"), limit=META_BYTES)
        require(
            all(
                meta[k] == job[k]
                for k in (
                    "marketDatasetRef",
                    "universeScopeRef",
                    "admissionProfile",
                    "sourceEvidence",
                )
            ),
            "MARKET_SOURCE_IDENTITY",
            "Source metadata differs from claimed admission",
        )
        return self.reader(job["marketDatasetRef"]["datasetRoot"])

    def cleanup(self):
        import shutil

        cache = self.root / "cache"
        if cache.exists():
            require(
                not cache.is_symlink() and cache.is_dir(),
                "DELIVERY_INTEGRITY",
                "Unexpected market cache entry",
            )
            require(
                not any(p.is_symlink() for p in cache.rglob("*")),
                "DELIVERY_INTEGRITY",
                "Market cache links prevent automatic cleanup",
            )
            shutil.rmtree(cache)
        return super().cleanup()
