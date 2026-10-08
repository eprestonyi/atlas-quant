"""Lease-bound control and raw receipt transport. This client never calls Tushare."""

import base64
import re
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from ..runner import RunnerError
from .planner import validate_cache
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


class AcquisitionClient:
    def __init__(self, config, session=None):
        self.base = config["api_base"].rstrip("/") + "/runner/financial-acquire"
        self.prefix = urlsplit(self.base).path + "/"
        self.secret = config["runner_secret"]
        self.allow_fixtures = config.get("allow_acquisition_fixtures") is True
        self.session = session or requests.Session()
        if session is None:
            self.session.mount("https://", HTTPAdapter(max_retries=0))
            self.session.mount("http://", HTTPAdapter(max_retries=0))
            self.session.trust_env = False

    def request(
        self,
        method,
        route,
        *,
        payload=None,
        raw=None,
        lease=None,
        extra=None,
        limit=META_BYTES,
        deadline=None,
    ):
        require(
            isinstance(route, str)
            and not route.startswith("/")
            and not any(x in route for x in ("..", "#", "%", "\\", "://")),
            "ACQUISITION_ROUTE",
            "Unsafe acquisition route",
        )
        deadline = min(deadline or float("inf"), time.monotonic() + 40)
        content = encode(payload) if payload is not None else raw
        require(
            content is None
            or isinstance(content, bytes)
            and len(content) <= PROFILE["maxResponseBytes"],
            "ACQUISITION_BYTE_BUDGET",
            "Control request exceeds its budget",
        )
        headers = {
            "Authorization": "Bearer " + self.secret,
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }
        if lease:
            headers["X-Acquisition-Lease"] = identifier(lease)
        if extra:
            headers.update(extra)
        remaining = deadline - time.monotonic()
        require(remaining > 0, "ACQUISITION_DEADLINE", "Control deadline elapsed")
        try:
            with self.session.request(
                method,
                self.base + "/" + route,
                data=content,
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=(min(10, remaining), min(20, remaining)),
            ) as response:
                if response.status_code != 200:
                    raise RunnerError(
                        "ACQUISITION_HTTP",
                        "Acquisition control request was not acknowledged",
                        http_status=response.status_code,
                    )
                require(
                    response.headers.get("Content-Encoding", "identity").lower()
                    in ("", "identity"),
                    "ACQUISITION_INTEGRITY",
                    "Compressed control payload refused",
                )
                declared = response.headers.get("Content-Length")
                require(
                    declared is None or declared.isdecimal() and int(declared) <= limit,
                    "ACQUISITION_BYTE_BUDGET",
                    "Control response declaration exceeds budget",
                )
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    require(
                        size <= limit and time.monotonic() < deadline,
                        "ACQUISITION_BYTE_BUDGET",
                        "Control response exceeded byte/time budget",
                    )
                    chunks.append(chunk)
                require(
                    declared is None or size == int(declared),
                    "ACQUISITION_INTEGRITY",
                    "Incomplete control response",
                )
                return b"".join(chunks), response.headers
        except requests.RequestException:
            raise RunnerError(
                "ACQUISITION_NETWORK",
                "Control connection interrupted; retain durable state",
            ) from None

    def post(self, route, payload, *, deadline=None):
        require(
            route in {"heartbeat", "claim"}
            or re.fullmatch(
                r"jobs/[a-f0-9-]{36}/(?:fail|complete|publication|requests/[a-f0-9]{64}/(?:begin|unknown))",
                route,
            ),
            "ACQUISITION_ROUTE",
            "Unknown acquisition control operation",
        )
        raw, _ = self.request("POST", route, payload=payload, deadline=deadline)
        value = decode(raw, limit=META_BYTES)
        require(
            isinstance(value, dict),
            "ACQUISITION_PROTOCOL",
            "Control acknowledgement must be an object",
        )
        return value

    def input(self, job, *, deadline=None):
        route = "jobs/" + identifier(job["id"]) + "/input"
        require(
            job["inputUrl"] == self.prefix + route,
            "ACQUISITION_ROUTE",
            "Input URL does not belong to current claim",
        )
        raw, _ = self.request("GET", route, lease=job["leaseToken"], deadline=deadline)
        return decode(raw, limit=META_BYTES)

    def get_receipt(self, job, request, *, deadline=None):
        raw, headers = self.request(
            "GET",
            f"jobs/{identifier(job['id'])}/requests/{digest(request['requestKey'])}/receipt",
            lease=job["leaseToken"],
            limit=PROFILE["maxResponseBytes"],
            deadline=deadline,
        )
        token = headers.get("X-Acquisition-Receipt", "")
        require(
            isinstance(token, str)
            and len(token) <= 4096
            and re.fullmatch("[A-Za-z0-9_-]+", token),
            "ACQUISITION_RECEIPT",
            "Missing pinned receipt metadata",
        )
        try:
            meta = decode(
                base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)), limit=4096
            )
        except Exception:
            raise RunnerError(
                "ACQUISITION_RECEIPT", "Malformed receipt metadata"
            ) from None
        self.check_receipt(meta, request["requestKey"], raw)
        if request["cache"]["status"] == "frozen":
            require(
                {k: meta[k] for k in request["cache"] if k != "status"}
                == {k: v for k, v in request["cache"].items() if k != "status"},
                "ACQUISITION_CACHE",
                "Downloaded receipt differs from reviewed cache",
            )
        return {**meta, "raw": raw}

    def check_receipt(self, meta, request_key, raw):
        require(
            isinstance(meta, dict)
            and set(meta)
            in (
                {
                    "receiptId",
                    "requestKey",
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                },
                {
                    "receiptId",
                    "requestKey",
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                    "idempotent",
                },
            ),
            "ACQUISITION_RECEIPT",
            "Unexpected receipt metadata",
        )
        validate_cache(
            {
                "status": "frozen",
                **{
                    k: meta[k]
                    for k in (
                        "receiptId",
                        "sha256",
                        "byteLength",
                        "httpStatus",
                        "retrievedAt",
                        "sourceKind",
                    )
                },
            },
            allow_fixtures=self.allow_fixtures,
        )
        require(
            meta["requestKey"] == request_key
            and meta["sha256"] == sha(raw)
            and meta["byteLength"] == len(raw),
            "ACQUISITION_RECEIPT",
            "Raw receipt identity differs",
        )

    def put_receipt(self, job, request, receipt, attempt_id, *, deadline=None):
        meta = {
            "attemptId": identifier(attempt_id),
            **{
                k: receipt[k]
                for k in (
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                )
            },
        }
        token = base64.urlsafe_b64encode(encode(meta)).decode().rstrip("=")
        raw, _ = self.request(
            "PUT",
            f"jobs/{identifier(job['id'])}/requests/{digest(request['requestKey'])}/receipt",
            raw=receipt["raw"],
            lease=job["leaseToken"],
            extra={"X-Acquisition-Receipt": token},
            deadline=deadline,
        )
        answer = decode(raw, limit=META_BYTES)
        self.check_receipt(answer, request["requestKey"], receipt["raw"])
        require(
            all(
                answer[k] == receipt[k]
                for k in (
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                )
            ),
            "ACQUISITION_RECEIPT",
            "Receipt acknowledgement changed its source identity",
        )
        return answer

    def put_chunk(self, job, manifest_hash, collection, ordinal, raw, *, deadline=None):
        require(
            collection in {"package", "calendar"}
            and type(ordinal) is int
            and 0 <= ordinal < 48
            and len(raw) <= CHUNK_BYTES,
            "ACQUISITION_PUBLICATION",
            "Invalid publication part",
        )
        result, _ = self.request(
            "PUT",
            f"jobs/{identifier(job['id'])}/publication/{collection}/{ordinal}?manifestSha256={digest(manifest_hash)}",
            raw=raw,
            lease=job["leaseToken"],
            deadline=deadline,
        )
        return decode(result, limit=META_BYTES)
