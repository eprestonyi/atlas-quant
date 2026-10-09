"""Private, frozen inputs for replaying a forecast without fetching or fitting.

The report and the input snapshot are separate bounded artifacts. Snapshots are
encrypted in the runner's durable spool and uploaded through the leased runner
API, never appended to a user's public report or copied into the source tree.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import pandas as pd

from .provider import _records, _validate_panel, canonical_hash


MAX_SNAPSHOT_BYTES = 24 * 1024 * 1024


def _fail(code, message):
    # Keep the runner's public error vocabulary without an import-time cycle.
    from .runner import RunnerError
    raise RunnerError(code, message)


def _encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode()
    except (ValueError, TypeError):
        _fail("SNAPSHOT_FORMAT", "冻结数据不是有效的有限数值 JSON。")


def freeze_input(strategy, data, provenance, *, max_bytes=None):
    """Preserve exact floating-point input; retain the provider's canonical hash."""
    if not isinstance(provenance, dict):
        _fail("SNAPSHOT_FORMAT", "冻结数据缺少来源记录。")
    fingerprint = canonical_hash(_records(data))
    if provenance.get("dataFingerprint") != fingerprint:
        _fail("SNAPSHOT_FINGERPRINT", "行情内容与来源指纹不一致，不能生成可重放实验。")
    # to_dict preserves Python float round-trips, unlike pandas.to_json's fixed
    # decimal precision. Nulls remain null and are not filled with zeros.
    rows = data.astype(object).where(pd.notna(data), None).to_dict(orient="records")
    from .engine import _prepare_data
    _, _, audit = _prepare_data(data, strategy, provenance)
    snapshot = {"schemaVersion": 1, "rows": rows,
                "provenance": copy.deepcopy(provenance), "sourceDataFingerprint": fingerprint,
                "dataFingerprint": audit["dataSha256"],
                "fingerprintVersion": ("research_input_context_v1" if audit.get("contextSourceRoot")
                                       else "research_input_v1")}
    if len(_encode(snapshot)) > (MAX_SNAPSHOT_BYTES if max_bytes is None else max_bytes):
        _fail("SNAPSHOT_SIZE", "冻结行情超过独立产物大小限制。")
    return snapshot


def restore_input(strategy, snapshot, expected_fingerprint=None, *, max_bytes=None):
    """Validate the immutable input before executing already-issued forecasts."""
    if (not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1
            or not isinstance(snapshot.get("provenance"), dict)
            or not isinstance(snapshot.get("rows"), list)):
        _fail("SNAPSHOT_FORMAT", "冻结行情格式无效。")
    if len(_encode(snapshot)) > (MAX_SNAPSHOT_BYTES if max_bytes is None else max_bytes):
        _fail("SNAPSHOT_SIZE", "冻结行情超过独立产物大小限制。")
    provenance = copy.deepcopy(snapshot["provenance"])
    fingerprint = snapshot.get("dataFingerprint")
    source_fingerprint = snapshot.get("sourceDataFingerprint")
    version = "research_input_context_v1" if provenance.get("contextSourceRoot") else "research_input_v1"
    if (snapshot.get("fingerprintVersion") != version
            or not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
            or provenance.get("dataFingerprint") != source_fingerprint
            or (expected_fingerprint is not None and expected_fingerprint != fingerprint)):
        _fail("SNAPSHOT_FINGERPRINT", "冻结行情与预测产物的数据指纹不一致。")
    frame = _validate_panel(strategy, snapshot["rows"],
                            external_fields=provenance.get("externalFields"))
    if canonical_hash(_records(frame)) != source_fingerprint:
        _fail("SNAPSHOT_FINGERPRINT", "冻结行情内容已改变，不能重放原预测。")
    from .engine import _prepare_data
    _, _, audit = _prepare_data(frame, strategy, provenance)
    if audit["dataSha256"] != fingerprint:
        _fail("SNAPSHOT_FINGERPRINT", "冻结行情精度、特征或交易日历已改变，不能重放原预测。")
    return frame, provenance


class SnapshotSpool:
    """Encrypted input storage, bound to queue, lease and completion identity."""

    def __init__(self, completion_spool):
        self.root = completion_spool.root / "snapshots"
        if self.root.is_symlink():
            _fail("DELIVERY_PATH", "冻结行情目录不能是符号链接。")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            _fail("DELIVERY_PERMISSIONS", "冻结行情目录权限须为 0700。")
        self.cipher = completion_spool.cipher
        self.aad = completion_spool.aad + b":input-snapshot-v1"

    @staticmethod
    def key(identity):
        return hashlib.sha256((str(identity["id"]) + "\0" +
                               str(identity["leaseToken"])).encode()).hexdigest()

    def path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            _fail("DELIVERY_INTEGRITY", "冻结行情引用无效。")
        return self.root / (key + ".enc")

    def write(self, identity, snapshot):
        content = _encode({"id": identity["id"], "leaseToken": identity["leaseToken"],
                           "snapshot": snapshot})
        if len(content) > MAX_SNAPSHOT_BYTES + 4096:
            _fail("SNAPSHOT_SIZE", "冻结行情投递内容过大。")
        key = self.key(identity)
        path = self.path(key)
        nonce = os.urandom(12)
        encrypted = b"AQS1" + nonce + self.cipher.encrypt(nonce, content, self.aad)
        temp = path.with_suffix(".tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            _fail("DELIVERY_WRITE", "无法私密保存冻结行情；暂停领取新任务。")
        return key

    def read(self, key, identity):
        path = self.path(key)
        try:
            if (key != self.key(identity) or path.is_symlink()
                    or path.stat().st_size > MAX_SNAPSHOT_BYTES + 8192
                    or stat.S_IMODE(path.stat().st_mode) & 0o077):
                raise ValueError()
            encrypted = path.read_bytes()
            if encrypted[:4] != b"AQS1":
                raise ValueError()
            value = json.loads(self.cipher.decrypt(encrypted[4:16], encrypted[16:], self.aad))
            if value.get("id") != identity["id"] or value.get("leaseToken") != identity["leaseToken"]:
                raise ValueError()
            if not isinstance(value.get("snapshot"), dict):
                raise ValueError()
            return value["snapshot"]
        except Exception:
            _fail("DELIVERY_INTEGRITY", "冻结行情无法验证；保留原件并停止投递。")

    def acknowledge(self, key):
        self.path(key).unlink(missing_ok=True)
