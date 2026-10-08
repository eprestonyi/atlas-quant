"""Same-origin, lease-bound HTTP with byte and wall-clock budgets; no retries."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from ..runner import RunnerError
from .protocol import (
    CHUNK_BYTES,
    META_BYTES,
    PACKAGE_BYTES,
    REGISTRY_BYTES,
    REGISTRIES_BYTES,
    decode,
    digest,
    encode,
    fail,
    identifier,
    sha,
)


class FinancialClient:
    def __init__(self, config, session=None):
        # Production configuration is validated by runner.load_config. Tests may
        # inject a local Miniflare URL without changing production URL policy.
        self.base = config["api_base"].rstrip("/") + "/runner/financial"
        self.prefix = urlsplit(self.base).path + "/"
        self.secret = config["runner_secret"]
        self.session = session or requests.Session()

    def _request(
        self,
        method,
        route,
        *,
        payload=None,
        raw=None,
        lease=None,
        limit=META_BYTES,
        deadline=None,
        heartbeat=None,
    ):
        if (
            not isinstance(route, str)
            or route.startswith("/")
            or any(part in route for part in ("..", "#", "%", "\\", "://"))
        ):
            fail("FINANCIAL_ROUTE", "财务接口路径无效。")
        if deadline is None:
            deadline = time.monotonic() + 60
        deadline = min(deadline, time.monotonic() + 60)
        content = encode(payload) if payload is not None else raw
        if content is not None and (
            not isinstance(content, bytes) or len(content) > CHUNK_BYTES
        ):
            fail("FINANCIAL_BYTE_BUDGET", "财务请求超过分片预算。")
        headers = {
            "Authorization": "Bearer " + self.secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if lease is not None:
            headers["X-Financial-Lease"] = identifier(lease)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("FINANCIAL_DEADLINE", "财务任务的读取时间已耗尽。")
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
                        "FINANCIAL_HTTP",
                        "财务队列未成功响应。",
                        http_status=response.status_code,
                    )
                declared = response.headers.get("Content-Length")
                if declared is not None and (
                    not declared.isdecimal() or int(declared) > limit
                ):
                    fail("FINANCIAL_BYTE_BUDGET", "财务响应声明大小超过限制。")
                parts, size = [], 0
                for block in response.iter_content(65536):
                    size += len(block)
                    if size > limit:
                        fail("FINANCIAL_BYTE_BUDGET", "财务响应实际大小超过限制。")
                    if time.monotonic() >= deadline:
                        fail("FINANCIAL_DEADLINE", "财务响应超过读取时间。")
                    if heartbeat:
                        heartbeat()
                    parts.append(block)
                if declared is not None and size != int(declared):
                    fail("FINANCIAL_INTEGRITY", "财务响应没有完整传输。")
                return b"".join(parts)
        except RunnerError:
            raise
        except requests.RequestException:
            raise RunnerError(
                "FINANCIAL_NETWORK", "财务队列连接中断；保存原领取身份。"
            ) from None

    def post(self, route, payload, *, deadline=None):
        if route not in {
            "heartbeat",
            "claim",
            "publications/begin",
            "complete",
            "fail",
        }:
            fail("FINANCIAL_ROUTE", "未知财务写入接口。")
        value = decode(
            self._request("POST", route, payload=payload, deadline=deadline),
            limit=META_BYTES,
        )
        if not isinstance(value, dict):
            fail("FINANCIAL_PROTOCOL", "财务回执须为对象。")
        return value

    def get(self, route, lease, *, deadline=None):
        value = decode(
            self._request("GET", route, lease=lease, deadline=deadline),
            limit=META_BYTES,
        )
        if not isinstance(value, dict):
            fail("FINANCIAL_PROTOCOL", "财务元数据须为对象。")
        return value

    def descriptor(self, item, job, *, registry=False):
        if not isinstance(item, dict):
            fail("FINANCIAL_PROTOCOL", "缺少冻结输入描述。")
        maximum = REGISTRY_BYTES if registry else PACKAGE_BYTES
        size = item.get("byteLength")
        if type(size) is not int or not 1 <= size <= maximum:
            fail("FINANCIAL_BYTE_BUDGET", "冻结输入声明超过限制。")
        digest(item.get("sha256"))
        expected = self.prefix + "jobs/" + identifier(job["id"]) + "/"
        expected += "registry/" + identifier(item.get("ref")) if registry else "source"
        if item.get("url") != expected:
            fail("FINANCIAL_ROUTE", "冻结输入地址不属于当前任务。")
        return expected[len(self.prefix) :]

    def download(self, item, job, *, registry=False, deadline=None, heartbeat=None):
        route = self.descriptor(item, job, registry=registry)
        raw = self._request(
            "GET",
            route,
            lease=job["leaseToken"],
            limit=item["byteLength"],
            deadline=deadline,
            heartbeat=heartbeat,
        )
        if len(raw) != item["byteLength"] or sha(raw) != item["sha256"]:
            fail("FINANCIAL_INTEGRITY", "冻结输入的字节与哈希不匹配。")
        return raw

    def inputs(self, job, *, deadline=None, heartbeat=None):
        job_id = identifier(job["id"])
        meta = self.get(
            "jobs/" + job_id + "/input", job["leaseToken"], deadline=deadline
        )
        if meta.get("job") != {key: job[key] for key in ("id", "kind", "inputId")}:
            fail("FINANCIAL_IDENTITY", "输入回执与当前任务不一致。")
        proofs = meta.get("proofs")
        if not isinstance(proofs, list) or len(proofs) > 256:
            fail("FINANCIAL_BYTE_BUDGET", "单位证明数量超过限制。")
        registries = [meta.get("calendar"), *proofs]
        # Validate every reference and the aggregate before the first source read.
        self.descriptor(meta.get("source"), job)
        refs, total = set(), 0
        for entry in registries:
            self.descriptor(entry, job, registry=True)
            if entry["ref"] in refs:
                fail("FINANCIAL_PROTOCOL", "冻结证明引用重复。")
            refs.add(entry["ref"])
            total += entry["byteLength"]
        if total > REGISTRIES_BYTES:
            fail("FINANCIAL_BYTE_BUDGET", "冻结证明总大小超过限制。")
        source = self.download(
            meta["source"], job, deadline=deadline, heartbeat=heartbeat
        )
        evidence = {
            entry["ref"]: self.download(
                entry, job, registry=True, deadline=deadline, heartbeat=heartbeat
            )
            for entry in registries
        }
        return meta, source, evidence

    def upload(
        self,
        publication_id,
        manifest_sha,
        collection,
        ordinal,
        raw,
        job,
        *,
        deadline=None,
    ):
        from .protocol import COLLECTIONS, CHUNK_COUNT

        if (
            collection not in COLLECTIONS
            or type(ordinal) is not int
            or not 0 <= ordinal < CHUNK_COUNT
        ):
            fail("FINANCIAL_PROTOCOL", "未知财务分片。")
        route = (
            "publications/"
            + identifier(publication_id)
            + "/chunks/"
            + collection
            + "/"
            + str(ordinal)
            + "?manifestSha256="
            + digest(manifest_sha)
        )
        value = decode(
            self._request(
                "PUT", route, raw=raw, lease=job["leaseToken"], deadline=deadline
            ),
            limit=META_BYTES,
        )
        if not isinstance(value, dict):
            fail("FINANCIAL_PROTOCOL", "财务分片回执须为对象。")
        return value
