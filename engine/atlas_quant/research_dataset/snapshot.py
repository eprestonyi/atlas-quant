"""Explicit financial snapshot; old snapshot/bundle codecs are unchanged."""

from copy import deepcopy

from ..engine import _prepare_data
from ..financial_statements.prepare import _safe_rows
from ..statistical_quant.schema import validate
from .codec import decode, digest, encode, keys, require, sha, uuid
from .profile import FORMAT, VERSION, DEFAULT_PROFILE
from .manifest import validate_manifest

FINGERPRINT_VERSION = "research_input_financial_v1"


def validate_research_profile(strategy, scope):
    # Require explicit false before schema defaults can obscure user intent.
    require(
        isinstance(strategy, dict)
        and strategy.get("schemaVersion") == 2
        and isinstance(strategy.get("execution"), dict)
        and strategy["execution"].get("enabled") is False,
        "DATASET_FORECAST_ONLY",
        "Financial dataset profile requires explicit forecast-only schema 2",
    )
    normalized = validate(strategy)
    require(
        normalized["research"]["mode"] == "statistical_quant"
        and normalized["target"]["kind"] == "asset_price"
        and normalized["model"]["family"] == "fundamental"
        and normalized["model"]["estimator"] == "ridge",
        "DATASET_RESEARCH_PROFILE",
        "This profile admits only fundamental Ridge asset-price forecasts",
    )
    require(
        {key: normalized["universe"][key] for key in ("symbols", "start", "end")}
        == scope,
        "DATASET_SCOPE",
        "Research must use the exact frozen dataset universe and interval",
    )
    return normalized


def dataset_reference(value):
    keys(value, {"datasetId", "datasetRoot", "format", "version"})
    uuid(value["datasetId"])
    digest(value["datasetRoot"])
    require(
        value["format"] == FORMAT
        and type(value["version"]) is int
        and value["version"] == VERSION,
        "DATASET_FORMAT",
        "Unknown dataset reference",
    )
    return value


def freeze_financial_input(
    strategy, result, dataset_ref, *, manifest_bytes, profile=DEFAULT_PROFILE
):
    dataset_reference(dataset_ref)
    manifest = validate_manifest(
        manifest_bytes, expected_root=dataset_ref["datasetRoot"], profile=profile
    )
    joined = next(
        c for c in manifest["components"] if c["componentId"] == "researchRows"
    )
    require(
        sha(encode(result.to_dataset())) == joined["payloadSha256"],
        "DATASET_SNAPSHOT",
        "Composed rows/provenance do not match the referenced source closure",
    )
    normalized = validate_research_profile(strategy, manifest["scope"])
    _, _, audit = _prepare_data(result.data, normalized, result.provenance)
    snapshot = {
        "schemaVersion": 2,
        "fingerprintVersion": FINGERPRINT_VERSION,
        "datasetRef": deepcopy(dataset_ref),
        "sourceEvidenceClosure": "separate_research_dataset_v1",
        "rows": _safe_rows(result.data),
        "provenance": deepcopy(result.provenance),
        "dataFingerprint": audit["dataSha256"],
        "sourceDataFingerprint": result.provenance["dataFingerprint"],
        "financialSourceCommitment": audit["financialSourceCommitment"],
    }
    require(
        len(encode(snapshot)) <= profile.joined_bytes,
        "DATASET_BUDGET",
        "Financial snapshot exceeds its byte budget",
    )
    return snapshot


def restore_financial_input(strategy, snapshot_bytes, reader, authorized_registry):
    from .reader import restore_dataset_for_research

    snapshot = decode(snapshot_bytes, reader.profile.joined_bytes)
    keys(
        snapshot,
        {
            "schemaVersion",
            "fingerprintVersion",
            "datasetRef",
            "sourceEvidenceClosure",
            "rows",
            "provenance",
            "dataFingerprint",
            "sourceDataFingerprint",
            "financialSourceCommitment",
        },
    )
    require(
        type(snapshot["schemaVersion"]) is int
        and snapshot["schemaVersion"] == 2
        and snapshot["fingerprintVersion"] == FINGERPRINT_VERSION
        and snapshot["sourceEvidenceClosure"] == "separate_research_dataset_v1",
        "DATASET_SNAPSHOT",
        "Unknown financial snapshot discriminator",
    )
    dataset_reference(snapshot["datasetRef"])
    require(
        snapshot["datasetRef"]["datasetRoot"] == reader.dataset_root,
        "DATASET_ROOT",
        "Financial snapshot belongs to a different source closure",
    )
    result = restore_dataset_for_research(strategy, reader, authorized_registry)
    regenerated = freeze_financial_input(
        strategy,
        result,
        snapshot["datasetRef"],
        manifest_bytes=reader.manifest_bytes,
        profile=reader.profile,
    )
    require(
        encode(regenerated) == snapshot_bytes,
        "DATASET_SNAPSHOT",
        "Financial snapshot differs from recomposed data, all columns or source commitment",
    )
    return result
