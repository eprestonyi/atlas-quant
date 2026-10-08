"""Private, authenticated financial claim and publication recovery; no plaintext."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import time
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .protocol import (
    CHUNK_BYTES,
    CHUNK_COUNT,
    COLLECTIONS,
    MANIFEST_BYTES,
    PACKAGE_BYTES,
    RESULT_BYTES,
    decode,
    digest,
    encode,
    fail,
    identifier,
    sha,
)

PHASES = {"claiming", "claimed", "computing", "publishing", "failing", "terminal"}
TERMINAL = {"completed", "failed", "cancelled", "empty"}


def _sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private(path):
    if not path.is_absolute() or path.is_symlink():
        fail("FINANCIAL_SPOOL_PATH", "财务恢复目录必须是独立私有绝对路径。")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        fail("FINANCIAL_SPOOL_PERMISSIONS", "财务恢复目录权限须为 0700。")
    return path


class FinancialSpool:
    """One active claim, with a separate key/AAD domain from research delivery."""

    def __init__(self, config):
        research = Path(config["delivery_dir"]).expanduser()
        self.root = Path(
            config.get(
                "financial_delivery_dir",
                str(research.with_name(research.name + "-financial")),
            )
        ).expanduser()
        if self.root.is_symlink() or not self.root.is_absolute():
            fail("FINANCIAL_SPOOL_PATH", "财务恢复目录必须为独立绝对路径。")
        self.root, research = self.root.resolve(), research.resolve()
        if (
            self.root == research
            or research in self.root.parents
            or self.root in research.parents
        ):
            fail("FINANCIAL_SPOOL_PATH", "财务与研究恢复目录必须独立。")
        _private(self.root)
        self.key = hashlib.sha256(
            b"atlas-quant-financial-key-v1\0" + config["runner_secret"].encode()
        ).digest()
        self.aad = ("atlas-quant-financial-spool-v1:" + config["api_base"]).encode()
        self.cipher = AESGCM(self.key)

    @contextmanager
    def locked(self):
        path = self.root / "service.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
                fail("FINANCIAL_SPOOL_PERMISSIONS", "财务服务锁必须为私有文件。")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("FINANCIAL_ALREADY_ACTIVE", "此财务恢复目录已有运行服务。")
            yield
        finally:
            os.close(descriptor)

    def _write(self, path, raw, aad):
        if path.is_symlink():
            fail("FINANCIAL_SPOOL_INTEGRITY", "恢复文件不能是符号链接。")
        nonce = os.urandom(12)
        encrypted = b"AQF1" + nonce + self.cipher.encrypt(nonce, raw, aad)
        temp = path.parent / ("write-" + uuid.uuid4().hex + ".tmp")
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            temp.replace(path)
            _sync(path.parent)
        except OSError:
            fail("FINANCIAL_SPOOL_WRITE", "无法持久保存财务任务；停止领取新任务。")
        finally:
            if temp.exists():
                temp.unlink()

    def _read(self, path, maximum, aad):
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or stat.S_IMODE(path.stat().st_mode) & 0o077
                or not 32 <= path.stat().st_size <= maximum + 32
            ):
                raise ValueError()
            raw = path.read_bytes()
            if raw[:4] != b"AQF1":
                raise ValueError()
            return self.cipher.decrypt(raw[4:16], raw[16:], aad)
        except Exception:
            fail("FINANCIAL_SPOOL_INTEGRITY", "财务恢复文件无法验证；保留原件并停止。")

    def read(self):
        path = self.root / "current.enc"
        if not path.exists() and not path.is_symlink():
            return None
        value = decode(self._read(path, 16384, self.aad + b":intent"), limit=16384)
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("phase") not in PHASES
        ):
            fail("FINANCIAL_SPOOL_INTEGRITY", "财务领取状态无效。")
        identifier(value.get("requestId"))
        if value["phase"] not in {"claiming", "terminal"} and not isinstance(
            value.get("job"), dict
        ):
            fail("FINANCIAL_SPOOL_INTEGRITY", "财务恢复缺少已领取任务。")
        return value

    def save(self, value):
        current = self.read()
        identifier(value.get("requestId"))
        if current and current["requestId"] != value["requestId"]:
            fail("FINANCIAL_SPOOL_INTEGRITY", "未知结果不能替换原领取身份。")
        raw = encode(value)
        if len(raw) > 16384 or value.get("phase") not in PHASES:
            fail("FINANCIAL_SPOOL_INTEGRITY", "财务恢复状态超出限制。")
        self._write(self.root / "current.enc", raw, self.aad + b":intent")
        return value

    def current_or_create(self):
        return self.read() or self.save(
            {
                "version": 1,
                "requestId": str(uuid.uuid4()),
                "createdAt": time.time(),
                "phase": "claiming",
            }
        )

    def publication(self, job):
        return PublicationSpool(self, job)

    def context(self, job):
        # Only the dedicated child receives this local key, never the network.
        return {"root": str(self.root), "key": self.key, "aad": self.aad, "job": job}

    @classmethod
    def from_context(cls, context):
        instance = cls.__new__(cls)
        instance.root = _private(Path(context["root"]))
        instance.key, instance.aad = context["key"], context["aad"]
        instance.cipher = AESGCM(instance.key)
        return instance

    def acknowledge(self, state, status):
        if status not in TERMINAL:
            fail("FINANCIAL_PROTOCOL", "缺少可确认的财务终态。")
        terminal = self.save(dict(state, phase="terminal", terminalStatus=status))
        self.cleanup(terminal)

    def cleanup(self, state):
        current = self.read()
        if current is None:
            return
        if (
            current["requestId"] != state["requestId"]
            or current["phase"] != "terminal"
            or current.get("terminalStatus") not in TERMINAL
        ):
            fail("FINANCIAL_SPOOL_INTEGRITY", "只能清除已确认的原任务。")
        if current.get("job"):
            self.publication(current["job"]).cleanup()
        (self.root / "current.enc").unlink()
        _sync(self.root)


class PublicationSpool:
    """Each chunk binds queue, job, lease, collection and ordinal through AAD."""

    def __init__(self, spool, job):
        self.spool, self.job = spool, job
        key = sha(
            (identifier(job["id"]) + "\0" + identifier(job["leaseToken"])).encode()
        )
        self.root = _private(spool.root / key)
        self.aad = spool.aad + b":publication:" + key.encode()
        self.written = {
            path.stem: max(0, path.stat().st_size - 32)
            for path in self.root.glob("*.enc")
            if path.name != "manifest.enc"
        }

    def _name(self, collection, ordinal):
        if (
            collection not in COLLECTIONS
            or type(ordinal) is not int
            or not 0 <= ordinal < CHUNK_COUNT
        ):
            fail("FINANCIAL_PROTOCOL", "财务分片引用不合法。")
        return collection + "-" + str(ordinal)

    def _read(self, name, maximum=CHUNK_BYTES):
        return self.spool._read(
            self.root / (name + ".enc"), maximum, self.aad + b":" + name.encode()
        )

    def write_chunk(self, collection, ordinal, raw):
        name = self._name(collection, ordinal)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= CHUNK_BYTES:
            fail("FINANCIAL_CHUNK_BUDGET", "财务分片必须在 512 KiB 内。")
        if (self.root / "manifest.enc").exists() or (
            self.root / (name + ".enc")
        ).exists():
            fail("FINANCIAL_SPOOL_INTEGRITY", "已保存的财务计算片不可替换。")
        if (
            len(self.written) >= CHUNK_COUNT
            or sum(self.written.values()) + len(raw) > RESULT_BYTES
        ):
            fail("FINANCIAL_RESULT_BUDGET", "财务产物超过总量限制。")
        self.spool._write(
            self.root / (name + ".enc"), raw, self.aad + b":" + name.encode()
        )
        self.written[name] = len(raw)

    def read_chunk(self, collection, part):
        raw = self._read(self._name(collection, part["ordinal"]))
        if len(raw) != part["byteLength"] or sha(raw) != part["sha256"]:
            fail("FINANCIAL_SPOOL_INTEGRITY", "财务分片与完整清单不一致。")
        return raw

    def validate_manifest(self, manifest):
        kinds = {
            "financial_validate": "validated",
            "financial_revise": "revised",
            "financial_prepare": "prepared",
        }
        if (
            not isinstance(manifest, dict)
            or len(encode(manifest)) > MANIFEST_BYTES
            or manifest.get("format") != "atlas.quant.financial-result"
            or manifest.get("version") != 1
            or manifest.get("inputId") != self.job["inputId"]
            or manifest.get("kind") != kinds.get(self.job["kind"])
        ):
            fail("FINANCIAL_MANIFEST", "完整财务清单与当前任务不匹配。")
        collections = manifest.get("collections")
        expected = COLLECTIONS if manifest["kind"] == "prepared" else {"package"}
        if not isinstance(collections, dict) or set(collections) != expected:
            fail("FINANCIAL_MANIFEST", "财务清单缺少必需集合。")
        # Admit the complete metadata/byte plan before opening any payload file.
        count, total, names = 0, 0, set()
        for name, collection in collections.items():
            if not isinstance(collection, dict) or not isinstance(
                collection.get("chunks"), list
            ):
                fail("FINANCIAL_MANIFEST", "财务集合描述无效。")
            byte_collection = name == "package"
            if collection.get("encoding") != (
                "bytes" if byte_collection else "json_records"
            ):
                fail("FINANCIAL_MANIFEST", "财务集合编码不匹配。")
            count += len(collection["chunks"])
            if count > CHUNK_COUNT:
                fail("FINANCIAL_RESULT_BUDGET", "财务分片数量超过限制。")
            size, rows = 0, 0
            for ordinal, part in enumerate(collection["chunks"]):
                if (
                    not isinstance(part, dict)
                    or type(part.get("ordinal")) is not int
                    or part["ordinal"] != ordinal
                    or type(part.get("byteLength")) is not int
                    or not 1 <= part["byteLength"] <= CHUNK_BYTES
                ):
                    fail("FINANCIAL_MANIFEST", "财务分片编号或大小无效。")
                digest(part.get("sha256"))
                size += part["byteLength"]
                names.add(self._name(name, ordinal) + ".enc")
                if byte_collection:
                    if (
                        part.get("rowCount") is not None
                        or part.get("startRow") is not None
                    ):
                        fail("FINANCIAL_MANIFEST", "字节集合不能包含行号。")
                else:
                    if (
                        type(part.get("rowCount")) is not int
                        or not 1 <= part["rowCount"] <= 500
                        or type(part.get("startRow")) is not int
                        or part["startRow"] != rows
                    ):
                        fail("FINANCIAL_MANIFEST", "财务记录计数或顺序无效。")
                    rows += part["rowCount"]
            if (
                type(collection.get("byteLength")) is not int
                or collection["byteLength"] != size
            ):
                fail("FINANCIAL_MANIFEST", "财务集合字节总数不匹配。")
            if byte_collection:
                digest(collection.get("sha256"))
                if (
                    not 1 <= size <= PACKAGE_BYTES
                    or collection.get("rowCount") is not None
                ):
                    fail("FINANCIAL_MANIFEST", "冻结包大小或计数无效。")
            else:
                maximum = (
                    110000 if name == "panel" else 10000 if name == "events" else 500000
                )
                if (
                    type(collection.get("rowCount")) is not int
                    or collection["rowCount"] != rows
                    or rows > maximum
                ):
                    fail("FINANCIAL_MANIFEST", "财务集合记录总数不匹配或超限。")
            total += size
            if total > RESULT_BYTES:
                fail("FINANCIAL_RESULT_BUDGET", "财务完整产物超出限制。")
        actual = {
            path.name for path in self.root.iterdir() if path.name != "manifest.enc"
        }
        if actual != names:
            fail("FINANCIAL_MANIFEST", "财务清单不覆盖全部已保存分片。")
        for name, collection in collections.items():
            hasher = hashlib.sha256()
            for part in collection["chunks"]:
                raw = self.read_chunk(name, part)
                if name == "package":
                    hasher.update(raw)
                else:
                    values = decode(raw, limit=CHUNK_BYTES)
                    if (
                        not isinstance(values, list)
                        or len(values) != part["rowCount"]
                        or any(not isinstance(v, dict) for v in values)
                    ):
                        fail("FINANCIAL_MANIFEST", "财务实际记录与声明不一致。")
            if name == "package" and hasher.hexdigest() != collection["sha256"]:
                fail("FINANCIAL_MANIFEST", "冻结包整体哈希不匹配。")
        return manifest

    def finalize(self, manifest):
        self.validate_manifest(manifest)
        if (self.root / "manifest.enc").exists():
            fail("FINANCIAL_SPOOL_INTEGRITY", "完整财务清单不可替换。")
        self.spool._write(
            self.root / "manifest.enc", encode(manifest), self.aad + b":manifest"
        )

    def manifest(self):
        path = self.root / "manifest.enc"
        if not path.exists() and not path.is_symlink():
            return None
        value = decode(self._read("manifest", MANIFEST_BYTES), limit=MANIFEST_BYTES)
        return self.validate_manifest(value)

    def cleanup(self):
        # This directory is derived from the original authenticated job+lease.
        # Only private regular files are removed; never follow external links.
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_file():
                fail("FINANCIAL_SPOOL_INTEGRITY", "财务恢复目录含未知对象，保留原件。")
        for path in self.root.iterdir():
            path.unlink()
        self.root.rmdir()
        _sync(self.spool.root)
