"""Separate key and path domains for graph-composition claims and source output."""
import hashlib
import resource
import shutil
import sys
import time
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ..dataset_runner.spool import DatasetSpool,DatasetPublicationSpool
from ..dataset_runner.protocol import require,encode,decode,sha
from ..financial_runner.spool import _private,_sync,TERMINAL
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

    def rejection(self, state):
        job = state.get("job")
        if not job:
            return None
        key = sha((job["id"] + "\0" + job["leaseToken"]).encode())
        root = self.root / "quarantine"
        path = root / (key + ".enc")
        require(not root.is_symlink(), "DATASET_SPOOL_INTEGRITY")
        if not path.exists() and not path.is_symlink():
            return None
        _private(root)
        value = decode(self._read(path, 32768, self.aad + b":rejection:" + key.encode()), limit=32768)
        require(isinstance(value, dict) and set(value) == {"schemaVersion", "originalState", "rejection"}
                and value["schemaVersion"] == 1 and value["originalState"].get("requestId") == state["requestId"]
                and value["originalState"].get("job") == job, "DATASET_SPOOL_INTEGRITY")
        return value

    def preserve_rejection(self, state, error):
        current = self.read()
        require(current is not None and current["requestId"] == state["requestId"]
                and state["phase"] == "publishing", "DATASET_SPOOL_INTEGRITY")
        if self.rejection(state) is not None:
            return
        job = state["job"]
        key = sha((job["id"] + "\0" + job["leaseToken"]).encode())
        root = _private(self.root / "quarantine")
        value = {"schemaVersion": 1, "originalState": state,
                 "rejection": {"httpStatus": error.http_status, "clientCode": error.code, "recordedAt": time.time()}}
        self._write(root / (key + ".enc"), encode(value), self.aad + b":rejection:" + key.encode())

    def read(self):
        state = super().read()
        if state is not None and state["phase"] not in {"failing", "terminal"} and self.rejection(state) is not None:
            # Crash after the rejection marker: settle the failure, never publish again.
            return dict(state, phase="failing", error={"code": "DATASET_RESULT_REJECTED",
                        "message": "组成产物被拒绝；加密原件已隔离保留。"})
        return state

    def cleanup(self, state):
        if self.rejection(state) is None:
            return super().cleanup(state)
        current = self.read()
        if current is None:
            return
        require(current["requestId"] == state["requestId"]
                and current["phase"] == "terminal" and current.get("terminalStatus") in TERMINAL,
                "DATASET_SPOOL_INTEGRITY")
        # Preserve source cache, metadata, output and rejection; only release the claim.
        (self.root / "current.enc").unlink()
        _sync(self.root)


class GraphPublicationSpool(DatasetPublicationSpool):
    COMPONENT_PATTERN = COMPONENT
    VERSION = 3
    PROFILE_ID = PROFILE
    READER_CLASS = GraphDatasetReader
    manifest_decoder = staticmethod(validate_manifest)

    def finalize(self, manifest):
        self.validate_manifest(manifest)
        if getattr(self, "before_commit", None) is not None:
            self.before_commit()
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        require(peak <= 3 * 1024**3, "CAPACITY_MEMORY")
        require(shutil.disk_usage(self.root).free >= 500 * 1024**2, "CAPACITY_DISK")
        require(not (self.root / "manifest.enc").exists(), "DATASET_SPOOL_INTEGRITY")
        self.spool._write(self.root / "manifest.enc", encode(manifest), self.aad + b":manifest")
