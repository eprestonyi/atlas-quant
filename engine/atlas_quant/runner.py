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

from . import __version__
from .provider import ProviderError, load_tushare, load_tushare_proxy, validate_upload
from .fixtures import make_demo_data
from .connectors import PCDReadClient, join_pcd_asof

MAX_JOB_BYTES = 26 * 1024 * 1024
MAX_RESULT_BYTES = 24 * 1024 * 1024
DEFAULT_TIMEOUT = 900
BUNDLE_DELIVERY_SECONDS = 300
STOP = False


class RunnerError(ValueError):
    def __init__(self, code, message, *, http_status=None, remote_code=None):
        self.code = code
        self.http_status = http_status
        self.remote_code = remote_code
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
    pcd_access = config.get("pcd_access")
    if pcd_access is not None:
        if not isinstance(pcd_access, dict) or set(pcd_access) != {"url", "token"}:
            raise RunnerError("CONFIG_PCD", "PCD 私有配置需包含固定 url 与 token。")
        try:
            PCDReadClient(pcd_access["url"], pcd_access["token"])
        except (ProviderError, ValueError, TypeError):
            raise RunnerError("CONFIG_PCD", "PCD 读取地址或凭据无效。") from None
    delivery = Path(config.get("delivery_dir", str(target.parent / "delivery"))).expanduser()
    if not delivery.is_absolute() or delivery.is_symlink():
        raise RunnerError("CONFIG_DELIVERY", "结果重试目录必须是绝对私有路径。")
    config["delivery_dir"] = str(delivery)
    if type(config.get("financial_dataset_research_enabled", False)) is not bool:
        raise RunnerError("CONFIG_DATASET", "冻结财务预测开关必须是布尔值。")
    if type(config.get("financial_graph_research_enabled", False)) is not bool:
        raise RunnerError("CONFIG_DATASET", "冻结财务图预测开关必须是布尔值。")
    if config.get("financial_graph_research_enabled") is True and not config.get("compute_lock_path"):
        raise RunnerError("CONFIG_COMPUTE_SLOT", "财务图研究必须配置共享私有计算锁。")
    if type(config.get("market_dataset_research_enabled", False)) is not bool:
        raise RunnerError("CONFIG_MARKET", "完整市场预测开关必须是布尔值。")
    if config.get("market_dataset_research_enabled") is True and not config.get(
        "compute_lock_path"
    ):
        raise RunnerError("CONFIG_COMPUTE_SLOT", "完整市场研究必须配置共享私有计算锁。")
    if "compute_lock_path" in config:
        from .compute_slot import ComputeSlotError, validate_slot_path

        try:
            validate_slot_path(config["compute_lock_path"])
        except ComputeSlotError:
            raise RunnerError("CONFIG_COMPUTE_SLOT", "计算锁须为本用户私有目录中的规范绝对路径；现存锁须为0600普通文件。") from None
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
    if job.get("jobKind") == "execution" or job.get("dataSource") in {
        "ready_dataset",
        "ready_market",
    }:
        # Replay is strictly an immutable-input operation. No provider or PCD
        # credentials should be present even when the original source was real.
        prepared.pop("providerAccess", None)
        prepared.pop("pcdAccess", None)
        return prepared
    if job.get("dataSource") == "tushare" and config.get("provider_access"):
        prepared["providerAccess"] = dict(config["provider_access"])
    strategy = job.get("strategy")
    bindings = strategy.get("dataBindings") if isinstance(strategy, dict) else None
    if isinstance(bindings, dict) and bindings.get("pcd") and config.get("pcd_access"):
        prepared["pcdAccess"] = dict(config["pcd_access"])
    return prepared


def run_job(job, *, token=None, cache_dir=None, allowed_proxy_hosts=None, snapshot_sink=None,
            forecast_plan_sink=None, result_limit=None, compute_lock_path=None, deadline=None):
    """Run a single trusted edge job; callable locally with no queue interaction."""
    if not isinstance(job, dict) or not isinstance(job.get("strategy"), dict):
        raise RunnerError("INVALID_JOB", "任务缺少策略。")
    try:
        # A replay contains two separately downloaded, independently bounded
        # artifacts. A normal claim still has the original single-job bound.
        limit = (result_limit + MAX_JOB_BYTES if result_limit is not None else 2 * MAX_JOB_BYTES) if job.get("jobKind") == "execution" else MAX_JOB_BYTES
        if len(json.dumps(job, allow_nan=False).encode()) > limit:
            raise RunnerError("JOB_SIZE", "任务数据过大。")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, RunnerError):
            raise
        raise RunnerError("INVALID_JOB", "任务不是有效 JSON。") from None
    strategy = job["strategy"]
    if job.get("jobKind") == "execution":
        from .runner_artifacts import restore_input
        from .statistical_quant import execute_forecasts
        replay = job.get("replay")
        if not isinstance(replay, dict) or not isinstance(replay.get("artifact"), dict):
            raise RunnerError("REPLAY_INPUT", "执行实验缺少原始预测产物。")
        artifact = replay["artifact"]
        if len(json.dumps(artifact, ensure_ascii=False, separators=(",", ":")).encode()) > (MAX_RESULT_BYTES if result_limit is None else result_limit):
            raise RunnerError("REPLAY_SIZE", "原始预测产物超过重放大小限制。")
        if (not isinstance(job.get("forecastArtifactId"), str) or not job["forecastArtifactId"]
                or artifact.get("artifactId") != job["forecastArtifactId"]):
            raise RunnerError("REPLAY_IDENTITY", "执行实验与原始预测身份不一致。")
        from .compute_slot import compute_slot
        with compute_slot(compute_lock_path, deadline=deadline):
            data, provenance = restore_input(strategy, replay.get("snapshot"), artifact.get("dataFingerprint"), max_bytes=result_limit)
            result = execute_forecasts(strategy, data, artifact, provenance=provenance)
        return _validate_result(result, limit=result_limit)
    data_bindings = strategy.get("dataBindings") or {}
    if not isinstance(data_bindings, dict):
        raise RunnerError("INVALID_DATA_BINDINGS", "数据映射必须是对象。")
    pcd_bindings = data_bindings.get("pcd") or {}
    if not isinstance(pcd_bindings, dict):
        raise RunnerError("INVALID_DATA_BINDINGS", "PCD 映射必须是对象。")
    deferred = set(pcd_bindings)
    source = job.get("dataSource")
    if source == "demo":
        if pcd_bindings:
            raise RunnerError("PCD_REAL_DATA_REQUIRED", "PCD 披露因子需搭配真实行情或导入数据，不能使用教程价格。")
        data, provenance = make_demo_data(strategy)
    elif source == "upload":
        data, provenance = validate_upload(strategy, job.get("dataset"), deferred_fields=deferred)
    elif source == "tushare":
        access = job.get("providerAccess")
        # Separate cache directories by workspace before token fingerprinting.
        if cache_dir:
            owner = job.get("workspaceId", job.get("id", "local"))
            if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner):
                raise RunnerError("INVALID_JOB_OWNER", "任务身份无效。")
            cache_dir = str(Path(cache_dir) / owner)
        if token:
            data, provenance = load_tushare(strategy, token, cache_dir, deferred_fields=deferred)
        elif isinstance(access, dict):
            url = access.get("proxyUrl", "")
            parsed = urlparse(url) if isinstance(url, str) else None
            hosts = allowed_proxy_hosts or ["atlas-aletheia.com"]
            if not parsed or parsed.scheme != "https" or parsed.hostname not in hosts or parsed.port not in (None, 443):
                raise RunnerError("PROVIDER_PROXY_FORBIDDEN", "行情代理域名未获运行端授权。")
            data, provenance = load_tushare_proxy(strategy, url, access.get("serviceToken"), cache_dir, deferred_fields=deferred)
        else:
            raise ProviderError("TUSHARE_TOKEN_MISSING", "本任务没有获授权的 Tushare 数据入口；不会自动改用演示数据。")
    else:
        raise RunnerError("DATA_SOURCE", "数据来源必须为 demo、upload 或 tushare。")
    if pcd_bindings:
        access = job.get("pcdAccess")
        if not isinstance(access, dict) or set(access) != {"url", "token"}:
            raise RunnerError("PCD_ACCESS_MISSING", "尚未配置 PCD 私有只读连接。")
        client = PCDReadClient(access["url"], access["token"])
        data, pcd_provenance = join_pcd_asof(data, provenance["tradingDates"], pcd_bindings, client)
        external = {**provenance.get("externalFields", {}), **pcd_provenance.pop("externalFields")}
        provenance.update(pcd_provenance, externalFields=external)
        from .provider import canonical_hash, _records
        provenance["dataFingerprint"] = canonical_hash(_records(data))
        provenance.setdefault("datasets", []).append("PCD_SELECTED_OBSERVATIONS")
        provenance.setdefault("warnings", []).append("PCD 仅使用显式映射的主体、记录与单位；可用日不早于系统获知及来源时间，缺失不填零。十进制原值保存在 PCD，研究矩阵使用 float64。")
    # Deferred import: data ingestion errors remain distinguishable from engine errors.
    from .engine import run_research
    try:
        from threadpoolctl import threadpool_limits
        scope = threadpool_limits(limits=2)
    except ImportError:
        scope = contextlib.nullcontext()
    from .compute_slot import compute_slot
    with scope, compute_slot(compute_lock_path, deadline=deadline):
        result = run_research(strategy, data, provenance, forecast_plan_sink=forecast_plan_sink)
    _validate_result(result, limit=result_limit)
    if snapshot_sink is not None and strategy.get("research", {}).get("mode") == "statistical_quant":
        from .runner_artifacts import freeze_input
        snapshot_sink(freeze_input(strategy, data, provenance, max_bytes=result_limit))
    return result


def _validate_result(result, *, limit=None):
    if not isinstance(result, dict):
        raise RunnerError("ENGINE_RESULT", "研究引擎返回格式无效。")
    try:
        size = 0
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        for piece in encoder.iterencode(result):
            size += len(piece.encode())
            if size > (MAX_RESULT_BYTES if limit is None else limit):
                raise RunnerError("RESULT_SIZE", "研究结果超出大小限制。")
    except RunnerError:
        raise
    except (TypeError, ValueError, UnicodeEncodeError):
        raise RunnerError("ENGINE_RESULT", "研究结果包含不可序列化数据。") from None
    return result


def _child_entry(connection, job, token, cache_dir, allowed_proxy_hosts, capture_snapshot=False, bundle_context=None, compute_lock_path=None, deadline=None):
    try:
        if job.get("dataSource") == "ready_market":
            if bundle_context is None or token is not None:
                raise RunnerError(
                    "MARKET_SOURCE_IDENTITY", "完整市场预测须使用隔离冻结输入。"
                )
            os.environ.pop("TUSHARE_TOKEN", None)
            from .market_research_runner.compute import compute

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                answer = compute(
                    job, bundle_context, slot_path=compute_lock_path, deadline=deadline
                )
            connection.send(answer)
            return
        if job.get("dataSource") == "ready_dataset":
            if bundle_context is None or token is not None:
                raise RunnerError("DATASET_INPUT_IDENTITY", "数据集预测须使用隔离冻结输入。")
            os.environ.pop("TUSHARE_TOKEN", None)
            from .graph_research_runner.protocol import requested as graph_requested
            if graph_requested(job):
                from .graph_research_runner.compute import compute
            else:
                from .research_dataset_runner.compute import compute
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                answer = compute(job, bundle_context, slot_path=compute_lock_path, deadline=deadline)
            connection.send(answer)
            return
        snapshots, plans = [], []
        if bundle_context is not None:
            from .bundle import BUNDLE_LIMIT
            from .bundle_spool import BundleSpool
            bundle_store = BundleSpool(bundle_context)
            if job.get("replayBundle"):
                reference = job["replayBundle"]
                if reference.get("_bundleKey") != bundle_store.key:
                    raise RunnerError("REPLAY_IDENTITY", "本地分片引用不属于本任务。")
                source = bundle_store.reader(reference.get("bundleId"))
                source.verify_integrity()
                replay = {"artifact": dict(source.document("forecast"), artifactId=source.manifest["forecastArtifactId"]),
                          "snapshot": source.document("snapshot"), "coverage": source.document("coverage")}
                job = dict(job, replay=replay)
        # Third-party libraries may print. Never forward child stdout/stderr to service logs.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run_job(job, token=token, cache_dir=cache_dir, allowed_proxy_hosts=allowed_proxy_hosts,
                             snapshot_sink=snapshots.append if capture_snapshot else None,
                             forecast_plan_sink=plans.append if bundle_context is not None else None,
                             result_limit=BUNDLE_LIMIT if bundle_context is not None else None,
                             compute_lock_path=compute_lock_path, deadline=deadline)
        if bundle_context is not None:
            coverage = plans[0] if plans else job.get("replay", {}).get("coverage")
            if coverage is None and job.get("jobKind") == "execution":
                # Older artifacts never claimed an independent pre-fit origin plan.
                artifact = result["forecasts"]
                coverage = {"schemaVersion": 1, "source": "legacy_artifact_derived",
                    "holdoutStart": artifact["diagnostics"]["holdoutStart"],
                    "baselineRequired": "baselineRows" in artifact["diagnostics"].get("factorIncrement", {}),
                    "origins": [{k: row[k] for k in ("date", "targetId", "entryDate", "targetDate")}
                                | {"inputValid": row.get("invalidReason") not in ("incomplete_state_or_formation", "no_observed_event", "no_observed_fundamental_predictor")}
                                for row in artifact["rows"]]}
            answer = bundle_store.build(result, snapshots[0] if snapshots else None, coverage)
        else:
            answer = {"result": result}
            if snapshots:
                answer["snapshot"] = snapshots[0]
        connection.send(answer)
    except BaseException as exc:
        connection.send({"error": _safe_error(exc)})
    finally:
        connection.close()


def execute_bounded(job, *, timeout=DEFAULT_TIMEOUT, token=None, cache_dir=None, allowed_proxy_hosts=None, heartbeat=None, stop_requested=None, capture_snapshot=False, bundle_context=None, compute_lock_path=None):
    """Hard wall-clock process bound, with optional lease/cancellation callback."""
    market_budget = None
    from .graph_research_runner.protocol import requested as graph_requested
    if graph_requested(job) and bundle_context is not None:
        from .graph_research_runner.limits import GraphProcessBudget
        market_budget = GraphProcessBudget(bundle_context)
    if job.get("dataSource") == "ready_market" and bundle_context is not None:
        from .market_research_runner.limits import MarketProcessBudget
        market_budget = MarketProcessBudget(bundle_context)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    deadline = time.monotonic() + timeout
    process = ctx.Process(target=_child_entry, args=(child, job, token, cache_dir, allowed_proxy_hosts, capture_snapshot, bundle_context, compute_lock_path, deadline), daemon=True)
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
            if market_budget:
                try:
                    market_budget.check(process.pid)
                except RunnerError as exc:
                    # A completed child can disappear between is_alive and ps.
                    # Accept its already serialized final reply, never skip a
                    # failed sample while a model is still executing.
                    if exc.code == "CAPACITY_MONITOR" and not process.is_alive() and parent.poll(0.2):
                        try:
                            return parent.recv()
                        except EOFError:
                            pass
                    return {"error": _safe_error(exc)}
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)
        parent.close()


def _http_rejection(response):
    """Keep only a bounded machine code; never retain server message/body text."""
    remote = None
    try:
        parts, size = [], 0
        for block in response.iter_content(4096):
            size += len(block)
            if size > 4096:
                break
            parts.append(block)
        else:
            value = json.loads(b"".join(parts))
            candidate = value.get("error", value) if isinstance(value, dict) else None
            candidate = candidate.get("code") if isinstance(candidate, dict) else None
            if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", candidate):
                remote = candidate
    except Exception:
        pass
    return RunnerError("QUEUE_HTTP", "队列服务未成功响应。", http_status=response.status_code, remote_code=remote)


class QueueClient:
    def __init__(self, config, session=None):
        self.base = config["api_base"].rstrip("/")
        self.secret = config["runner_secret"]
        self.session = session or requests.Session()

    def post(self, route, payload, *, deadline=None):
        if route not in ("claim", "heartbeat", "complete", "snapshot", "replay", "bundles/begin", "bundles/finalize", "financial-bundles/begin", "financial-bundles/finalize", "financial-bundles/complete", "financial-graph-bundles/begin", "financial-graph-bundles/finalize", "financial-graph-bundles/complete"):
            raise RunnerError("QUEUE_ROUTE", "队列接口无效。")
        deadline = min(deadline, time.monotonic()+60) if deadline is not None else time.monotonic()+60
        try:
            # Use exactly the compact UTF-8 representation used by size budgets.
            # requests' json= defaults to ASCII escapes/spaces and can expand a
            # permitted 24 MiB snapshot beyond the queue's envelope limit.
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise RunnerError("QUEUE_DEADLINE", "队列读取超过本次时间预算。")
            response = self.session.post(self.base + "/runner/" + route, data=encoded,
                headers={"Authorization": "Bearer " + self.secret, "Accept": "application/json", "Content-Type": "application/json"},
                timeout=(min(10, remaining), min(35, remaining)), allow_redirects=False, stream=True)
            with response:
                if response.status_code != 200:
                    raise _http_rejection(response)
                parts, size = [], 0
                for block in response.iter_content(65536):
                    if time.monotonic() > deadline:
                        raise RunnerError("QUEUE_DEADLINE", "队列读取超过本次时间预算。")
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

    def bundle_chunk(self, method, bundle_id, collection, ordinal, identity, *, raw=None, stage_id=None, deadline=None, namespace="bundles"):
        from .bundle import HASH, COLLECTIONS, CHUNK_LIMIT
        if namespace == "financial-graph-bundles":
            from .financial_bundle_v2 import COLLECTIONS
        if (namespace not in ("bundles", "financial-bundles", "financial-graph-bundles") or method not in ("GET", "PUT")
                or namespace == "financial-graph-bundles" and method != "PUT" or not isinstance(bundle_id, str) or not HASH.fullmatch(bundle_id)
                or collection not in COLLECTIONS or not isinstance(ordinal, int) or isinstance(ordinal, bool) or not 0 <= ordinal < 256):
            raise RunnerError("QUEUE_ROUTE", "分片接口身份无效。")
        if method == "PUT" and (not isinstance(raw, bytes) or len(raw) > CHUNK_LIMIT or not stage_id):
            raise RunnerError("BUNDLE_SIZE", "分片上传内容或阶段无效。")
        deadline = min(deadline, time.monotonic()+60) if deadline is not None else time.monotonic()+60
        headers = {"Authorization": "Bearer " + self.secret, "Content-Type": "application/json",
                   "X-Quant-Job": identity["id"], "X-Quant-Lease": identity["leaseToken"]}
        if stage_id:
            headers["X-Quant-Stage"] = stage_id
        try:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise RunnerError("QUEUE_DEADLINE", "分片读取超过本次时间预算。")
            url = self.base + f"/runner/{namespace}/{bundle_id}/chunks/{collection}/{ordinal}"
            with self.session.request(method, url, data=raw, headers=headers, allow_redirects=False,
                    timeout=(min(10, remaining), min(35, remaining)), stream=True) as response:
                if response.status_code != 200:
                    raise _http_rejection(response)
                parts, size = [], 0
                for piece in response.iter_content(65536):
                    size += len(piece)
                    if size > (CHUNK_LIMIT if method == "GET" else 65536):
                        raise RunnerError("QUEUE_SIZE", "分片响应超过大小限制。")
                    if time.monotonic() >= deadline:
                        raise RunnerError("QUEUE_DEADLINE", "分片读取超过本次时间预算。")
                    parts.append(piece)
                content = b"".join(parts)
                if method == "GET":
                    return content
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise RunnerError("BUNDLE_PROTOCOL", "分片回执格式无效。")
                return value
        except RunnerError:
            raise
        except (requests.RequestException, ValueError, TypeError):
            raise RunnerError("QUEUE_NETWORK", "无法访问分片服务。") from None


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
        self.encryption_key = hashlib.sha256(b"atlas-quant-delivery-key-v1\0" + config["runner_secret"].encode()).digest()
        self.cipher = AESGCM(self.encryption_key)

    def write(self, payload):
        payload = dict(payload)
        if "snapshot" in payload:
            from .runner_artifacts import SnapshotSpool
            snapshot = payload.pop("snapshot")
            payload["_snapshotKey"] = SnapshotSpool(self).write(payload, snapshot)
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
                if not isinstance(result, dict) or not result.get("id") or not result.get("leaseToken") or sum(key in result for key in ("result", "error", "bundleId")) != 1:
                    raise ValueError()
                yield path, result
            except Exception:
                raise RunnerError("DELIVERY_INTEGRITY", "待回传结果无法验证；保持原件并暂停领取新任务。") from None

    def acknowledge(self, path):
        path.unlink()


def flush_completions(client, spool):
    from .runner_claims import ClaimIntent, claim_request, validate_receipt
    from .bundle_spool import BundleSpool, deliver_bundle
    private_keys = {"_snapshotKey", "_claimRequestId", "_bundleKey", "_bundleFormat", "_datasetKey", "_marketKey", "_terminalConfirmed", "_quarantined"}
    from .delivery_quarantine import read as read_quarantine, preserve as preserve_rejection, rejected
    for path, payload in spool.pending():
        quarantine = read_quarantine(spool, payload)
        if quarantine is not None and not payload.get("_quarantined"):
            payload = rejected(payload)
            path = spool.write(payload)
        if payload.get("_quarantined") and quarantine is None:
            raise RunnerError("DELIVERY_QUARANTINE", "缺少原始拒绝证据；停止清理。")
        bundle_format = payload.get("_bundleFormat", "atlas.quant.bundle/1")
        if bundle_format not in {"atlas.quant.bundle/1", "atlas.quant.financial_bundle/1", "atlas.quant.financial_bundle/2"}:
            raise RunnerError("DELIVERY_INTEGRITY", "未知的持久结果格式；保留原件。")
        store_class, deliver = BundleSpool, deliver_bundle
        complete_route = "complete"
        if bundle_format == "atlas.quant.financial_bundle/1":
            from .financial_bundle_spool import FinancialBundleSpool, deliver_financial_bundle
            store_class, deliver = FinancialBundleSpool, deliver_financial_bundle
            complete_route = "financial-bundles/complete"
        if bundle_format == "atlas.quant.financial_bundle/2":
            from .financial_graph_bundle_spool import FinancialGraphBundleSpool, deliver_graph_bundle
            store_class, deliver = FinancialGraphBundleSpool, deliver_graph_bundle
            complete_route = "financial-graph-bundles/complete"
        request_id = payload.get("_claimRequestId")
        intent = {"requestId": request_id, "jobId": payload["id"], "leaseToken": payload["leaseToken"]}
        snapshot_key = payload.get("_snapshotKey")
        snapshot_store = bundle_store = dataset_store = None
        if payload.get("_datasetKey") is not None:
            from .research_dataset_runner.spool import ResearchDatasetSpool
            if bundle_format == "atlas.quant.financial_bundle/2":
                from .graph_research_runner.spool import GraphResearchSpool
                dataset_store = GraphResearchSpool.from_spool(spool, payload)
            else:
                dataset_store = ResearchDatasetSpool.from_spool(spool, payload)
            if dataset_store.key != payload["_datasetKey"]:
                raise RunnerError("DELIVERY_INTEGRITY", "冻结数据集与待回传任务身份不一致。")
        if payload.get("_marketKey") is not None:
            from .market_research_runner.spool import MarketResearchSpool

            if dataset_store is not None:
                raise RunnerError(
                    "DELIVERY_INTEGRITY", "单个结果不能绑定两种冻结来源。"
                )
            dataset_store = MarketResearchSpool.from_spool(spool, payload)
            if dataset_store.key != payload["_marketKey"]:
                raise RunnerError(
                    "DELIVERY_INTEGRITY", "完整市场来源与待回传任务身份不一致。"
                )
        if snapshot_key is not None:
            from .runner_artifacts import SnapshotSpool

            snapshot_store = SnapshotSpool(spool)
            snapshot = snapshot_store.read(snapshot_key, payload)
        if payload.get("_bundleKey") is not None:
            bundle_store = store_class.from_spool(spool, payload)
            if bundle_store.key != payload["_bundleKey"]:
                raise RunnerError("DELIVERY_INTEGRITY", "分片身份与待回传任务不一致。")
        delivery_deadline = time.monotonic()+BUNDLE_DELIVERY_SECONDS if bundle_store is not None else None
        delivered = False
        for attempt in range(5):
            submitting_result = True
            try:
                if delivery_deadline is not None and time.monotonic() >= delivery_deadline:
                    raise RunnerError("DELIVERY_DEADLINE", "本次回传总时间预算耗尽；保留原件并只恢复投递。")
                terminal_discard = False
                if not payload.get("_terminalConfirmed"):
                    if "bundleId" in payload:
                        stage = deliver(client, spool, payload, deadline=delivery_deadline)
                        terminal_discard = stage is None
                        if stage is not None and payload.get("stageId") != stage:
                            payload = dict(payload, stageId=stage)
                            path = spool.write(payload)
                    public_payload = {key: value for key, value in payload.items() if key not in private_keys}
                    if snapshot_store is not None and "result" in public_payload:
                        client.post("snapshot", {"id": payload["id"], "leaseToken": payload["leaseToken"],
                                                  "snapshot": snapshot})
                    if not terminal_discard:
                        route = "complete" if "error" in public_payload else complete_route
                        if delivery_deadline is None:
                            client.post(route, public_payload)
                        else:
                            client.post(route, public_payload, deadline=delivery_deadline)
                submitting_result = False
                if request_id is not None:
                    receipt = (client.post("claim", claim_request(request_id)) if delivery_deadline is None
                               else client.post("claim", claim_request(request_id), deadline=delivery_deadline))
                    validate_receipt(receipt, intent, terminal_only=True)
                    ClaimIntent(spool).clear(intent)
                if bundle_store is not None or dataset_store is not None:
                    # A crash halfway through deleting multiple chunk files must
                    # resume cleanup, never re-upload a now-incomplete directory.
                    if not payload.get("_terminalConfirmed"):
                        payload = dict(payload, _terminalConfirmed=True)
                        path = spool.write(payload)
                    if not payload.get("_quarantined"):
                        if bundle_store is not None:
                            bundle_store.cleanup()
                        if dataset_store is not None:
                            dataset_store.cleanup()
                if snapshot_store is not None and not payload.get("_quarantined"):
                    snapshot_store.acknowledge(snapshot_key)
                spool.acknowledge(path)
                delivered = True
                break
            except RunnerError as exc:
                if exc.code.startswith(("CLAIM_", "DELIVERY_", "BUNDLE_PROTOCOL")):
                    raise
                if submitting_result and exc.http_status in (400, 413) and ("result" in payload or "bundleId" in payload):
                    preserve_rejection(spool, payload, exc)
                    payload = rejected(payload)
                    path = spool.write(payload)
                    continue
                _wait(min(2**attempt, 10, max(0., delivery_deadline-time.monotonic())) if delivery_deadline is not None else min(2**attempt, 10))
        if not delivered:
            raise RunnerError("COMPLETION_UNCONFIRMED", "结果回传未确认；已私密保存，仅重试回传，不重放计算。")


def _stop(signum, frame):
    global STOP
    STOP = True


def _wait(seconds):
    end = time.monotonic() + seconds
    while not STOP and time.monotonic() < end:
        time.sleep(min(0.5, max(0, end-time.monotonic())))


def fetch_replay(client, identity, *, deadline=None):
    """Read each immutable object separately; retry transport, never rebuild it."""
    deadline = deadline if deadline is not None else time.monotonic()+60
    parts = {}
    for kind, key in (("forecast", "artifact"), ("dataset", "snapshot")):
        for attempt in range(3):
            try:
                if time.monotonic() >= deadline:
                    raise RunnerError("REPLAY_UNAVAILABLE", "读取冻结输入超过时间预算；没有重新拟合或重新取数。")
                response = client.post("replay", dict(identity, kind=kind), deadline=deadline)
                value = response.get(key)
                if not isinstance(value, dict):
                    raise RunnerError("REPLAY_INPUT", "执行实验的冻结输入缺失或格式无效。")
                parts[key] = value
                break
            except RunnerError as exc:
                if exc.code == "REPLAY_INPUT" or exc.http_status in (400, 404, 413):
                    raise RunnerError("REPLAY_INPUT", "原预测或冻结行情无法读取；不会重新获取数据替代。") from None
                if exc.http_status in (401, 403, 409) or exc.code == "REPLAY_UNAVAILABLE":
                    raise
                if attempt == 2:
                    raise RunnerError("REPLAY_UNAVAILABLE", "读取冻结输入多次失败；本次执行未重新拟合或重新取数。") from None
                _wait(2 ** attempt)
    return parts


def fetch_bundle_replay(client, spool, identity, source, *, deadline):
    from .bundle import sha, BundleReader
    from .bundle_spool import BundleSpool
    response = client.post("replay", dict(identity, kind="bundle"), deadline=deadline)
    raw = response.get("manifestText")
    bundle_id = source.get("bundleId")
    if not isinstance(raw, str) or response.get("bundleId") != bundle_id:
        raise RunnerError("REPLAY_INPUT", "来源分片清单缺失或身份不匹配。")
    store = BundleSpool.from_spool(spool, identity)
    store.import_manifest(raw.encode(), bundle_id)
    reader = store.reader(bundle_id)
    for info in reader.manifest["collections"]:
        for descriptor in info["chunks"]:
            for attempt in range(3):
                try:
                    content = client.bundle_chunk("GET", bundle_id, info["id"], descriptor["ordinal"], identity, deadline=deadline)
                    if sha(content) != descriptor["sha256"] or len(content) != descriptor["byteLength"]:
                        raise RunnerError("REPLAY_INPUT", "来源分片已损坏；不会重取数据替代。")
                    store.write_chunk(info["id"], descriptor["ordinal"], content)
                    break
                except RunnerError as exc:
                    if exc.code == "REPLAY_INPUT" or exc.http_status in (400, 401, 403, 404, 409, 413) or attempt == 2:
                        raise
                    _wait(2**attempt)
    # The isolated child verifies the complete stream and materializes Python
    # objects from chunks. Only this small encrypted reference crosses IPC.
    return {"bundleId": reader.bundle_id, "_bundleKey": store.key}


def _serve(config, spool, *, once=False):
    from .runner_claims import ClaimIntent, claim_request, validate_receipt
    client = QueueClient(config)
    claims = ClaimIntent(spool)
    # Resume delivery first after restart. Never rerun a completed calculation.
    flush_completions(client, spool)
    while not STOP:
        try:
            intent = claims.current_or_create()
            claimed = client.post(
                "claim",
                claim_request(
                    intent["requestId"],
                    financial_datasets=config.get(
                        "financial_dataset_research_enabled", False
                    ),
                    financial_graphs=config.get("financial_graph_research_enabled", False),
                    market_datasets=config.get(
                        "market_dataset_research_enabled", False
                    ),
                ),
            )
            status = validate_receipt(claimed, intent)
            job = claimed.get("job")
            if job is None:
                if intent["phase"] == "executing" and intent.get("sourceKind") in {
                    "ready_dataset",
                    "ready_market",
                }:
                    # A server-side expiry/cancellation may have happened while
                    # this process was offline. Persist cleanup before clearing
                    # the only durable reference to its encrypted directories.
                    from .financial_bundle_spool import FinancialBundleSpool
                    from .research_dataset_runner.spool import ResearchDatasetSpool

                    identity = {
                        "id": intent["jobId"],
                        "leaseToken": intent["leaseToken"],
                    }
                    if intent.get("sourceKind") == "ready_market":
                        from .market_research_runner.spool import MarketResearchSpool
                        from .bundle_spool import BundleSpool

                        closure = {
                            "_bundleKey": BundleSpool.from_spool(spool, identity).key,
                            "_marketKey": MarketResearchSpool.from_spool(
                                spool, identity
                            ).key,
                        }
                    elif intent.get("sourceFormat") == "atlas.quant.research_dataset/3":
                        from .graph_research_runner.spool import GraphResearchSpool
                        from .financial_graph_bundle_spool import FinancialGraphBundleSpool
                        closure = {
                            "_bundleFormat": "atlas.quant.financial_bundle/2",
                            "_bundleKey": FinancialGraphBundleSpool.from_spool(spool, identity).key,
                            "_datasetKey": GraphResearchSpool.from_spool(spool, identity).key,
                        }
                    else:
                        closure = {
                            "_bundleFormat": "atlas.quant.financial_bundle/1",
                            "_bundleKey": FinancialBundleSpool.from_spool(
                                spool, identity
                            ).key,
                            "_datasetKey": ResearchDatasetSpool.from_spool(
                                spool, identity
                            ).key,
                        }
                    spool.write(
                        {
                            **identity,
                            "_claimRequestId": intent["requestId"],
                            **closure,
                            "error": {
                                "code": "REMOTE_TERMINAL",
                                "message": "终态已确认，仅清理本地私有中间文件。",
                            },
                            "_terminalConfirmed": True,
                        }
                    )
                    flush_completions(client, spool)
                else:
                    claims.clear(dict(intent, jobId=claimed["claim"].get("jobId")))
                if once:
                    return 0
                _wait(config.get("poll_seconds", 10))
                continue
            if not isinstance(job, dict) or not all(
                isinstance(job.get(k), str) and job[k] for k in ("id", "leaseToken")
            ):
                raise RunnerError("QUEUE_JOB", "队列任务格式无效。")
            identity = {"id": job["id"], "leaseToken": job["leaseToken"]}
            financial = job.get("dataSource") == "ready_dataset"
            from .graph_research_runner.protocol import requested as graph_requested
            graph = graph_requested(job)
            market = job.get("dataSource") == "ready_market"
            from .bundle_spool import BundleSpool

            store_class = BundleSpool
            private_format = {}
            if graph:
                from .financial_graph_bundle_spool import FinancialGraphBundleSpool
                from .graph_research_runner.spool import GraphResearchSpool
                store_class = FinancialGraphBundleSpool
                private_format = {
                    "_bundleFormat": "atlas.quant.financial_bundle/2",
                    "_datasetKey": GraphResearchSpool.from_spool(spool, identity).key,
                }
            elif financial:
                from .financial_bundle_spool import FinancialBundleSpool
                from .research_dataset_runner.spool import ResearchDatasetSpool

                store_class = FinancialBundleSpool
                private_format = {
                    "_bundleFormat": "atlas.quant.financial_bundle/1",
                    "_datasetKey": ResearchDatasetSpool.from_spool(spool, identity).key,
                }
            elif market:
                from .market_research_runner.spool import MarketResearchSpool

                private_format = {
                    "_marketKey": MarketResearchSpool.from_spool(spool, identity).key
                }
            if intent["phase"] == "executing":
                # Input/provider acquisition may already have started before the
                # process died. Do not replay paid reads or numerical computation.
                interrupted_bundle = store_class.from_spool(spool, identity)
                recovered = {
                    "error": {
                        "code": "RUNNER_INTERRUPTED",
                        "message": "运行服务在计算或取数期间中断；本次实验停止，未自动重放。",
                    }
                }
                if (financial or market) and (
                    interrupted_bundle.root / "manifest.enc"
                ).exists():
                    reader = interrupted_bundle.reader()
                    reader.verify_integrity()
                    if financial and reader.manifest["sourceEvidence"] != job.get(
                        "sourceEvidence"
                    ):
                        raise RunnerError(
                            "DELIVERY_INTEGRITY",
                            "已保存金融结果与领取的数据集身份不符。",
                        )
                    if market:
                        MarketResearchSpool.from_spool(spool, identity).inputs(
                            dict(job, **private_format)
                        )
                    recovered = {"bundleId": reader.bundle_id}
                spool.write(
                    {
                        **identity,
                        **private_format,
                        **recovered,
                        "_claimRequestId": intent["requestId"],
                        "_bundleKey": interrupted_bundle.key,
                    }
                )
                flush_completions(client, spool)
                if once:
                    return 0
                continue
            claims.executing(intent, job)
            job_deadline = time.monotonic() + config.get("job_timeout", DEFAULT_TIMEOUT)
            if financial and not graph:
                job_deadline = min(job_deadline, time.monotonic() + 600)
            bundle_context = None
            if (isinstance(job.get("strategy"), dict) and job["strategy"].get("schemaVersion") == 2
                    and job.get("resultTransport") == {"format": "atlas.quant.financial_bundle" if financial else "atlas.quant.bundle", "version": 2 if graph else 1}):
                bundle_context = store_class.context_for(spool, identity)
            input_error = None
            last_input_heartbeat = 0.
            def check_dataset_input():
                nonlocal last_input_heartbeat
                if STOP or time.monotonic() >= job_deadline:
                    raise RunnerError("JOB_TIMEOUT", "读取冻结输入超过任务时限或服务已停止。")
                if time.monotonic() >= last_input_heartbeat + 15:
                    state = client.post("heartbeat", dict(identity, status="running"), deadline=job_deadline)
                    if state.get("cancelled") or state.get("leaseValid") is False:
                        raise RunnerError("JOB_CANCELLED", "任务已取消或租约失效。")
                    last_input_heartbeat = time.monotonic()
            if financial:
                try:
                    enabled = config.get("financial_graph_research_enabled" if graph else "financial_dataset_research_enabled")
                    if enabled is not True or bundle_context is None:
                        raise RunnerError("DATASET_RESEARCH_DISABLED", "此运行端未启用相应版本的冻结财务预测。")
                    if graph:
                        from .graph_research_runner.client import GraphResearchClient as InputClient
                    else:
                        from .research_dataset_runner.client import ResearchDatasetClient as InputClient
                    job = InputClient(config).prepare(job, spool, deadline=job_deadline, check=check_dataset_input)
                except Exception as exc:
                    input_error = {"error": _safe_error(exc)}
            elif market:
                try:
                    if (
                        config.get("market_dataset_research_enabled") is not True
                        or bundle_context is None
                    ):
                        raise RunnerError(
                            "MARKET_RESEARCH_DISABLED",
                            "此运行端未启用完整冻结市场预测。",
                        )
                    from .market_research_runner.client import MarketResearchClient

                    job = MarketResearchClient(config).prepare(
                        job, spool, deadline=job_deadline, check=check_dataset_input
                    )
                except Exception as exc:
                    input_error = {"error": _safe_error(exc)}
            if job.get("jobKind") == "execution":
                try:
                    replay_deadline = min(job_deadline, time.monotonic()+60)
                    if job.get("sourceTransport", {}).get("format") == "atlas.quant.bundle":
                        reference = fetch_bundle_replay(client, spool, identity, job["sourceTransport"], deadline=replay_deadline)
                        job = dict(job, replayBundle=reference)
                    else:
                        replay = fetch_replay(client, identity, deadline=replay_deadline)
                        job = dict(job, replay=replay)
                except RunnerError as exc:
                    # A lease was already claimed. Persist even an input failure
                    # under that lease before doing anything else. Network/auth
                    # problems during completion then use the normal encrypted
                    # retry spool instead of abandoning a running queue record.
                    input_error = {"error": _safe_error(exc)}
            def heartbeat():
                try:
                    return client.post("heartbeat", dict(identity, status="running", engineVersion=__version__))
                except RunnerError:
                    return None  # A transient network error is not a cancellation.
            remaining = job_deadline-time.monotonic()
            if remaining <= 0 and input_error is None:
                input_error = {"error": {"code": "JOB_TIMEOUT", "message": "读取冻结输入后研究时间预算已耗尽。"}}
            answer = input_error or execute_bounded(
                prepare_job(job, config),
                timeout=remaining,
                token=(
                    None
                    if financial or market or job.get("jobKind") == "execution"
                    else os.environ.get("TUSHARE_TOKEN")
                ),
                cache_dir=config.get("cache_dir"),
                allowed_proxy_hosts=config.get("allowed_proxy_hosts"),
                heartbeat=heartbeat,
                stop_requested=lambda: STOP,
                capture_snapshot=job.get("jobKind") != "execution",
                bundle_context=bundle_context,
                compute_lock_path=config.get("compute_lock_path"),
            )
            # Persist authenticated encrypted bytes before exact idempotent delivery.
            complete = {**identity, **private_format, **answer, "_claimRequestId": intent["requestId"]}
            if bundle_context is not None:
                complete["_bundleKey"] = store_class(bundle_context).key
            spool.write(complete)
            flush_completions(client, spool)
            if once:
                return 0
        except RunnerError as exc:
            print(json.dumps({"event": "runner_error", "code": exc.code}, ensure_ascii=False), flush=True)
            if once or exc.code.startswith(("DELIVERY_", "CLAIM_")) or exc.code == "COMPLETION_UNCONFIRMED":
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
