"""Read-only source contracts, reproducible universes, and announcement-time joins.

No credential is stored in catalogue metadata. A schema field is not evidence of
populated history. Vendor revision history remains a separately reported limit.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import re
from urllib.parse import urlparse, quote, urlencode
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

EXTERNAL_RE = re.compile(r"^(?:pcd|fd|ext|model)_[a-z0-9_]{1,60}$")
FINANCIAL_FIELDS = {
    "eps": ("基本每股收益", "currency_per_share"),
    "bps": ("每股净资产", "currency_per_share"),
    "ocfps": ("每股经营现金流", "currency_per_share"),
    "roe": ("净资产收益率", "percent"),
    "roa": ("总资产报酬率", "percent"),
    "roic": ("投入资本回报率", "percent"),
    "grossprofit_margin": ("销售毛利率", "percent"),
    "netprofit_margin": ("销售净利率", "percent"),
    "debt_to_assets": ("资产负债率", "percent"),
    "current_ratio": ("流动比率", "ratio"),
    "quick_ratio": ("速动比率", "ratio"),
    "assets_turn": ("总资产周转率", "ratio"),
    "inv_turn": ("存货周转率", "ratio"),
    "ar_turn": ("应收账款周转率", "ratio"),
    "or_yoy": ("营业收入同比增长", "percent"),
    "netprofit_yoy": ("归母净利润同比增长", "percent"),
    "ocf_to_or": ("经营现金流与收入之比", "ratio"),
    "fcff": ("企业自由现金流", "CNY"),
    "fcfe": ("股权自由现金流", "CNY"),
    "ebit": ("息税前利润", "CNY"),
    "ebitda": ("息税折旧摊销前利润", "CNY"),
}
FINANCIAL_ALIASES = {"fd_" + field: field for field in FINANCIAL_FIELDS}

EXTRA_DATASETS = {
    "stock_basic": "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs",
    "index_basic": "ts_code,name,market,publisher,category,base_date,list_date",
    "index_classify": "index_code,industry_name,level,industry_code,is_pub,parent_code",
    "index_member_all": "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new",
    "index_weight": "index_code,con_code,trade_date,weight",
    "fina_indicator": "ts_code,ann_date,end_date," + ",".join(FINANCIAL_FIELDS),
    "income": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,total_revenue,revenue,operate_profit,total_profit,n_income,n_income_attr_p,basic_eps,diluted_eps,ebit,ebitda",
    "balancesheet": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,total_assets,total_liab,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab,money_cap,accounts_receiv,inventories,goodwill,st_borr,lt_borr",
    "cashflow": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act,c_pay_acq_const_fiolta,free_cashflow,net_profit",
}
ENDPOINT_PARAMS = {
    "stock_basic": {"ts_code", "name", "exchange", "market", "is_hs", "list_status"},
    "index_basic": {"ts_code", "name", "market", "publisher", "category"},
    "index_classify": {"index_code", "level", "src"},
    "index_member_all": {"l1_code", "l2_code", "l3_code", "ts_code", "is_new"},
    "index_weight": {"index_code", "trade_date", "start_date", "end_date"},
    "fina_indicator": {"ts_code", "ann_date", "start_date", "end_date", "period"},
    **{api: {"ts_code", "ann_date", "start_date", "end_date", "period", "report_type", "comp_type"}
       for api in ("income", "balancesheet", "cashflow")},
}
RESPONSE_LIMITS = {"stock_basic": 6000, "index_member_all": 2000, "fina_indicator": 100,
                   "income": 1000, "balancesheet": 1000, "cashflow": 1000}


def validate_endpoint(api, params):
    from .provider import ProviderError, parse_date, SYMBOL_RE
    def invalid():
        raise ProviderError("PROVIDER_PARAMS", "数据接口参数超出已登记的只读范围。")
    if not isinstance(params, dict) or set(params) - ENDPOINT_PARAMS[api]:
        invalid()
    for key, value in params.items():
        if not isinstance(value, str) or len(value) > 80:
            invalid()
        if key in {"start_date", "end_date", "trade_date", "ann_date", "period"}:
            parse_date(value)
        if key == "ts_code" and api != "index_basic" and not SYMBOL_RE.fullmatch(value):
            invalid()
        if key in {"index_code", "l1_code", "l2_code", "l3_code"} and not re.fullmatch(r"\d{6}\.(?:SH|SZ|SI|CSI|CNI)", value):
            invalid()
    if "start_date" in params or "end_date" in params:
        if not {"start_date", "end_date"}.issubset(params) or params["start_date"] > params["end_date"]:
            invalid()
    if api == "stock_basic":
        if params.get("list_status", "L") not in {"L", "D", "P", "G", "UN"} or params.get("exchange", "") not in {"", "SSE", "SZSE", "BSE"}:
            invalid()
    if api == "index_classify" and (params.get("level", "L1") not in {"L1", "L2", "L3"} or params.get("src", "SW2021") not in {"SW2014", "SW2021"}):
        invalid()
    if api == "index_member_all" and (not {"l1_code", "l2_code", "l3_code", "ts_code"}.intersection(params) or params.get("is_new", "Y") not in {"Y", "N"}):
        invalid()
    if api == "index_weight" and ("index_code" not in params or not ({"start_date", "end_date"}.issubset(params) or "trade_date" in params)):
        invalid()
    if api in {"fina_indicator", "income", "balancesheet", "cashflow"} and ("ts_code" not in params or not ({"start_date", "end_date"}.issubset(params) or "period" in params or "ann_date" in params)):
        invalid()


def validate_external_fields(frame, metadata):
    """Retain explicitly mapped numeric upload columns with per-observation PIT proof."""
    from .provider import ProviderError, parse_date
    aliases = sorted(c for c in frame.columns if EXTERNAL_RE.fullmatch(c) and not c.endswith("__available_date"))
    if not aliases:
        return {}
    if len(aliases) > 64 or not isinstance(metadata, dict):
        raise ProviderError("EXTERNAL_FIELD_MAPPING", "扩展数据需要明确字段映射；单次最多 64 列。")
    result = {}
    for alias in aliases:
        m = metadata.get(alias)
        companion = alias + "__available_date"
        if (not isinstance(m, dict) or m.get("dataType") not in {"number", "decimal", "integer"} or m.get("availabilityPolicy") != "point_in_time_asof"
                or m.get("availableDateColumn") != companion or companion not in frame
                or not isinstance(m.get("source"), str) or not m["source"].strip()
                or not isinstance(m.get("path"), str) or not m["path"].strip()):
            raise ProviderError("EXTERNAL_FIELD_MAPPING", "扩展字段需有数值类型、来源路径及逐行可用日期。")
        # Numeric strings are not quietly promoted into verified structured facts.
        if frame[alias].map(lambda v: not pd.isna(v) and (isinstance(v, (bool, str)) or not isinstance(v, (int, float, np.number)))).any():
            raise ProviderError("EXTERNAL_FIELD_TYPE", "扩展字段只接受有限数值或 null，不能由文本自动转换。")
        values = pd.to_numeric(frame[alias], errors="raise").astype(float)
        if np.isinf(values).any():
            raise ProviderError("EXTERNAL_FIELD_TYPE", "扩展字段不能含 Infinity。")
        for present, available, date in zip(values.notna(), frame[companion], frame.trade_date):
            if not present:
                continue
            parse_date(available)
            if available > date:
                raise ProviderError("EXTERNAL_FUTURE_DATA", "扩展字段包含当时尚未可知的数据。")
        frame[alias] = values
        result[alias] = {"source": m["source"][:80], "path": m["path"][:240], "dataType": "number",
                         "availabilityPolicy": "point_in_time_asof", "availableDateColumn": companion,
                         "unit": str(m.get("unit", "source_unit"))[:40]}
    return result


def join_financial_asof(panel, reports, aliases, trading_dates):
    """Announcement-day data becomes available only on the following session.

    At each date choose the newest reported period then newest known disclosure
    for that period. Later revisions of an older period cannot replace a newer
    period. Null disclosures remain null; they are not backfilled from history.
    """
    from .provider import ProviderError, parse_date, canonical_hash, _records
    if any(a not in FINANCIAL_ALIASES for a in aliases):
        raise ProviderError("UNMAPPED_FINANCIAL_FIELD", "财务因子尚未映射到已实现的接口。")
    result = panel.copy()
    reports = reports.copy()
    fields = [FINANCIAL_ALIASES[a] for a in aliases]
    if not {"ts_code", "ann_date", "end_date", *fields}.issubset(reports.columns):
        raise ProviderError("FINANCIAL_FIELDS_MISSING", "财务数据缺少公告日期、报告期或所需字段。")
    events = defaultdict(dict); ambiguous = set()
    for row in reports.to_dict("records"):
        symbol, announced, period = row["ts_code"], row["ann_date"], row["end_date"]
        parse_date(announced); parse_date(period)
        if announced < period:
            raise ProviderError("FINANCIAL_DATE", "实际财务指标的公告日期不得早于报告期结束。")
        if symbol not in set(panel.ts_code):
            raise ProviderError("PROVIDER_IDENTITY", "财务接口返回了证券池外的证券。")
        key = (announced, period)
        values = []
        for field in fields:
            value = row[field]
            if pd.isna(value):
                values.append(None)
            elif isinstance(value, bool) or not isinstance(value, (int, float, np.number)) or not math.isfinite(value):
                raise ProviderError("FINANCIAL_NUMERIC", "财务指标必须为有限数值或 null。")
            else:
                values.append(float(value))
        previous = events[symbol].get(key)
        if previous is not None:
            for i, field in enumerate(fields):
                coordinate = (symbol, announced, period, field)
                if previous[i] != values[i] or coordinate in ambiguous:
                    # Conflicting same-day versions have no knowable sequence.
                    # Quarantine only that field/disclosure; never select a value.
                    ambiguous.add(coordinate)
                    values[i] = None
        events[symbol][key] = values
    for alias in aliases:
        result[alias] = np.nan
        result[alias + "__available_date"] = None
    for symbol, indexes in result.groupby("ts_code").groups.items():
        pending = []
        for (announced, period), values in sorted(events[symbol].items()):
            position = bisect_right(trading_dates, announced)
            if position < len(trading_dates):
                pending.append((trading_dates[position], announced, period, values))
        pending.sort(); current = {}; i = 0
        for index in sorted(indexes, key=lambda k: result.at[k, "trade_date"]):
            date = result.at[index, "trade_date"]
            while i < len(pending) and pending[i][0] <= date:
                available, announced, period, values = pending[i]
                current[period] = (available, announced, values); i += 1
            if current:
                available, announced, values = current[max(current)]
                for j, alias in enumerate(aliases):
                    result.at[index, alias] = values[j] if values[j] is not None else np.nan
                    result.at[index, alias + "__available_date"] = available if values[j] is not None else None
    mapping = {a: {"source": "TUSHARE_FUNDAMENTAL", "path": "fina_indicator." + FINANCIAL_ALIASES[a],
                   "dataType": "number", "availabilityPolicy": "point_in_time_asof", "availableDateColumn": a + "__available_date",
                   "unit": FINANCIAL_FIELDS[FINANCIAL_ALIASES[a]][1]} for a in aliases}
    conflicts = [{"ts_code": s, "ann_date": a, "end_date": p, "field": f} for s,a,p,f in sorted(ambiguous)]
    return result, {"externalFields": mapping, "financialSourceRows": len(reports),
                    "financialSnapshotHash": canonical_hash(_records(reports)),
                    "financialAvailability": "first official trading session strictly after ann_date",
                    "financialAmbiguityPolicy": "conflicting values for the same symbol/announcement/period/field are null, never ordered or guessed",
                    "financialAmbiguousDisclosures": len(conflicts), "financialAmbiguitySample": conflicts[:100],
                    "financialAmbiguityHash": canonical_hash(conflicts),
                    "financialRevisionHistory": "PROVIDER_ORIGINAL_AS_PUBLISHED_VERSIONS_UNVERIFIED"}


def load_financial_history(client, symbol, start, end):
    """Fetch bounded two-year slices; split any truncated interval, never use it."""
    from .provider import ProviderError
    frames = []
    def fetch(first, last):
        try:
            data = client.call("fina_indicator", {"ts_code": symbol, "start_date": first, "end_date": last})
        except ProviderError as error:
            if error.code != "TUSHARE_TRUNCATED" or first == last:
                raise
            a, b = datetime.strptime(first, "%Y%m%d"), datetime.strptime(last, "%Y%m%d")
            mid = a + timedelta(days=(b-a).days//2)
            fetch(first, mid.strftime("%Y%m%d"))
            fetch((mid+timedelta(days=1)).strftime("%Y%m%d"), last)
            return
        if not data.empty:
            frames.append(data)
    # Two earlier years allow a pre-start disclosure without inventing values.
    for year in range(max(1990, int(start[:4])-2), int(end[:4])+1, 2):
        fetch(f"{year}0101", min(f"{year+1}1231", end))
    if not frames:
        raise ProviderError("FINANCIAL_DATA_MISSING", "Tushare 未返回证券的历史财务指标：" + symbol)
    return pd.concat(frames, ignore_index=True).drop_duplicates().reset_index(drop=True)


def build_universe_catalog(client, *, include_industries=True, indexes=None):
    """Full memberships from official source responses; never invented or sliced."""
    from .provider import canonical_hash, utc_now, ProviderError, SYMBOL_RE, _records
    fetched = utc_now(); snapshots = []; frames = []
    for exchange in ("SSE", "SZSE", "BSE"):
        for status in ("L", "D", "P"):
            data = client.call("stock_basic", {"exchange": exchange, "list_status": status})
            snapshots.append({"api": "stock_basic", "params": {"exchange": exchange, "list_status": status},
                              "rows": len(data), "hash": canonical_hash(_records(data))})
            if not data.empty:
                frames.append(data)
    if not frames:
        raise ProviderError("UNIVERSE_EMPTY", "未读取到真实股票目录。")
    stocks = pd.concat(frames, ignore_index=True)
    if stocks.ts_code.duplicated().any() or any(not isinstance(s, str) for s in stocks.ts_code):
        raise ProviderError("UNIVERSE_IDENTITY", "股票目录的证券身份重复或格式无效。")
    stockrows = _records(stocks.sort_values("ts_code"))
    def in_scope(s):
        return bool(SYMBOL_RE.fullmatch(s)) and not s.startswith(("900", "200"))
    excluded = [{"ts_code": s, "reason": "B_SHARE_OUTSIDE_A_SHARE_SCOPE" if SYMBOL_RE.fullmatch(s) else "UNSUPPORTED_HISTORICAL_SYMBOL_FORMAT"}
                for s in stocks.ts_code if not in_scope(s)]
    active = stocks[(stocks.list_status == "L") & stocks.ts_code.map(in_scope)]
    pools = []
    def add(name, category, symbols, definition, source="stock_basic", membership="current_snapshot", **extra):
        members = sorted(set(s for s in symbols if isinstance(s, str) and in_scope(s)))
        if len(members) < 3:
            return
        identity = canonical_hash({"source": source, "category": category, "definition": definition})[:20]
        pools.append({"id": "universe_"+identity, "name": name, "category": category,
                      "source": "TUSHARE_PRO", "providerApi": source, "definition": definition,
                      "symbols": members, "memberCount": len(members), "membershipKind": membership,
                      "symbolCount": len(members), "recommendedSymbols": members[:20],
                      "recommendationMethod": "symbol_code_ascending_first_20_explicit_subset",
                      "asOf": fetched, "availability": "ready", "selectionRequired": len(members)>20,
                      "snapshotHash": canonical_hash(members), "fetchedAt": fetched,
                      "historicalMembershipVerified": False, **extra})
    add("A 股 · 当前上市", "market", active.ts_code, {"list_status": "L"})
    for field, label in (("industry", "行业"), ("area", "地域"), ("exchange", "交易所"), ("market", "板块")):
        for value, group in active.dropna(subset=[field]).groupby(field):
            if str(value).strip():
                add(f"{label} · {value}", field, group.ts_code, {field: str(value), "list_status": "L"})
    for (industry, area), group in active.dropna(subset=["industry", "area"]).groupby(["industry", "area"]):
        add(f"{industry} · {area}", "industry_region", group.ts_code, {"industry": str(industry), "area": str(area), "list_status": "L"})
    for (industry, exchange), group in active.dropna(subset=["industry", "exchange"]).groupby(["industry", "exchange"]):
        add(f"{industry} · {exchange}", "industry_exchange", group.ts_code, {"industry": str(industry), "exchange": str(exchange), "list_status": "L"})
    gaps = []
    if include_industries:
        try:
            sectors = client.call("index_classify", {"level": "L1", "src": "SW2021"})
            snapshots.append({"api": "index_classify", "params": {"level": "L1", "src": "SW2021"}, "rows": len(sectors), "hash": canonical_hash(_records(sectors))})
            for _, sector in sectors.iterrows():
                members = client.call("index_member_all", {"l1_code": sector.index_code, "is_new": "Y"})
                snapshots.append({"api": "index_member_all", "params": {"l1_code": sector.index_code, "is_new": "Y"}, "rows": len(members), "hash": canonical_hash(_records(members))})
                for level in (1, 2, 3):
                    for (code, name), group in members.dropna(subset=[f"l{level}_code", f"l{level}_name"]).groupby([f"l{level}_code", f"l{level}_name"]):
                        add(f"申万 L{level} · {name}", "sw_industry", group.ts_code,
                            {"level": level, "index_code": code, "is_new": "Y"}, "index_member_all",
                            membership="dated_current_membership", membershipDatesAvailable=True)
        except ProviderError as error:
            gaps.append({"source": "index_member_all", "status": error.code})
    if indexes:
        today = datetime.now(timezone.utc).date()
        month_end = today.replace(day=1)-timedelta(days=1)
        month_start = month_end.replace(day=1)
        for code, label in indexes:
            try:
                members = client.call("index_weight", {"index_code": code, "start_date": month_start.strftime("%Y%m%d"), "end_date": month_end.strftime("%Y%m%d")})
                snapshots.append({"api": "index_weight", "params": {"index_code": code, "start_date": month_start.strftime("%Y%m%d"), "end_date": month_end.strftime("%Y%m%d")}, "rows": len(members), "hash": canonical_hash(_records(members))})
                if members.empty:
                    gaps.append({"source": "index_weight", "index": code, "status": "NO_DATA"}); continue
                date = members.trade_date.max()
                selected = members[members.trade_date == date]
                add(label, "index", selected.con_code, {"index_code": code, "trade_date": date}, "index_weight",
                    membership="dated_index_snapshot", effectiveDate=date)
            except ProviderError as error:
                gaps.append({"source": "index_weight", "index": code, "status": error.code})
    pools = list({p["id"]: p for p in pools}.values())
    pools.sort(key=lambda p: (p["category"], p["name"]))
    mark_curated_universes(pools)
    return {"schemaVersion": 1, "source": "TUSHARE_PRO", "fetchedAt": fetched, "synthetic": False,
            "counts": {"securities": len(stocks), "currentlyListed": len(active), "excludedFromASharePools": len(excluded), "universes": len(pools)},
            "excludedSecurities": excluded,
            "securities": stockrows, "items": pools, "sourceSnapshots": snapshots, "gaps": gaps,
            "warnings": ["当前行业/地域分类与成员快照不是历史时点证券池，回测须披露幸存者偏差。", "完整成员保存在快照中；计算子集须显式选取并保留所选规则与快照哈希。"]}


def mark_curated_universes(pools):
    """Navigation recommendations describe research coverage, never expected return."""
    focus = {"半导体", "软件服务", "银行", "生物制药", "化学制药", "医疗保健", "电气设备", "通信设备", "汽车整车"}
    for pool in pools:
        index = pool["category"] == "index"
        industry = pool["category"] == "industry" and pool.get("definition", {}).get("industry") in focus
        pool["curated"] = pool["recommended"] = bool(index or industry)
        pool["curationRationale"] = ("常用指数，便于研究宽基或代表性板块的横截面差异；不表示预期收益或完整复制指数。" if index else
            "行业边界清晰，适合比较同类公司的价格、估值与披露特征；不表示投资推荐。" if industry else None)
    return pools


class PCDReadClient:
    """Bounded authenticated reader; there is deliberately no write method."""
    HOSTS = {"yicapital-pcd-v3.eprestonyi.workers.dev", "yicapital-pcd-terminal-test-20261006.eprestonyi.workers.dev"}

    def __init__(self, url, token, *, session=None, max_calls=256):
        from .provider import ProviderError
        if not isinstance(url, str):
            raise ProviderError("PCD_ENDPOINT", "PCD 读取地址必须是固定 HTTPS URL。")
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.HOSTS or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ProviderError("PCD_ENDPOINT", "PCD 读取仅支持固定的已登记 HTTPS 服务。")
        if not isinstance(token, str) or not 24 <= len(token) <= 512 or any(c.isspace() for c in token):
            raise ProviderError("PCD_CREDENTIAL", "未配置 PCD 私有读取凭据。")
        self.url, self.token, self.session = url.rstrip("/"), token, session or requests.Session()
        self.calls, self.max_calls = 0, max(1, min(1024, max_calls))
        self.cache = {}

    def get(self, path):
        from .provider import ProviderError
        if not path.startswith("/v1/") or "#" in path or ".." in path:
            raise ProviderError("PCD_PATH", "PCD 读取路径无效。")
        if path in self.cache:
            return self.cache[path]
        if self.calls >= self.max_calls:
            raise ProviderError("PCD_BUDGET", "PCD 读取达到本次请求预算。")
        self.calls += 1
        try:
            with self.session.get(self.url+path, headers={"Authorization": "Bearer "+self.token},
                                  timeout=(10, 30), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ProviderError("PCD_READ_FAILED", "PCD 读取未成功，请核对权限、记录与服务状态。")
                blocks, size = [], 0
                for block in response.iter_content(65536):
                    size += len(block)
                    if size > 4*1024*1024:
                        raise ProviderError("PCD_RESPONSE_SIZE", "PCD 单次响应超过读取限制。")
                    blocks.append(block)
                data = json.loads(b"".join(blocks))
        except ProviderError:
            raise
        except (requests.RequestException, ValueError, TypeError):
            raise ProviderError("PCD_UNAVAILABLE", "PCD 读取服务不可用。") from None
        if not isinstance(data, dict):
            raise ProviderError("PCD_RESPONSE", "PCD 响应结构无效。")
        self.cache[path] = data
        return data

    def selections(self, record_id, field_id):
        from .provider import ProviderError
        rows, cursor, seen = [], "", set()
        for _ in range(20):
            data = self.get("/v1/records/"+quote(record_id, safe="")+"/selections?"+urlencode({"field_id": field_id, "limit": 100, "after": cursor}))
            page = data.get("selections")
            if not isinstance(page, list):
                raise ProviderError("PCD_RESPONSE", "PCD 历史选值格式无效。")
            rows.extend(page)
            cursor = data.get("next_after")
            if cursor is None:
                return rows
            if str(cursor) in seen:
                raise ProviderError("PCD_CURSOR", "PCD 历史分页未前进。")
            seen.add(str(cursor))
        raise ProviderError("PCD_BUDGET", "PCD 历史页数超过本次限制。")


def join_pcd_asof(panel, trading_dates, bindings, client):
    """Explicit record/security mappings, immutable observations, and selected history.

    bindings[alias] = {fieldId, unitCode, records:[{ts_code,entityId,recordId}]}.
    Repeated rows require an explicit upstream aggregation; this reader cannot
    decide which customer, subsidiary, accounting scope or currency is intended.
    """
    from .provider import ProviderError, canonical_hash
    if not isinstance(bindings, dict) or not 1 <= len(bindings) <= 32:
        raise ProviderError("PCD_MAPPING", "PCD 对接需要 1–32 个明确字段映射。")
    def stamp(value):
        try:
            t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if t.tzinfo is None:
                raise ValueError()
            return t.astimezone(timezone.utc)
        except (ValueError, TypeError):
            raise ProviderError("PCD_TIME", "PCD 事实缺少带时区的获知时间。") from None
    def get(kind, identity):
        return client.get("/v1/"+kind+"/"+quote(identity, safe=""))
    seen_observations = {}
    def observation_time(identity, visiting=None):
        if identity in seen_observations:
            return seen_observations[identity]
        visiting = set() if visiting is None else set(visiting)
        if identity in visiting or len(visiting) > 20:
            raise ProviderError("PCD_LINEAGE", "PCD 来源链循环或超出深度。")
        visiting.add(identity)
        data = get("observations", identity); o = data.get("observation")
        if not isinstance(o, dict) or o.get("observation_id") != identity:
            raise ProviderError("PCD_IDENTITY", "PCD 观察值身份不匹配。")
        times = [stamp(o.get("recorded_at"))]
        evidence = data.get("evidence")
        if o.get("assertion_kind") == "REPORTED":
            if not isinstance(evidence, dict) or not evidence.get("document_id"):
                raise ProviderError("PCD_EVIDENCE", "PCD 直接披露缺少原文证据。")
            source = get("sources", evidence["document_id"])
            times.append(stamp(source.get("retrieved_at")))
            if source.get("publication_precision") == "TIMESTAMP":
                times.append(stamp(source.get("published_at")))
            elif source.get("publication_precision") == "DATE_ONLY":
                try:
                    times.append(datetime.strptime(source["published_date"], "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=ZoneInfo("Asia/Hong_Kong")).astimezone(timezone.utc))
                except (KeyError, ValueError):
                    raise ProviderError("PCD_TIME", "PCD 公告日期无效。") from None
            elif source.get("publication_precision") != "UNKNOWN":
                raise ProviderError("PCD_TIME", "PCD 公告时间精度无效。")
        else:
            inputs = data.get("inputs")
            if not isinstance(inputs, list) or not inputs:
                raise ProviderError("PCD_LINEAGE", "PCD 派生事实缺少冻结的输入来源链。")
            times.extend(observation_time(i["input_observation_id"], visiting)[1] for i in inputs)
        result = o, max(times)
        seen_observations[identity] = result
        return result
    result = panel.copy(); mapping = {}; all_events = []; symbol_entities = {}
    for alias, binding in bindings.items():
        if not EXTERNAL_RE.fullmatch(alias) or not alias.startswith("pcd_") or not isinstance(binding, dict):
            raise ProviderError("PCD_MAPPING", "PCD 字段别名无效。")
        field_id, unit = binding.get("fieldId"), binding.get("unitCode")
        records = binding.get("records")
        if not isinstance(field_id, str) or not isinstance(unit, str) or not isinstance(records, list) or not 1 <= len(records) <= 200:
            raise ProviderError("PCD_MAPPING", "PCD 映射必须指定字段、单位及有限记录集合。")
        events = defaultdict(list); coordinates = set(); identities = set()
        for item in records:
            if not isinstance(item, dict):
                raise ProviderError("PCD_MAPPING", "PCD 记录映射必须是对象。")
            symbol, entity, record_id = item.get("ts_code"), item.get("entityId"), item.get("recordId")
            if symbol not in set(panel.ts_code) or not isinstance(entity, str) or not isinstance(record_id, str):
                raise ProviderError("PCD_MAPPING", "PCD 证券与主体映射无效。")
            if (symbol, entity, record_id) in identities:
                raise ProviderError("PCD_MAPPING_DUPLICATE", "PCD 同字段的证券、主体与记录映射重复。")
            identities.add((symbol, entity, record_id))
            if symbol in symbol_entities and symbol_entities[symbol] != entity:
                raise ProviderError("PCD_ENTITY_CONFLICT", "同证券映射到多个主体；需先完成有日期的身份归并。")
            symbol_entities[symbol] = entity
            data = get("records", record_id); record = data.get("record", {})
            if record.get("entity_id") != entity or record.get("record_id") != record_id:
                raise ProviderError("PCD_IDENTITY", "PCD 记录与显式主体映射不一致。")
            cells = [c for c in data.get("cells", []) if c.get("field_id") == field_id]
            if len(cells) != 1 or cells[0].get("dtype") not in {"decimal", "integer"}:
                raise ProviderError("PCD_NUMERIC_FIELD", "PCD 字段未登记为 decimal/integer。")
            context = get("registry/context", record["context_id"])
            context = context.get("item", context)
            period = get("registry/period", context["period_id"])
            period = period.get("item", period)
            period_end = period.get("end_date")
            if not period_end or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period_end):
                raise ProviderError("PCD_PERIOD_MAPPING", "PCD 记录必须有明确报告期末；有效期与重复对象需先显式转换。")
            period_rank = period_end.replace("-", "")
            coordinate = symbol, period_rank
            if coordinate in coordinates:
                raise ProviderError("PCD_SCOPE_AMBIGUOUS", "同证券、报告期有多个对象或口径；请先选择或显式聚合。")
            coordinates.add(coordinate)
            for selection in client.selections(record_id, field_id):
                o, known = observation_time(selection["observation_id"])
                if o.get("record_id") != record_id or o.get("field_id") != field_id or selection.get("record_id") != record_id or selection.get("field_id") != field_id:
                    raise ProviderError("PCD_IDENTITY", "PCD 选值历史与观察值坐标不匹配。")
                known = max(known, stamp(selection.get("recorded_at")), stamp(record.get("created_at")))
                known_date = known.astimezone(ZoneInfo("Asia/Hong_Kong")).strftime("%Y%m%d")
                pos = bisect_right(trading_dates, known_date)
                if pos >= len(trading_dates):
                    continue
                value = None
                if o.get("value_state") == "PRESENT":
                    if o.get("unit_code") != unit:
                        raise ProviderError("PCD_UNIT_MISMATCH", "PCD 实际单位与所选口径不一致。")
                    raw = o.get("value_decimal") if cells[0]["dtype"] == "decimal" else o.get("value_integer")
                    try:
                        dec = Decimal(str(raw)); value = float(dec)
                        if not dec.is_finite() or not math.isfinite(value):raise ValueError()
                    except (InvalidOperation, ValueError, TypeError):
                        raise ProviderError("PCD_NUMERIC_FIELD", "PCD 数值格式无效。") from None
                events[symbol].append((trading_dates[pos], period_rank, int(selection["revision"]), value, o["observation_id"]))
        result[alias] = np.nan; result[alias+"__available_date"] = None
        for symbol, indexes in result.groupby("ts_code").groups.items():
            pending = sorted(events[symbol]); current = {}; i = 0
            for index in sorted(indexes, key=lambda k: result.at[k, "trade_date"]):
                date = result.at[index, "trade_date"]
                while i < len(pending) and pending[i][0] <= date:
                    available, period_rank, revision, value, observation_id = pending[i]
                    current[period_rank] = (available, value); i += 1
                if current:
                    available, value = current[max(current)]
                    if value is not None:
                        result.at[index, alias] = value; result.at[index, alias+"__available_date"] = available
            all_events.extend((alias, symbol, *e) for e in pending)
        mapping[alias] = {"source": "PCD", "path": field_id, "unit": unit, "dataType": "number",
                          "availabilityPolicy": "point_in_time_asof", "availableDateColumn": alias+"__available_date"}
    return result, {"externalFields": mapping, "pcdSnapshotHash": canonical_hash(all_events),
                    "pcdSelectionEvents": len(all_events), "pcdAvailability": "next session after max(publication, retrieval, observation, selection, record creation)",
                    "pcdKnownTimeFallback": "UNKNOWN publication never backdates system-known observations"}
