"""Independent encrypted intent/raw-response log with one writer and fsync gates."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .protocol import (
    CHUNK_BYTES,
    META_BYTES,
    PROFILE,
    decode,
    digest,
    encode,
    identifier,
    require,
    sha,
)


class AcquisitionSpool:
    def __init__(self, config):
        research = Path(config["delivery_dir"]).expanduser().resolve()
        financial = (
            Path(config.get("financial_delivery_dir", str(research) + "-financial"))
            .expanduser()
            .resolve()
        )
        root = Path(
            config.get("acquisition_delivery_dir", str(research) + "-acquisition")
        ).expanduser()
        require(
            root.is_absolute() and not root.is_symlink(),
            "ACQUISITION_SPOOL_PATH",
            "An independent private absolute directory is required",
        )
        self.root = root.resolve()
        for other in (research, financial):
            require(
                self.root != other
                and other not in self.root.parents
                and self.root not in other.parents,
                "ACQUISITION_SPOOL_PATH",
                "Acquisition state cannot share research/preparation directories",
            )
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(
            self.root.is_dir() and not stat.S_IMODE(self.root.stat().st_mode) & 0o077,
            "ACQUISITION_SPOOL_PERMISSIONS",
            "Acquisition directory must be private",
        )
        self.key = hashlib.sha256(
            b"atlas-quant-acquisition-key-v1\0" + config["runner_secret"].encode()
        ).digest()
        self.aad = (
            "atlas-quant-acquisition-spool-v1:"
            + config["api_base"]
            + ":"
            + config["authorization_scope"]
        ).encode()
        self.cipher = AESGCM(self.key)

    @classmethod
    def from_context(cls, context):
        value = cls.__new__(cls)
        value.root = Path(context["root"])
        value.key, value.aad = context["key"], context["aad"]
        value.cipher = AESGCM(value.key)
        return value

    def context(self):
        return {"root": str(self.root), "key": self.key, "aad": self.aad}

    @contextmanager
    def locked(self):
        fd = os.open(
            self.root / "service.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        try:
            require(
                not stat.S_IMODE(os.fstat(fd).st_mode) & 0o077,
                "ACQUISITION_SPOOL_PERMISSIONS",
                "Private lock required",
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                require(
                    False,
                    "ACQUISITION_ALREADY_ACTIVE",
                    "Another acquisition consumer owns this directory",
                )
            yield
        finally:
            os.close(fd)

    def _write(self, name, raw, maximum):
        require(
            isinstance(raw, bytes) and len(raw) <= maximum,
            "ACQUISITION_SPOOL_BUDGET",
            "Recovery payload exceeds its budget",
        )
        target = self.root / name
        require(
            not target.is_symlink(),
            "ACQUISITION_SPOOL_INTEGRITY",
            "Recovery symlink refused",
        )
        nonce = os.urandom(12)
        encrypted = (
            b"AQA1"
            + nonce
            + self.cipher.encrypt(nonce, raw, self.aad + b":" + name.encode())
        )
        temp = self.root / ("write-" + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, target)
            fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temp.exists():
                temp.unlink()

    def _read(self, name, maximum):
        target = self.root / name
        if not target.exists() and not target.is_symlink():
            return None
        try:
            require(
                target.is_file()
                and not target.is_symlink()
                and not stat.S_IMODE(target.stat().st_mode) & 0o077
                and 32 <= target.stat().st_size <= maximum + 32,
                "ACQUISITION_SPOOL_INTEGRITY",
                "Invalid private recovery file",
            )
            raw = target.read_bytes()
            require(
                raw[:4] == b"AQA1",
                "ACQUISITION_SPOOL_INTEGRITY",
                "Unexpected recovery envelope",
            )
            return self.cipher.decrypt(
                raw[4:16], raw[16:], self.aad + b":" + name.encode()
            )
        except Exception:
            require(
                False,
                "ACQUISITION_SPOOL_INTEGRITY",
                "Recovery authentication failed; retained for manual review",
            )

    def state(self):
        raw = self._read("current.enc", META_BYTES)
        return decode(raw, limit=META_BYTES) if raw is not None else None

    def save(self, state):
        identifier(state["requestId"])
        previous = self.state()
        require(
            previous is None or previous["requestId"] == state["requestId"],
            "ACQUISITION_SPOOL_INTEGRITY",
            "An unresolved claim cannot be replaced",
        )
        self._write("current.enc", encode(state), META_BYTES)
        return state

    def current_or_create(self):
        return self.state() or self.save(
            {
                "version": 1,
                "phase": "claiming",
                "requestId": str(uuid.uuid4()),
                "requests": {},
            }
        )

    def metadata(self, job, value=None):
        name = identifier(job["id"]) + "-input.enc"
        if value is not None:
            self._write(name, encode(value), META_BYTES)
        raw = self._read(name, META_BYTES)
        return decode(raw, limit=META_BYTES) if raw is not None else None

    def save_receipt(self, job, key, receipt):
        raw = receipt["raw"]
        meta = {k: v for k, v in receipt.items() if k != "raw"}
        require(
            sha(raw) == meta["sha256"] and len(raw) == meta["byteLength"],
            "ACQUISITION_RECEIPT",
            "Raw receipt differs before persistence",
        )
        head = encode(meta)
        require(len(head) <= 4096, "ACQUISITION_RECEIPT", "Receipt metadata too large")
        self._write(
            f'{identifier(job["id"])}-{digest(key)}.raw.enc',
            len(head).to_bytes(4, "big") + head + raw,
            PROFILE["maxResponseBytes"] + 4100,
        )

    def receipt(self, job, key):
        raw = self._read(
            f'{identifier(job["id"])}-{digest(key)}.raw.enc',
            PROFILE["maxResponseBytes"] + 4100,
        )
        if raw is None:
            return None
        n = int.from_bytes(raw[:4], "big")
        require(
            0 < n <= 4096, "ACQUISITION_SPOOL_INTEGRITY", "Invalid receipt metadata"
        )
        meta = decode(raw[4 : 4 + n], limit=4096)
        body = raw[4 + n :]
        require(
            meta["sha256"] == sha(body) and meta["byteLength"] == len(body),
            "ACQUISITION_SPOOL_INTEGRITY",
            "Receipt body identity differs",
        )
        return {**meta, "raw": body}

    def save_publication(self, job, manifest, chunks):
        for (collection, ordinal), raw in chunks.items():
            self._write(self._part(job, collection, ordinal), raw, CHUNK_BYTES)
        # Manifest is the commit marker, written only after every durable part.
        self._write(
            identifier(job["id"]) + "-manifest.enc", encode(manifest), 128 * 1024
        )

    def publication(self, job):
        raw = self._read(identifier(job["id"]) + "-manifest.enc", 128 * 1024)
        return decode(raw, limit=128 * 1024) if raw is not None else None

    def _part(self, job, collection, ordinal):
        require(
            collection in {"package", "calendar"}
            and type(ordinal) is int
            and 0 <= ordinal < 48,
            "ACQUISITION_PUBLICATION",
            "Invalid publication part name",
        )
        return f'{identifier(job["id"])}-{collection}-{ordinal}.part.enc'

    def chunk(self, job, collection, part):
        raw = self._read(self._part(job, collection, part["ordinal"]), CHUNK_BYTES)
        require(
            raw is not None
            and len(raw) == part["byteLength"]
            and sha(raw) == part["sha256"],
            "ACQUISITION_SPOOL_INTEGRITY",
            "Missing or changed publication part",
        )
        return raw

    def terminal(self, state, status, *, retain=False):
        require(
            status in {"completed", "failed", "cancelled", "empty"},
            "ACQUISITION_TERMINAL",
            "A terminal claim receipt is required",
        )
        state = self.save(
            {**state, "phase": "terminal", "terminalStatus": status, "retain": retain}
        )
        job_id = state.get("job", {}).get("id")
        if retain:
            name = (
                identifier(job_id) if job_id else identifier(state["requestId"])
            ) + "-review.enc"
            self._write(name, encode(state), META_BYTES)
        elif job_id:
            for path in self.root.glob(identifier(job_id) + "-*.enc"):
                require(
                    path.is_file() and not path.is_symlink(),
                    "ACQUISITION_SPOOL_INTEGRITY",
                    "Recovery cleanup refused",
                )
                path.unlink()
        (self.root / "current.enc").unlink()
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
