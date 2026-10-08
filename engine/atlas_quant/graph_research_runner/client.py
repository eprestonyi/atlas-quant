"""Graph-only metadata admission using the fixed lease-fenced byte reader."""
from ..dataset_runner.protocol import require
from ..research_dataset_runner.client import ResearchDatasetClient
from ..research_dataset.graph_v3.snapshot import dataset_reference, RESEARCH_PROFILE, validate_research_profile
from .spool import GraphResearchSpool


class GraphResearchClient(ResearchDatasetClient):
    def source_contract(self, job):
        reference = dataset_reference(job.get("datasetRef"))
        require(job.get("admissionProfile") == RESEARCH_PROFILE, "DATASET_INPUT_IDENTITY")
        require(job.get("resultTransport") == {"format": "atlas.quant.financial_bundle", "version": 2},
                "DATASET_INPUT_IDENTITY")
        require(not any(k in job for k in ("providerAccess", "pcdAccess", "replay", "replayBundle")),
                "DATASET_INPUT_IDENTITY")
        return reference, RESEARCH_PROFILE

    def source_store(self, spool, job):
        return GraphResearchSpool.from_spool(spool, job)

    def source_reader(self, manifest, store, reference, job):
        from ..research_dataset.graph_v3.dataset import GraphDatasetReader
        reader = GraphDatasetReader(manifest, lambda c, n: store.read(store.part_name(c, n)),
                                    expected_root=reference["datasetRoot"])
        validate_research_profile(job.get("strategy"), reader.manifest["scope"], research_profile=RESEARCH_PROFILE)
        return reader
