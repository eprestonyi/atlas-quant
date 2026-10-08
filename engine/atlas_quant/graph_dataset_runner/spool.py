"""Separate key and path domains for graph-composition claims and source output."""
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ..dataset_runner.spool import DatasetSpool,DatasetPublicationSpool
from ..dataset_runner.protocol import require
from ..research_dataset.graph_v3.dataset import GraphDatasetReader
from ..research_dataset.graph_v3.manifest import validate_manifest
from .protocol import PROFILE,COMPONENT


class GraphDatasetSpool(DatasetSpool):
    def __init__(self, config):
        root = config.get("graph_dataset_delivery_dir")
        require(isinstance(root, str) and Path(root).is_absolute(), "DATASET_SPOOL_PATH")
        old = config.get("dataset_delivery_dir")
        if old is not None:
            target, legacy = Path(root).resolve(), Path(old).resolve()
            require(target != legacy and target not in legacy.parents and legacy not in target.parents,
                    "DATASET_SPOOL_PATH")
        super().__init__({**config, "dataset_delivery_dir": root})
        self.key = hashlib.sha256(b"atlas-quant-graph-dataset-key-v3\0" + config["runner_secret"].encode()).digest()
        self.aad = ("atlas-quant-graph-dataset-spool-v3:" + config["api_base"]).encode()
        self.cipher = AESGCM(self.key)

    def publication(self, job):
        return GraphPublicationSpool(self, job)


class GraphPublicationSpool(DatasetPublicationSpool):
    COMPONENT_PATTERN = COMPONENT
    VERSION = 3
    PROFILE_ID = PROFILE
    READER_CLASS = GraphDatasetReader
    manifest_decoder = staticmethod(validate_manifest)
