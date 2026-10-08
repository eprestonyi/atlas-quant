"""Independent stdlib audit of financial forecast transport and its sidecar.

Chunk hashes, typed source rows, all forecast origins/references and numerical
forecast identities are checked. The engine, model fit and provider are never
imported. Source authority requires separately authorized registry pins.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import tempfile

from bundle_audit import (
    BundleAudit,
    Collection,
    LIMIT_MANIFEST,
    canonical,
    decode,
    is_hash,
    require,
    sha,
)
from bundle_archive import _open_archive, _header, _write_body
from dataset_audit import Checks, audit_semantics, load_closure

FORMAT = "atlas.quant.financial_bundle"
CODECS = {
    "forecast": "forecast_json_v1",
    "report": "forecast_json_v1",
    "coverage": "forecast_json_v1",
    "snapshot": "financial_json_v1",
}
PROFILES = {1: "financial_compose_50_v1", 2: "financial_snapshot_view_50_v1"}
AUTO_PROFILE = "financial_fundamental_auto_50_v1"
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


def financial_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def bounded_decode(raw):
    depth = 0
    quoted = escaped = False
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
            require(depth <= 64, "JSON nesting budget exceeded")
        elif byte in (93, 125):
            depth -= 1
            require(depth >= 0, "Unbalanced JSON")
    require(depth == 0 and not quoted, "Truncated JSON")
    return decode(raw)


class FinancialBundleAudit(BundleAudit):
    def decode_json(self, raw):
        return bounded_decode(raw)

    def validate_transport_header(self, raw, manifest):
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
            "Financial manifest fields differ",
        )
        require(
            manifest["format"] == FORMAT
            and type(manifest["version"]) is int
            and manifest["version"] == 1
            and manifest["kind"] == "forecast",
            "Unsupported financial transport; execution is disabled",
        )
        require(canonical(manifest) == raw, "Manifest is not canonical")
        evidence = manifest["sourceEvidence"]
        require(
            isinstance(evidence, dict)
            and set(evidence) == {"datasetRef", "admissionProfile"},
            "Source evidence fields differ",
        )
        ref = evidence["datasetRef"]
        require(
            isinstance(ref, dict)
            and set(ref) == {"datasetId", "datasetRoot", "format", "version"},
            "Dataset reference fields differ",
        )
        require(
            isinstance(ref["datasetId"], str)
            and re.fullmatch(
                r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", ref["datasetId"]
            )
            and is_hash(ref["datasetRoot"])
            and ref["format"] == "atlas.quant.research_dataset"
            and type(ref["version"]) is int
            and ref["version"] in PROFILES
            and (evidence["admissionProfile"] == PROFILES[ref["version"]] or ref["version"] == 2 and evidence["admissionProfile"] == AUTO_PROFILE),
            "Unregistered dataset reference",
        )
        require(set(manifest["documents"]) == set(CODECS), "Required documents differ")
        for name, descriptor in manifest["documents"].items():
            require(
                set(descriptor) == {"parts", "sha256", "byteLength", "codec"}
                and descriptor["codec"] == CODECS[name],
                "Document codec differs",
            )
            require(
                isinstance(descriptor["parts"], list)
                and 0 < len(descriptor["parts"]) <= 2048,
                "Document instruction budget exceeded",
            )
        require(
            type(manifest["documents"]["snapshot"]["byteLength"]) is int
            and 0 < manifest["documents"]["snapshot"]["byteLength"] <= 24 * 1024 * 1024,
            "Financial snapshot exceeds budget",
        )
        for collection in manifest["collections"]:
            require(
                set(collection) == {"id", "document", "path", "rowCount", "chunks"},
                "Collection fields differ",
            )
            for chunk in collection["chunks"]:
                require(
                    set(chunk) == {"ordinal", "start", "count", "sha256", "byteLength"},
                    "Chunk descriptor fields differ",
                )

    def document_encoder(self, name):
        return financial_json if name == "snapshot" else canonical

    def validate_snapshot(self):
        snapshot = self.documents["snapshot"]
        report = self.documents["report"]
        reference = self.manifest["sourceEvidence"]["datasetRef"]
        require(
            set(snapshot) == SNAPSHOT_KEYS
            and type(snapshot["schemaVersion"]) is int
            and snapshot["schemaVersion"] == 2
            and snapshot["fingerprintVersion"] == "research_input_financial_v1"
            and snapshot["sourceEvidenceClosure"]
            == f'separate_research_dataset_v{reference["version"]}',
            "Financial snapshot discriminator differs",
        )
        require(
            snapshot["datasetRef"] == reference
            and snapshot["dataFingerprint"] == self.manifest["dataFingerprint"]
            and snapshot["sourceDataFingerprint"]
            == snapshot["provenance"]["dataFingerprint"],
            "Financial snapshot roots differ",
        )
        require(
            canonical(snapshot["financialSourceCommitment"])
            == canonical(report["provenance"]["financialSourceCommitment"]),
            "Report source commitment differs",
        )
        require(
            snapshot["rows"] == Collection("snapshotRows"), "Snapshot rows not chunked"
        )
        for strategy in (
            report["strategy"],
            self.documents["forecast"]["sourceStrategy"],
        ):
            require(
                strategy["schemaVersion"] == 2
                and strategy["research"]["mode"] == "statistical_quant"
                and strategy["target"]["kind"] == "asset_price"
                and strategy["model"]["family"] == "fundamental"
                and strategy["model"]["estimator"] == ("auto" if self.manifest["sourceEvidence"]["admissionProfile"] == AUTO_PROFILE else "ridge")
                and strategy["execution"]["enabled"] is False,
                "Financial forecast-only profile differs",
            )
            if self.manifest["sourceEvidence"]["admissionProfile"] == AUTO_PROFILE:
                from datetime import datetime
                u = strategy["universe"]
                span = (datetime.strptime(u["end"], "%Y%m%d")-datetime.strptime(u["start"], "%Y%m%d")).days
                require(not u.get("selection") and 1 <= len(u["symbols"]) <= 50 and 0 <= span <= 366 and len(strategy["factors"]) <= 16
                        and strategy["validation"]["innerFolds"] == strategy["validation"]["outerFolds"] == 2
                        and strategy["model"]["refitDays"] >= 20
                        and all(f["role"] == "predictor" for f in strategy["factors"])
                        and not any(strategy.get("dataBindings", {}).values()), "Auto financial resource admission differs")
            prediction = {
                k: v
                for k, v in strategy.items()
                if k not in {"execution", "portfolio", "costs", "name", "graph"}
            }
            require(
                sha(canonical(prediction)) == self.manifest["predictionConfigHash"],
                "Prediction configuration identity differs",
            )
        require(
            canonical(report["strategy"])
            == canonical(self.documents["forecast"]["sourceStrategy"])
            and report["research"]["executionOnly"] is False
            and report["metrics"] is None,
            "Financial result cannot perform execution or replace strategy",
        )
        for name in ("equity", "trades", "riskLedger", "decisions"):
            require(
                name in self.collections and self.count(name) == 0,
                "Financial execution is disabled",
            )

    def verify_dataset(self, source, pins):
        check = Checks()
        ref = self.manifest["sourceEvidence"]["datasetRef"]
        raw, manifest, payloads = load_closure(source, check, ref["datasetRoot"])
        require(
            manifest["version"] == ref["version"]
            and manifest["profile"]
            == PROFILES[ref["version"]],
            "Sidecar format/profile differs",
        )
        details = audit_semantics(manifest, payloads, check, pins)
        joined = bounded_decode(payloads["researchRows"])
        snapshot = self.documents["snapshot"]
        require(
            financial_json(snapshot["provenance"])
            == financial_json(joined["provenance"]),
            "Snapshot provenance differs from complete source sidecar",
        )
        require(
            len(joined["rows"]) == self.count("snapshotRows"),
            "Source/snapshot row count differs",
        )
        for retained, frozen in zip(
            joined["rows"], self.rows("snapshotRows"), strict=True
        ):
            require(
                financial_json(retained) == financial_json(frozen),
                "Snapshot changed source row order, value, type or signed zero",
            )
        commitment = {
            key: joined["provenance"][key]
            for key in (
                "financialCompositionVersion",
                "marketRoot",
                "financialDatasetRoot",
                "financialInputs",
            )
        }
        require(
            financial_json(snapshot["financialSourceCommitment"])
            == financial_json(commitment),
            "Financial commitment differs from source closure",
        )
        strategy = self.documents["forecast"]["sourceStrategy"]
        require(
            {k: strategy["universe"][k] for k in ("symbols", "start", "end")}
            == manifest["scope"],
            "Forecast does not use the exact declared dataset scope",
        )
        return {
            "sourceEvidenceClosed": True,
            "sourceViewProjectionVerified": manifest["version"] == 2,
            "datasetRoot": sha(raw),
            "datasetChecks": check.count,
            "datasetDetails": details,
            "registryTrustStatus": (
                "external_registry_bytes_matched" if pins is not None else "unverified"
            ),
        }


@contextmanager
def financial_directory(path):
    """Read a directory or exact bounded Atlas USTAR, without publishing files."""
    source = Path(path)
    if source.is_dir():
        yield source
        return
    with tempfile.TemporaryDirectory(prefix="atlas-financial-audit-") as temporary:
        directory = Path(temporary)
        with _open_archive(source) as reader:
            size = _header(reader, "manifest.json")
            _write_body(reader, directory / "manifest.json", size)
            with sqlite3.connect(":memory:") as db:
                planned = FinancialBundleAudit(directory, db).manifest
            for collection in planned["collections"]:
                for chunk in collection["chunks"]:
                    name = f'chunks/{collection["id"]}/{chunk["ordinal"]}.json'
                    _header(reader, name, chunk["byteLength"])
                    target = directory / name
                    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    _write_body(reader, target, chunk["byteLength"], chunk["sha256"])
            reader.finish()
        yield directory


def audit_financial_bundle(path, *, source_dataset=None, registry_pins=None):
    with financial_directory(path) as directory, tempfile.TemporaryDirectory(
        prefix="atlas-financial-index-"
    ) as temporary:
        with sqlite3.connect(str(Path(temporary) / "index.sqlite")) as db:
            verifier = FinancialBundleAudit(directory, db)
            result = verifier.run()
            source = (
                {"sourceEvidenceClosed": False, "registryTrustStatus": "unverified"}
                if source_dataset is None
                else verifier.verify_dataset(source_dataset, registry_pins)
            )
            return {
                **result,
                **source,
                "status": (
                    "PASS" if source["sourceEvidenceClosed"] else "INCOMPLETE_SOURCE"
                ),
                "auditor": "atlas.financial_bundle.stdlib_audit/1",
                "transportVerified": True,
                "recomposition": "not_performed",
                "sourceResearchFingerprintRecomputed": False,
                "financialFormulasRecomputed": False,
                "pdfAuthenticityVerified": False,
                "modelFitted": False,
                "providerCalls": 0,
                "limitations": [
                    "Registry pins must be separately authorized; matching bytes is not PDF authentication.",
                    "This independent audit checks closure and numerical forecast identities; it does not refit models or recompute financial formulas.",
                    "Strategy-specific pandas CSV and historical provider fingerprints are not rederived. Exact rows, provenance and closure commitments are checked.",
                ],
            }
