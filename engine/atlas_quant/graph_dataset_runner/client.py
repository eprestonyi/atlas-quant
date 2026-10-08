"""Graph source composition routes with unchanged bounded byte transport."""
from urllib.parse import urlsplit
from ..dataset_runner.client import DatasetClient
from ..research_dataset.graph_v3.manifest import scope
from .protocol import PROFILE,COMPONENT,NAMESPACE


class GraphDatasetClient(DatasetClient):
    PROFILE_ID = PROFILE
    COMPONENT_PATTERN = COMPONENT

    def __init__(self, config, session=None):
        super().__init__(config, session)
        self.base = config["api_base"].rstrip("/") + "/runner/" + NAMESPACE
        self.prefix = urlsplit(self.base).path + "/"

    def source_plan(self, meta, job):
        refs = super().source_plan(meta, job)
        scope(meta["plan"]["scope"])
        return refs
