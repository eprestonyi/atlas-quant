"""One encrypted, durable claim intent; no unknown outcome creates a new UUID."""
from __future__ import annotations

import json
import os
import re
import stat
import time
import uuid

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TERMINAL = {"completed", "failed", "cancelled"}


def _error(code, message):
    from .runner import RunnerError
    return RunnerError(code, message)


def claim_request(request_id, *, financial_datasets=False, market_datasets=False, financial_graphs=False, market_trend_auto=False):
    from . import __version__
    request = {"requestId": request_id, "runnerVersion": "atlas-quant-runner/" + __version__,
            "engineVersion": __version__, "transportFormats": ["atlas.quant.bundle/1"]}
    if financial_datasets is True:
        request["transportFormats"].append("atlas.quant.financial_bundle/1")
        request["datasetFormats"] = ["atlas.quant.research_dataset/2"]
        request["snapshotFormats"] = ["financial_json_v1"]
        from .research_dataset.research_profile import AUTO_PROFILE, SOURCE_PROFILES
        request["financialResearchProfiles"] = [SOURCE_PROFILES[2], AUTO_PROFILE]
    if financial_graphs is True:
        from .research_dataset.graph_v3.snapshot import RESEARCH_PROFILE
        request["transportFormats"].append("atlas.quant.financial_bundle/2")
        request.setdefault("datasetFormats", []).append("atlas.quant.research_dataset/3")
        request.setdefault("snapshotFormats", []).append("financial_column_snapshot_v1")
        request.setdefault("financialResearchProfiles", []).append(RESEARCH_PROFILE)
    if market_datasets is True:
        from .capacity.profiles import FULL_FILTER_PROFILE_ID,AUTO_FILTER_CANDIDATE_ID
        request['marketResearchProfiles']=[FULL_FILTER_PROFILE_ID,AUTO_FILTER_CANDIDATE_ID]
        if market_trend_auto is True:
            from .capacity.profiles import TREND_AUTO_PROFILE_ID
            request['marketResearchProfiles'].append(TREND_AUTO_PROFILE_ID)
    return request


def validate_receipt(response, intent, *, terminal_only=False):
    """Only an authenticated echo of this durable UUID can release the intent."""
    receipt = response.get("claim") if isinstance(response, dict) else None
    job = response.get("job") if isinstance(response, dict) else None
    valid = (isinstance(receipt, dict) and receipt.get("requestId") == intent["requestId"]
             and receipt.get("status") in TERMINAL | {"running", "empty"})
    if not valid:
        raise _error("CLAIM_PROTOCOL", "领取回执缺少匹配身份；保留领取意图并停止，需先升级兼容服务端。")
    status = receipt["status"]
    job_id = receipt.get("jobId")
    if status == "empty":
        valid = job is None and job_id is None and not intent.get("jobId") and not terminal_only
    else:
        valid = isinstance(job_id, str) and bool(job_id) and (not intent.get("jobId") or intent["jobId"] == job_id)
        if status == "running":
            valid = (valid and not terminal_only and isinstance(job, dict) and job.get("id") == job_id
                     and isinstance(job.get("leaseToken"), str) and bool(job["leaseToken"])
                     and (not intent.get("leaseToken") or intent["leaseToken"] == job["leaseToken"]))
        else:
            valid = valid and job is None
    if not valid:
        raise _error("CLAIM_PROTOCOL", "领取回执与保存的任务或租约不一致；保留原件并停止。")
    return status


class ClaimIntent:
    def __init__(self, spool):
        self.root = spool.root / "claims"
        if self.root.is_symlink():
            raise _error("CLAIM_INTEGRITY", "领取意图目录不能是符号链接。")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise _error("CLAIM_INTEGRITY", "领取意图目录权限须为 0700。")
        self.path = self.root / "current.enc"
        self.cipher, self.aad = spool.cipher, spool.aad + b":claim-intent-v1"

    def read(self):
        if not self.path.exists() and not self.path.is_symlink():
            return None
        try:
            if (self.path.is_symlink() or self.path.stat().st_size > 8192
                    or stat.S_IMODE(self.path.stat().st_mode) & 0o077):
                raise ValueError()
            raw = self.path.read_bytes()
            if raw[:4] != b"AQC1":
                raise ValueError()
            value = json.loads(self.cipher.decrypt(raw[4:16], raw[16:], self.aad))
            if (not isinstance(value, dict) or not isinstance(value.get("requestId"), str)
                    or not UUID.fullmatch(value["requestId"]) or value.get("phase") not in {"pending_claim", "executing"}
                    or isinstance(value.get("createdAt"), bool) or not isinstance(value.get("createdAt"), (int, float))):
                raise ValueError()
            if value["phase"] == "executing" and not all(isinstance(value.get(k), str) and value[k] for k in ("jobId", "leaseToken")):
                raise ValueError()
            return value
        except Exception:
            raise _error("CLAIM_INTEGRITY", "领取意图无法验证；保持原件并停止领取。") from None

    def _sync(self):
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, value):
        raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        nonce = os.urandom(12)
        data = b"AQC1" + nonce + self.cipher.encrypt(nonce, raw, self.aad)
        temp = self.root / ("intent-" + uuid.uuid4().hex + ".tmp")
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(self.path)
            self._sync()
        except OSError:
            raise _error("CLAIM_WRITE", "无法持久保存领取意图；停止领取新任务。") from None
        return value

    def current_or_create(self):
        value = self.read()
        return value if value is not None else self._write({"requestId": str(uuid.uuid4()),
                      "phase": "pending_claim", "createdAt": time.time()})

    def executing(self, intent, job):
        current = self.read()
        if current != intent or intent["phase"] != "pending_claim":
            raise _error("CLAIM_INTEGRITY", "领取意图发生变化；停止执行。")
        extra = {"sourceKind": job['dataSource']} if job.get("dataSource") in {'ready_dataset','ready_market'} else {}
        from .graph_research_runner.protocol import requested as graph_requested
        if graph_requested(job):
            extra["sourceFormat"] = "atlas.quant.research_dataset/3"
        return self._write(dict(intent, **extra, phase="executing", jobId=job["id"], leaseToken=job["leaseToken"]))

    def clear(self, expected):
        current = self.read()
        if current is None:  # crash after clearing, before acknowledging completion
            return
        if (current["requestId"] != expected["requestId"]
                or (current.get("jobId") and current["jobId"] != expected.get("jobId"))):
            raise _error("CLAIM_INTEGRITY", "不能清除其他领取请求的意图。")
        try:
            self.path.unlink()
            self._sync()
        except OSError:
            raise _error("CLAIM_WRITE", "无法清除已确认领取意图；停止领取。") from None
