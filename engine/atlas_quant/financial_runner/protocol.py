"""Boundaries shared by the financial consumer, publication writer and tests."""

from __future__ import annotations

import hashlib
import json
import math
import re

from ..runner import RunnerError

CAPABILITY = "financial-input/v1"
PACKAGE_BYTES = 24 * 1024 * 1024
RESULT_BYTES = 64 * 1024 * 1024
REGISTRY_BYTES = 256 * 1024
REGISTRIES_BYTES = 32 * 1024 * 1024
META_BYTES = MANIFEST_BYTES = 256 * 1024
CHUNK_BYTES = 512 * 1024
CHUNK_COUNT = 512
COLLECTIONS = frozenset(
    {"package", "panel", "events", "dependencies", "assignments", "coverage"}
)
KINDS = {"financial_validate": 180, "financial_revise": 180, "financial_prepare": 600}
UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
HASH = re.compile(r"[a-f0-9]{64}")


def fail(code, message):
    raise RunnerError(code, message)


def encode(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (ValueError, TypeError, RecursionError, OverflowError):
        fail("FINANCIAL_JSON", "财务任务必须包含有限、可编码的 JSON。")


def decode(raw, *, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        fail("FINANCIAL_BYTE_BUDGET", "财务响应超出本次读取预算。")

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                fail("FINANCIAL_JSON", "财务 JSON 含重复键。")
            value[key] = item
        return value

    def nonfinite(_):
        fail("FINANCIAL_JSON", "财务 JSON 不接受非有限数值。")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            nonfinite(value)
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=nonfinite,
            parse_float=finite_float,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        fail("FINANCIAL_JSON", "财务响应不是有效 UTF-8 JSON。")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not UUID.fullmatch(value):
        fail("FINANCIAL_IDENTITY", "财务任务标识无效。")
    return value


def digest(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        fail("FINANCIAL_IDENTITY", "财务内容哈希无效。")
    return value
