"""Finite canonical JSON preserving integral floats and signed zero exactly."""

import hashlib
import json
import math
import re


class DatasetError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise DatasetError(code, message)


def encode(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise DatasetError("DATASET_JSON", "Finite bounded JSON is required") from exc


def decode(raw, limit):
    require(
        isinstance(raw, bytes) and 0 < len(raw) <= limit,
        "DATASET_BUDGET",
        "Document exceeds its byte budget",
    )

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DATASET_JSON", "Duplicate JSON key")
            result[key] = value
        return result

    def number(value):
        result = float(value)
        require(math.isfinite(result), "DATASET_JSON", "Nonfinite JSON number")
        return result

    def nonfinite(_):
        raise DatasetError("DATASET_JSON", "Nonfinite JSON token")

    try:
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=number,
            parse_constant=nonfinite,
        )
        require(
            encode(result) == raw, "DATASET_CANONICAL", "Exact canonical bytes required"
        )
        return result
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, DatasetError):
            raise
        raise DatasetError("DATASET_JSON", "Invalid bounded UTF-8 JSON") from exc


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def keys(value, expected):
    require(
        isinstance(value, dict) and set(value) == set(expected),
        "DATASET_SHAPE",
        "Unexpected or missing typed fields",
    )


def digest(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
        "DATASET_IDENTITY",
        "Expected lowercase SHA-256",
    )
    return value


def uuid(value):
    require(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value),
        "DATASET_IDENTITY",
        "Expected lowercase UUID",
    )
    return value
