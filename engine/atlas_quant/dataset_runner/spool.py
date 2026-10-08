"""Reuse private authenticated filesystem primitives in a separate key domain."""

import hashlib
from pathlib import Path
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..financial_runner.spool import FinancialSpool, PublicationSpool, _sync, TERMINAL
from ..research_dataset import DatasetReader
from ..research_dataset.manifest import validate_manifest
from .protocol import LIMITS, PROFILE, encode, require, sha

COMPONENT = re.compile(
    r"(?:registryEvidence|marketOrigin|marketDataset|researchRows|schema|coverage|financialInput[0-7]|financialPrepared[0-7])"
)


class DatasetSpool(FinancialSpool):
    def __init__(self, config):
        root = config.get("dataset_delivery_dir")
        require(
            isinstance(root, str) and Path(root).is_absolute(), "DATASET_SPOOL_PATH"
        )
        root = Path(root).expanduser()
        require(not root.is_symlink(), "DATASET_SPOOL_PATH")
        for value in (
            config["delivery_dir"],
            config.get(
                "financial_delivery_dir",
                str(
                    Path(config["delivery_dir"]).with_name(
                        Path(config["delivery_dir"]).name + "-financial"
                    )
                ),
            ),
            config.get("acquisition_delivery_dir"),
        ):
            if value is not None:
                other = Path(value).expanduser().resolve()
                target = root.resolve()
                require(
                    target != other
                    and target not in other.parents
                    and other not in target.parents,
                    "DATASET_SPOOL_PATH",
                )
        super().__init__({**config, "financial_delivery_dir": str(root)})
        self.key = hashlib.sha256(
            b"atlas-quant-dataset-key-v1\0" + config["runner_secret"].encode()
        ).digest()
        self.aad = ("atlas-quant-dataset-spool-v1:" + config["api_base"]).encode()
        self.cipher = AESGCM(self.key)

    def publication(self, job):
        return DatasetPublicationSpool(self, job)

    def _input_path(self, job):
        key = sha((job["id"] + "\0" + job["leaseToken"]).encode())
        return (
            self.root / ("input-" + key + ".enc"),
            self.aad + b":input:" + key.encode(),
        )

    def remember_input(self, job, metadata):
        raw = encode(metadata)
        require(len(raw) <= LIMITS["inputMetadataBytes"], "DATASET_INPUT_BUDGET")
        path, aad = self._input_path(job)
        if path.exists() or path.is_symlink():
            require(
                self._read(path, LIMITS["inputMetadataBytes"], aad) == raw,
                "DATASET_INPUT_CHANGED",
            )
        else:
            self._write(path, raw, aad)

    def cleanup(self, state):
        # Parent confirms the same durable UUID is terminal before deleting parts.
        current = self.read()
        if current is None:
            return
        require(
            current["requestId"] == state["requestId"]
            and current["phase"] == "terminal"
            and current.get("terminalStatus") in TERMINAL,
            "DATASET_SPOOL_INTEGRITY",
        )
        job = state.get("job")
        if job:
            path, aad = self._input_path(job)
            require(not path.is_symlink(), "DATASET_SPOOL_INTEGRITY")
            if path.exists():
                self._read(path, LIMITS["inputMetadataBytes"], aad)
            from .source_spool import cleanup_sources

            cleanup_sources(self, job)
        if job:
            if path.exists():
                path.unlink()
                _sync(self.root)
        super().cleanup(state)


class DatasetPublicationSpool(PublicationSpool):
    def _name(self, component, ordinal):
        require(
            isinstance(component, str)
            and COMPONENT.fullmatch(component)
            and type(ordinal) is int
            and 0 <= ordinal < LIMITS["parts"],
            "DATASET_PART_IDENTITY",
        )
        return component + "-" + str(ordinal)

    def write_chunk(self, component, ordinal, raw):
        require(len(self.written) < LIMITS["parts"], "DATASET_RESULT_BUDGET")
        super().write_chunk(component, ordinal, raw)

    def validate_manifest(self, manifest):
        raw = encode(manifest)
        parsed = validate_manifest(raw)
        require(
            parsed["version"] == 2 and parsed["profile"] == PROFILE, "DATASET_MANIFEST"
        )
        names = {
            self._name(c["componentId"], p["ordinal"]) + ".enc"
            for c in parsed["components"]
            for p in c["parts"]
        }
        require(
            {p.name for p in self.root.iterdir() if p.name != "manifest.enc"} == names,
            "DATASET_MANIFEST",
        )
        descriptors = {
            (c["componentId"], p["ordinal"]): p
            for c in parsed["components"]
            for p in c["parts"]
        }
        reader = DatasetReader(
            raw, lambda name, ordinal: self.read_chunk(name, descriptors[name, ordinal])
        )
        reader.verify_integrity()
        return parsed
