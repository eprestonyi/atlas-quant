"""Offline typed closure construction, with real financial preparation."""

from dataclasses import dataclass, replace
from copy import deepcopy

from ..financial_runner.trust import (
    _registry,
    calendar_scope,
    resolve_package_registry,
)
from ..financial_statements.dataset import (
    DatasetBudget,
    FinancialDatasetResult,
    compose_financial_dataset,
)
from ..financial_statements.package import _private_content
from ..financial_statements.prepare import _safe_rows
from ..provider import _validate_panel
from .codec import decode, digest, encode, keys, require, sha, uuid
from .manifest import component_root, scope, validate_manifest
from .profile import (
    DEFAULT_PROFILE,
    FORMAT,
    PROFILE_ID,
    VERSION,
    VIEW_VERSION,
    VIEW_PROFILE_ID,
    check_profile,
)


@dataclass(frozen=True)
class FinancialSource:
    package_bytes: bytes
    prepared_root: str
    calendar_ref: str
    proof_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatasetPublication:
    manifest_bytes: bytes
    result: FinancialDatasetResult

    @property
    def dataset_root(self):
        return sha(self.manifest_bytes)


def registry_payload(authorized_registry, required_refs, profile):
    """Out-of-band authority: these exact bytes are never inferred from uploads."""
    require(
        isinstance(authorized_registry, dict)
        and set(authorized_registry) == required_refs,
        "DATASET_REGISTRY",
        "An exact externally authorized registry closure is required",
    )
    total, entries = 0, []
    for ref in sorted(required_refs):
        uuid(ref)
        raw = authorized_registry[ref]
        record = decode(raw, profile.registry_bytes)
        _private_content(record)
        total += len(raw)
        require(
            total <= profile.registries_bytes,
            "DATASET_BUDGET",
            "Registry total budget exceeded",
        )
        # rawText retains the complete original canonical byte identity; later
        # restore must match against independent authorized bytes again.
        entries.append(
            {
                "ref": ref,
                "sha256": sha(raw),
                "byteLength": len(raw),
                "rawText": raw.decode("utf-8"),
            }
        )
    return encode({"entries": entries})


def _prepare(
    scope_value,
    market_bytes,
    sources,
    authorized_registry,
    market_calendar_ref,
    profile,
):
    check_profile(profile)
    scope(scope_value, profile)
    uuid(market_calendar_ref)
    require(
        isinstance(sources, (tuple, list))
        and 1 <= len(sources) <= profile.max_inputs
        and all(isinstance(s, FinancialSource) for s in sources),
        "DATASET_SOURCE",
        "Explicit bounded financial sources are required",
    )
    require(
        5 + 2 * len(sources) <= min(profile.max_components, profile.max_parts),
        "DATASET_BUDGET",
        "Known component count exceeds component/part budget",
    )
    require(
        isinstance(market_bytes, bytes) and len(market_bytes) <= profile.market_bytes,
        "DATASET_BUDGET",
        "Frozen market input exceeds its budget",
    )
    known = len(market_bytes)
    refs = {market_calendar_ref}
    for source in sources:
        digest(source.prepared_root)
        uuid(source.calendar_ref)
        require(
            isinstance(source.package_bytes, bytes)
            and isinstance(source.proof_refs, tuple)
            and len(source.proof_refs) <= 256,
            "DATASET_SOURCE",
            "Financial source bytes/proof references are invalid",
        )
        for ref in source.proof_refs:
            uuid(ref)
        require(
            list(source.proof_refs) == sorted(set(source.proof_refs)),
            "DATASET_SOURCE",
            "Proof references must be sorted and unique",
        )
        refs.update((source.calendar_ref, *source.proof_refs))
        known += len(source.package_bytes)
    require(
        known - len(market_bytes) <= profile.package_bytes
        and known + profile.manifest_bytes < profile.total_bytes,
        "DATASET_BUDGET",
        "Known source bytes exceed the closure budget",
    )
    registry_raw = registry_payload(authorized_registry, refs, profile)
    require(
        known + len(registry_raw) + profile.manifest_bytes < profile.total_bytes,
        "DATASET_BUDGET",
        "Known registry/source bytes exceed the closure budget",
    )
    market = decode(market_bytes, profile.market_bytes)
    keys(market, {"schemaVersion", "rows", "provenance"})
    require(
        type(market["schemaVersion"]) is int and market["schemaVersion"] == 1,
        "DATASET_MARKET",
        "Unsupported market dataset version",
    )
    _private_content(market)
    meta, rows = market["provenance"], market["rows"]
    require(
        isinstance(meta, dict)
        and isinstance(rows, list)
        and 1 <= len(rows) <= profile.max_rows,
        "DATASET_MARKET",
        "Market rows/provenance missing or oversized",
    )
    require(
        all(isinstance(r, dict) for r in rows),
        "DATASET_MARKET",
        "Market rows must be objects",
    )
    input_columns = set(rows[0])
    require(
        all(set(row) == input_columns for row in rows)
        and not any(k.startswith("model_fin_") for k in input_columns),
        "DATASET_COLUMNS",
        "Rows need exact common columns, with no prefilled financial states",
    )
    # Existing panel validator defines supported numerical columns. Reject its
    # projected-away columns explicitly rather than losing them silently.
    frame = _validate_panel(
        {"universe": scope_value}, rows, external_fields=meta.get("externalFields")
    )
    require(
        input_columns == set(frame.columns),
        "DATASET_COLUMNS",
        "Unregistered market columns would be discarded",
    )
    calendar = _registry(authorized_registry[market_calendar_ref], "calendar")
    require(
        calendar["scope"] == calendar_scope(calendar["payload"]),
        "DATASET_CALENDAR",
        "Market calendar registry scope mismatch",
    )
    dates = [
        d
        for d in calendar["payload"]["sessions"]
        if scope_value["start"] <= d <= scope_value["end"]
    ]
    require(
        calendar["payload"]["complete"] is True
        and calendar["payload"]["coverage_start"] <= scope_value["start"]
        and calendar["payload"]["coverage_end"] >= scope_value["end"]
        and dates
        and meta.get("tradingDates") == dates,
        "DATASET_CALENDAR",
        "Market sessions must match the externally authorized calendar exactly",
    )
    for key in ("symbols", "start", "end"):
        if key in meta:
            value = meta[key]
            if key == "symbols":
                # Acquisition order is retained in the frozen source bytes.
                # Universe membership is unordered, but duplicates are invalid.
                require(
                    isinstance(value, list)
                    and all(isinstance(symbol, str) for symbol in value)
                    and len(value) == len(set(value)),
                    "DATASET_SCOPE",
                    "Market provenance symbols must be unique strings",
                )
                value = sorted(value)
            require(
                value == scope_value[key],
                "DATASET_SCOPE",
                "Market provenance scope differs",
            )
    trusted = []
    for source in sources:
        value = resolve_package_registry(
            source.package_bytes,
            authorized_registry[source.calendar_ref],
            [authorized_registry[ref] for ref in source.proof_refs],
        )
        # Financial source bytes have their own canonical contract. No decimal
        # normalization or bundle canonicalizer may rewrite them.
        require(
            encode(value.package) == source.package_bytes,
            "DATASET_CANONICAL",
            "Financial package must retain its canonical bytes",
        )
        trusted.append(value)
    ordering = sorted(range(len(sources)), key=lambda i: trusted[i].package["packRoot"])
    sources = [sources[i] for i in ordering]
    trusted = [trusted[i] for i in ordering]
    budget = DatasetBudget(
        max_inputs=profile.max_inputs,
        max_input_bytes=profile.package_bytes,
        max_output_bytes=profile.joined_bytes,
        max_rows=profile.max_rows,
        max_total_bytes=profile.total_bytes
        - len(registry_raw)
        - profile.manifest_bytes,
    )
    result = compose_financial_dataset(
        {"universe": scope_value},
        market,
        [s.package_bytes for s in sources],
        trusted_unit_proofs=any(t.reviewed_proofs for t in trusted),
        budget=budget,
    )
    for source, artifact in zip(sources, result.financial_artifacts):
        require(
            source.prepared_root == artifact.prepared.provenance["preparedRoot"],
            "DATASET_PREPARATION",
            "Recomputed preparation differs from the pinned source",
        )
    return sources, registry_raw, result


def prepared_payload(artifact):
    p = artifact.prepared
    return {
        "panel": _safe_rows(p.panel),
        "provenance": p.provenance,
        "stateEvents": p.state_events,
        "assignments": p.assignments,
        "coverage": p.coverage,
    }


def _compose_dataset_components(
    scope_value,
    market_bytes,
    financial_sources,
    authorized_registry,
    write_part,
    *,
    market_calendar_ref,
    profile=DEFAULT_PROFILE,
    origin_view=None,
):
    """Return a complete manifest only after every bounded callback succeeds.

    write_part(component_id, ordinal, raw_bytes) must stage privately. The caller
    publishes only after the returned manifest is durable. No network or fit.
    """
    child_profile = profile
    if origin_view is not None:
        origin_parts = (
            len(origin_view.origin_bytes) + profile.part_bytes - 1
        ) // profile.part_bytes
        require(
            profile.total_bytes > len(origin_view.origin_bytes)
            and profile.max_parts > origin_parts
            and profile.max_components > 1,
            "DATASET_BUDGET",
            "Origin exhausts the parent budget before financial preparation",
        )
        child_profile = replace(
            profile,
            total_bytes=profile.total_bytes - len(origin_view.origin_bytes),
            max_parts=profile.max_parts - origin_parts,
            max_components=profile.max_components - 1,
        )
    sources, registry_raw, result = _prepare(
        scope_value,
        market_bytes,
        financial_sources,
        authorized_registry,
        market_calendar_ref,
        child_profile,
    )
    payloads, components = [], []
    total, count = 0, 0

    def add(name, kind, raw, roots, dependencies):
        nonlocal total, count
        require(
            isinstance(raw, bytes) and raw, "DATASET_BYTES", "Component bytes missing"
        )
        needed_parts = (len(raw) + profile.part_bytes - 1) // profile.part_bytes
        require(
            total + len(raw) + profile.manifest_bytes <= profile.total_bytes
            and count + needed_parts <= profile.max_parts,
            "DATASET_BUDGET",
            "Known payload exceeds remaining bytes/parts before descriptor allocation",
        )
        parts = [
            {
                "ordinal": i,
                "byteLength": len(raw[offset : offset + profile.part_bytes]),
                "sha256": sha(raw[offset : offset + profile.part_bytes]),
            }
            for i, offset in enumerate(range(0, len(raw), profile.part_bytes))
        ]
        total += len(raw)
        count += len(parts)
        require(
            total + profile.manifest_bytes <= profile.total_bytes
            and count <= profile.max_parts,
            "DATASET_BUDGET",
            "Complete source/derived closure exceeds profile",
        )
        item = {
            "componentId": name,
            "type": kind,
            "version": 1,
            "semanticRoots": roots,
            "encoding": "raw_bytes",
            "payloadSha256": sha(raw),
            "byteLength": len(raw),
            "dependencies": sorted(dependencies),
            "parts": parts,
        }
        item["componentRoot"] = component_root(item)
        components.append(item)
        payloads.append((name, raw))
        return item["componentRoot"]

    registry_root = add("registryEvidence", "registry_evidence", registry_raw, {}, [])
    market_dependencies = [registry_root]
    if origin_view is not None:
        receipt = origin_view.receipt
        require(
            receipt["marketRoot"] == result.provenance["marketRoot"],
            "DATASET_ROOT",
            "Origin market bytes disagree with composition",
        )
        origin = decode(origin_view.origin_bytes, profile.total_bytes)
        market_dependencies.append(
            add(
                "marketOrigin",
                "snapshot_scope_origin",
                origin_view.origin_bytes,
                {
                    "sourceBundleId": origin["source"]["bundleId"],
                    "sourceSnapshotSha256": origin["source"]["snapshotSha256"],
                    "marketRoot": receipt["marketRoot"],
                },
                [],
            )
        )
    market_root = add(
        "marketDataset",
        "market_dataset",
        market_bytes,
        {"marketRoot": result.provenance["marketRoot"]},
        market_dependencies,
    )
    prepared_roots, source_refs = [], []
    for i, (source, artifact) in enumerate(zip(sources, result.financial_artifacts)):
        input_root = add(
            f"financialInput{i}",
            "financial_input",
            source.package_bytes,
            {
                "inputRoot": artifact.package["inputRoot"],
                "packRoot": artifact.package["packRoot"],
            },
            [registry_root],
        )
        prepared_roots.append(
            add(
                f"financialPrepared{i}",
                "financial_prepared",
                encode(prepared_payload(artifact)),
                {
                    "packRoot": artifact.package["packRoot"],
                    "preparedRoot": source.prepared_root,
                    "calendarRoot": artifact.summary["calendarRoot"],
                },
                [input_root],
            )
        )
        source_refs.append(
            {
                "componentId": f"financialInput{i}",
                "calendarRef": source.calendar_ref,
                "proofRefs": list(source.proof_refs),
                "preparedRoot": source.prepared_root,
            }
        )
    dependencies = [market_root, *prepared_roots]
    joined = encode(result.to_dataset())
    require(
        len(joined) <= profile.joined_bytes,
        "DATASET_BUDGET",
        "Joined data exceeds profile",
    )
    add(
        "researchRows",
        "research_rows",
        joined,
        {"financialDatasetRoot": result.provenance["financialDatasetRoot"]},
        dependencies,
    )
    add(
        "schema",
        "dataset_schema",
        encode(
            {
                "columns": list(result.data.columns),
                "externalFields": result.provenance["externalFields"],
                "calendarSessions": result.provenance["tradingDates"],
            }
        ),
        {},
        dependencies,
    )
    add(
        "coverage",
        "dataset_coverage",
        encode(
            {
                "marketRows": len(result.data),
                "observedSymbols": sorted(result.data.ts_code.unique().tolist()),
                "financial": [a.summary for a in result.financial_artifacts],
            }
        ),
        {},
        dependencies,
    )
    manifest = {
        "format": FORMAT,
        "version": VIEW_VERSION if origin_view is not None else VERSION,
        "profile": VIEW_PROFILE_ID if origin_view is not None else PROFILE_ID,
        "scope": deepcopy(scope_value),
        "marketCalendarRef": market_calendar_ref,
        "financialSources": source_refs,
        "components": components,
        "roots": {
            k: result.provenance[k] for k in ("marketRoot", "financialDatasetRoot")
        },
    }
    raw_manifest = encode(manifest)
    validate_manifest(raw_manifest, profile=profile)
    # All allocations, count/byte budgets and identities are checked before the
    # first externally visible staging callback. Failed writes return no manifest.
    for name, raw in payloads:
        for ordinal, offset in enumerate(range(0, len(raw), profile.part_bytes)):
            write_part(name, ordinal, raw[offset : offset + profile.part_bytes])
    return DatasetPublication(raw_manifest, result)


def compose_dataset_components(
    scope_value,
    market_bytes,
    financial_sources,
    authorized_registry,
    write_part,
    *,
    market_calendar_ref,
    profile=DEFAULT_PROFILE,
):
    """Compose the unchanged version-1 direct market-input profile."""
    return _compose_dataset_components(
        scope_value,
        market_bytes,
        financial_sources,
        authorized_registry,
        write_part,
        market_calendar_ref=market_calendar_ref,
        profile=profile,
    )


def compose_snapshot_dataset_components(
    view,
    financial_sources,
    authorized_registry,
    write_part,
    *,
    market_calendar_ref,
    profile=DEFAULT_PROFILE,
):
    """Compose version 2 only after revalidating its complete retained origin."""
    from .snapshot_view import SnapshotMarketView, validate_snapshot_scope_origin

    check_profile(profile)
    require(
        isinstance(view, SnapshotMarketView),
        "DATASET_VIEW",
        "An explicit snapshot market view is required",
    )
    checked = validate_snapshot_scope_origin(view.origin_bytes, profile=profile)
    require(
        checked.market_bytes == view.market_bytes,
        "DATASET_VIEW_ORIGIN",
        "Supplied market bytes differ from origin recomputation",
    )
    return _compose_dataset_components(
        checked.receipt["targetScope"],
        checked.market_bytes,
        financial_sources,
        authorized_registry,
        write_part,
        market_calendar_ref=market_calendar_ref,
        profile=profile,
        origin_view=checked,
    )
