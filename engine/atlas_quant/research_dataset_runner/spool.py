"""Exact source bytes, isolated from both model outputs and composition jobs."""

from ..bundle_spool import BundleSpool
from ..dataset_runner.protocol import LIMITS, decode, encode, require, sha
from ..research_dataset import DatasetReader


class ResearchDatasetSpool(BundleSpool):
    DIRECTORY = "research-dataset-inputs"
    AAD_TAG = b":research-dataset-input-v1:"
    MAGIC = b"AQI1"

    def part_name(self, component, ordinal):
        # IDs/ordinals are admitted by DatasetReader before this method is used.
        return "part-" + component + "-" + str(ordinal)

    def reader(self, expected_root):
        return DatasetReader(
            self.read("manifest"),
            lambda c, n: self.read(self.part_name(c, n)),
            expected_root=expected_root,
        )

    def inputs(self, job):
        require(job.get("_datasetKey") == self.key, "DATASET_INPUT_IDENTITY")
        meta = decode(self.read("input"), limit=LIMITS["inputMetadataBytes"])
        require(meta["datasetRef"] == job["datasetRef"], "DATASET_INPUT_IDENTITY")
        require(
            meta["sourceEvidence"] == job["sourceEvidence"], "DATASET_INPUT_IDENTITY"
        )
        index = decode(self.read("registry-index"), limit=LIMITS["partBytes"])
        require(isinstance(index, list) and len(index) == meta["registry"]["count"])
        registry, total = {}, 0
        for item in index:
            raw = self.read("registry-" + item["ref"])
            require(
                item["ref"] not in registry
                and len(raw) == item["byteLength"]
                and sha(raw) == item["sha256"],
                "DATASET_INTEGRITY",
            )
            total += len(raw)
            registry[item["ref"]] = raw
        require(total == meta["registry"]["totalBytes"], "DATASET_INTEGRITY")
        return self.reader(job["datasetRef"]["datasetRoot"]), registry
