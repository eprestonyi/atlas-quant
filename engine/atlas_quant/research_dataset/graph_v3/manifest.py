"""Closed typed graph; descriptors cannot request arbitrary paths or decoding."""

import re
from datetime import datetime

from ..codec import decode, digest, encode, keys, require, sha, uuid
from ..profile import DEFAULT_PROFILE,FORMAT,check_profile
from ..manifest import scope as legacy_scope,component_root

GRAPH_VERSION = 3
GRAPH_PROFILE_ID = "financial_snapshot_graph_50_v1"

TYPES = {
    "snapshot_scope_origin": {"sourceBundleId", "sourceSnapshotSha256", "marketRoot"},
    "registry_evidence": set(),
    "market_dataset": {"marketRoot"},
    "financial_input": {"inputRoot", "packRoot"},
    "financial_prepared_graph": {"packRoot", "preparedRoot", "calendarRoot", "preparedPayloadSha256"},
    "research_columns": {"financialDatasetRoot", "logicalJoinedSha256"},
    "dataset_schema": set(),
    "dataset_coverage": set(),
}


def scope(value,profile=DEFAULT_PROFILE):
    legacy_scope(value,profile)
    require((datetime.strptime(value["end"],"%Y%m%d")-datetime.strptime(value["start"],"%Y%m%d")).days+1<=366,
            "DATASET_SCOPE","Graph source scope exceeds 366 inclusive calendar days")
    return value


def validate_manifest(raw, *, expected_root=None, profile=DEFAULT_PROFILE):
    check_profile(profile)
    value = decode(raw, profile.manifest_bytes)
    keys(
        value,
        {
            "format",
            "version",
            "profile",
            "scope",
            "marketCalendarRef",
            "financialSources",
            "components",
            "roots",
        },
    )
    require(
        value["format"] == FORMAT
        and type(value["version"]) is int
        and value["version"] == GRAPH_VERSION and value["profile"] == GRAPH_PROFILE_ID,
        "DATASET_FORMAT",
        "Unknown dataset format or profile",
    )
    if expected_root is not None:
        require(
            sha(raw) == digest(expected_root), "DATASET_ROOT", "Dataset root mismatch"
        )
    scope(value["scope"], profile)
    uuid(value["marketCalendarRef"])
    keys(value["roots"], {"marketRoot", "financialDatasetRoot"})
    for root in value["roots"].values():
        digest(root)
    components = value["components"]
    require(
        isinstance(components, list) and 1 <= len(components) <= profile.max_components,
        "DATASET_BUDGET",
        "Component count exceeds profile",
    )
    ids, roots, depth, total, parts, input_bytes = {}, {}, {}, len(raw), 0, 0
    for item in components:
        keys(
            item,
            {
                "componentId",
                "type",
                "version",
                "componentRoot",
                "semanticRoots",
                "encoding",
                "payloadSha256",
                "byteLength",
                "dependencies",
                "parts",
            },
        )
        name = item["componentId"]
        require(
            isinstance(name, str)
            and re.fullmatch(r"[a-z][A-Za-z0-9]{0,39}", name)
            and name not in ids,
            "DATASET_IDENTITY",
            "Duplicate or invalid component ID",
        )
        require(
            isinstance(item["type"], str)
            and item["type"] in TYPES
            and type(item["version"]) is int
            and item["version"] == 1
            and item["encoding"] == "raw_bytes",
            "DATASET_TYPE",
            "Unregistered component encoding",
        )
        keys(item["semanticRoots"], TYPES[item["type"]])
        for root in item["semanticRoots"].values():
            digest(root)
        digest(item["payloadSha256"])
        require(
            digest(item["componentRoot"]) == component_root(item)
            and item["componentRoot"] not in roots,
            "DATASET_ROOT",
            "Changed or duplicated component root",
        )
        dependencies = item["dependencies"]
        require(
            isinstance(dependencies, list)
            and all(isinstance(root, str) and root in roots for root in dependencies)
            and dependencies == sorted(set(dependencies)),
            "DATASET_GRAPH",
            "Dependencies must be unique earlier component roots",
        )
        depth[item["componentRoot"]] = (
            0 if not dependencies else 1 + max(depth[d] for d in dependencies)
        )
        require(
            depth[item["componentRoot"]] <= profile.max_depth,
            "DATASET_GRAPH",
            "Component dependency graph is too deep",
        )
        require(
            type(item["byteLength"]) is int
            and item["byteLength"] > 0
            and isinstance(item["parts"], list)
            and item["parts"],
            "DATASET_BUDGET",
            "Nonempty bounded component bytes required",
        )
        size = 0
        for ordinal, part in enumerate(item["parts"]):
            keys(part, {"ordinal", "byteLength", "sha256"})
            require(
                type(part["ordinal"]) is int
                and part["ordinal"] == ordinal
                and type(part["byteLength"]) is int
                and 1 <= part["byteLength"] <= profile.part_bytes,
                "DATASET_BUDGET",
                "Invalid part ordinal or byte length",
            )
            digest(part["sha256"])
            size += part["byteLength"]
        require(
            size == item["byteLength"], "DATASET_BUDGET", "Component byte count differs"
        )
        ceiling = {
            "market_dataset": profile.market_bytes,
            "financial_input": profile.package_bytes,
            "research_columns": profile.joined_bytes,
        }.get(item["type"], profile.total_bytes)
        require(
            size <= ceiling,
            "DATASET_BUDGET",
            "Component exceeds its typed byte ceiling",
        )
        if item["type"] == "financial_input":
            input_bytes += size
            require(
                input_bytes <= profile.package_bytes,
                "DATASET_BUDGET",
                "Financial packages exceed their shared budget",
            )
        total += size
        parts += len(item["parts"])
        require(
            total <= profile.total_bytes and parts <= profile.max_parts,
            "DATASET_BUDGET",
            "Closure exceeds its shared byte/part budget",
        )
        ids[name] = item
        roots[item["componentRoot"]] = item
    sources = value["financialSources"]
    require(
        isinstance(sources, list) and 1 <= len(sources) <= profile.max_inputs,
        "DATASET_BUDGET",
        "Financial source count exceeds profile",
    )
    fixed = {
        "registryEvidence": "registry_evidence",
        "marketDataset": "market_dataset",
        "researchColumns": "research_columns",
        "schema": "dataset_schema",
        "coverage": "dataset_coverage",
    }
    if value["version"] == GRAPH_VERSION:
        fixed["marketOrigin"] = "snapshot_scope_origin"
    for i, source in enumerate(sources):
        keys(source, {"componentId", "calendarRef", "proofRefs", "preparedRoot"})
        require(
            source["componentId"] == f"financialInput{i}",
            "DATASET_TYPE",
            "Financial source order differs",
        )
        uuid(source["calendarRef"])
        require(
            isinstance(source["proofRefs"], list) and len(source["proofRefs"]) <= 256,
            "DATASET_BUDGET",
            "Too many proof references",
        )
        for ref in source["proofRefs"]:
            uuid(ref)
        require(
            source["proofRefs"] == sorted(set(source["proofRefs"])),
            "DATASET_IDENTITY",
            "Duplicate proof reference",
        )
        digest(source["preparedRoot"])
        fixed[f"financialInput{i}"] = "financial_input"
        fixed[f"financialGraph{i}"] = "financial_prepared_graph"
    require(
        set(ids) == set(fixed) and all(ids[k]["type"] == v for k, v in fixed.items()),
        "DATASET_TYPE",
        "Missing or unexpected typed component",
    )
    registry = ids["registryEvidence"]["componentRoot"]
    expected_dependencies = {"registryEvidence": [], "marketDataset": [registry]}
    if value["version"] == GRAPH_VERSION:
        expected_dependencies["marketOrigin"] = []
        expected_dependencies["marketDataset"] = sorted(
            [registry, ids["marketOrigin"]["componentRoot"]]
        )
    for i in range(len(sources)):
        expected_dependencies[f"financialInput{i}"] = [registry]
        expected_dependencies[f"financialGraph{i}"] = [
            ids[f"financialInput{i}"]["componentRoot"]
        ]
    derived = sorted(
        [
            ids["marketDataset"]["componentRoot"],
            *[
                ids[f"financialGraph{i}"]["componentRoot"]
                for i in range(len(sources))
            ],
        ]
    )
    expected_dependencies.update(researchColumns=derived, schema=derived, coverage=derived)
    require(
        all(ids[k]["dependencies"] == v for k, v in expected_dependencies.items()),
        "DATASET_GRAPH",
        "Typed dependencies differ from the registered closure",
    )
    require(
        ids["marketDataset"]["semanticRoots"]["marketRoot"]
        == value["roots"]["marketRoot"]
        and ids["researchColumns"]["semanticRoots"]["financialDatasetRoot"]
        == value["roots"]["financialDatasetRoot"],
        "DATASET_ROOT",
        "Manifest semantic roots differ from components",
    )
    if value["version"] == GRAPH_VERSION:
        require(
            ids["marketOrigin"]["semanticRoots"]["marketRoot"]
            == value["roots"]["marketRoot"],
            "DATASET_ROOT",
            "Origin and derived market root disagree",
        )
    for i, source in enumerate(sources):
        financial, prepared = ids[f"financialInput{i}"], ids[f"financialGraph{i}"]
        require(
            prepared["semanticRoots"]["packRoot"]
            == financial["semanticRoots"]["packRoot"]
            and prepared["semanticRoots"]["preparedRoot"] == source["preparedRoot"],
            "DATASET_ROOT",
            "Prepared/source references disagree",
        )
    return value
