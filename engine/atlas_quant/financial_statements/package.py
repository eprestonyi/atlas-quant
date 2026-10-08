"""Bounded, immutable financial input packages; no file or network side effects.

Hashes establish integrity, not document authenticity. Public callers may import
declarations; accepting reviewed proofs requires a trusted out-of-band decision.
Calendar authenticity is likewise the caller's responsibility, never inferred
from an uploaded ``kind='official'`` string.
"""

from dataclasses import asdict
import json
import math
import re

from .contracts import (
    ContractError,
    SourceRef,
    TradingCalendar,
    UnitEvidence,
    parse_date,
)
from .results import canonical_hash
from .unit_bindings import (
    DeclaredUnitBinding,
    DocumentUnitBinding,
    FixtureUnitBinding,
    GlobalUnitBinding,
    validate_unit_contract,
)

FORMAT = "atlas.quant.financial-input"
VERSION = 1
MAX_PACKAGE_BYTES = 24 * 1024 * 1024
MAX_SNAPSHOTS = 128
MAX_BINDINGS = 20000
MAX_CALENDAR_SESSIONS = 10000
SNAPSHOT_BODY_KEYS = frozenset(
    {
        "endpoint",
        "params",
        "fields",
        "rows",
        "retrievedAt",
        "sourceKind",
        "sourceProvider",
        "representation",
        "wireBytesAvailable",
        "wireNumericLexemesAvailable",
    }
)
METADATA_FIELDS = frozenset(
    {
        "ts_code",
        "ann_date",
        "f_ann_date",
        "end_date",
        "report_type",
        "comp_type",
        "update_flag",
    }
)
BINDING_TYPES = {
    cls.__name__: cls
    for cls in (
        DeclaredUnitBinding,
        DocumentUnitBinding,
        FixtureUnitBinding,
        GlobalUnitBinding,
    )
}
SENSITIVE_KEY = re.compile(
    r"^(token|serviceToken|api_key|password|authorization)$", re.I
)


def _encoded(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ContractError("financial package requires finite bounded JSON") from error


def _private_content(value, depth=0):
    if depth > 30:
        raise ContractError("financial package nesting exceeds its bound")
    if isinstance(value, dict):
        if any(not isinstance(k, str) or SENSITIVE_KEY.fullmatch(k) for k in value):
            raise ContractError(
                "financial package cannot contain credentials or non-string keys"
            )
        for item in value.values():
            _private_content(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _private_content(item, depth + 1)


def raw_input(
    snapshots,
    calendar,
    *,
    source_kind="provider",
    source_provider="TUSHARE_PRO",
    max_source_rows=20000,
    max_source_bytes=32 * 1024 * 1024
):
    """Return canonical raw input and its root, excluding all unit declarations."""
    if not isinstance(calendar, TradingCalendar):
        raise ContractError(
            "TradingCalendar required; a session list alone is insufficient"
        )
    if (
        len(calendar.sessions) > MAX_CALENDAR_SESSIONS
        or len(_encoded(asdict(calendar))) > 1024 * 1024
    ):
        raise ContractError("frozen calendar exceeds its session/byte budget")
    if (
        not isinstance(source_kind, str)
        or source_kind not in {"fixture", "provider"}
        or not isinstance(source_provider, str)
        or not 1 <= len(source_provider.strip()) <= 80
    ):
        raise ContractError("explicit bounded source kind/provider required")
    if (
        not isinstance(snapshots, (list, tuple))
        or not 1 <= len(snapshots) <= MAX_SNAPSHOTS
    ):
        raise ContractError("financial input requires 1–128 frozen table snapshots")
    normalized, seen, total_rows, total_bytes = [], set(), 0, 0
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_BODY_KEYS | {
            "id",
            "rowCount",
            "byteLength",
        }:
            raise ContractError("normalized snapshot structure is invalid")
        body = {key: snapshot[key] for key in SNAPSHOT_BODY_KEYS}
        _private_content(body)
        byte_length = len(_encoded(body))
        total_bytes += byte_length
        rows, fields, params = body["rows"], body["fields"], body["params"]
        if (
            not isinstance(rows, list)
            or not isinstance(fields, list)
            or not 1 <= len(fields) <= 256
        ):
            raise ContractError("snapshot requires bounded fields and row arrays")
        total_rows += len(rows)
        if total_rows > max_source_rows or total_bytes > max_source_bytes:
            raise ContractError("financial raw input exceeds source row/byte budget")
        if (
            any(not isinstance(f, str) or not 1 <= len(f) <= 80 for f in fields)
            or len(fields) != len(set(fields))
            or not METADATA_FIELDS.issubset(fields)
        ):
            raise ContractError(
                "snapshot financial metadata columns are missing or duplicated"
            )
        if not isinstance(body["endpoint"], str) or body["endpoint"] not in {
            "income",
            "balancesheet",
            "cashflow",
        }:
            raise ContractError("unknown statement endpoint")
        if (
            body["sourceKind"] != source_kind
            or body["sourceProvider"] != source_provider
        ):
            raise ContractError("snapshot source identity differs from frozen input")
        if (
            body["representation"] != "normalized_provider_table_snapshot"
            or body["wireBytesAvailable"] is not False
            or body["wireNumericLexemesAvailable"] is not False
        ):
            raise ContractError("normalized snapshots cannot claim raw wire identity")
        SourceRef(
            source_provider, "pending_hash_validation", body["retrievedAt"], source_kind
        )
        if not isinstance(params, dict) or set(params) - {
            "ts_code",
            "period",
            "ann_date",
            "start_date",
            "end_date",
            "report_type",
            "comp_type",
        }:
            raise ContractError("snapshot request parameters are unsupported")
        if not isinstance(params.get("ts_code"), str) or not re.fullmatch(
            r"\d{6}\.(SH|SZ)", params["ts_code"]
        ):
            raise ContractError("snapshot request needs an exact security")
        selectors = (
            int("period" in params)
            + int("ann_date" in params)
            + int("start_date" in params or "end_date" in params)
        )
        if selectors != 1 or ("start_date" in params) != ("end_date" in params):
            raise ContractError(
                "snapshot request requires exactly one complete date selector"
            )
        for key in ("period", "ann_date", "start_date", "end_date"):
            if key in params:
                parse_date(params[key])
        if "start_date" in params and params["start_date"] > params["end_date"]:
            raise ContractError("snapshot request date interval is reversed")
        for key in ("report_type", "comp_type"):
            if key in params and (
                not isinstance(params[key], str) or not 1 <= len(params[key]) <= 8
            ):
                raise ContractError("snapshot classification selector is invalid")
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(fields):
                raise ContractError(
                    "snapshot row must match its exact declared columns"
                )
            for value in row.values():
                if value is not None and (
                    not isinstance(value, (str, int, float, bool))
                    or isinstance(value, float)
                    and not math.isfinite(value)
                ):
                    raise ContractError("snapshot cells must be finite JSON scalars")
        if (
            snapshot["id"] != canonical_hash(body)
            or type(snapshot["rowCount"]) is not int
            or snapshot["rowCount"] != len(rows)
            or type(snapshot["byteLength"]) is not int
            or snapshot["byteLength"] != byte_length
        ):
            raise ContractError(
                "snapshot hash, row count or byte length does not match"
            )
        if snapshot["id"] in seen:
            raise ContractError("duplicate frozen snapshot identity")
        seen.add(snapshot["id"])
        normalized.append(json.loads(_encoded(snapshot)))
    raw = {
        "snapshots": sorted(normalized, key=lambda s: s["id"]),
        "calendar": asdict(calendar),
        "sourceKind": source_kind,
        "sourceProvider": source_provider,
    }
    return raw, canonical_hash(raw)


def _bindings_to_json(unit_contract, source_kind, *, trusted_unit_proofs):
    if not isinstance(trusted_unit_proofs, bool):
        raise ContractError("proof trust must be an explicit caller boolean")
    contract = validate_unit_contract(unit_contract, source_kind)
    if sum(map(len, contract.values())) > MAX_BINDINGS:
        raise ContractError("unit binding count exceeds its bound")
    result = {}
    for field, bindings in contract.items():
        result[field] = []
        for binding in bindings:
            if not trusted_unit_proofs and not isinstance(binding, DeclaredUnitBinding):
                raise ContractError(
                    "uploaded proofs require an out-of-band trusted reviewer; declarations are unverified"
                )
            result[field].append(
                {"type": type(binding).__name__, "value": asdict(binding)}
            )
        result[field].sort(key=canonical_hash)
    return result


def decode_bindings(encoded, source_kind, *, trusted_unit_proofs=False):
    if not isinstance(trusted_unit_proofs, bool):
        raise ContractError("proof trust must be an explicit caller boolean")
    if not isinstance(encoded, dict):
        raise ContractError("unit bindings must be a field mapping")
    contract, count = {}, 0
    try:
        for field, entries in encoded.items():
            if not isinstance(entries, list) or not entries:
                raise ContractError("unit bindings require nonempty arrays")
            contract[field] = []
            for entry in entries:
                count += 1
                if (
                    count > MAX_BINDINGS
                    or not isinstance(entry, dict)
                    or set(entry) != {"type", "value"}
                ):
                    raise ContractError("unit binding representation is invalid")
                cls = BINDING_TYPES.get(entry["type"])
                if cls is None or not isinstance(entry["value"], dict):
                    raise ContractError("unknown unit binding type")
                value = dict(entry["value"])
                evidence = value.pop("evidence")
                if not isinstance(evidence, dict) or evidence.get("scope") is not None:
                    raise ContractError("package bindings require unresolved evidence")
                binding = cls(evidence=UnitEvidence(**evidence), **value)
                if not trusted_unit_proofs and not isinstance(
                    binding, DeclaredUnitBinding
                ):
                    raise ContractError(
                        "uploaded proofs require an out-of-band trusted reviewer"
                    )
                contract[field].append(binding)
    except (TypeError, ValueError, KeyError) as error:
        if isinstance(error, ContractError):
            raise
        raise ContractError("unit binding representation is malformed") from error
    return validate_unit_contract(contract, source_kind)


def freeze_package(
    snapshots,
    calendar,
    strategy,
    selected_ids,
    unit_contract,
    *,
    announcement_start,
    source_kind="provider",
    source_provider="TUSHARE_PRO",
    scope="consolidated",
    flow_basis="ytd",
    unit_policy="verified_only",
    trusted_unit_proofs=False
):
    """Freeze inputs; this neither computes states nor authenticates documents."""
    from .prepare import validate_selection

    selected_ids = tuple(selected_ids)
    validate_selection(
        strategy,
        selected_ids,
        calendar,
        announcement_start=announcement_start,
        source_kind=source_kind,
        scope=scope,
        flow_basis=flow_basis,
        unit_policy=unit_policy,
    )
    raw, root = raw_input(
        snapshots, calendar, source_kind=source_kind, source_provider=source_provider
    )
    bindings = _bindings_to_json(
        unit_contract, source_kind, trusted_unit_proofs=trusted_unit_proofs
    )
    for entries in bindings.values():
        for entry in entries:
            if (
                entry["type"] == "DeclaredUnitBinding"
                and entry["value"]["input_root"] != root
            ):
                raise ContractError(
                    "declaration scope does not match the exact raw input root"
                )
    package = {
        "format": FORMAT,
        "version": VERSION,
        "inputRoot": root,
        "raw": raw,
        "selection": {
            "universe": dict(strategy["universe"]),
            "selectedStates": list(selected_ids),
            "announcementStart": announcement_start,
            "scope": scope,
            "flowBasis": flow_basis,
        },
        "unitPolicy": unit_policy,
        "bindings": bindings,
    }
    package["packRoot"] = canonical_hash(package)
    _private_content(package)
    if len(_encoded(package)) > MAX_PACKAGE_BYTES:
        raise ContractError("financial input package exceeds 24 MiB")
    return json.loads(_encoded(package))


def validate_package(package, *, trusted_unit_proofs=False):
    if not isinstance(package, dict) or set(package) != {
        "format",
        "version",
        "inputRoot",
        "packRoot",
        "raw",
        "selection",
        "unitPolicy",
        "bindings",
    }:
        raise ContractError("financial package structure is invalid")
    _private_content(package)
    if len(_encoded(package)) > MAX_PACKAGE_BYTES:
        raise ContractError("financial input package exceeds 24 MiB")
    if (
        package["format"] != FORMAT
        or type(package["version"]) is not int
        or package["version"] != VERSION
    ):
        raise ContractError("financial package format/version is unsupported")
    if package["packRoot"] != canonical_hash(
        {k: v for k, v in package.items() if k != "packRoot"}
    ):
        raise ContractError("financial package root mismatch")
    raw, selection = package["raw"], package["selection"]
    if not isinstance(raw, dict) or set(raw) != {
        "snapshots",
        "calendar",
        "sourceKind",
        "sourceProvider",
    }:
        raise ContractError("raw input structure is invalid")
    if not isinstance(selection, dict) or set(selection) != {
        "universe",
        "selectedStates",
        "announcementStart",
        "scope",
        "flowBasis",
    }:
        raise ContractError("financial selection structure is invalid")
    try:
        calendar = TradingCalendar(**raw["calendar"])
    except (TypeError, ValueError) as error:
        raise ContractError("frozen calendar contract is invalid") from error
    bindings = decode_bindings(
        package["bindings"], raw["sourceKind"], trusted_unit_proofs=trusted_unit_proofs
    )
    rebuilt = freeze_package(
        raw["snapshots"],
        calendar,
        {"universe": selection["universe"]},
        selection["selectedStates"],
        bindings,
        announcement_start=selection["announcementStart"],
        source_kind=raw["sourceKind"],
        source_provider=raw["sourceProvider"],
        scope=selection["scope"],
        flow_basis=selection["flowBasis"],
        unit_policy=package["unitPolicy"],
        trusted_unit_proofs=trusted_unit_proofs,
    )
    if rebuilt != package:
        raise ContractError("financial package is not in its canonical normalized form")
    return calendar, bindings


def decode_package(raw, *, trusted_unit_proofs=False):
    if not isinstance(raw, bytes) or len(raw) > MAX_PACKAGE_BYTES:
        raise ContractError("financial package bytes exceed the input budget")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError("duplicate JSON key in financial package")
            result[key] = value
        return result

    try:
        package = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, RecursionError) as error:
        raise ContractError("financial package JSON is invalid") from error
    validate_package(package, trusted_unit_proofs=trusted_unit_proofs)
    return package


def prepare_package(package, *, trusted_unit_proofs=False, budget=None):
    from .prepare import prepare_statement_states

    calendar, bindings = validate_package(
        package, trusted_unit_proofs=trusted_unit_proofs
    )
    raw, selection = package["raw"], package["selection"]
    result = prepare_statement_states(
        raw["snapshots"],
        {"universe": selection["universe"]},
        selection["selectedStates"],
        calendar,
        bindings,
        announcement_start=selection["announcementStart"],
        source_kind=raw["sourceKind"],
        source_provider=raw["sourceProvider"],
        scope=selection["scope"],
        flow_basis=selection["flowBasis"],
        unit_policy=package["unitPolicy"],
        budget=budget,
    )
    result.provenance["packRoot"] = package["packRoot"]
    return result
