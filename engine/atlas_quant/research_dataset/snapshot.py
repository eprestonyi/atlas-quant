"""Explicit financial snapshot; old snapshot/bundle codecs are unchanged."""

from copy import deepcopy

from ..engine import _prepare_data
from ..financial_statements.prepare import _safe_rows
from .codec import decode, digest, encode, keys, require, sha, uuid
from .profile import FORMAT, VERSION, VIEW_VERSION, DEFAULT_PROFILE
from .manifest import validate_manifest
from .research_profile import validate_research_profile

FINGERPRINT_VERSION = "research_input_financial_v1"


def dataset_reference(value):
    keys(value, {"datasetId", "datasetRoot", "format", "version"})
    uuid(value["datasetId"])
    digest(value["datasetRoot"])
    require(
        value["format"] == FORMAT
        and type(value["version"]) is int
        and value["version"] in {VERSION, VIEW_VERSION},
        "DATASET_FORMAT",
        "Unknown dataset reference",
    )
    return value


def freeze_financial_input(
    strategy, result, dataset_ref, *, manifest_bytes, profile=DEFAULT_PROFILE, research_profile=None
):
    dataset_reference(dataset_ref)
    manifest = validate_manifest(
        manifest_bytes, expected_root=dataset_ref["datasetRoot"], profile=profile
    )
    require(
        dataset_ref["version"] == manifest["version"],
        "DATASET_FORMAT",
        "Dataset reference version differs from its actual manifest",
    )
    joined = next(
        c for c in manifest["components"] if c["componentId"] == "researchRows"
    )
    require(
        sha(encode(result.to_dataset())) == joined["payloadSha256"],
        "DATASET_SNAPSHOT",
        "Composed rows/provenance do not match the referenced source closure",
    )
    normalized = validate_research_profile(strategy, manifest["scope"], research_profile=research_profile, dataset_version=manifest["version"])
    _, _, audit = _prepare_data(result.data, normalized, result.provenance)
    snapshot = {
        "schemaVersion": 2,
        "fingerprintVersion": FINGERPRINT_VERSION,
        "datasetRef": deepcopy(dataset_ref),
        "sourceEvidenceClosure": f"separate_research_dataset_v{manifest["version"]}",
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


def restore_financial_input(strategy, snapshot_bytes, reader, authorized_registry, *, research_profile=None):
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
        and snapshot["sourceEvidenceClosure"]
        in {"separate_research_dataset_v1", "separate_research_dataset_v2"},
        "DATASET_SNAPSHOT",
        "Unknown financial snapshot discriminator",
    )
    dataset_reference(snapshot["datasetRef"])
    require(
        snapshot["sourceEvidenceClosure"]
        == f"separate_research_dataset_v{snapshot["datasetRef"]["version"]}",
        "DATASET_FORMAT",
        "Snapshot source discriminator differs from the referenced dataset version",
    )
    require(
        snapshot["datasetRef"]["version"] == reader.manifest["version"],
        "DATASET_FORMAT",
        "Dataset reference version differs from source closure",
    )
    require(
        snapshot["datasetRef"]["datasetRoot"] == reader.dataset_root,
        "DATASET_ROOT",
        "Financial snapshot belongs to a different source closure",
    )
    result = restore_dataset_for_research(strategy, reader, authorized_registry, research_profile=research_profile)
    regenerated = freeze_financial_input(
        strategy,
        result,
        snapshot["datasetRef"],
        manifest_bytes=reader.manifest_bytes,
        profile=reader.profile,
        research_profile=research_profile,
    )
    require(
        encode(regenerated) == snapshot_bytes,
        "DATASET_SNAPSHOT",
        "Financial snapshot differs from recomposed data, all columns or source commitment",
    )
    return result
