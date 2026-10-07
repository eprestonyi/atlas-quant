"""Strict public metadata grammar shared with the edge's version-2 contract."""
from __future__ import annotations
import re
from datetime import datetime
from .schema import fail


def text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value)>limit:
        fail("INVALID_STATISTICAL_QUANT", f"{name}需要1–{limit}字符")
    return value.strip()


def keys(obj, allowed, name):
    if not isinstance(obj, dict) or set(obj)-set(allowed) or any(v is None for v in obj.values()):
        fail("INVALID_STATISTICAL_QUANT", f"{name}结构或字段无效")


def normalize_universe(u):
    keys(u, {"symbols", "start", "end", "selection", "resolutionHash", "snapshotHash", "subsetPolicy", "catalogSnapshot", "presetId", "snapshotDate"}, "股票池")
    result = {k: u[k] for k in ("symbols", "start", "end")}
    def hash_(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            fail("INVALID_STATISTICAL_QUANT", "股票池版本哈希无效")
        return value
    if "selection" in u:
        sel = u["selection"]
        keys(sel, {"version", "includeGroups", "excludeGroups", "includeSymbols", "excludeSymbols"}, "股票池规则")
        if isinstance(sel.get("version"), bool) or sel.get("version") != 1:
            fail("INVALID_STATISTICAL_QUANT", "股票池规则版本无效")
        include, exclude = sel.get("includeGroups", []), sel.get("excludeGroups", [])
        if not isinstance(include, list) or not isinstance(exclude, list) or len(include)+len(exclude)>20:
            fail("INVALID_STATISTICAL_QUANT", "股票池规则最多20组")
        seen = set()
        def group(g):
            keys(g, {"id", "name", "filters"}, "股票池分组")
            ident = text(g.get("id"), "分组ID", 80)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", ident) or ident in seen:
                fail("INVALID_STATISTICAL_QUANT", "分组ID无效或重复")
            seen.add(ident)
            filters = g.get("filters")
            if not isinstance(filters, list) or not 1 <= len(filters) <= 20:
                fail("INVALID_STATISTICAL_QUANT", "分组需要1–20个筛选条件")
            normalized = []
            for f in filters:
                keys(f, {"field", "value"}, "筛选条件")
                if not isinstance(f.get("field"), str) or f["field"] not in {"universe", "area", "industry", "market", "exchange", "list_status", "is_hs"}:
                    fail("INVALID_STATISTICAL_QUANT", "筛选字段无效")
                values = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
                if not 1 <= len(values) <= 64:
                    fail("INVALID_STATISTICAL_QUANT", "筛选值数量无效")
                clean = sorted(set(text(v, "筛选值", 160) for v in values))
                normalized.append({"field": f["field"], "value": clean[0] if len(clean)==1 else clean})
            return {"id": ident, "name": text(g.get("name", ident), "分组名称", 100), "filters": normalized}
        normalized = {"version": 1, "includeGroups": [group(g) for g in include], "excludeGroups": [group(g) for g in exclude]}
        for key in ("includeSymbols", "excludeSymbols"):
            vals = sel.get(key, [])
            if not isinstance(vals, list) or len(vals)>6000 or any(not isinstance(v, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", v) or v.startswith(("900", "200")) for v in vals):
                fail("INVALID_STATISTICAL_QUANT", "股票池明确加入/排除证券无效")
            normalized[key] = sorted(set(vals))
        if not isinstance(u.get("subsetPolicy"), str) or u["subsetPolicy"] not in {"all", "explicit"}:
            fail("INVALID_STATISTICAL_QUANT", "必须声明完整池或明确子集")
        result.update(selection=normalized, resolutionHash=hash_(u.get("resolutionHash")), snapshotHash=hash_(u.get("snapshotHash")), subsetPolicy=u["subsetPolicy"])
        if "catalogSnapshot" in u:
            snap = u["catalogSnapshot"]
            if (not isinstance(snap, dict) or set(snap)-{"hash", "asOf", "historicalMembershipVerified"}
                    or snap.get("historicalMembershipVerified", False) is not False
                    or (snap.get("asOf") is not None and (not isinstance(snap["asOf"], str) or len(snap["asOf"])>80))):
                fail("INVALID_STATISTICAL_QUANT", "股票池目录快照结构无效")
            result["catalogSnapshot"] = {"hash": hash_(snap.get("hash")), "asOf": snap.get("asOf"), "historicalMembershipVerified": False}
    elif any(k in u for k in ("catalogSnapshot", "resolutionHash", "snapshotHash", "subsetPolicy")):
        fail("INVALID_STATISTICAL_QUANT", "版本元数据需要明确股票池规则")
    if "presetId" in u:
        result["presetId"] = text(u["presetId"], "票池ID", 120)
    if "snapshotDate" in u:
        value = u["snapshotDate"]
        try:
            if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
                raise ValueError()
            datetime.strptime(value, "%Y%m%d")
        except ValueError:
            fail("INVALID_STATISTICAL_QUANT", "股票池快照日期无效")
        result["snapshotDate"] = value
    return result


def normalize_bindings(bindings, symbols):
    keys(bindings, {"pcd"}, "数据绑定")
    pcd = bindings.get("pcd", {})
    if not isinstance(pcd, dict) or len(pcd)>32:
        fail("INVALID_STATISTICAL_QUANT", "PCD最多绑定32个字段")
    result = {}
    for alias, value in pcd.items():
        if not re.fullmatch(r"pcd_[a-z0-9_]{1,60}", alias):
            fail("INVALID_STATISTICAL_QUANT", "PCD别名无效")
        keys(value, {"fieldId", "unitCode", "records"}, "PCD绑定")
        records = value.get("records")
        if not isinstance(records, list) or not 1 <= len(records) <= 50:
            fail("INVALID_STATISTICAL_QUANT", "PCD绑定需要1–50条精确记录")
        clean, seen = [], set()
        for record in records:
            keys(record, {"ts_code", "entityId", "recordId"}, "PCD记录")
            if record.get("ts_code") not in symbols:
                fail("INVALID_STATISTICAL_QUANT", "PCD映射不属于当前股票池")
            rid = text(record.get("recordId"), "记录ID", 200)
            if rid in seen:
                fail("INVALID_STATISTICAL_QUANT", "PCD精确记录重复")
            seen.add(rid)
            clean.append({"ts_code": record["ts_code"], "entityId": text(record.get("entityId"), "主体ID", 200), "recordId": rid})
        result[alias] = {"fieldId": text(value.get("fieldId"), "字段ID", 220), "unitCode": text(value.get("unitCode"), "单位", 80), "records": clean}
    return {"pcd": result}
