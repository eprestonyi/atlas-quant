"""Market-specific encryption domain over the shared fsync/file-lock primitives."""

import hashlib
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ..financial_acquisition.spool import AcquisitionSpool
from .protocol import *


class MarketSpool(AcquisitionSpool):
    def __init__(self, config):
        root = Path(
            config.get("market_delivery_dir", str(config["delivery_dir"]) + "-market")
        ).expanduser()
        prior = (
            Path(
                config.get(
                    "acquisition_delivery_dir",
                    str(config["delivery_dir"]) + "-acquisition",
                )
            )
            .expanduser()
            .resolve()
        )
        require(
            root.is_absolute()
            and root.resolve() != prior
            and prior not in root.resolve().parents
            and root.resolve() not in prior.parents,
            "MARKET_SPOOL_PATH",
            "Market spool must be independent of provider/preparation/research state",
        )
        super().__init__({**config, "acquisition_delivery_dir": str(root)})
        self.key = hashlib.sha256(
            b"atlas-quant-market-acquisition-key-v1\0"
            + config["runner_secret"].encode()
        ).digest()
        self.aad = (
            "atlas-quant-market-acquisition-spool-v1:"
            + config["api_base"]
            + ":"
            + config["authorization_scope"]
        ).encode()
        self.cipher = AESGCM(self.key)

    def state(self):
        raw = self._read("current.enc", META_BYTES)
        return decode(raw, limit=META_BYTES) if raw is not None else None

    def save(self, state):
        identifier(state["requestId"])
        old = self.state()
        require(
            old is None or old["requestId"] == state["requestId"],
            "MARKET_SPOOL_IDENTITY",
            "Cannot replace unresolved market claim",
        )
        self._write("current.enc", encode(state), META_BYTES)
        return state

    def metadata(self, job, value=None):
        name = identifier(job["id"]) + "-input.enc"
        if value is not None:
            self._write(name, encode(value), META_BYTES)
        raw = self._read(name, META_BYTES)
        return decode(raw, limit=META_BYTES) if raw is not None else None

    def _part(self, job, collection, ordinal):
        require(
            collection in {"rows", "provenance", "receipts"}
            and type(ordinal) is int
            and 0 <= ordinal < MAX_CHUNKS,
            "MARKET_SPOOL_PART",
            "Unknown market part",
        )
        return f'{identifier(job["id"])}-{collection}-{ordinal}.part.enc'

    def write_chunk(self, job, collection, ordinal, raw):
        self._write(self._part(job, collection, ordinal), raw, CHUNK_BYTES)

    def save_manifest(self, job, manifest):
        for name, c in manifest["collections"].items():
            for p in c["chunks"]:
                self.chunk(job, name, p)
        self._write(
            identifier(job["id"]) + "-manifest.enc", encode(manifest), MANIFEST_BYTES
        )

    def publication(self, job):
        raw = self._read(identifier(job["id"]) + "-manifest.enc", MANIFEST_BYTES)
        return decode(raw, limit=MANIFEST_BYTES) if raw is not None else None

    def terminal(self, state, status, *, retain=False):
        require(
            status in {"completed", "failed", "cancelled", "empty"},
            "MARKET_TERMINAL",
            "Explicit terminal receipt required",
        )
        state = self.save(
            {**state, "phase": "terminal", "terminalStatus": status, "retain": retain}
        )
        jobid = state.get("job", {}).get("id")
        if retain:
            self._write(
                identifier(jobid or state["requestId"]) + "-review.enc",
                encode(state),
                META_BYTES,
            )
        elif jobid:
            for p in self.root.glob(identifier(jobid) + "-*.enc"):
                require(
                    p.is_file() and not p.is_symlink(),
                    "MARKET_SPOOL_PATH",
                    "Unsafe cleanup path",
                )
                p.unlink()
        (self.root / "current.enc").unlink()
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
