"""Bounded market data ingestion. Credentials and upstream errors never leave here."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

OFFICIAL_URL = "https://api.tushare.pro"
MAX_SYMBOLS = 20
MAX_ROWS = 80000
MAX_CALENDAR_DAYS = 3660
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_CACHE_FILES = 32
REQUIRED = ["ts_code", "trade_date", "open", "high", "low", "close", "raw_close", "vol", "amount", "adj_factor"]
OPTIONAL_FIELDS = frozenset("turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv".split())
SYMBOL_RE = re.compile(r"^(?:\d{6})\.(?:SH|SZ|BJ)$")
DATASETS = {
    "trade_cal": "exchange,cal_date,is_open,pretrade_date",
    "daily": "ts_code,trade_date,open,high,low,close,vol,amount",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "daily_basic": "ts_code,trade_date," + ",".join(sorted(OPTIONAL_FIELDS)),
}


class ProviderError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ProviderError("INVALID_DATE", "日期必须是 YYYYMMDD。")
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ProviderError("INVALID_DATE", "日期无效。") from None


def validate_universe(strategy):
    if not isinstance(strategy, dict) or not isinstance(strategy.get("universe"), dict):
        raise ProviderError("INVALID_UNIVERSE", "缺少证券池。")
    u = strategy["universe"]
    symbols, start, end = u.get("symbols"), u.get("start"), u.get("end")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS:
        raise ProviderError("UNIVERSE_LIMIT", "证券池须包含 1–20 只 A 股。")
    if any(not isinstance(s, str) or not SYMBOL_RE.fullmatch(s) for s in symbols) or len(set(symbols)) != len(symbols):
        raise ProviderError("INVALID_SYMBOL", "证券代码须为唯一的六位 A 股代码及 SH/SZ/BJ 后缀。")
    a, b = parse_date(start), parse_date(end)
    if b < a or (b-a).days > MAX_CALENDAR_DAYS:
        raise ProviderError("DATE_RANGE_LIMIT", "日期范围须递增，且不超过十年。")
    if b.date() > datetime.now(ZoneInfo("Asia/Hong_Kong")).date():
        raise ProviderError("FUTURE_DATE", "数据结束日期不能晚于今天。")
    return list(symbols), start, end


def _records(frame):
    # Avoid numpy NaN/Infinity or scalar types in artifacts and fingerprints.
    return json.loads(frame.to_json(orient="records", double_precision=12))


def _factor_fields(strategy):
    from .factors import required_fields
    expressions = [f.get("expression", "") for f in strategy.get("factors", []) if isinstance(f, dict)]
    return required_fields(expressions) if expressions else set()


def _validate_panel(strategy, rows, *, allow_raw_defaults=False):
    symbols, start, end = validate_universe(strategy)
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise ProviderError("DATASET_SIZE", "数据须为非空记录数组，最多 80,000 行。")
    if any(not isinstance(row, dict) for row in rows):
        raise ProviderError("INVALID_DATASET", "每条记录必须是对象。")
    frame = pd.DataFrame(rows)
    if allow_raw_defaults:
        if "raw_close" not in frame and "close" in frame:
            frame["raw_close"] = frame["close"]
        if "adj_factor" not in frame:
            frame["adj_factor"] = 1.0
    if any(c not in frame for c in REQUIRED):
        raise ProviderError("MISSING_COLUMNS", "数据必须包含 ts_code、trade_date、OHLC、raw_close、vol、amount、adj_factor。")
    optional = sorted(OPTIONAL_FIELDS.intersection(frame.columns))
    frame = frame[REQUIRED + optional].copy()
    if any(not isinstance(v, str) or v not in symbols for v in frame.ts_code):
        raise ProviderError("DATASET_SYMBOL", "数据包含证券池外代码。")
    dates = frame.trade_date.tolist()
    if any(not isinstance(d, str) for d in dates):
        raise ProviderError("INVALID_DATE", "日期必须是 YYYYMMDD 字符串。")
    for d in set(dates):
        parse_date(d)
        if not start <= d <= end:
            raise ProviderError("DATASET_RANGE", "数据包含请求范围外日期。")
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise ProviderError("DUPLICATE_OBSERVATION", "同一证券与日期不能有重复记录。")
    for col in REQUIRED[2:]:
        try:
            # bools should not silently become numeric observations.
            if frame[col].map(lambda x: isinstance(x, bool)).any():
                raise ValueError()
            frame[col] = pd.to_numeric(frame[col], errors="raise").astype(float)
        except (TypeError, ValueError):
            raise ProviderError("INVALID_NUMERIC_DATA", "行情字段必须是有限数值。") from None
        if not np.isfinite(frame[col]).all():
            raise ProviderError("INVALID_NUMERIC_DATA", "行情字段不能缺失或含 Infinity。")
    for col in optional:
        try:
            if frame[col].map(lambda x: isinstance(x, bool)).any():
                raise ValueError()
            frame[col] = pd.to_numeric(frame[col], errors="raise").astype(float)
        except (ValueError, TypeError):
            raise ProviderError("INVALID_NUMERIC_DATA", "扩展因子字段须为有限数值或 null。") from None
        if np.isinf(frame[col]).any():
            raise ProviderError("INVALID_NUMERIC_DATA", "扩展因子字段不能含 Infinity。")
    if (frame[["open", "high", "low", "close", "raw_close", "adj_factor"]] <= 0).any().any() or (frame[["vol", "amount"]] < 0).any().any():
        raise ProviderError("INVALID_PRICE", "价格及复权因子须为正，成交量与成交额不能为负。")
    tolerance = 1e-9
    if ((frame.high + tolerance < frame[["open", "close", "low"]].max(axis=1)) | (frame.low - tolerance > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ProviderError("INVALID_OHLC", "OHLC 的最高价、最低价关系不一致。")
    missing = sorted(set(symbols)-set(frame.ts_code))
    if missing:
        raise ProviderError("MISSING_SYMBOL_DATA", "证券池中有代码没有数据：" + ", ".join(missing))
    return frame.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def validate_upload(strategy, dataset):
    """Accept already adjusted research OHLC, or explicit raw prices with factor=1."""
    if not isinstance(dataset, dict):
        raise ProviderError("INVALID_DATASET", "上传数据必须包含 rows。")
    try:
        if len(json.dumps(dataset, ensure_ascii=False, allow_nan=False).encode()) > MAX_UPLOAD_BYTES:
            raise ProviderError("DATASET_SIZE", "上传文件过大。")
    except (ValueError, TypeError):
        raise ProviderError("INVALID_DATASET", "上传数据不是有效的有限数值 JSON。") from None
    meta = dataset.get("provenance") or {}
    if not isinstance(meta, dict):
        raise ProviderError("INVALID_PROVENANCE", "provenance 必须为对象。")
    # Never manufacture a claim of independent provider verification.
    rows = dataset.get("rows")
    derived_count = 0
    if isinstance(rows, list) and any(isinstance(r, dict) and r.get("amount") is None for r in rows):
        if meta.get("amountDerivation") != "vol*close*100/1000":
            raise ProviderError("AMOUNT_REQUIRED", "请提供 amount，或显式声明 amountDerivation=vol*close*100/1000 以生成估算值。")
        derived = []
        for row in rows:
            if not isinstance(row, dict):
                derived.append(row)
                continue
            r = dict(row)
            if r.get("amount") is None:
                try:
                    vol, close = float(r["vol"]), float(r["close"])
                    raw_close = float(r.get("raw_close", close))
                    if not math.isclose(raw_close, close, rel_tol=1e-9) or not math.isfinite(vol*close):
                        raise ValueError()
                    r["amount"] = vol*close*100/1000
                except (ValueError, TypeError, KeyError):
                    raise ProviderError("AMOUNT_DERIVATION", "成交额估算要求有效 vol/close，且 close 与 raw_close 相同；不使用复权价估算。") from None
                derived_count += 1
            derived.append(r)
        rows = derived
    has_adjustment = isinstance(rows, list) and any("raw_close" in r or "adj_factor" in r for r in rows if isinstance(r, dict))
    frame = _validate_panel(strategy, rows, allow_raw_defaults=not has_adjustment)
    missing_factor_fields = _factor_fields(strategy)-set(frame.columns)
    if missing_factor_fields:
        raise ProviderError("MISSING_FACTOR_DATA", "上传缺少所选因子字段：" + ", ".join(sorted(missing_factor_fields)))
    symbols, start, end = validate_universe(strategy)
    observed = sorted(frame.trade_date.unique().tolist())
    dates = meta.get("tradingDates", observed)
    if not isinstance(dates, list) or len(dates) > MAX_CALENDAR_DAYS or not dates:
        raise ProviderError("INVALID_CALENDAR", "交易日历无效。")
    for d in dates:
        parse_date(d)
    if dates != sorted(set(dates)) or any(not start <= d <= end for d in dates) or not set(observed).issubset(dates):
        raise ProviderError("INVALID_CALENDAR", "交易日历须唯一、升序、涵盖全部数据并位于日期范围内。")
    warnings = ["用户上传数据；Atlas 未独立核实来源、授权或复权处理。"]
    if "tradingDates" not in meta:
        warnings.append("未提供独立交易日历，使用上传记录的日期并集；全证券同时缺失的交易日无法识别。")
    if not has_adjustment:
        warnings.append("上传未提供复权字段，按未复权价格处理；公司行动可能扭曲收益。")
    if derived_count:
        warnings.append("amount 根据 vol*close*100/1000 估算，假设成交量单位为手；并非真实成交额。")
    declared_synthetic = (meta.get("synthetic") is True or str(meta.get("source", "")).upper().startswith("SYNTHETIC")
                          or str(meta.get("classification", "")).upper().startswith("SYNTHETIC"))
    if declared_synthetic:
        warnings.insert(0, "上传数据声明为合成教学数据；重新导入不会将其转变为真实行情。")
    provenance = {"source": "USER_UPLOAD", "classification": "SYNTHETIC_USER_UPLOAD_UNVERIFIED" if declared_synthetic else "USER_PROVIDED_UNVERIFIED", "synthetic": declared_synthetic,
                  "retrievedAt": utc_now(), "symbols": symbols, "start": start, "end": end,
                  "tradingDates": dates, "rows": len(frame), "dataFingerprint": canonical_hash(_records(frame)),
                  "adjustment": "user_supplied" if has_adjustment else "none", "warnings": warnings}
    provenance["observedColumns"] = [c for c in frame.columns if (c != "amount" or not derived_count) and (has_adjustment or c not in {"raw_close", "adj_factor"})]
    provenance["derivedColumns"] = ({"amount": {"formula": "vol*close*100/1000", "rows": derived_count, "classification": "APPROXIMATION_NOT_OBSERVED"}} if derived_count else {})
    if not has_adjustment:
        provenance["derivedColumns"].update({"raw_close": {"formula": "close", "classification": "USER_ASSUMED_UNADJUSTED"}, "adj_factor": {"formula": "1", "classification": "USER_ASSUMED_UNADJUSTED"}})
    provenance["optionalFieldCoverage"] = {c: float(frame[c].notna().mean()) for c in sorted(OPTIONAL_FIELDS.intersection(frame.columns))}
    for key in ("source", "license", "asOf"):
        if isinstance(meta.get(key), str):
            provenance["declared" + key[0].upper() + key[1:]] = meta[key][:300]
    return frame, provenance


class TushareClient:
    def __init__(self, token, *, proxy_url=None, service_token=None, session=None):
        self.proxy_url = proxy_url
        if proxy_url:
            parsed = urlparse(proxy_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
                raise ProviderError("INVALID_PROXY", "内部行情代理必须是固定 HTTPS URL。")
            if not service_token or not isinstance(service_token, str) or len(service_token) < 24:
                raise ProviderError("PROVIDER_AUTH_MISSING", "未配置内部行情服务凭据。")
        elif not isinstance(token, str) or not token.strip():
            raise ProviderError("TUSHARE_TOKEN_MISSING", "未配置 TUSHARE_TOKEN；不能自动改用演示数据。")
        self.token = token
        self.service_token = service_token
        self.session = session or requests.Session()
        self.calls = 0

    def call(self, api_name, params):
        if api_name not in DATASETS:
            raise ProviderError("DATASET_FORBIDDEN", "不允许此 Tushare 接口。")
        expected = {"exchange", "start_date", "end_date"} if api_name == "trade_cal" else {"ts_code", "start_date", "end_date"}
        if not isinstance(params, dict) or set(params) != expected:
            raise ProviderError("PROVIDER_PARAMS", "行情接口参数不在允许范围内。")
        a, b = parse_date(params["start_date"]), parse_date(params["end_date"])
        if b < a or (b-a).days > MAX_CALENDAR_DAYS:
            raise ProviderError("DATE_RANGE_LIMIT", "行情日期范围超出限制。")
        if api_name == "trade_cal":
            if params["exchange"] != "SSE":
                raise ProviderError("PROVIDER_PARAMS", "本版本仅支持 SSE 官方交易日历。")
        elif not isinstance(params["ts_code"], str) or not SYMBOL_RE.fullmatch(params["ts_code"]):
            raise ProviderError("PROVIDER_PARAMS", "行情证券代码无效。")
        if self.calls >= 64:
            raise ProviderError("PROVIDER_BUDGET", "本次数据请求已达到接口预算。")
        self.calls += 1
        payload = {"api_name": api_name, "params": params, "fields": DATASETS[api_name]}
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.proxy_url:
            headers["Authorization"] = "Bearer " + self.service_token
        else:
            payload["token"] = self.token
        try:
            response = self.session.post(self.proxy_url or OFFICIAL_URL, json=payload, headers=headers,
                                         timeout=(10, 35), allow_redirects=False, stream=True)
            with response:
                if response.status_code == 429:
                    raise ProviderError("TUSHARE_RATE_LIMIT", "行情服务限流，请稍后重试。")
                if response.status_code in (401, 403):
                    raise ProviderError("TUSHARE_PERMISSION", "行情凭据或数据权限不足。")
                if response.status_code != 200:
                    raise ProviderError("TUSHARE_HTTP_ERROR", "行情服务未成功响应。")
                chunks, size = [], 0
                for block in response.iter_content(65536):
                    size += len(block)
                    if size > MAX_RESPONSE_BYTES:
                        raise ProviderError("PROVIDER_RESPONSE_SIZE", "行情响应超过安全大小限制。")
                    chunks.append(block)
                result = json.loads(b"".join(chunks))
        except ProviderError:
            raise
        except (requests.RequestException, ValueError, TypeError):
            raise ProviderError("TUSHARE_NETWORK", "无法读取行情服务；请检查连接与服务状态。") from None
        if not isinstance(result, dict):
            raise ProviderError("TUSHARE_RESPONSE", "行情服务返回结构无效。")
        if result.get("code") != 0:
            # Raw provider msg can echo request details, so never propagate it.
            msg = str(result.get("msg", ""))
            if any(word in msg.lower() for word in ("频次", "频率", "每分钟", "每小时", "限流", "rate limit")):
                raise ProviderError("TUSHARE_RATE_LIMIT", "Tushare 调用频次受限。")
            raise ProviderError("TUSHARE_PERMISSION", "Tushare 拒绝请求，请核对 Token、积分与接口权限。")
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("fields"), list) or not isinstance(data.get("items"), list):
            raise ProviderError("TUSHARE_RESPONSE", "行情数据格式无效。")
        fields, items = data["fields"], data["items"]
        if any(not isinstance(f, str) for f in fields) or len(set(fields)) != len(fields) or not set(DATASETS[api_name].split(",")).issubset(fields):
            raise ProviderError("TUSHARE_RESPONSE", "行情数据缺少必要字段。")
        if len(items) >= 6000:
            raise ProviderError("TUSHARE_TRUNCATED", "行情响应触及行数上限，拒绝使用可能截断的数据。")
        if any(not isinstance(row, list) or len(row) != len(fields) for row in items):
            raise ProviderError("TUSHARE_RESPONSE", "行情字段与记录长度不一致。")
        return pd.DataFrame(items, columns=fields)


def _load(strategy, client, cache_dir, cache_identity):
    symbols, start, end = validate_universe(strategy)
    optional_required = sorted(_factor_fields(strategy).intersection(OPTIONAL_FIELDS))
    key = canonical_hash({"version": 2, "symbols": symbols, "start": start, "end": end, "dailyBasicFields": optional_required})
    cache_path = None
    if cache_dir:
        root = Path(cache_dir) / hashlib.sha256(cache_identity.encode()).hexdigest()[:24]
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        cache_path = root / (key + ".json")
        if cache_path.exists() and time.time()-cache_path.stat().st_mtime < 3600 and cache_path.stat().st_size <= MAX_UPLOAD_BYTES:
            try:
                cached = json.loads(cache_path.read_text())
                frame = _validate_panel(strategy, cached["rows"])
                provenance = cached["provenance"]
                if canonical_hash(_records(frame)) == provenance["dataFingerprint"]:
                    provenance = dict(provenance, cacheHit=True)
                    return frame, provenance
            except (ValueError, KeyError, TypeError, OSError):
                pass
    calendar = client.call("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end})
    if calendar.empty:
        raise ProviderError("NO_TRADING_CALENDAR", "未获取到交易日历。")
    calendar["cal_date"] = calendar.cal_date.astype(str)
    expected_calendar = pd.date_range(pd.to_datetime(start), pd.to_datetime(end)).strftime("%Y%m%d").tolist()
    if (calendar.cal_date.duplicated().any() or sorted(calendar.cal_date.tolist()) != expected_calendar
            or not set(calendar.exchange).issubset({"SSE"})
            or not pd.to_numeric(calendar.is_open, errors="coerce").isin([0, 1]).all()):
        raise ProviderError("INCOMPLETE_CALENDAR", "官方交易日历不完整或格式无效，拒绝推断缺失交易日。")
    dates = sorted(calendar.loc[pd.to_numeric(calendar.is_open, errors="coerce") == 1, "cal_date"].unique().tolist())
    if not dates or any(not start <= d <= end for d in dates):
        raise ProviderError("INVALID_CALENDAR", "交易日历为空或超出请求范围。")
    for d in dates:
        parse_date(d)
    panels = []
    for symbol in symbols:
        params = {"ts_code": symbol, "start_date": start, "end_date": end}
        daily = client.call("daily", params)
        adjustment = client.call("adj_factor", params)
        if daily.empty or adjustment.empty:
            raise ProviderError("MISSING_SYMBOL_DATA", "Tushare 未返回证券所需的行情与复权数据：" + symbol)
        for part in (daily, adjustment):
            if part.duplicated(["ts_code", "trade_date"]).any() or set(part.ts_code) != {symbol}:
                raise ProviderError("PROVIDER_IDENTITY", "行情身份重复或不匹配。")
            if any(not isinstance(x, str) or not start <= x <= end for x in part.trade_date):
                raise ProviderError("PROVIDER_DATE", "行情日期无效或超出范围。")
        daily = daily.merge(adjustment[["ts_code", "trade_date", "adj_factor"]], on=["ts_code", "trade_date"], how="left", validate="one_to_one").sort_values("trade_date")
        try:
            factors = pd.to_numeric(daily.adj_factor, errors="raise").astype(float)
            if not np.isfinite(factors).all() or (factors <= 0).any():
                raise ValueError()
            daily["raw_close"] = pd.to_numeric(daily.close, errors="raise")
            scale = factors / factors.iloc[0]
            for col in ("open", "high", "low", "close"):
                daily[col] = pd.to_numeric(daily[col], errors="raise") * scale
        except (ValueError, TypeError):
            raise ProviderError("ADJUSTMENT_MISSING", "复权因子缺失或无效；拒绝使用不完整复权数据。") from None
        if optional_required:
            basic = client.call("daily_basic", params)
            if basic.empty:
                raise ProviderError("MISSING_FACTOR_DATA", "Tushare 未返回所选因子需要的 daily_basic 数据：" + symbol)
            if basic.duplicated(["ts_code", "trade_date"]).any() or set(basic.ts_code) != {symbol}:
                raise ProviderError("PROVIDER_IDENTITY", "daily_basic 身份重复或不匹配。")
            if any(not isinstance(d, str) or not start <= d <= end for d in basic.trade_date):
                raise ProviderError("PROVIDER_DATE", "daily_basic 日期无效或超出范围。")
            daily = daily.merge(basic[["ts_code", "trade_date"] + optional_required], on=["ts_code", "trade_date"], how="left", validate="one_to_one")
        panels.append(daily)
    frame = _validate_panel(strategy, _records(pd.concat(panels, ignore_index=True)))
    if not set(frame.trade_date).issubset(dates):
        raise ProviderError("CALENDAR_MISMATCH", "行情记录包含官方日历之外的交易日。")
    provenance = {"source": "TUSHARE_PRO", "classification": "PROVIDER_DATA", "synthetic": False,
                  "transport": "private_proxy" if client.proxy_url else "official_https_rest", "retrievedAt": utc_now(),
                  "symbols": symbols, "start": start, "end": end, "tradingDates": dates,
                  "rows": len(frame), "dataFingerprint": canonical_hash(_records(frame)), "cacheHit": False,
                  "providerCalls": client.calls, "datasets": ["trade_cal", "daily", "adj_factor"] + (["daily_basic"] if optional_required else []),
                  "adjustment": "OHLC multiplied by adj_factor / first observed adj_factor per symbol",
                  "calendar": "Tushare SSE official trading calendar; SH/SZ/BJ session alignment assumed",
                  "warnings": ["证券池由当前用户选择，未消除幸存者偏差。", "停牌或缺失行情不填充；复权后的研究单位不是实际券商股数。", "Tushare 成交量单位为手，成交额单位为千元。"]}
    provenance["observedColumns"] = ["raw_close", "vol", "amount", "adj_factor"] + optional_required
    provenance["derivedColumns"] = {c: {"formula": "raw_" + c + " * adj_factor / first_adj_factor", "classification": "CORPORATE_ACTION_ADJUSTED"} for c in ("open", "high", "low", "close")}
    provenance["optionalFieldCoverage"] = {c: float(frame[c].notna().mean()) for c in optional_required}
    if optional_required:
        provenance["warnings"].append("daily_basic 按交易日期合并，缺失值保留；尚未验证历史修订版本与当时可获知时间。")
    if cache_path:
        content = json.dumps({"rows": _records(frame), "provenance": provenance}, ensure_ascii=False, allow_nan=False)
        if len(content.encode()) <= MAX_UPLOAD_BYTES:
            temp = cache_path.with_suffix(".tmp")
            fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(content)
            temp.replace(cache_path)
            for old in sorted(cache_path.parent.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[MAX_CACHE_FILES:]:
                old.unlink(missing_ok=True)
    return frame, provenance


def load_tushare(strategy, token, cache_dir=None):
    return _load(strategy, TushareClient(token), cache_dir, token)


def load_tushare_proxy(strategy, proxy_url, service_token, cache_dir=None):
    return _load(strategy, TushareClient(None, proxy_url=proxy_url, service_token=service_token), cache_dir, service_token)
