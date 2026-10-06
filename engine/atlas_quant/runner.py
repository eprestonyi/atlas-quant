"""Outbound-only leased job runner. One bounded child process per research job."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .provider import ProviderError, load_tushare, load_tushare_proxy, validate_upload
from .fixtures import make_demo_data

MAX_JOB_BYTES = 22 * 1024 * 1024
MAX_RESULT_BYTES = 12 * 1024 * 1024
DEFAULT_TIMEOUT = 600
STOP = False


class RunnerError(ValueError):
    def __init__(self, code, message, *, http_status=None):
        self.code = code
        self.http_status = http_status
        super().__init__(message)


def load_config(path):
    target = Path(path).expanduser()
    if not target.is_absolute() or not target.is_file() or target.is_symlink():
        raise RunnerError("CONFIG_FILE", "运行配置必须是外部私有绝对路径文件。")
    mode = stat.S_IMODE(target.stat().st_mode)
    if mode & 0o077:
        raise RunnerError("CONFIG_PERMISSIONS", "运行配置必须使用 0600 或更严格权限。")
    if target.stat().st_size > 32768:
        raise RunnerError("CONFIG_SIZE", "运行配置过大。")
    try:
        config = json.loads(target.read_text())
    except (ValueError, OSError):
        raise RunnerError("CONFIG_FILE", "运行配置无效。") from None
    if not isinstance(config, dict):
        raise RunnerError("CONFIG_FILE", "运行配置必须为对象。")
    base = str(config.get("api_base", "")).rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RunnerError("CONFIG_URL", "队列地址必须是固定 HTTPS URL。")
    secret = config.get("runner_secret")
    if not isinstance(secret, str) or len(secret) < 32 or len(secret) > 512 or any(c.isspace() for c in secret):
        raise RunnerError("CONFIG_SECRET", "缺少有效的 runner_secret。")
    hosts = config.get("allowed_proxy_hosts", ["atlas-aletheia.com"])
    if not isinstance(hosts, list) or not hosts or len(hosts) > 8 or any(not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", h) for h in hosts):
        raise RunnerError("CONFIG_PROXY", "内部行情代理域名配置无效。")
    config["api_base"] = base
    config["allowed_proxy_hosts"] = hosts
    access = config.get("provider_access")
    if access is not None:
        if not isinstance(access, dict) or set(access) != {"proxyUrl", "serviceToken"}:
            raise RunnerError("CONFIG_PROVIDER", "私有行情配置需包含 proxyUrl 与 serviceToken。")
        parsed_proxy = urlparse(access["proxyUrl"]) if isinstance(access["proxyUrl"], str) else None
        try:
            valid_proxy = (parsed_proxy and parsed_proxy.scheme == "https" and parsed_proxy.hostname in hosts
                           and parsed_proxy.port in (None, 443) and not parsed_proxy.username and not parsed_proxy.password
                           and not parsed_proxy.query and not parsed_proxy.fragment)
        except ValueError:
            valid_proxy = False
        if not valid_proxy:
            raise RunnerError("CONFIG_PROVIDER", "私有行情 URL 必须为已授权域名上的固定 HTTPS 地址。")
        secret_value = access["serviceToken"]
        if not isinstance(secret_value, str) or not 32 <= len(secret_value) <= 512 or any(c.isspace() for c in secret_value):
            raise RunnerError("CONFIG_PROVIDER", "私有行情服务凭据格式无效。")
    delivery = Path(config.get("delivery_dir", str(target.parent / "delivery"))).expanduser()
    if not delivery.is_absolute() or delivery.is_symlink():
        raise RunnerError("CONFIG_DELIVERY", "结果重试目录必须是绝对私有路径。")
    config["delivery_dir"] = str(delivery)
    try:
        config["job_timeout"] = max(30, min(900, int(config.get("job_timeout", DEFAULT_TIMEOUT))))
        config["poll_seconds"] = max(3, min(60, int(config.get("poll_seconds", 10))))
    except (TypeError, ValueError):
        raise RunnerError("CONFIG_BUDGET", "运行时间预算无效。") from None
    return config


def _safe_error(exc):
    code = str(getattr(exc, "code", "JOB_FAILED"))
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code):
        code = "JOB_FAILED"
    # No arbitrary exception repr/traceback, requests headers, URLs or provider bodies.
    if isinstance(exc, (ProviderError, RunnerError)) or exc.__class__.__name__ == "ResearchError":
        message = str(exc)[:400]
    else:
        message = "研究任务执行失败；请检查数据与策略配置。"
    return {"code": code, "message": message}


def prepare_job(job, config):
    """Keep fixed private provider access in process memory, never in queue artifacts."""
    prepared = dict(job)
    if job.get("dataSource") == "tushare" and config.get("provider_access"):
        prepared["providerAccess"] = dict(config["provider_access"])
    return prepared


def run_job(job, *, token=None, cache_dir=None, allowed_proxy_hosts=None):
    """Run a single trusted edge job; callable locally with no queue interaction."""
    if not isinstance(job, dict) or not isinstance(job.get("strategy"), dict):
        raise RunnerError("INVALID_JOB", "任务缺少策略。")
    try:
        if len(json.dumps(job, allow_nan=False).encode()) > MAX_JOB_BYTES:
            raise RunnerError("JOB_SIZE", "任务数据过大。")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, RunnerError):
            raise
        raise RunnerError("INVALID_JOB", "任务不是有效 JSON。") from None
    strategy = job["strategy"]
    source = job.get("dataSource")
    if source == "demo":
        data, provenance = make_demo_data(strategy)
    elif source == "upload":
        data, provenance = validate_upload(strategy, job.get("dataset"))
    elif source == "tushare":
        access = job.get("providerAccess")
        # Separate cache directories by workspace before token fingerprinting.
        if cache_dir:
            owner = job.get("workspaceId", job.get("id", "local"))
            if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner):
                raise RunnerError("INVALID_JOB_OWNER", "任务身份无效。")
            cache_dir = str(Path(cache_dir) / owner)
        if token:
            data, provenance = load_tushare(strategy, token, cache_dir)
        elif isinstance(access, dict):
            url = access.get("proxyUrl", "")
            parsed = urlparse(url) if isinstance(url, str) else None
            hosts = allowed_proxy_hosts or ["atlas-aletheia.com"]
            if not parsed or parsed.scheme != "https" or parsed.hostname not in hosts or parsed.port not in (None, 443):
                raise RunnerError("PROVIDER_PROXY_FORBIDDEN", "行情代理域名未获运行端授权。")
            data, provenance = load_tushare_proxy(strategy, url, access.get("serviceToken"), cache_dir)
        else:
            raise ProviderError("TUSHARE_TOKEN_MISSING", "本任务没有获授权的 Tushare 数据入口；不会自动改用演示数据。")
    else:
        raise RunnerError("DATA_SOURCE", "数据来源必须为 demo、upload 或 tushare。")
    # Deferred import: data ingestion errors remain distinguishable from engine errors.
    from .engine import run_research
    try:
        from threadpoolctl import threadpool_limits
        scope = threadpool_limits(limits=2)
    except ImportError:
        scope = contextlib.nullcontext()
    with scope:
        result = run_research(strategy, data, provenance)
    if not isinstance(result, dict):
        raise RunnerError("ENGINE_RESULT", "研究引擎返回格式无效。")
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise RunnerError("ENGINE_RESULT", "研究结果包含不可序列化数据。") from None
    if len(encoded.encode()) > MAX_RESULT_BYTES:
        raise RunnerError("RESULT_SIZE", "研究结果超出大小限制。")
    return result


def _child_entry(connection, job, token, cache_dir, allowed_proxy_hosts):
    try:
        # Third-party libraries may print. Never forward child stdout/stderr to service logs.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run_job(job, token=token, cache_dir=cache_dir, allowed_proxy_hosts=allowed_proxy_hosts)
        connection.send({"result": result})
    except BaseException as exc:
        connection.send({"error": _safe_error(exc)})
    finally:
        connection.close()


def execute_bounded(job, *, timeout=DEFAULT_TIMEOUT, token=None, cache_dir=None, allowed_proxy_hosts=None, heartbeat=None, stop_requested=None):
    """Hard wall-clock process bound, with optional lease/cancellation callback."""
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child_entry, args=(child, job, token, cache_dir, allowed_proxy_hosts), daemon=True)
    deadline = time.monotonic() + timeout
    process.start()
    child.close()
    next_heartbeat = time.monotonic() + 15
    try:
        while True:
            if stop_requested and stop_requested():
                return {"error": {"code": "RUNNER_STOPPED", "message": "运行服务停止，任务已中止。"}}
            if time.monotonic() >= deadline:
                return {"error": {"code": "JOB_TIMEOUT", "message": "研究任务超过运行时间限制。"}}
            if parent.poll(min(0.2, max(0, deadline-time.monotonic()))):
                try:
                    return parent.recv()
                except EOFError:
                    return {"error": {"code": "RUNNER_CHILD_EXIT", "message": "研究进程意外退出。"}}
            if heartbeat and time.monotonic() >= next_heartbeat:
                state = heartbeat()
                if state and (state.get("cancelled") or state.get("leaseValid") is False):
                    return {"error": {"code": "JOB_CANCELLED", "message": "任务已取消或租约失效。"}}
                next_heartbeat = time.monotonic() + 15
            if not process.is_alive():
                if parent.poll(0.1):
                    continue
                return {"error": {"code": "RUNNER_CHILD_EXIT", "message": "研究进程意外退出。"}}
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)
        parent.close()


class QueueClient:
    def __init__(self, config, session=None):
        self.base = config["api_base"].rstrip("/")
        self.secret = config["runner_secret"]
        self.session = session or requests.Session()

    def post(self, route, payload):
        if route not in ("claim", "heartbeat", "complete"):
            raise RunnerError("QUEUE_ROUTE", "队列接口无效。")
        try:
            response = self.session.post(self.base + "/runner/" + route, json=payload,
                headers={"Authorization": "Bearer " + self.secret, "Accept": "application/json"},
                timeout=(10, 35), allow_redirects=False, stream=True)
            with response:
                if response.status_code != 200:
                    raise RunnerError("QUEUE_HTTP", "队列服务未成功响应。", http_status=response.status_code)
                parts, size = [], 0
                for block in response.iter_content(65536):
                    size += len(block)
                    if size > MAX_JOB_BYTES:
                        raise RunnerError("QUEUE_SIZE", "队列响应过大。")
                    parts.append(block)
                value = json.loads(b"".join(parts))
            if not isinstance(value, dict):
                raise RunnerError("QUEUE_RESPONSE", "队列响应无效。")
            return value
        except RunnerError:
            raise
        except (requests.RequestException, ValueError, TypeError):
            raise RunnerError("QUEUE_NETWORK", "无法访问队列服务。") from None


class CompletionSpool:
    """Encrypted durable delivery, authenticated to the configured queue and runner."""
    def __init__(self, config):
        self.root = Path(config["delivery_dir"])
        if not self.root.is_absolute() or self.root.is_symlink():
            raise RunnerError("DELIVERY_PATH", "结果重试目录无效。")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise RunnerError("DELIVERY_PERMISSIONS", "结果重试目录权限须为 0700。")
        self.aad = ("atlas-quant-completion-v1:" + config["api_base"]).encode()
        self.cipher = AESGCM(hashlib.sha256(b"atlas-quant-delivery-key-v1\0" + config["runner_secret"].encode()).digest())

    def write(self, payload):
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if len(content) > MAX_RESULT_BYTES + 4096:
            raise RunnerError("DELIVERY_SIZE", "结果回传内容过大。")
        identity = str(payload["id"]) + "\0" + str(payload["leaseToken"])
        path = self.root / (hashlib.sha256(identity.encode()).hexdigest() + ".enc")
        nonce = os.urandom(12)
        data = b"AQD1" + nonce + self.cipher.encrypt(nonce, content, self.aad)
        temp = path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            raise RunnerError("DELIVERY_WRITE", "无法保存待回传结果；未领取新任务。") from None
        return path

    def pending(self):
        paths = sorted(self.root.glob("*.enc"), key=lambda p: p.stat().st_mtime)
        if len(paths) > 128:
            raise RunnerError("DELIVERY_BACKLOG", "待回传结果过多，暂停领取新任务。")
        for path in paths:
            try:
                if path.is_symlink() or path.stat().st_size > MAX_RESULT_BYTES + 8192 or stat.S_IMODE(path.stat().st_mode) & 0o077:
                    raise ValueError()
                data = path.read_bytes()
                if data[:4] != b"AQD1":
                    raise ValueError()
                result = json.loads(self.cipher.decrypt(data[4:16], data[16:], self.aad))
                if not isinstance(result, dict) or not result.get("id") or not result.get("leaseToken") or ("result" in result) == ("error" in result):
                    raise ValueError()
                yield path, result
            except Exception:
                raise RunnerError("DELIVERY_INTEGRITY", "待回传结果无法验证；保持原件并暂停领取新任务。") from None

    def acknowledge(self, path):
        path.unlink()


def flush_completions(client, spool):
    for path, payload in spool.pending():
        delivered = False
        for attempt in range(5):
            try:
                client.post("complete", payload)
                spool.acknowledge(path)
                delivered = True
                break
            except RunnerError as exc:
                if exc.http_status in (400, 413) and "result" in payload:
                    # The queue definitively rejected these bytes. Replace the durable
                    # delivery with a small sanitized terminal failure, never silently
                    # drop it or endlessly poison subsequent jobs with the same report.
                    payload = {"id": payload["id"], "leaseToken": payload["leaseToken"],
                               "error": {"code": "RESULT_REJECTED", "message": "研究结果未通过服务端接收校验，此次实验已停止；请检查策略与数据后重新运行。"}}
                    path = spool.write(payload)
                    continue
                _wait(min(2**attempt, 10))
        if not delivered:
            raise RunnerError("COMPLETION_UNCONFIRMED", "结果回传未确认；已私密保存，仅重试回传，不重放计算。")


def _stop(signum, frame):
    global STOP
    STOP = True


def _wait(seconds):
    end = time.monotonic() + seconds
    while not STOP and time.monotonic() < end:
        time.sleep(min(0.5, max(0, end-time.monotonic())))


def _serve(config, spool, *, once=False):
    client = QueueClient(config)
    # Resume delivery first after restart. Never rerun a completed calculation.
    flush_completions(client, spool)
    while not STOP:
        try:
            claimed = client.post("claim", {"runnerVersion": "atlas-quant-runner/0.1", "leaseSeconds": config.get("job_timeout", DEFAULT_TIMEOUT) + 90})
            job = claimed.get("job")
            if job is None:
                if once:
                    return 0
                _wait(config.get("poll_seconds", 10))
                continue
            if not isinstance(job, dict) or not all(isinstance(job.get(k), str) and job[k] for k in ("id", "leaseToken")):
                raise RunnerError("QUEUE_JOB", "队列任务格式无效。")
            identity = {"id": job["id"], "leaseToken": job["leaseToken"]}
            def heartbeat():
                try:
                    return client.post("heartbeat", dict(identity, status="running"))
                except RunnerError:
                    return None  # A transient network error is not a cancellation.
            answer = execute_bounded(prepare_job(job, config), timeout=config.get("job_timeout", DEFAULT_TIMEOUT),
                token=os.environ.get("TUSHARE_TOKEN"), cache_dir=config.get("cache_dir"),
                allowed_proxy_hosts=config.get("allowed_proxy_hosts"), heartbeat=heartbeat,
                stop_requested=lambda: STOP)
            # Persist authenticated encrypted bytes before exact idempotent delivery.
            complete = dict(identity, **answer)
            spool.write(complete)
            flush_completions(client, spool)
            if once:
                return 0
        except RunnerError as exc:
            print(json.dumps({"event": "runner_error", "code": exc.code}, ensure_ascii=False), flush=True)
            if once or exc.code.startswith("DELIVERY_") or exc.code == "COMPLETION_UNCONFIRMED":
                return 1
            _wait(config.get("poll_seconds", 10))
    return 0


def serve(config, *, once=False):
    spool = CompletionSpool(config)
    descriptor = os.open(spool.root / "runner.lock", os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerError("RUNNER_ALREADY_ACTIVE", "同一配置的运行服务已启动。") from None
        return _serve(config, spool, once=once)
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Atlas Quant outbound-only worker")
    parser.add_argument("--config", required=True, help="Private 0600 JSON config outside repository")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        return serve(load_config(args.config), once=args.once)
    except RunnerError as exc:
        print(json.dumps({"error": _safe_error(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
