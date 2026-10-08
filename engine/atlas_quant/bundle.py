"""Bounded JSON collections with the unchanged forecast-v1 canonical identity.

The numerical engine still owns its in-memory arrays. This transport never parses
an entire serialized report: only a bounded manifest skeleton and individual
bounded chunks are decoded. No compression, executable serialization or paths in
untrusted manifests are supported.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from itertools import zip_longest
from pathlib import Path

MANIFEST_LIMIT = 512 * 1024
CHUNK_TARGET = 4 * 1024 * 1024
CHUNK_LIMIT = 8 * 1024 * 1024
BUNDLE_LIMIT = 256 * 1024 * 1024
CHUNK_COUNT_LIMIT = 256
CHUNK_ROW_LIMIT = 10000
TOTAL_ROW_LIMIT = 1000000
HASH = re.compile(r"[a-f0-9]{64}")
COLLECTIONS = {
    "forecasts": ("forecast", "/rows"),
    "targets": ("forecast", "/targetDefinitions"),
    "modelFits": ("forecast", "/modelFits"),
    "factorFeatures": ("forecast", "/factorResearch/diagnostics/features"),
    "factorJointDistributions": ("forecast", "/factorResearch/diagnostics/dependence/jointDistributions"),
    "hedgeFits": ("forecast", "/hedgeFits"),
    "perTarget": ("forecast", "/diagnostics/perTarget"),
    "outerFolds": ("forecast", "/diagnostics/outerFolds"),
    "finalTrials": ("forecast", "/diagnostics/finalTrials"),
    "baselineRows": ("forecast", "/diagnostics/factorIncrement/baselineRows"),
    "baselineModelFits": ("forecast", "/diagnostics/factorIncrement/baselineModelFits"),
    "dailyLosses": ("forecast", "/diagnostics/factorIncrement/dailyLosses"),
    "baselinePerTarget": ("forecast", "/diagnostics/factorIncrement/baselineValidation/perTarget"),
    "baselineOuterFolds": ("forecast", "/diagnostics/factorIncrement/baselineValidation/outerFolds"),
    "baselineFinalTrials": ("forecast", "/diagnostics/factorIncrement/baselineValidation/finalTrials"),
    "equity": ("report", "/equity"),
    "trades": ("report", "/trades"),
    "riskLedger": ("report", "/execution/ledger"),
    "decisions": ("report", "/execution/decisions"),
    "snapshotRows": ("snapshot", "/rows"),
    "plannedOrigins": ("coverage", "/origins"),
}


def fail(code, message):
    from .runner import RunnerError
    raise RunnerError(code, message)


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def encode(value):
    try:
        return json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, OverflowError):
        fail("BUNDLE_FORMAT", "分片内容必须为有限数值 JSON。")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite literal")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _integer(value, low=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _path(root, pointer, value=None, assign=False):
    keys = pointer.strip("/").split("/")
    obj = root
    try:
        for key in keys[:-1]:
            obj = obj[key]
        if assign:
            obj[keys[-1]] = value
        return obj[keys[-1]]
    except (KeyError, TypeError):
        fail("BUNDLE_LAYOUT", "分片声明路径与文档结构不一致。")


def _markers(value, pointer=""):
    if isinstance(value, dict):
        if set(value) in ({"__bundle_collection__"}, {"__bundle_document__"}):
            yield pointer, next(iter(value)), next(iter(value.values()))
        else:
            for key, item in value.items():
                yield from _markers(item, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _markers(item, pointer + "/" + str(index))


def _without_object_braces(pieces):
    first, tail = True, b""
    for piece in pieces:
        if not piece:
            continue
        if first:
            if piece[:1] != b"{":
                fail("BUNDLE_LAYOUT", "预测文档须为对象。")
            piece, first = piece[1:], False
        combined = tail + piece
        if combined:
            yield combined[:-1]
            tail = combined[-1:]
    if first or tail != b"}":
        fail("BUNDLE_LAYOUT", "预测文档对象未闭合。")


def iter_document_bytes(manifest, document, read_chunk):
    collections = {c["id"]: c for c in manifest["collections"]}
    for part in manifest["documents"][document]["parts"]:
        if "literal" in part:
            yield part["literal"].encode()
        elif "collection" in part:
            yield b"["
            emitted = False
            for chunk in collections[part["collection"]]["chunks"]:
                raw = read_chunk(part["collection"], chunk["ordinal"])
                if len(raw) != chunk["byteLength"] or sha(raw) != chunk["sha256"]:
                    fail("BUNDLE_INTEGRITY", "分片字节或指纹不匹配。")
                if raw[:1] != b"[" or raw[-1:] != b"]":
                    fail("BUNDLE_LAYOUT", "分片必须是无外层空白的 JSON 数组。")
                if chunk["count"]:
                    if emitted:
                        yield b","
                    yield raw[1:-1]
                    emitted = True
            yield b"]"
        else:
            yield b'{"artifactId":' + encode(part["wrapArtifactId"]) + b","
            yield from _without_object_braces(iter_document_bytes(manifest, "forecast", read_chunk))
            yield b"}"


def build_bundle(report, snapshot, coverage, write_chunk, read_chunk, *, chunk_target=CHUNK_TARGET):
    """Write complete arrays incrementally; return a small canonical manifest."""
    if not isinstance(report, dict) or not isinstance(report.get("forecasts"), dict):
        fail("BUNDLE_FORMAT", "需要完整预测研究报告。")
    if not _integer(chunk_target, 2) or chunk_target > CHUNK_LIMIT:
        fail("BUNDLE_BUDGET", "分片目标大小无效。")
    artifact = report["forecasts"]
    manifest = {"format": "atlas.quant.bundle", "version": 1,
        "kind": "execution" if report["research"]["executionOnly"] else "forecast",
        "forecastArtifactId": artifact["artifactId"], "predictionConfigHash": artifact["predictionConfigHash"],
        "dataFingerprint": artifact["dataFingerprint"], "documents": {}, "collections": [],
        "totals": {"chunkCount": 0, "chunkBytes": 0}}
    documents = {"forecast": {k: v for k, v in artifact.items() if k != "artifactId"},
                 "report": report, "coverage": coverage}
    if snapshot is not None:
        documents["snapshot"] = snapshot
    elif manifest["kind"] == "forecast":
        fail("BUNDLE_FORMAT", "新预测分片缺少冻结输入。")
    lookup = {pair: name for name, pair in COLLECTIONS.items()}

    def collection(name, document, pointer, values):
        if not isinstance(values, list):
            fail("BUNDLE_FORMAT", "分片集合必须为数组。")
        info = {"id": name, "document": document, "path": pointer,
                "rowCount": len(values), "chunks": []}
        pending, size, start = [], 2, 0
        def flush():
            nonlocal pending, size, start
            if not pending:
                return
            raw = b"[" + b",".join(pending) + b"]"
            totals = manifest["totals"]
            totals["chunkCount"] += 1
            totals["chunkBytes"] += len(raw)
            if totals["chunkCount"] > CHUNK_COUNT_LIMIT or totals["chunkBytes"] > BUNDLE_LIMIT:
                fail("BUNDLE_SIZE", "完整分片超过总资源预算；没有截断输出。")
            descriptor = {"ordinal": len(info["chunks"]), "start": start,
                          "count": len(pending), "sha256": sha(raw), "byteLength": len(raw)}
            write_chunk(name, descriptor["ordinal"], raw)
            info["chunks"].append(descriptor)
            start += len(pending)
            pending, size = [], 2
        for value in values:
            if not isinstance(value, dict):
                fail("BUNDLE_FORMAT", "研究集合的每项必须为对象。")
            raw = encode(value)
            if len(raw) + 2 > CHUNK_LIMIT:
                fail("BUNDLE_ITEM_SIZE", "单条研究记录超过分片硬上限；没有切断记录。")
            if pending and (size + 1 + len(raw) > chunk_target or len(pending) >= CHUNK_ROW_LIMIT):
                flush()
            size += len(raw) + bool(pending)
            pending.append(raw)
        flush()
        manifest["collections"].append(info)

    for document, root in documents.items():
        parts = []
        def literal(raw):
            value = raw.decode() if isinstance(raw, bytes) else raw
            if parts and "literal" in parts[-1]:
                parts[-1]["literal"] += value
            else:
                parts.append({"literal": value})
        def walk(value, pointer=""):
            if document == "report" and pointer == "/forecasts":
                parts.append({"document": "forecast", "wrapArtifactId": artifact["artifactId"]})
            elif (document, pointer) in lookup:
                name = lookup[(document, pointer)]
                collection(name, document, pointer, value)
                parts.append({"collection": name})
            elif isinstance(value, dict):
                literal("{")
                for i, key in enumerate(sorted(value)):
                    if i:
                        literal(",")
                    literal(encode(key) + b":")
                    walk(value[key], pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
                literal("}")
            else:
                literal(encode(value))
        walk(root)
        manifest["documents"][document] = {"parts": parts}
    for name, definition in manifest["documents"].items():
        h, length = hashlib.sha256(), 0
        for raw in iter_document_bytes(manifest, name, read_chunk):
            h.update(raw)
            length += len(raw)
        definition.update(sha256=h.hexdigest(), byteLength=length)
    raw = encode(manifest)
    validate_manifest(raw)
    if manifest["documents"]["forecast"]["sha256"] != artifact["artifactId"]:
        fail("BUNDLE_INTEGRITY", "分片重建未保留原预测身份。")
    return raw


def validate_manifest(raw, expected_id=None):
    if not isinstance(raw, bytes) or len(raw) > MANIFEST_LIMIT:
        fail("BUNDLE_MANIFEST_SIZE", "传输清单超过大小限制。")
    if expected_id is not None and sha(raw) != expected_id:
        fail("BUNDLE_INTEGRITY", "传输清单身份不匹配。")
    try:
        manifest = decode(raw)
        if encode(manifest) != raw:
            raise ValueError()
        if set(manifest) != {"format", "version", "kind", "forecastArtifactId", "predictionConfigHash", "dataFingerprint", "documents", "collections", "totals"}:
            raise ValueError()
        if manifest["format"] != "atlas.quant.bundle" or type(manifest["version"]) is not int or manifest["version"] != 1 or manifest["kind"] not in ("forecast", "execution"):
            raise ValueError()
        if not all(isinstance(manifest[k], str) and HASH.fullmatch(manifest[k]) for k in ("forecastArtifactId", "predictionConfigHash", "dataFingerprint")):
            raise ValueError()
        names = {"forecast", "report", "coverage"} | ({"snapshot"} if manifest["kind"] == "forecast" else set())
        if set(manifest["documents"]) != names or not isinstance(manifest["collections"], list):
            raise ValueError()
        collections, chunk_count, chunk_bytes, total_rows = {}, 0, 0, 0
        for info in manifest["collections"]:
            if set(info) != {"id", "document", "path", "rowCount", "chunks"} or info["id"] in collections:
                raise ValueError()
            if COLLECTIONS.get(info["id"]) != (info["document"], info["path"]) or not _integer(info["rowCount"]) or not isinstance(info["chunks"], list):
                raise ValueError()
            collections[info["id"]] = info
            total_rows += info["rowCount"]
            count = 0
            for ordinal, descriptor in enumerate(info["chunks"]):
                if set(descriptor) != {"ordinal", "start", "count", "sha256", "byteLength"}:
                    raise ValueError()
                if (not _integer(descriptor["ordinal"]) or not _integer(descriptor["start"])
                        or descriptor["ordinal"] != ordinal or descriptor["start"] != count
                        or not _integer(descriptor["count"], 1) or descriptor["count"] > CHUNK_ROW_LIMIT):
                    raise ValueError()
                if not _integer(descriptor["byteLength"], 2) or descriptor["byteLength"] > CHUNK_LIMIT or not HASH.fullmatch(descriptor["sha256"]):
                    raise ValueError()
                count += descriptor["count"]
                chunk_count += 1
                chunk_bytes += descriptor["byteLength"]
            if count != info["rowCount"]:
                raise ValueError()
        if (manifest["totals"] != {"chunkCount": chunk_count, "chunkBytes": chunk_bytes}
                or not all(_integer(value) for value in manifest["totals"].values())):
            raise ValueError()
        if chunk_count > CHUNK_COUNT_LIMIT or chunk_bytes + len(raw) > BUNDLE_LIMIT or total_rows > TOTAL_ROW_LIMIT:
            fail("BUNDLE_SIZE", "传输集合超过总资源预算。")
        referenced = []
        for name, document in manifest["documents"].items():
            if set(document) != {"parts", "sha256", "byteLength"} or not isinstance(document["parts"], list) or not HASH.fullmatch(document["sha256"]) or not _integer(document["byteLength"], 2):
                raise ValueError()
            document_refs = 0
            for part in document["parts"]:
                if set(part) == {"literal"} and isinstance(part["literal"], str):
                    pass
                elif set(part) == {"collection"} and part["collection"] in collections and collections[part["collection"]]["document"] == name:
                    referenced.append(part["collection"])
                elif name == "report" and part == {"document": "forecast", "wrapArtifactId": manifest["forecastArtifactId"]}:
                    document_refs += 1
                else:
                    raise ValueError()
            skeleton = document_skeleton(manifest, name)
            for info in collections.values():
                if info["document"] == name and _path(skeleton, info["path"]) != {"__bundle_collection__": info["id"]}:
                    raise ValueError()
            if name == "report" and skeleton.get("forecasts") != {"__bundle_document__": "forecast"}:
                raise ValueError()
            if document_refs != (1 if name == "report" else 0):
                raise ValueError()
            expected_markers = [(info["path"], "__bundle_collection__", info["id"])
                                for info in collections.values() if info["document"] == name]
            if name == "report":
                expected_markers.append(("/forecasts", "__bundle_document__", "forecast"))
            if sorted(_markers(skeleton)) != sorted(expected_markers):
                raise ValueError()
        if sorted(referenced) != sorted(collections):
            raise ValueError()
        required = {"forecasts", "targets", "modelFits", "hedgeFits", "equity", "trades", "riskLedger", "decisions", "plannedOrigins"}
        if manifest["kind"] == "forecast":
            required.add("snapshotRows")
        if not required <= set(collections) or manifest["documents"]["forecast"]["sha256"] != manifest["forecastArtifactId"]:
            raise ValueError()
        return manifest
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        if getattr(exc, "code", None):
            raise
        fail("BUNDLE_FORMAT", "分片清单格式或布局无效。")


def document_skeleton(manifest, name):
    parts = []
    for part in manifest["documents"][name]["parts"]:
        if "literal" in part:
            parts.append(part["literal"])
        elif "collection" in part:
            parts.append(encode({"__bundle_collection__": part["collection"]}).decode())
        else:
            parts.append('{"__bundle_document__":"forecast"}')
    try:
        return decode("".join(parts))
    except (ValueError, TypeError):
        fail("BUNDLE_LAYOUT", "文档结构不是有效 JSON。")


def canonical_skeleton_parts(manifest, name):
    """Canonicalize only the bounded metadata, preserving collection references."""
    parts = []
    def literal(raw):
        parts.append({"literal": raw.decode() if isinstance(raw, bytes) else raw})
    def walk(value):
        if isinstance(value, dict) and set(value) == {"__bundle_collection__"}:
            parts.append({"collection": value["__bundle_collection__"]})
        elif isinstance(value, dict) and set(value) == {"__bundle_document__"}:
            parts.append({"document": "forecast", "wrapArtifactId": manifest["forecastArtifactId"]})
        elif isinstance(value, dict):
            literal("{")
            for index, key in enumerate(sorted(value)):
                if index:
                    literal(",")
                literal(encode(key)+b":")
                walk(value[key])
            literal("}")
        elif isinstance(value, list):
            literal("[")
            for index, item in enumerate(value):
                if index:
                    literal(",")
                walk(item)
            literal("]")
        else:
            literal(encode(value))
    walk(document_skeleton(manifest, name))
    return parts


class BundleReader:
    def __init__(self, manifest_raw, read_chunk, expected_id=None):
        self.manifest = validate_manifest(manifest_raw, expected_id)
        self.bundle_id = sha(manifest_raw)
        self.manifest_raw = manifest_raw
        self.read_chunk = read_chunk
        self.collections = {c["id"]: c for c in self.manifest["collections"]}

    def rows(self, collection):
        for descriptor in self.collections[collection]["chunks"]:
            raw = self.read_chunk(collection, descriptor["ordinal"])
            if len(raw) != descriptor["byteLength"] or len(raw) > CHUNK_LIMIT or sha(raw) != descriptor["sha256"]:
                fail("BUNDLE_INTEGRITY", "分片指纹或长度不匹配。")
            try:
                values = decode(raw)
                if not isinstance(values, list) or len(values) != descriptor["count"] or any(not isinstance(v, dict) for v in values) or encode(values) != raw:
                    raise ValueError()
            except (ValueError, TypeError):
                fail("BUNDLE_INTEGRITY", "分片记录数量或规范编码不匹配。")
            yield from values

    def verify_hashes(self):
        for info in self.collections.values():
            for _ in self.rows(info["id"]):
                pass
        for name, document in self.manifest["documents"].items():
            h, length = hashlib.sha256(), 0
            for raw in iter_document_bytes(self.manifest, name, self.read_chunk):
                h.update(raw)
                length += len(raw)
            if length != document["byteLength"] or h.hexdigest() != document["sha256"]:
                fail("BUNDLE_INTEGRITY", "文档完整流指纹不匹配。")
        normalized = {**self.manifest, "documents": {name: {**info, "parts": canonical_skeleton_parts(self.manifest, name)}
                        for name, info in self.manifest["documents"].items()}}
        for name, document in normalized["documents"].items():
            h = hashlib.sha256()
            for raw in iter_document_bytes(normalized, name, self.read_chunk):
                h.update(raw)
            if h.hexdigest() != document["sha256"]:
                fail("BUNDLE_INTEGRITY", "文档不是原v1规范数值与键顺序编码。")

    def verify_integrity(self):
        self.verify_hashes()
        coverage = self.document("coverage")
        forecast_meta = document_skeleton(self.manifest, "forecast")
        if (coverage.get("schemaVersion") != 1 or type(coverage.get("baselineRequired")) is not bool
                or coverage.get("source") not in {"samples_before_model_fitting", "legacy_artifact_derived"}
                or (self.manifest["kind"] == "forecast" and coverage["source"] != "samples_before_model_fitting")
                or coverage.get("holdoutStart") != forecast_meta.get("diagnostics", {}).get("holdoutStart")
                or coverage["baselineRequired"] != ("baselineRows" in self.collections)
                or forecast_meta.get("truncated") is not False
                or forecast_meta.get("totalRows") != self.collections["forecasts"]["rowCount"]
                or forecast_meta.get("dataFingerprint") != self.manifest["dataFingerprint"]
                or forecast_meta.get("predictionConfigHash") != self.manifest["predictionConfigHash"]):
            fail("BUNDLE_COVERAGE", "完整覆盖计划或预测元数据不一致。")
        def ids(collection):
            seen = set()
            for item in self.rows(collection):
                key = item.get("id")
                if not isinstance(key, str) or not key or key in seen:
                    fail("BUNDLE_REFERENCE", "模型或目标身份重复或缺失。")
                seen.add(key)
            return seen
        targets, fits = ids("targets"), ids("modelFits")
        if coverage["baselineRequired"]:
            if "baselineModelFits" not in self.collections:
                fail("BUNDLE_REFERENCE", "因子基线缺少模型拟合引用。")
            baseline_fits = ids("baselineModelFits")
        forecast_ids = set()
        for collection, model_ids in [("forecasts", fits)] + ([("baselineRows", baseline_fits)] if coverage["baselineRequired"] else []):
            origins, row_ids, previous_date = set(), set(), ""
            for plan, row in zip_longest(self.rows("plannedOrigins"), self.rows(collection)):
                if plan is None or row is None or any(plan.get(k) != row.get(k) for k in ("date", "targetId", "entryDate", "targetDate")):
                    fail("BUNDLE_COVERAGE", "预测记录未完整匹配拟合前计划。")
                origin = (plan.get("date"), plan.get("targetId"))
                row_id = row.get("forecastId")
                if (not all(isinstance(k, str) for k in origin) or origin in origins
                        or origin[0] < previous_date or type(plan.get("inputValid")) is not bool
                        or not isinstance(row_id, str) or not row_id or row_id in row_ids
                        or row.get("status") not in {"valid", "invalid"}
                        or row.get("modelFitId") not in model_ids):
                    fail("BUNDLE_COVERAGE", "预测计划或身份重复、无序或无效。")
                if row.get("targetId") not in targets and not (row["status"] == "invalid" and row["targetId"] == "unavailable" and plan["inputValid"] is False):
                    fail("BUNDLE_REFERENCE", "预测引用了不存在的冻结目标。")
                if row["status"] == "valid" and not plan["inputValid"]:
                    fail("BUNDLE_COVERAGE", "无效输入不能变成有效预测。")
                origins.add(origin)
                row_ids.add(row_id)
                previous_date = origin[0]
            if collection == "forecasts":
                forecast_ids = row_ids
        for trade in self.rows("trades"):
            if trade.get("forecastId") not in forecast_ids:
                fail("BUNDLE_REFERENCE", "成交未引用本次预测。")
        return {"bundleId": self.bundle_id, "forecastArtifactId": self.manifest["forecastArtifactId"],
                "forecastRows": len(forecast_ids), "verified": True}

    def document(self, name):
        """Materialize Python objects from bounded chunks, never one giant parse."""
        result = document_skeleton(self.manifest, name)
        for info in self.collections.values():
            if info["document"] == name:
                _path(result, info["path"], list(self.rows(info["id"])), assign=True)
        if name == "report":
            result["forecasts"] = dict(self.document("forecast"), artifactId=self.manifest["forecastArtifactId"])
        return result


def directory_reader(path):
    root = Path(path)
    def read(path, limit):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    fail("BUNDLE_SIZE", "本地分片不是有界普通文件。")
                raw = stream.read(limit+1)
            if len(raw) > limit:
                fail("BUNDLE_SIZE", "本地分片读取超过大小限制。")
            return raw
        except OSError:
            fail("BUNDLE_INTEGRITY", "本地分片文件缺失或不安全。")
    raw = read(root / "manifest.json", MANIFEST_LIMIT)
    return BundleReader(raw, lambda name, ordinal: read(root / "chunks" / name / f"{ordinal}.json", CHUNK_LIMIT))


def export_bundle(reader, path):
    root = Path(path)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / "chunks").mkdir(mode=0o700)
    try:
        for info in reader.manifest["collections"]:
            target = root / "chunks" / info["id"]
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            for descriptor in info["chunks"]:
                raw = reader.read_chunk(info["id"], descriptor["ordinal"])
                if sha(raw) != descriptor["sha256"]:
                    fail("BUNDLE_INTEGRITY", "导出前分片校验失败。")
                file = target / f'{descriptor["ordinal"]}.json'
                file.touch(mode=0o600, exist_ok=False)
                file.write_bytes(raw)
        manifest = root / "manifest.json"
        manifest.touch(mode=0o600, exist_ok=False)
        manifest.write_bytes(reader.manifest_raw)
    except Exception:
        # No valid manifest is published for an incomplete export. Keep pieces
        # as explicit incomplete evidence rather than report success or delete.
        raise
