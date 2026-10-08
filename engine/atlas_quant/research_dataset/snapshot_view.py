"""Explicit lossless views of a complete legacy snapshot, with retained origin.

This is an offline source adapter. The caller supplies authenticated source pins;
these bytes alone establish neither owner authorization nor provider authority.
"""

from copy import deepcopy
from dataclasses import dataclass

from .. import bundle
from ..financial_statements.package import _private_content
from ..provider import _records, _validate_panel, canonical_hash
from ..runner_artifacts import restore_input
from .codec import decode, digest, encode, keys, require, sha
from .manifest import scope
from .profile import DEFAULT_PROFILE, VIEW_PROFILE_ID, VIEW_VERSION, check_profile

ORIGIN_FORMAT = "atlas.quant.snapshot_scope_origin"


@dataclass(frozen=True)
class SnapshotMarketView:
    market_bytes: bytes
    origin_bytes: bytes

    @property
    def receipt(self):
        return decode(self.origin_bytes, len(self.origin_bytes))["receipt"]


def _transform(value, source_scope, profile):
    keys(value, {"kind", "version", "mode", "symbols", "start", "end"})
    require(
        value["kind"] == "snapshot_scope_view"
        and type(value["version"]) is int
        and value["version"] == 1
        and isinstance(value["mode"], str)
        and value["mode"] in {"exact", "explicit_subset"},
        "DATASET_VIEW",
        "Unknown explicit snapshot transformation",
    )
    target = {key: deepcopy(value[key]) for key in ("symbols", "start", "end")}
    scope(target, profile)
    require(
        set(target["symbols"]) <= set(source_scope["symbols"])
        and source_scope["start"]
        <= target["start"]
        <= target["end"]
        <= source_scope["end"],
        "DATASET_VIEW_SCOPE",
        "Requested view extends outside the original snapshot scope",
    )
    require(
        (target == source_scope) == (value["mode"] == "exact"),
        "DATASET_VIEW_SCOPE",
        "Exact and proper-subset modes must describe the visible selection",
    )
    return target


def _source(snapshot_bytes, manifest_bytes, bundle_id, snapshot_sha256, profile):
    require(
        isinstance(snapshot_bytes, bytes)
        and 0 < len(snapshot_bytes) <= profile.market_bytes
        and isinstance(manifest_bytes, bytes)
        and 0 < len(manifest_bytes) <= bundle.MANIFEST_LIMIT
        and len(snapshot_bytes) + len(manifest_bytes) + profile.manifest_bytes
        < profile.total_bytes,
        "DATASET_BUDGET",
        "Complete original snapshot and manifest exceed the source budget",
    )
    manifest = bundle.validate_manifest(manifest_bytes, digest(bundle_id))
    require(
        manifest["kind"] == "forecast",
        "DATASET_VIEW_SOURCE",
        "A forecast snapshot is required",
    )
    descriptor = manifest["documents"]["snapshot"]
    require(
        sha(snapshot_bytes) == digest(snapshot_sha256) == descriptor["sha256"]
        and len(snapshot_bytes) == descriptor["byteLength"],
        "DATASET_VIEW_SOURCE",
        "Original snapshot differs from its pinned document identity",
    )
    # The old codec determines the bytes actually stored in an old bundle. We do
    # not pass the new market or typed financial data through that codec.
    snapshot = bundle.decode(snapshot_bytes)
    require(
        bundle.encode(snapshot) == snapshot_bytes,
        "DATASET_VIEW_SOURCE",
        "Original snapshot is not the declared legacy canonical document",
    )
    keys(
        snapshot,
        {
            "schemaVersion",
            "rows",
            "provenance",
            "sourceDataFingerprint",
            "dataFingerprint",
            "fingerprintVersion",
        },
    )
    require(
        type(snapshot["schemaVersion"]) is int
        and snapshot["schemaVersion"] == 1
        and snapshot["fingerprintVersion"] == "research_input_v1"
        and isinstance(snapshot["rows"], list)
        and 1 <= len(snapshot["rows"]) <= profile.max_rows,
        "DATASET_VIEW_SOURCE",
        "Only a bounded non-financial legacy snapshot is eligible",
    )
    skeleton = bundle.document_skeleton(manifest, "snapshot")
    expected = deepcopy(snapshot)
    expected["rows"] = {"__bundle_collection__": "snapshotRows"}
    require(
        encode(skeleton) == encode(expected)
        and next(c for c in manifest["collections"] if c["id"] == "snapshotRows")[
            "rowCount"
        ]
        == len(snapshot["rows"]),
        "DATASET_VIEW_SOURCE",
        "Original snapshot layout or row count differs from the manifest",
    )
    source_collection = next(
        c for c in manifest["collections"] if c["id"] == "snapshotRows"
    )
    for part in source_collection["chunks"]:
        chunk = bundle.encode(
            snapshot["rows"][part["start"] : part["start"] + part["count"]]
        )
        require(
            len(chunk) == part["byteLength"] and sha(chunk) == part["sha256"],
            "DATASET_VIEW_SOURCE",
            "Original snapshot rows differ from their source chunk descriptors",
        )
    for name in manifest["documents"]:
        _private_content(bundle.document_skeleton(manifest, name))
    forecast = bundle.document_skeleton(manifest, "forecast")
    strategy = forecast.get("sourceStrategy")
    require(
        isinstance(strategy, dict), "DATASET_VIEW_SOURCE", "Original strategy is absent"
    )
    require(
        forecast.get("dataFingerprint") == manifest["dataFingerprint"]
        and snapshot["dataFingerprint"] == manifest["dataFingerprint"],
        "DATASET_VIEW_SOURCE",
        "Original forecast and snapshot fingerprints disagree",
    )
    meta, rows = snapshot["provenance"], snapshot["rows"]
    require(
        isinstance(meta, dict) and all(isinstance(row, dict) for row in rows),
        "DATASET_VIEW_SOURCE",
        "Original rows or provenance are invalid",
    )
    require(
        isinstance(meta.get("externalFields", {}), dict),
        "DATASET_VIEW_SOURCE",
        "Original external field metadata are invalid",
    )
    require(
        not any(k.startswith("model_fin_") for row in rows for k in row)
        and not any(
            k in meta
            for k in (
                "financialDatasetRoot",
                "financialCompositionVersion",
                "financialInputs",
                "financialSourceCommitment",
            )
        )
        and not any(k.startswith("model_fin_") for k in meta.get("externalFields", {})),
        "DATASET_VIEW_FINANCIAL_SOURCE",
        "Financial source data require their typed closure, not a legacy adapter",
    )
    _private_content(snapshot)
    # This runs before any view filtering. It validates all original input rows,
    # full calendar, provider fingerprint and the research precision fingerprint.
    frame, _ = restore_input(
        strategy, snapshot, manifest["dataFingerprint"], max_bytes=profile.market_bytes
    )
    columns = set(rows[0])
    require(
        all(set(row) == columns for row in rows) and columns == set(frame.columns),
        "DATASET_COLUMNS",
        "A source view cannot silently derive, drop or fill columns",
    )
    universe = strategy.get("universe", {})
    source_scope = {
        key: deepcopy(universe.get(key)) for key in ("symbols", "start", "end")
    }
    require(
        isinstance(source_scope["symbols"], list),
        "DATASET_VIEW_SOURCE",
        "Original universe is absent",
    )
    original_symbols = source_scope["symbols"]
    require(
        all(isinstance(s, str) for s in original_symbols)
        and len(original_symbols) == len(set(original_symbols)),
        "DATASET_VIEW_SOURCE",
        "Duplicate original securities",
    )
    source_scope["symbols"] = sorted(original_symbols)
    scope(source_scope, profile)
    require(
        all(
            isinstance(row["ts_code"], str)
            and isinstance(row["trade_date"], str)
            and row["ts_code"] in source_scope["symbols"]
            and source_scope["start"] <= row["trade_date"] <= source_scope["end"]
            for row in rows
        ),
        "DATASET_VIEW_SOURCE",
        "Original rows extend outside their original strategy scope",
    )
    dates = meta.get("tradingDates")
    require(
        isinstance(dates, list)
        and dates
        and all(isinstance(d, str) for d in dates)
        and dates == sorted(set(dates))
        and source_scope["start"] <= dates[0] <= dates[-1] <= source_scope["end"],
        "DATASET_CALENDAR",
        "Original complete session list is invalid",
    )
    date_set = set(dates)
    require(
        all(row["trade_date"] in date_set for row in rows),
        "DATASET_CALENDAR",
        "Original observed rows include a date absent from the complete calendar",
    )
    for key in ("symbols", "start", "end"):
        if key in meta:
            value = meta[key]
            if key == "symbols":
                require(
                    isinstance(value, list)
                    and all(isinstance(x, str) for x in value)
                    and len(value) == len(set(value)),
                    "DATASET_VIEW_SOURCE",
                    "Original provenance symbols are invalid",
                )
                value = sorted(value)
            require(
                value == source_scope[key],
                "DATASET_VIEW_SOURCE",
                "Original provenance disagrees with its strategy scope",
            )
    return snapshot, strategy, source_scope


def derive_market_snapshot_view(
    snapshot_bytes,
    bundle_manifest_bytes,
    transform,
    *,
    expected_bundle_id,
    expected_snapshot_sha256,
    profile=DEFAULT_PROFILE,
):
    check_profile(profile)
    snapshot, strategy, source_scope = _source(
        snapshot_bytes,
        bundle_manifest_bytes,
        expected_bundle_id,
        expected_snapshot_sha256,
        profile,
    )
    target = _transform(transform, source_scope, profile)
    symbols = set(target["symbols"])
    rows = [
        deepcopy(row)
        for row in snapshot["rows"]
        if row["ts_code"] in symbols
        and target["start"] <= row["trade_date"] <= target["end"]
    ]
    require(
        rows and {row["ts_code"] for row in rows} == symbols,
        "DATASET_VIEW_EMPTY",
        "Every explicitly selected security needs an observed row in the selected interval",
    )
    source_meta = snapshot["provenance"]
    dates = [
        d for d in source_meta["tradingDates"] if target["start"] <= d <= target["end"]
    ]
    require(
        dates, "DATASET_CALENDAR", "Selected interval has no original official sessions"
    )
    meta = deepcopy(source_meta)
    frame = _validate_panel(
        {"universe": target}, rows, external_fields=meta.get("externalFields")
    )
    require(
        set(rows[0]) == set(frame.columns),
        "DATASET_COLUMNS",
        "Projection changed input columns",
    )
    fingerprint = canonical_hash(_records(frame))
    derivation = {
        "kind": "snapshot_scope_view",
        "version": 1,
        "mode": transform["mode"],
        "sourceBundleId": expected_bundle_id,
        "sourceSnapshotSha256": expected_snapshot_sha256,
        "sourceDataFingerprint": snapshot["sourceDataFingerprint"],
        "sourceResearchFingerprint": snapshot["dataFingerprint"],
        "targetScope": target,
    }
    meta.update(deepcopy(target))
    meta.update(
        tradingDates=dates,
        rows=len(rows),
        dataFingerprint=fingerprint,
        marketDerivation=derivation,
    )
    market = {"schemaVersion": 1, "rows": rows, "provenance": meta}
    market_bytes = encode(market)
    require(
        len(market_bytes) <= profile.market_bytes,
        "DATASET_BUDGET",
        "Derived market bytes exceed the profile",
    )
    receipt = {
        "sourceScope": source_scope,
        "targetScope": target,
        "sourceRows": len(snapshot["rows"]),
        "selectedRows": len(rows),
        "removedRows": len(snapshot["rows"]) - len(rows),
        "sourceTradingDatesSha256": sha(encode(source_meta["tradingDates"])),
        "selectedTradingDatesSha256": sha(encode(dates)),
        "sourceDataFingerprint": snapshot["sourceDataFingerprint"],
        "sourceResearchFingerprint": snapshot["dataFingerprint"],
        "derivedDataFingerprint": fingerprint,
        "marketRoot": sha(market_bytes),
        "preservesRelativeRowOrder": True,
        "imputation": "none",
        "warmupExtension": "none",
    }
    origin = {
        "format": ORIGIN_FORMAT,
        "version": 1,
        "source": {
            "bundleId": expected_bundle_id,
            "manifestRawText": bundle_manifest_bytes.decode("utf-8"),
            "snapshotSha256": expected_snapshot_sha256,
            "snapshotRawText": snapshot_bytes.decode("utf-8"),
            "sourceStrategy": strategy,
            "sourceStrategySha256": bundle.sha(bundle.encode(strategy)),
        },
        "transform": deepcopy(transform),
        "receipt": receipt,
    }
    origin_bytes = encode(origin)
    require(
        len(origin_bytes) + len(market_bytes) + profile.manifest_bytes
        < profile.total_bytes,
        "DATASET_BUDGET",
        "Retained origin escaping overhead and market exceed the shared budget",
    )
    return SnapshotMarketView(market_bytes, origin_bytes)


def validate_snapshot_scope_origin(origin_bytes, *, profile=DEFAULT_PROFILE):
    check_profile(profile)
    origin = decode(origin_bytes, profile.total_bytes)
    keys(origin, {"format", "version", "source", "transform", "receipt"})
    require(
        origin["format"] == ORIGIN_FORMAT
        and type(origin["version"]) is int
        and origin["version"] == 1,
        "DATASET_VIEW_ORIGIN",
        "Unknown retained origin",
    )
    source = origin["source"]
    keys(
        source,
        {
            "bundleId",
            "manifestRawText",
            "snapshotSha256",
            "snapshotRawText",
            "sourceStrategy",
            "sourceStrategySha256",
        },
    )
    require(
        isinstance(source["manifestRawText"], str)
        and isinstance(source["snapshotRawText"], str),
        "DATASET_VIEW_ORIGIN",
        "Exact original UTF-8 bytes are required",
    )
    view = derive_market_snapshot_view(
        source["snapshotRawText"].encode("utf-8"),
        source["manifestRawText"].encode("utf-8"),
        origin["transform"],
        expected_bundle_id=source["bundleId"],
        expected_snapshot_sha256=source["snapshotSha256"],
        profile=profile,
    )
    require(
        view.origin_bytes == origin_bytes,
        "DATASET_VIEW_ORIGIN",
        "Source strategy or projection receipt differs from full recomputation",
    )
    return view
