"""Financial forecast transport; exact typed snapshot, unchanged forecast IDs.

This module never fits, grants source authority, executes a portfolio or changes
the legacy bundle codec. Legacy coverage/reference checks are reused only after
this format's own strict manifest and document encodings have been checked.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from . import bundle as legacy
from .research_dataset.codec import encode as financial_encode
from .statistical_quant.schema import prediction_config, validate as validate_strategy

FORMAT = "atlas.quant.financial_bundle"
VERSION = 1
CAPABILITY = FORMAT + "/1"
SNAPSHOT_LIMIT = 24 * 1024 * 1024
JSON_DEPTH = 64
CODECS = {
    "forecast": "forecast_json_v1",
    "report": "forecast_json_v1",
    "coverage": "forecast_json_v1",
    "snapshot": "financial_json_v1",
}
DATASET_PROFILES = {1: "financial_compose_50_v1", 2: "financial_snapshot_view_50_v1"}
SNAPSHOT_KEYS = {
    "schemaVersion",
    "fingerprintVersion",
    "datasetRef",
    "sourceEvidenceClosure",
    "rows",
    "provenance",
    "dataFingerprint",
    "sourceDataFingerprint",
    "financialSourceCommitment",
}


def require(condition, message, code="FINANCIAL_BUNDLE_FORMAT"):
    if not condition:
        legacy.fail(code, message)


def check_depth(raw):
    """Bound nesting before a JSON decoder allocates recursive structures."""
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            require(depth <= JSON_DEPTH, "JSON nesting budget exceeded")
        elif byte in (93, 125):
            depth -= 1
            require(depth >= 0, "Unbalanced JSON structure")
    require(depth == 0 and not quoted, "Unclosed JSON structure")


def decode_exact(raw, maximum, *, financial=False):
    require(
        isinstance(raw, bytes) and 0 < len(raw) <= maximum,
        "JSON byte budget exceeded",
        "FINANCIAL_BUNDLE_BUDGET",
    )
    check_depth(raw)
    try:
        value = legacy.decode(raw.decode("utf-8"))
        encoded = financial_encode(value) if financial else legacy.encode(value)
        require(encoded == raw, "Noncanonical document bytes")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        if getattr(exc, "code", None):
            raise
        legacy.fail("FINANCIAL_BUNDLE_FORMAT", "Invalid finite UTF-8 JSON")


def validate_source_evidence(value):
    require(
        isinstance(value, dict) and set(value) == {"datasetRef", "admissionProfile"},
        "Explicit registered source evidence is required",
    )
    ref = value["datasetRef"]
    require(
        isinstance(ref, dict)
        and set(ref) == {"datasetId", "datasetRoot", "format", "version"},
        "Dataset reference fields differ",
    )
    require(
        isinstance(ref["datasetId"], str)
        and re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", ref["datasetId"]
        ),
        "Dataset owner-scoped reference is invalid",
    )
    require(
        isinstance(ref["datasetRoot"], str)
        and legacy.HASH.fullmatch(ref["datasetRoot"]),
        "Dataset root is invalid",
    )
    require(
        ref["format"] == "atlas.quant.research_dataset"
        and type(ref["version"]) is int
        and DATASET_PROFILES.get(ref["version"]) == value["admissionProfile"],
        "Dataset version/profile is not registered",
    )
    return deepcopy(value)


def legacy_layout(manifest):
    """Private structural projection, never a v1 identity or published artifact."""
    projected = deepcopy(manifest)
    projected.pop("sourceEvidence")
    projected["format"] = "atlas.quant.bundle"
    for document in projected["documents"].values():
        document.pop("codec")
    return projected


def validate_manifest(raw, expected_id=None):
    manifest = decode_exact(raw, legacy.MANIFEST_LIMIT)
    require(
        expected_id is None or legacy.sha(raw) == expected_id,
        "Financial transport identity differs",
        "FINANCIAL_BUNDLE_INTEGRITY",
    )
    require(
        isinstance(manifest, dict)
        and set(manifest)
        == {
            "format",
            "version",
            "kind",
            "forecastArtifactId",
            "predictionConfigHash",
            "dataFingerprint",
            "sourceEvidence",
            "documents",
            "collections",
            "totals",
        },
        "Unknown or missing manifest fields",
    )
    require(
        manifest["format"] == FORMAT
        and type(manifest["version"]) is int
        and manifest["version"] == VERSION
        and manifest["kind"] == "forecast",
        "This codec only accepts financial forecast bundles",
    )
    validate_source_evidence(manifest["sourceEvidence"])
    require(
        isinstance(manifest["documents"], dict)
        and set(manifest["documents"]) == set(CODECS),
        "Financial forecast needs exactly four documents",
    )
    for name, document in manifest["documents"].items():
        require(
            isinstance(document, dict)
            and set(document) == {"codec", "parts", "sha256", "byteLength"}
            and document["codec"] == CODECS[name],
            "Document codec cannot be changed",
        )
    require(
        type(manifest["documents"]["snapshot"]["byteLength"]) is int
        and manifest["documents"]["snapshot"]["byteLength"] <= SNAPSHOT_LIMIT,
        "Financial snapshot exceeds its unchanged budget",
        "FINANCIAL_BUNDLE_BUDGET",
    )
    legacy.validate_manifest(legacy.encode(legacy_layout(manifest)))
    # The actual manifest is slightly larger than the private structural view.
    require(
        manifest["totals"]["chunkBytes"] + len(raw) <= legacy.BUNDLE_LIMIT,
        "Financial parent budget exceeded",
        "FINANCIAL_BUNDLE_BUDGET",
    )
    return manifest


def validate_metadata(manifest):
    report = legacy.document_skeleton(manifest, "report")
    forecast = legacy.document_skeleton(manifest, "forecast")
    snapshot = legacy.document_skeleton(manifest, "snapshot")
    require(
        set(snapshot) == SNAPSHOT_KEYS
        and snapshot["schemaVersion"] == 2
        and type(snapshot["schemaVersion"]) is int
        and snapshot["fingerprintVersion"] == "research_input_financial_v1"
        and snapshot["sourceEvidenceClosure"]
        == f'separate_research_dataset_v{manifest["sourceEvidence"]["datasetRef"]["version"]}',
        "Snapshot discriminator or evidence closure differs",
    )
    require(
        snapshot["datasetRef"] == manifest["sourceEvidence"]["datasetRef"]
        and snapshot["dataFingerprint"] == manifest["dataFingerprint"]
        and snapshot["sourceDataFingerprint"]
        == snapshot["provenance"].get("dataFingerprint")
        and report["provenance"].get("dataSha256") == manifest["dataFingerprint"]
        and report["provenance"].get("financialSourceCommitment")
        == snapshot["financialSourceCommitment"],
        "Source roots or financial commitment differ",
        "FINANCIAL_BUNDLE_SOURCE",
    )
    for config in (report.get("strategy"), forecast.get("sourceStrategy")):
        require(
            isinstance(config, dict)
            and config.get("execution", {}).get("enabled") is False,
            "Explicit forecast-only strategy required",
            "FINANCIAL_BUNDLE_EXECUTION",
        )
        normalized = validate_strategy(config)
        require(
            normalized["schemaVersion"] == 2
            and normalized["research"]["mode"] == "statistical_quant"
            and normalized["target"]["kind"] == "asset_price"
            and normalized["model"]["family"] == "fundamental"
            and normalized["model"]["estimator"] == "ridge",
            "Financial research profile differs",
        )
        require(
            legacy.sha(legacy.encode(prediction_config(normalized)))
            == manifest["predictionConfigHash"],
            "Prediction configuration hash differs",
            "FINANCIAL_BUNDLE_INTEGRITY",
        )
    require(
        legacy.encode(report["strategy"]) == legacy.encode(forecast["sourceStrategy"])
        and report.get("schemaVersion") == 2
        and report.get("research", {}).get("executionOnly") is False
        and report.get("metrics") is None
        and report.get("execution", {}).get("forecastArtifactId")
        == manifest["forecastArtifactId"],
        "Financial report must remain forecast-only",
        "FINANCIAL_BUNDLE_EXECUTION",
    )
    collections = {item["id"]: item for item in manifest["collections"]}
    require(
        all(
            collections[name]["rowCount"] == 0
            for name in ("equity", "trades", "riskLedger", "decisions")
        ),
        "Financial execution records are forbidden",
        "FINANCIAL_BUNDLE_EXECUTION",
    )
    return snapshot


def canonical_parts(manifest, name):
    encoder = financial_encode if name == "snapshot" else legacy.encode
    parts = []

    def literal(raw):
        parts.append({"literal": raw.decode() if isinstance(raw, bytes) else raw})

    def walk(value):
        if isinstance(value, dict) and set(value) == {"__bundle_collection__"}:
            parts.append({"collection": value["__bundle_collection__"]})
        elif isinstance(value, dict) and set(value) == {"__bundle_document__"}:
            parts.append(
                {
                    "document": "forecast",
                    "wrapArtifactId": manifest["forecastArtifactId"],
                }
            )
        elif isinstance(value, dict):
            literal("{")
            for i, key in enumerate(sorted(value)):
                if i:
                    literal(",")
                literal(encoder(key) + b":")
                walk(value[key])
            literal("}")
        elif isinstance(value, list):
            literal("[")
            for i, item in enumerate(value):
                if i:
                    literal(",")
                walk(item)
            literal("]")
        else:
            literal(encoder(value))

    walk(legacy.document_skeleton(manifest, name))
    return parts


class FinancialBundleReader(legacy.BundleReader):
    def __init__(self, manifest_raw, read_chunk, expected_id=None):
        self._manifest = validate_manifest(manifest_raw, expected_id)
        self._raw = manifest_raw
        self._read = read_chunk
        self._collections = {c["id"]: c for c in self._manifest["collections"]}

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    @property
    def manifest_raw(self):
        return self._raw

    @property
    def bundle_id(self):
        return legacy.sha(self._raw)

    @property
    def collections(self):
        return deepcopy(self._collections)

    @property
    def read_chunk(self):
        return self._read

    def rows(self, collection):
        for descriptor in self._collections[collection]["chunks"]:
            raw = self._read(collection, descriptor["ordinal"])
            require(
                len(raw) == descriptor["byteLength"]
                and legacy.sha(raw) == descriptor["sha256"],
                "Chunk byte length or hash differs",
                "FINANCIAL_BUNDLE_INTEGRITY",
            )
            values = decode_exact(
                raw, legacy.CHUNK_LIMIT, financial=collection == "snapshotRows"
            )
            require(
                isinstance(values, list)
                and len(values) == descriptor["count"]
                and all(isinstance(v, dict) for v in values),
                "Chunk record count or type differs",
            )
            yield from values

    def verify_hashes(self):
        for name in self._collections:
            for _ in self.rows(name):
                pass
        normalized = deepcopy(self._manifest)
        for name in normalized["documents"]:
            normalized["documents"][name]["parts"] = canonical_parts(
                self._manifest, name
            )
        for definition in (self._manifest, normalized):
            for name, document in definition["documents"].items():
                digest, length = hashlib.sha256(), 0
                for raw in legacy.iter_document_bytes(definition, name, self._read):
                    digest.update(raw)
                    length += len(raw)
                require(
                    length == document["byteLength"]
                    and digest.hexdigest() == document["sha256"],
                    "Document hash, length or canonical layout differs",
                    "FINANCIAL_BUNDLE_INTEGRITY",
                )

    def verify_integrity(self):
        result = super().verify_integrity()
        validate_metadata(self._manifest)
        return {
            **result,
            "transportVerified": True,
            "sourceEvidenceClosed": False,
            "registryTrustStatus": "unverified",
            "recomposition": "not_performed",
            "status": "INCOMPLETE_SOURCE",
        }

    def snapshot_bytes(self):
        expected = self._manifest["documents"]["snapshot"]
        parts, size, digest = [], 0, hashlib.sha256()
        for raw in legacy.iter_document_bytes(self._manifest, "snapshot", self._read):
            size += len(raw)
            require(
                size <= expected["byteLength"] and size <= SNAPSHOT_LIMIT,
                "Snapshot reconstruction exceeds declared budget",
                "FINANCIAL_BUNDLE_BUDGET",
            )
            digest.update(raw)
            parts.append(raw)
        require(
            size == expected["byteLength"] and digest.hexdigest() == expected["sha256"],
            "Snapshot reconstructed identity differs",
            "FINANCIAL_BUNDLE_INTEGRITY",
        )
        return b"".join(parts)

    def restore_sources(self, dataset_reader, authorized_registry):
        """Explicit source recomposition only; never execution or model fitting."""
        self.verify_integrity()
        evidence = self._manifest["sourceEvidence"]
        require(
            dataset_reader.dataset_root == evidence["datasetRef"]["datasetRoot"],
            "Dataset sidecar identity differs",
            "FINANCIAL_BUNDLE_SOURCE",
        )
        require(
            dataset_reader.manifest["version"] == evidence["datasetRef"]["version"]
            and dataset_reader.manifest["profile"] == evidence["admissionProfile"],
            "Dataset sidecar profile differs",
            "FINANCIAL_BUNDLE_SOURCE",
        )
        from .research_dataset.snapshot import restore_financial_input

        strategy = legacy.document_skeleton(self._manifest, "forecast")[
            "sourceStrategy"
        ]
        return restore_financial_input(
            strategy, self.snapshot_bytes(), dataset_reader, authorized_registry
        )


def build_financial_bundle(
    report,
    snapshot_raw,
    coverage,
    source_evidence,
    write_chunk,
    read_chunk,
    *,
    chunk_target=legacy.CHUNK_TARGET,
):
    """Write bounded chunks; only return a manifest after full integrity checks."""
    source_evidence = validate_source_evidence(source_evidence)
    snapshot = decode_exact(snapshot_raw, SNAPSHOT_LIMIT, financial=True)
    if not isinstance(report, dict) or not isinstance(report.get("forecasts"), dict):
        legacy.fail("BUNDLE_FORMAT", "需要完整预测研究报告。")
    if not legacy._integer(chunk_target, 2) or chunk_target > legacy.CHUNK_LIMIT:
        legacy.fail("BUNDLE_BUDGET", "分片目标大小无效。")
    artifact = report["forecasts"]
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "sourceEvidence": source_evidence,
        "kind": "forecast",
        "forecastArtifactId": artifact["artifactId"],
        "predictionConfigHash": artifact["predictionConfigHash"],
        "dataFingerprint": artifact["dataFingerprint"],
        "documents": {},
        "collections": [],
        "totals": {"chunkCount": 0, "chunkBytes": 0},
    }
    documents = {
        "forecast": {k: v for k, v in artifact.items() if k != "artifactId"},
        "report": report,
        "coverage": coverage,
    }
    if snapshot is not None:
        documents["snapshot"] = snapshot
    elif manifest["kind"] == "forecast":
        legacy.fail("BUNDLE_FORMAT", "新预测分片缺少冻结输入。")
    lookup = {pair: name for name, pair in legacy.COLLECTIONS.items()}

    def collection(name, document, pointer, values):
        if not isinstance(values, list):
            legacy.fail("BUNDLE_FORMAT", "分片集合必须为数组。")
        info = {
            "id": name,
            "document": document,
            "path": pointer,
            "rowCount": len(values),
            "chunks": [],
        }
        pending, size, start = [], 2, 0

        def flush():
            nonlocal pending, size, start
            if not pending:
                return
            raw = b"[" + b",".join(pending) + b"]"
            totals = manifest["totals"]
            totals["chunkCount"] += 1
            totals["chunkBytes"] += len(raw)
            if (
                totals["chunkCount"] > legacy.CHUNK_COUNT_LIMIT
                or totals["chunkBytes"] > legacy.BUNDLE_LIMIT
            ):
                legacy.fail("BUNDLE_SIZE", "完整分片超过总资源预算；没有截断输出。")
            descriptor = {
                "ordinal": len(info["chunks"]),
                "start": start,
                "count": len(pending),
                "sha256": legacy.sha(raw),
                "byteLength": len(raw),
            }
            write_chunk(name, descriptor["ordinal"], raw)
            info["chunks"].append(descriptor)
            start += len(pending)
            pending, size = [], 2

        for value in values:
            if not isinstance(value, dict):
                legacy.fail("BUNDLE_FORMAT", "研究集合的每项必须为对象。")
            raw = encoder(value)
            if len(raw) + 2 > legacy.CHUNK_LIMIT:
                legacy.fail(
                    "BUNDLE_ITEM_SIZE", "单条研究记录超过分片硬上限；没有切断记录。"
                )
            if pending and (
                size + 1 + len(raw) > chunk_target
                or len(pending) >= legacy.CHUNK_ROW_LIMIT
            ):
                flush()
            size += len(raw) + bool(pending)
            pending.append(raw)
        flush()
        manifest["collections"].append(info)

    for document, root in documents.items():
        encoder = financial_encode if document == "snapshot" else legacy.encode
        parts = []

        def literal(raw):
            value = raw.decode() if isinstance(raw, bytes) else raw
            if parts and "literal" in parts[-1]:
                parts[-1]["literal"] += value
            else:
                parts.append({"literal": value})

        def walk(value, pointer=""):
            if document == "report" and pointer == "/forecasts":
                parts.append(
                    {"document": "forecast", "wrapArtifactId": artifact["artifactId"]}
                )
            elif (document, pointer) in lookup:
                name = lookup[(document, pointer)]
                collection(name, document, pointer, value)
                parts.append({"collection": name})
            elif isinstance(value, dict):
                literal("{")
                for i, key in enumerate(sorted(value)):
                    if i:
                        literal(",")
                    literal(encoder(key) + b":")
                    walk(
                        value[key],
                        pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
                    )
                literal("}")
            else:
                literal(encoder(value))

        walk(root)
        manifest["documents"][document] = {"codec": CODECS[document], "parts": parts}
    for name, definition in manifest["documents"].items():
        h, length = hashlib.sha256(), 0
        for raw in legacy.iter_document_bytes(manifest, name, read_chunk):
            h.update(raw)
            length += len(raw)
        definition.update(sha256=h.hexdigest(), byteLength=length)
    raw = legacy.encode(manifest)
    validate_manifest(raw)
    if manifest["documents"]["forecast"]["sha256"] != artifact["artifactId"]:
        legacy.fail("BUNDLE_INTEGRITY", "分片重建未保留原预测身份。")
    reader = FinancialBundleReader(raw, read_chunk)
    reader.verify_integrity()
    require(
        reader.snapshot_bytes() == snapshot_raw,
        "Snapshot bytes changed during transport",
        "FINANCIAL_BUNDLE_INTEGRITY",
    )
    return raw


def financial_directory_reader(directory):
    """Open an immutable local export; never interpret the new format as v1."""
    root = Path(directory).resolve(strict=True)

    def read_file(relative, limit):
        path = root / relative
        require(
            not path.is_symlink() and path.resolve().is_relative_to(root),
            "Financial export path escapes its directory",
        )
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit, "Financial export file exceeds budget")
        return raw

    raw = read_file("manifest.json", legacy.MANIFEST_LIMIT)
    return FinancialBundleReader(
        raw,
        lambda collection, ordinal: read_file(
            f"chunks/{collection}/{ordinal}.json", legacy.CHUNK_LIMIT
        ),
    )


def export_financial_bundle(reader, directory):
    """Save a new directory only after full transport verification.

    Source closure is separate; this operation does not grant registry trust.
    A failed I/O leaves explicit incomplete pieces and never a valid manifest.
    """
    require(isinstance(reader, FinancialBundleReader), "Financial reader required")
    reader.verify_integrity()
    legacy.export_bundle(reader, directory)
