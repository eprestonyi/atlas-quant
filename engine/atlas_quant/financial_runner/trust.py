"""Exact frozen-registry matching before the core's trusted-caller boundary.

This module has no I/O. The caller obtains owner-authorized immutable registry
bytes through the lease-bound client; payload hashes are not self-authentication.
"""

from copy import deepcopy
from dataclasses import dataclass

from ..financial_statements.contracts import FIELDS, TradingCalendar, UnitEvidence
from ..financial_statements.package import (
    MAX_BINDINGS,
    MAX_SNAPSHOTS,
    decode_package,
    freeze_package,
    validate_package,
)
from ..financial_statements.store import build_store
from ..financial_statements.unit_bindings import (
    DeclaredUnitBinding,
    normalized_row_hash,
)
from .protocol import (
    KINDS,
    PACKAGE_BYTES,
    REGISTRY_BYTES,
    REGISTRIES_BYTES,
    META_BYTES,
    decode,
    digest,
    encode,
    fail,
    identifier,
    sha,
)


@dataclass(frozen=True)
class TrustedInput:
    package: dict
    calendar: TradingCalendar
    bindings: dict
    reviewed_proofs: bool
    calendar_root: str


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        fail("FINANCIAL_TRUST", "冻结授权证据的结构不匹配。")


def calendar_scope(payload):
    _object(
        payload,
        {
            "sessions",
            "coverage_start",
            "coverage_end",
            "complete",
            "evidence_reference",
            "kind",
        },
    )
    try:
        calendar = TradingCalendar(**payload)
    except (TypeError, ValueError):
        fail("CALENDAR_REGISTRY_MISMATCH", "授权日历不是完整的冻结日历对象。")
    return {"calendarRoot": build_store([], calendar).calendar_evidence.root}


def proof_scope(payload):
    _object(payload, {"type", "value"})
    if not isinstance(payload["value"], dict):
        fail("FINANCIAL_TRUST", "单位证明缺少完整范围。")
    return {
        key: deepcopy(value)
        for key, value in payload["value"].items()
        if key != "evidence"
    }


def _check_bytes(raw, descriptor, limit):
    if (
        not isinstance(descriptor, dict)
        or not isinstance(raw, bytes)
        or type(descriptor.get("byteLength")) is not int
        or not 1 <= descriptor["byteLength"] <= limit
        or len(raw) != descriptor["byteLength"]
        or sha(raw) != digest(descriptor.get("sha256"))
    ):
        fail("FINANCIAL_INTEGRITY", "冻结输入或授权证据的完整字节身份不匹配。")


def _registry(raw, kind):
    record = decode(raw, limit=REGISTRY_BYTES)
    _object(record, {"kind", "payload", "registryVersion", "evidenceLevel", "scope"})
    if (
        record["kind"] != kind
        or type(record["registryVersion"]) is not int
        or record["registryVersion"] < 1
        or not isinstance(record["evidenceLevel"], str)
        or not 1 <= len(record["evidenceLevel"]) <= 100
    ):
        fail("FINANCIAL_TRUST", "授权证据的类型或版本无效。")
    return record


def _raw_scope(package):
    """Build an O(rows) coordinate index before inspecting proof candidates."""
    raw = package.get("raw")
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("sourceKind"), str)
        or raw["sourceKind"] not in {"provider", "fixture"}
    ):
        fail("FINANCIAL_SOURCE_KIND", "输入须明确区分供应商来源与合成教学来源。")
    snapshots = raw.get("snapshots")
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= MAX_SNAPSHOTS:
        fail("FINANCIAL_TRUST", "冻结供应商快照数量无效。")
    rows, count = {}, 0
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("rows"), list):
            fail("FINANCIAL_TRUST", "冻结快照缺少行记录。")
        count += len(snapshot["rows"])
        if count > 20000:
            fail("FINANCIAL_BYTE_BUDGET", "冻结快照行数超过限制。")
        snapshot_id = digest(snapshot.get("id"))
        for row in snapshot["rows"]:
            key = (snapshot_id, normalized_row_hash(snapshot.get("endpoint"), row))
            rows[key] = (snapshot.get("endpoint"), row)
    return raw, rows


def _proof_matches_source(entry, field, raw, rows):
    value = entry.get("value", {})
    if (
        field not in FIELDS
        or value.get("field_id") != field
        or value.get("provider") != raw.get("sourceProvider")
    ):
        fail("UNIT_PROOF_SCOPE_MISMATCH", "单位证明与输入的供应商或字段不匹配。")
    if entry["type"] == "GlobalUnitBinding":
        return
    actual = rows.get((value.get("source_snapshot"), value.get("normalized_row_hash")))
    if actual is None:
        fail("UNIT_PROOF_SCOPE_MISMATCH", "单位证明没有匹配的冻结规范化行。")
    endpoint, row = actual
    coordinates = {
        "symbol": "ts_code",
        "period_end": "end_date",
        "ann_date": "ann_date",
        "f_ann_date": "f_ann_date",
        "report_type": "report_type",
        "company_type": "comp_type",
    }
    if (
        endpoint != FIELDS[field].endpoint
        or FIELDS[field].provider_field not in row
        or any(value.get(key) != row.get(column) for key, column in coordinates.items())
    ):
        fail("UNIT_PROOF_SCOPE_MISMATCH", "单位证明的证券、期间或公告坐标不匹配。")


def resolve_package_registry(source_bytes, calendar_bytes, proof_bytes_list):
    """Pure domain boundary; registry bytes must be authorized outside the package.

    A serialized archive cannot authorize its own proof/calendar records. Callers
    must match them against a separate trusted registry before calling here.
    """
    if (
        not isinstance(source_bytes, bytes)
        or len(source_bytes) > PACKAGE_BYTES
        or not isinstance(proof_bytes_list, (list, tuple))
        or len(proof_bytes_list) > 256
    ):
        fail("FINANCIAL_BYTE_BUDGET", "冻结输入或证明集合超过边界。")
    entries = [calendar_bytes, *proof_bytes_list]
    if any(
        not isinstance(raw, bytes) or not 0 < len(raw) <= REGISTRY_BYTES
        for raw in entries
    ):
        fail("FINANCIAL_BYTE_BUDGET", "注册证据字节无效或超过边界。")
    if sum(map(len, entries)) > REGISTRIES_BYTES:
        fail("FINANCIAL_BYTE_BUDGET", "注册证据超过共同边界。")
    # Strict JSON inspection grants no trust. Core decoding happens only below.
    untrusted = decode(source_bytes, limit=PACKAGE_BYTES)
    if not isinstance(untrusted, dict):
        fail("FINANCIAL_TRUST", "财务输入包须为对象。")
    raw, rows = _raw_scope(untrusted)
    calendar_record = _registry(calendar_bytes, "calendar")
    if encode(raw.get("calendar")) != encode(
        calendar_record["payload"]
    ) or calendar_record["scope"] != calendar_scope(calendar_record["payload"]):
        fail("CALENDAR_REGISTRY_MISMATCH", "冻结日历与授权注册版本不完全匹配。")
    approved = set()
    for proof_bytes in proof_bytes_list:
        record = _registry(proof_bytes, "unit_proof")
        entry = record["payload"]
        _object(entry, {"type", "value"})
        if (
            entry["type"] not in ("DocumentUnitBinding", "GlobalUnitBinding")
            or record["scope"] != proof_scope(entry)
            or not isinstance(entry["value"].get("evidence"), dict)
            or record["evidenceLevel"] != entry["value"]["evidence"].get("kind")
        ):
            fail(
                "UNIT_PROOF_REGISTRY_MISMATCH", "授权单位证明的完整范围或证据等级无效。"
            )
        approved.add(encode(entry))
    bindings = untrusted.get("bindings")
    if not isinstance(bindings, dict):
        fail("FINANCIAL_TRUST", "财务输入缺少明确单位绑定。")
    count, reviewed = 0, False
    for field, entries in bindings.items():
        if not isinstance(entries, list) or not entries:
            fail("FINANCIAL_TRUST", "财务单位绑定须为非空集合。")
        count += len(entries)
        if count > MAX_BINDINGS:
            fail("FINANCIAL_BYTE_BUDGET", "财务单位绑定数量超过限制。")
        for entry in entries:
            _object(entry, {"type", "value"})
            if entry["type"] == "DeclaredUnitBinding":
                continue  # The strict core validates its unverified kind and root.
            if entry["type"] not in ("DocumentUnitBinding", "GlobalUnitBinding"):
                fail(
                    "FINANCIAL_PUBLIC_FIXTURE",
                    "公开输入不能携带 fixture 或未知证明类型。",
                )
            if encode(entry) not in approved:
                fail(
                    "UNIT_PROOF_REGISTRY_MISMATCH",
                    "单位证明未与任务授权注册版本完全匹配。",
                )
            _proof_matches_source(entry, field, raw, rows)
            reviewed = True
    package = decode_package(source_bytes, trusted_unit_proofs=reviewed)
    calendar, decoded_bindings = validate_package(package, trusted_unit_proofs=reviewed)
    return TrustedInput(
        package,
        calendar,
        decoded_bindings,
        reviewed,
        calendar_record["scope"]["calendarRoot"],
    )


def resolve_input(job, input_meta, source_bytes, registry_bytes):
    """Never enable trusted proofs before complete payload and scope equality."""
    if not isinstance(job, dict):
        fail("FINANCIAL_IDENTITY", "财务任务需要对象。")
    for key in ("id", "inputId"):
        identifier(job.get(key))
    if not isinstance(job.get("kind"), str) or job["kind"] not in KINDS:
        fail("FINANCIAL_IDENTITY", "未知财务任务类型。")
    if (
        not isinstance(input_meta, dict)
        or len(encode(input_meta)) > META_BYTES
        or input_meta.get("job") != {key: job[key] for key in ("id", "kind", "inputId")}
    ):
        fail("FINANCIAL_IDENTITY", "财务任务与冻结输入描述不匹配。")
    _check_bytes(source_bytes, input_meta.get("source"), PACKAGE_BYTES)
    proofs = input_meta.get("proofs")
    if (
        not isinstance(proofs, list)
        or len(proofs) > 256
        or not isinstance(registry_bytes, dict)
    ):
        fail("FINANCIAL_BYTE_BUDGET", "授权证明集合超过数量限制。")
    descriptors = [input_meta.get("calendar"), *proofs]
    refs, total = set(), 0
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            fail("FINANCIAL_TRUST", "授权证据描述无效。")
        ref = identifier(descriptor.get("ref"))
        if ref in refs:
            fail("FINANCIAL_TRUST", "授权证据引用重复。")
        refs.add(ref)
        raw_bytes = registry_bytes.get(ref)
        _check_bytes(raw_bytes, descriptor, REGISTRY_BYTES)
        total += len(raw_bytes)
        if total > REGISTRIES_BYTES:
            fail("FINANCIAL_BYTE_BUDGET", "授权证据集合超过总字节限制。")
    if set(registry_bytes) != refs:
        fail("FINANCIAL_TRUST", "授权证据集合与任务的冻结引用不匹配。")

    source = resolve_package_registry(
        source_bytes,
        registry_bytes[descriptors[0]["ref"]],
        [registry_bytes[descriptor["ref"]] for descriptor in proofs],
    )
    package = source.package
    operation = input_meta.get("operation")
    if not isinstance(operation, dict):
        fail("FINANCIAL_IDENTITY", "财务任务缺少明确操作参数。")
    if job["kind"] == "financial_validate":
        _object(operation, {"expectedUploadSha256"})
        if operation["expectedUploadSha256"] != sha(source_bytes):
            fail("FINANCIAL_INTEGRITY", "原上传字节与任务预期不匹配。")
    else:
        if job["kind"] == "financial_prepare":
            _object(operation, {"expectedPackRoot"})
        if operation.get("expectedPackRoot") != package["packRoot"]:
            fail("FINANCIAL_INTEGRITY", "冻结父输入包与任务预期不匹配。")
    return source


def revise_input(source, operation):
    _object(
        operation,
        {
            "expectedPackRoot",
            "parentId",
            "selection",
            "unitPolicy",
            "declarations",
            "declaredBy",
            "declaredAt",
        },
    )
    identifier(operation["parentId"])
    selection = operation["selection"]
    _object(
        selection,
        {
            "symbols",
            "start",
            "end",
            "announcementStart",
            "selectedStateIds",
            "scope",
            "flowBasis",
        },
    )
    declarations = operation["declarations"]
    if not isinstance(declarations, list) or len(declarations) > 64:
        fail("FINANCIAL_BYTE_BUDGET", "单位声明数量超过限制。")
    units = {
        field: [
            binding
            for binding in values
            if not isinstance(binding, DeclaredUnitBinding)
        ]
        for field, values in source.bindings.items()
    }
    units = {field: values for field, values in units.items() if values}
    for declaration in declarations:
        _object(
            declaration,
            {
                "fieldId",
                "inputRoot",
                "nativeUnit",
                "currency",
                "positiveOutflow",
                "statement",
            },
        )
        if declaration["inputRoot"] != source.package["inputRoot"]:
            fail("UNIT_DECLARATION_SCOPE", "单位声明不能跨冻结原始输入复用。")
        field = declaration["fieldId"]
        if not isinstance(field, str) or field not in FIELDS:
            fail("UNIT_DECLARATION_SCOPE", "单位声明必须引用已注册财务字段。")
        binding = DeclaredUnitBinding(
            UnitEvidence(
                declaration["nativeUnit"],
                declaration["currency"],
                False,
                "user_declared_assumption",
                declaration["statement"],
                declaration["positiveOutflow"],
            ),
            field,
            source.package["raw"]["sourceProvider"],
            source.package["inputRoot"],
            operation["declaredBy"],
            operation["declaredAt"],
            declaration["statement"],
        )
        units.setdefault(field, []).append(binding)
    raw = source.package["raw"]
    package = freeze_package(
        raw["snapshots"],
        source.calendar,
        {"universe": {key: selection[key] for key in ("symbols", "start", "end")}},
        selection["selectedStateIds"],
        units,
        announcement_start=selection["announcementStart"],
        source_kind=raw["sourceKind"],
        source_provider=raw["sourceProvider"],
        scope=selection["scope"],
        flow_basis=selection["flowBasis"],
        unit_policy=operation["unitPolicy"],
        trusted_unit_proofs=source.reviewed_proofs,
    )
    if package["inputRoot"] != source.package["inputRoot"]:
        fail("FINANCIAL_INTEGRITY", "修订不得替换原始输入或日历。")
    return package
