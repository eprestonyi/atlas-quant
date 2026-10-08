"""Encrypted graph source bytes; no legacy format shares this domain."""
from ..dataset_runner.protocol import require, decode, LIMITS
from ..research_dataset_runner.spool import ResearchDatasetSpool
from ..research_dataset.graph_v3.dataset import GraphDatasetReader
from ..research_dataset.graph_v3.snapshot import dataset_reference, RESEARCH_PROFILE


class GraphResearchSpool(ResearchDatasetSpool):
    DIRECTORY = "research-graph-inputs"
    AAD_TAG = b":research-graph-input-v3:"
    MAGIC = b"AQG3"

    def reader(self, expected_root):
        return GraphDatasetReader(self.read("manifest"),
            lambda c, n: self.read(self.part_name(c, n)), expected_root=expected_root)

    def inputs(self, job):
        reference = dataset_reference(job.get("datasetRef"))
        expected = {"datasetRef": reference, "admissionProfile": RESEARCH_PROFILE}
        require(job.get("sourceEvidence") == expected and job.get("admissionProfile") == RESEARCH_PROFILE,
                "DATASET_INPUT_IDENTITY")
        meta = decode(self.read("input"), limit=LIMITS["inputMetadataBytes"])
        require(meta.get("job") == {"id": job["id"], "kind": "forecast"}
                and meta.get("admissionProfile") == RESEARCH_PROFILE, "DATASET_INPUT_IDENTITY")
        return super().inputs(job)
