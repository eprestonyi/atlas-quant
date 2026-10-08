"""Individually authenticated private bundle files, durable across queue retries."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import time
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .bundle import (BundleReader, CHUNK_LIMIT, MANIFEST_LIMIT, COLLECTIONS,
                     build_bundle, encode, sha, fail, validate_manifest)


class BundleSpool:
    DIRECTORY = "bundles"
    AAD_TAG = b":bundle-v1:"
    MAGIC = b"AQB1"

    def __init__(self, context):
        self.context = context
        identity = context["identity"]
        self.key = hashlib.sha256((identity["id"] + "\0" + identity["leaseToken"]).encode()).hexdigest()
        parent = Path(context["root"]) / self.DIRECTORY
        if not parent.is_absolute() or parent.is_symlink():
            fail("DELIVERY_PATH", "分片目录必须是私有绝对路径。")
        parent.mkdir(mode=0o700, exist_ok=True)
        self.root = parent / self.key
        if self.root.is_symlink():
            fail("DELIVERY_PATH", "分片目录不能是符号链接。")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if any(stat.S_IMODE(path.stat().st_mode) & 0o077 for path in (parent, self.root)):
            fail("DELIVERY_PERMISSIONS", "分片目录权限须为0700。")
        self.cipher = AESGCM(context["encryptionKey"])
        self.aad = context["aad"] + self.AAD_TAG + self.key.encode()

    @staticmethod
    def context_for(completion_spool, identity):
        return {"root": str(completion_spool.root), "identity": dict(identity),
                "encryptionKey": completion_spool.encryption_key, "aad": completion_spool.aad}

    @classmethod
    def from_spool(cls, completion_spool, identity):
        return cls(cls.context_for(completion_spool, identity))

    @staticmethod
    def chunk_name(collection, ordinal):
        if collection not in COLLECTIONS or not isinstance(ordinal, int) or isinstance(ordinal, bool) or not 0 <= ordinal < 256:
            fail("BUNDLE_FORMAT", "分片引用无效。")
        return f"{collection}-{ordinal}"

    def _path(self, name):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,90}", name):
            fail("DELIVERY_INTEGRITY", "分片文件名无效。")
        return self.root / (name + ".enc")

    def write(self, name, raw):
        if not isinstance(raw, bytes) or len(raw) > CHUNK_LIMIT:
            fail("BUNDLE_SIZE", "分片文件超出硬上限。")
        target = self._path(name)
        nonce = os.urandom(12)
        data = self.MAGIC + nonce + self.cipher.encrypt(nonce, raw, self.aad + b":" + name.encode())
        temp = self.root / (uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(target)
            self._sync()
        except OSError:
            fail("DELIVERY_WRITE", "无法持久保存加密分片。")

    def read(self, name):
        path = self._path(name)
        try:
            if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077 or path.stat().st_size > CHUNK_LIMIT+64:
                raise ValueError()
            raw = path.read_bytes()
            if raw[:4] != self.MAGIC:
                raise ValueError()
            return self.cipher.decrypt(raw[4:16], raw[16:], self.aad + b":" + name.encode())
        except Exception:
            fail("DELIVERY_INTEGRITY", "加密分片验证失败；保留原件并停止回传。")

    def _sync(self):
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def write_chunk(self, collection, ordinal, raw):
        self.write(self.chunk_name(collection, ordinal), raw)

    def read_chunk(self, collection, ordinal):
        return self.read(self.chunk_name(collection, ordinal))

    def build(self, report, snapshot, coverage):
        raw = build_bundle(report, snapshot, coverage, self.write_chunk, self.read_chunk)
        self.write("manifest", raw)  # Last: incomplete computation has no manifest.
        return {"bundleId": sha(raw), "_bundleKey": self.key}

    def reader(self, expected_id=None):
        return BundleReader(self.read("manifest"), self.read_chunk, expected_id)

    def import_manifest(self, raw, expected_id):
        validate_manifest(raw, expected_id)
        self.write("manifest", raw)

    def cleanup(self):
        # Called only after a matching terminal receipt. Never follow symlinks or
        # recursively delete arbitrary paths from a queue response.
        try:
            for path in self.root.iterdir():
                if path.is_symlink() or not path.is_file() or path.suffix not in (".enc", ".tmp"):
                    fail("DELIVERY_INTEGRITY", "分片目录含未知文件；停止自动清理。")
            for path in self.root.iterdir():
                path.unlink()
            self._sync()
            self.root.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            fail("DELIVERY_WRITE", "无法清除已确认分片。")


def deliver_bundle(client, spool, payload, *, deadline, store_type=BundleSpool, route_prefix="bundles"):
    """Retry exact encrypted bytes; never regenerate a model or a manifest."""
    store = store_type.from_spool(spool, payload)
    if payload.get("_bundleKey") != store.key:
        fail("DELIVERY_INTEGRITY", "分片与任务身份不一致。")
    reader = store.reader(payload["bundleId"])
    identity = {"id": payload["id"], "leaseToken": payload["leaseToken"]}
    next_heartbeat = 0.
    def active():
        nonlocal next_heartbeat
        from . import runner
        if runner.STOP:
            fail("DELIVERY_INTERRUPTED", "回传被本地停止；已保存分片，仅恢复投递。")
        if time.monotonic() >= deadline:
            fail("DELIVERY_DEADLINE", "本次回传总时间预算已耗尽；已保存分片，仅恢复投递。")
        if time.monotonic() >= next_heartbeat:
            state = client.post("heartbeat", dict(identity, status="running"), deadline=deadline)
            next_heartbeat = time.monotonic() + 15
            if state.get("cancelled") or state.get("leaseValid") is False:
                return False
        return True
    if not active():
        return None
    response = client.post(route_prefix + "/begin", dict(identity, bundleId=reader.bundle_id,
                                                manifestText=reader.manifest_raw.decode()), deadline=deadline)
    if response.get("terminalDiscard"):
        return None
    stage = response.get("stageId")
    if (response.get("bundleId") != reader.bundle_id or not isinstance(stage, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", stage)
            or response.get("status") not in {"staging", "verified", "committed"} or not isinstance(response.get("missing"), list)):
        fail("BUNDLE_PROTOCOL", "分片开始回执无效；保留原件。")
    known = {(info["id"], chunk["ordinal"]) for info in reader.manifest["collections"] for chunk in info["chunks"]}
    missing = []
    for entry in response["missing"]:
        if (not isinstance(entry, dict) or set(entry) != {"collection", "ordinal"}
                or not isinstance(entry["collection"], str) or type(entry["ordinal"]) is not int):
            fail("BUNDLE_PROTOCOL", "服务端缺片列表无效。")
        key = (entry["collection"], entry["ordinal"])
        if key not in known or key in missing:
            fail("BUNDLE_PROTOCOL", "服务端请求了未知或重复分片。")
        missing.append(key)
    for collection, ordinal in missing:
        if not active():
            return None
        raw = store.read_chunk(collection, ordinal)
        ack = client.bundle_chunk("PUT", reader.bundle_id, collection, ordinal,
                                  identity, raw=raw, stage_id=stage, deadline=deadline,
                                  **({"namespace": route_prefix} if route_prefix != "bundles" else {}))
        if ack.get("terminalDiscard"):
            return None
        if ack.get("bundleId") != reader.bundle_id or ack.get("collection") != collection or ack.get("ordinal") != ordinal or ack.get("sha256") != sha(raw):
            fail("BUNDLE_PROTOCOL", "分片回执身份不匹配。")
    if not active():
        return None
    verified = client.post(route_prefix + "/finalize", dict(identity, bundleId=reader.bundle_id, stageId=stage), deadline=deadline)
    if verified.get("terminalDiscard"):
        return None
    if verified.get("bundleId") != reader.bundle_id or verified.get("status") not in {"verified", "committed"}:
        fail("BUNDLE_PROTOCOL", "分片未得到完整验证回执。")
    return stage
