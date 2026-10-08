"""Independent acquisition boundaries. Provider requests are never retried."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from ..financial_runner.protocol import decode, encode, identifier, digest
from ..runner import RunnerError

CAPABILITY = "financial-acquire/v1"
KIND = "financial_acquire"
PROFILE = json.loads(Path(__file__).with_name("profile.json").read_text())
META_BYTES = 256 * 1024
CHUNK_BYTES = 512 * 1024
PACKAGE_BYTES = 24 * 1024 * 1024
DEADLINE_SECONDS = 600
REQUEST_SECONDS = 30


def fail(code, message):
    raise RunnerError(code, message)


def require(condition, code, message):
    if not condition:
        fail(code, message)


def keys(value, expected):
    require(
        isinstance(value, dict) and set(value) == set(expected),
        "ACQUISITION_PROTOCOL",
        "Unexpected acquisition fields",
    )


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.timestamp()
    except (ValueError, TypeError, AttributeError):
        fail("ACQUISITION_TIME", "An explicit bounded UTC timestamp is required")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(error):
    code = str(getattr(error, "code", "ACQUISITION_FAILED"))
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code):
        code = "ACQUISITION_FAILED"
    return {
        "code": code,
        "message": "财务来源获取未完成；已保留请求与响应证据，请按错误代码检查。",
    }
