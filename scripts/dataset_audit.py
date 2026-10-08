"""Independent standard-library audit of atlas.quant.research_dataset/1.

No production modules, provider, model, pickle, dynamic import or evaluator.
An internally consistent archive does not authenticate its source assertions.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile

MIB = 1024 * 1024
TOTAL = 64 * MIB
MANIFEST = 256 * 1024
PART = 512 * 1024
MAX_PARTS = 256
TAR_MAX = TOTAL + (MAX_PARTS + 1) * 1023 + 1024
TYPES = {
    "registry_evidence": set(),
    "market_dataset": {"marketRoot"},
    "financial_input": {"inputRoot", "packRoot"},
    "financial_prepared": {"packRoot", "preparedRoot", "calendarRoot"},
    "research_rows": {"financialDatasetRoot"},
    "dataset_schema": set(),
    "dataset_coverage": set(),
}


class AuditError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class Checks:
    def __init__(self):
        self.count = 0

    def require(self, condition, message, code="DATASET_AUDIT"):
        self.count += 1
        if not condition:
            raise AuditError(code, message)

    def keys(self, value, names):
        self.require(
            isinstance(value, dict) and set(value) == set(names),
            "Unexpected or missing object fields",
            "SHAPE",
        )

    def equal(self, actual, expected, message):
        self.require(encode(actual) == encode(expected), message)


def encode(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise AuditError("JSON", "Value is not finite canonical UTF-8 JSON") from exc


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def root(value):
    return sha(encode(value))


def without(value, *keys):
    return {k: v for k, v in value.items() if k not in keys}


def decode(raw, ceiling, check):
    check.require(
        isinstance(raw, bytes) and 0 < len(raw) <= ceiling,
        "JSON byte budget exceeded",
        "BUDGET",
    )
    # Preflight nesting before invoking json's recursive decoder. This auditor
    # explicitly caps JSON container nesting at 64 (graph depth is separately 3).
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
            check.require(
                depth <= 64, "JSON nesting exceeds auditor limit", "JSON_DEPTH"
            )
        elif byte in (93, 125):
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            check.require(key not in result, "Duplicate JSON key", "JSON")
            result[key] = value
        return result

    def number(token):
        value = float(token)
        check.require(math.isfinite(value), "Nonfinite numeric token", "JSON")
        return value

    def constant(_):
        raise AuditError("JSON", "Nonfinite JSON constant")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=number,
            parse_constant=constant,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AuditError):
            raise
        raise AuditError("JSON", "Malformed bounded JSON") from exc
    check.require(encode(value) == raw, "Noncanonical JSON bytes", "CANONICAL")
    return value


def identity(value, check, uuid=False):
    pattern = (
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}" if uuid else r"[0-9a-f]{64}"
    )
    check.require(
        isinstance(value, str) and re.fullmatch(pattern, value) is not None,
        "Invalid UUID or SHA-256 identity",
        "IDENTITY",
    )
    return value


def date(value, check):
    try:
        valid = isinstance(value, str) and re.fullmatch(r"[0-9]{8}", value)
        valid = valid and datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value
    except ValueError:
        valid = False
    check.require(bool(valid), "Date must be valid YYYYMMDD", "DATE")
    return value


def integer(value, low, high, check):
    check.require(
        type(value) is int and low <= value <= high,
        "Invalid integer or bounded count",
        "BUDGET",
    )


def symbols(values, check, ordered=False):
    check.require(
        isinstance(values, list)
        and 1 <= len(values) <= 50
        and all(
            isinstance(s, str) and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", s) for s in values
        )
        and len(set(values)) == len(values),
        "Invalid symbol set",
        "SCOPE",
    )
    if ordered:
        check.require(
            values == sorted(values), "Manifest symbols must be sorted", "SCOPE"
        )


def validate_manifest(raw, check, expected_root=None):
    m = decode(raw, MANIFEST, check)
    check.keys(
        m,
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
    check.require(
        m["format"] == "atlas.quant.research_dataset"
        and type(m["version"]) is int
        and m["version"] == 1
        and m["profile"] == "financial_compose_50_v1",
        "Unknown format/profile",
        "FORMAT",
    )
    if expected_root is not None:
        check.require(
            sha(raw) == identity(expected_root, check),
            "Pinned dataset root differs",
            "ROOT",
        )
    check.keys(m["scope"], {"symbols", "start", "end"})
    symbols(m["scope"]["symbols"], check, True)
    check.require(
        date(m["scope"]["start"], check) <= date(m["scope"]["end"], check),
        "Reversed scope",
        "SCOPE",
    )
    identity(m["marketCalendarRef"], check, True)
    check.keys(m["roots"], {"marketRoot", "financialDatasetRoot"})
    for value in m["roots"].values():
        identity(value, check)
    components = m["components"]
    check.require(isinstance(components, list), "Components must be a list", "SHAPE")
    integer(len(components), 1, 32, check)
    by_id, by_root, depths = {}, {}, {}
    total, part_count, input_bytes = len(raw), 0, 0
    for c in components:
        check.keys(
            c,
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
        name = c["componentId"]
        check.require(
            isinstance(name, str)
            and re.fullmatch(r"[a-z][A-Za-z0-9]{0,39}", name)
            and name not in by_id,
            "Invalid/duplicate component ID",
            "IDENTITY",
        )
        check.require(
            isinstance(c["type"], str)
            and c["type"] in TYPES
            and type(c["version"]) is int
            and c["version"] == 1
            and c["encoding"] == "raw_bytes",
            "Unknown type/encoding",
            "TYPE",
        )
        check.keys(c["semanticRoots"], TYPES[c["type"]])
        for value in c["semanticRoots"].values():
            identity(value, check)
        identity(c["payloadSha256"], check)
        cr = identity(c["componentRoot"], check)
        check.require(
            cr == root(without(c, "componentRoot")) and cr not in by_root,
            "Descriptor identity differs or repeats",
            "ROOT",
        )
        deps = c["dependencies"]
        check.require(
            isinstance(deps, list)
            and all(isinstance(d, str) and d in by_root for d in deps)
            and deps == sorted(set(deps)),
            "Dependencies must refer to unique prior components",
            "GRAPH",
        )
        depths[cr] = 0 if not deps else 1 + max(depths[d] for d in deps)
        check.require(depths[cr] <= 3, "Graph depth exceeds three edges", "GRAPH")
        integer(c["byteLength"], 1, TOTAL, check)
        check.require(
            isinstance(c["parts"], list) and c["parts"], "Missing parts", "PARTS"
        )
        size = 0
        for ordinal, p in enumerate(c["parts"]):
            check.keys(p, {"ordinal", "byteLength", "sha256"})
            integer(p["ordinal"], ordinal, ordinal, check)
            integer(p["byteLength"], 1, PART, check)
            identity(p["sha256"], check)
            size += p["byteLength"]
        check.require(
            size == c["byteLength"], "Part lengths do not cover component", "PARTS"
        )
        ceiling = (
            24 * MIB
            if c["type"] in {"market_dataset", "financial_input", "research_rows"}
            else TOTAL
        )
        check.require(size <= ceiling, "Typed component budget exceeded", "BUDGET")
        total += size
        part_count += len(c["parts"])
        input_bytes += size if c["type"] == "financial_input" else 0
        check.require(
            total <= TOTAL and part_count <= MAX_PARTS and input_bytes <= 24 * MIB,
            "Complete closure budget exceeded",
            "BUDGET",
        )
        by_id[name], by_root[cr] = c, c
    sources = m["financialSources"]
    check.require(isinstance(sources, list), "Sources must be a list", "SHAPE")
    integer(len(sources), 1, 8, check)
    expected = {
        "registryEvidence": "registry_evidence",
        "marketDataset": "market_dataset",
        "researchRows": "research_rows",
        "schema": "dataset_schema",
        "coverage": "dataset_coverage",
    }
    for i, source in enumerate(sources):
        check.keys(source, {"componentId", "calendarRef", "proofRefs", "preparedRoot"})
        check.require(
            source["componentId"] == f"financialInput{i}",
            "Source/component ordering differs",
            "GRAPH",
        )
        identity(source["calendarRef"], check, True)
        identity(source["preparedRoot"], check)
        refs = source["proofRefs"]
        check.require(
            isinstance(refs, list) and len(refs) <= 256,
            "Proof reference budget",
            "BUDGET",
        )
        for ref in refs:
            identity(ref, check, True)
        check.require(
            refs == sorted(set(refs)), "Proof references must be sorted unique", "GRAPH"
        )
        expected[f"financialInput{i}"], expected[f"financialPrepared{i}"] = (
            "financial_input",
            "financial_prepared",
        )
    check.require(
        set(by_id) == set(expected),
        "Typed closure omitted or added a component",
        "GRAPH",
    )
    for name, kind in expected.items():
        check.require(
            by_id[name]["type"] == kind, "Component type/identity mismatch", "TYPE"
        )
    reg = by_id["registryEvidence"]["componentRoot"]
    global_deps = sorted(
        [by_id["marketDataset"]["componentRoot"]]
        + [by_id[f"financialPrepared{i}"]["componentRoot"] for i in range(len(sources))]
    )
    for name, c in by_id.items():
        if name == "registryEvidence":
            deps = []
        elif name in {"researchRows", "schema", "coverage"}:
            deps = global_deps
        elif name.startswith("financialPrepared"):
            deps = [by_id[name.replace("Prepared", "Input")]["componentRoot"]]
        else:
            deps = [reg]
        check.equal(c["dependencies"], deps, "Component dependency closure differs")
    check.equal(
        by_id["marketDataset"]["semanticRoots"],
        {"marketRoot": m["roots"]["marketRoot"]},
        "Market root link differs",
    )
    check.equal(
        by_id["researchRows"]["semanticRoots"],
        {"financialDatasetRoot": m["roots"]["financialDatasetRoot"]},
        "Joined root link differs",
    )
    for i, source in enumerate(sources):
        inp, prep = (
            by_id[f"financialInput{i}"]["semanticRoots"],
            by_id[f"financialPrepared{i}"]["semanticRoots"],
        )
        check.require(
            inp["packRoot"] == prep["packRoot"]
            and prep["preparedRoot"] == source["preparedRoot"],
            "Financial root links differ",
            "ROOT",
        )
    return m


@contextmanager
def regular(path, maximum):
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            raise AuditError("FILE", "Input must be a bounded nonempty regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def read_file(path, maximum):
    with regular(path, maximum) as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise AuditError("BUDGET", "Input grew beyond byte budget")
    return raw


def tar_header(name, size):
    """Independent exact USTAR profile; intentionally not tarfile.open()."""
    header = bytearray(512)
    raw_name = name.encode("ascii")
    if len(raw_name) >= 100:
        raise AuditError("TAR_PATH", "Name exceeds USTAR profile")
    header[: len(raw_name)] = raw_name
    for offset, width, value in (
        (100, 8, 0o600),
        (108, 8, 0),
        (116, 8, 0),
        (124, 12, size),
        (136, 12, 0),
    ):
        header[offset : offset + width] = (f"{value:0{width-1}o}\0").encode("ascii")
    header[148:156] = b" " * 8
    header[156:157] = b"0"
    header[257:265] = b"ustar\x0000"
    header[148:156] = f"{sum(header):06o}\0 ".encode("ascii")
    return bytes(header)


class Archive:
    def __init__(self, stream, check):
        self.stream, self.check, self.consumed = stream, check, 0

    def exact(self, n):
        self.check.require(
            0 <= n <= PART and self.consumed + n <= TAR_MAX,
            "Archive read budget",
            "BUDGET",
        )
        raw = self.stream.read(n)
        self.check.require(len(raw) == n, "Truncated archive", "TAR_TRUNCATED")
        self.consumed += n
        return raw

    def member(self, name, ceiling, size=None):
        header = self.exact(512)
        try:
            parsed = tarfile.TarInfo.frombuf(header, "ascii", "strict")
        except (tarfile.HeaderError, UnicodeError, ValueError) as exc:
            raise AuditError("TAR_HEADER", "Invalid USTAR checksum/header") from exc
        self.check.require(
            parsed.type == b"0"
            and 0 < parsed.size <= ceiling
            and (size is None or parsed.size == size),
            "Member type or length differs",
            "TAR_HEADER",
        )
        self.check.require(
            header == tar_header(name, parsed.size),
            "Unexpected USTAR path/order/metadata",
            "TAR_HEADER",
        )
        raw = self.exact(parsed.size)
        padding = (-parsed.size) % 512
        self.check.require(
            self.exact(padding) == bytes(padding),
            "Nonzero member padding",
            "TAR_PADDING",
        )
        return raw

    def finish(self):
        self.check.require(
            self.exact(1024) == bytes(1024) and self.stream.read(1) == b"",
            "Exact two-block EOF with no trailer required",
            "TAR_EOF",
        )


def load_closure(path, check, expected_root):
    """At most 64MiB retained component bytes; each part read is <=512KiB."""
    path = Path(path)
    if path.is_symlink():
        raise AuditError("FILE", "Symlink input is not allowed")
    payloads = {}
    if path.is_dir():
        check.require(
            set(p.name for p in path.iterdir()) == {"manifest.json", "parts"},
            "Unexpected directory members",
            "DIRECTORY",
        )
        raw = read_file(path / "manifest.json", MANIFEST)
        m = validate_manifest(raw, check, expected_root)
        parts_dir = path / "parts"
        check.require(
            not parts_dir.is_symlink() and parts_dir.is_dir(),
            "Invalid parts directory",
            "DIRECTORY",
        )
        check.require(
            set(p.name for p in parts_dir.iterdir())
            == {c["componentId"] for c in m["components"]},
            "Missing or extra component directory",
            "DIRECTORY",
        )
        for c in m["components"]:
            folder = parts_dir / c["componentId"]
            check.require(
                not folder.is_symlink() and folder.is_dir(),
                "Invalid component directory",
                "DIRECTORY",
            )
            check.require(
                set(p.name for p in folder.iterdir())
                == {f"{p['ordinal']}.bin" for p in c["parts"]},
                "Missing or extra part",
                "DIRECTORY",
            )
            pieces = [
                checked_part(read_file(folder / f"{p['ordinal']}.bin", PART), p, check)
                for p in c["parts"]
            ]
            payloads[c["componentId"]] = checked_payload(b"".join(pieces), c, check)
    else:
        with regular(path, TAR_MAX) as stream:
            archive = Archive(stream, check)
            raw = archive.member("manifest.json", MANIFEST)
            m = validate_manifest(raw, check, expected_root)
            for c in m["components"]:
                pieces = [
                    checked_part(
                        archive.member(
                            f"parts/{c['componentId']}/{p['ordinal']}.bin",
                            PART,
                            p["byteLength"],
                        ),
                        p,
                        check,
                    )
                    for p in c["parts"]
                ]
                payloads[c["componentId"]] = checked_payload(b"".join(pieces), c, check)
            archive.finish()
    return raw, m, payloads


def checked_part(raw, part, check):
    check.require(
        len(raw) == part["byteLength"] and sha(raw) == part["sha256"],
        "Part bytes or hash differ",
        "PART_HASH",
    )
    return raw


def checked_payload(raw, component, check):
    check.require(
        len(raw) == component["byteLength"] and sha(raw) == component["payloadSha256"],
        "Component bytes or hash differ",
        "PAYLOAD_HASH",
    )
    return raw


def calendar_evidence(payload, check):
    check.keys(
        payload,
        {
            "sessions",
            "coverage_start",
            "coverage_end",
            "complete",
            "kind",
            "evidence_reference",
        },
    )
    dates = payload["sessions"]
    check.require(
        isinstance(dates, list) and 0 < len(dates) <= 10000,
        "Calendar session budget",
        "CALENDAR",
    )
    for day in dates:
        date(day, check)
    check.require(
        dates == sorted(set(dates))
        and payload["complete"] is True
        and date(payload["coverage_start"], check) <= dates[0]
        and dates[-1] <= date(payload["coverage_end"], check)
        and payload["kind"] in {"official", "fixture"}
        and isinstance(payload["evidence_reference"], str)
        and payload["evidence_reference"],
        "Invalid complete calendar",
        "CALENDAR",
    )
    value = {"sessions_hash": root(dates), **without(payload, "sessions")}
    return {"root": root(value), **value}


def registry_records(manifest, payload, check, pins):
    check.keys(payload, {"entries"})
    required = {manifest["marketCalendarRef"]}
    for source in manifest["financialSources"]:
        required.update([source["calendarRef"], *source["proofRefs"]])
    check.require(
        isinstance(payload["entries"], list)
        and len(payload["entries"]) == len(required),
        "Registry closure differs",
        "REGISTRY",
    )
    records, raw_records, total = {}, {}, 0
    for entry in payload["entries"]:
        check.keys(entry, {"ref", "sha256", "byteLength", "rawText"})
        ref = identity(entry["ref"], check, True)
        check.require(
            isinstance(entry["rawText"], str), "Registry text missing", "REGISTRY"
        )
        raw = entry["rawText"].encode("utf-8")
        record = decode(raw, MANIFEST, check)
        integer(entry["byteLength"], 1, MANIFEST, check)
        check.require(
            ref not in records
            and entry["byteLength"] == len(raw)
            and identity(entry["sha256"], check) == sha(raw),
            "Registry bytes differ or repeat",
            "REGISTRY",
        )
        check.keys(
            record, {"kind", "payload", "registryVersion", "evidenceLevel", "scope"}
        )
        integer(record["registryVersion"], 1, 2**53 - 1, check)
        check.require(
            isinstance(record["evidenceLevel"], str)
            and 1 <= len(record["evidenceLevel"]) <= 100,
            "Invalid registry level",
            "REGISTRY",
        )
        if record["kind"] == "calendar":
            check.equal(
                record["scope"],
                {"calendarRoot": calendar_evidence(record["payload"], check)["root"]},
                "Calendar scope differs",
            )
        else:
            proof = record["payload"]
            check.require(
                record["kind"] == "unit_proof", "Unknown registry type", "REGISTRY"
            )
            check.keys(proof, {"type", "value"})
            check.require(
                proof["type"] in {"DocumentUnitBinding", "GlobalUnitBinding"}
                and isinstance(proof["value"], dict),
                "Unknown proof type",
                "REGISTRY",
            )
            check.equal(
                record["scope"],
                without(proof["value"], "evidence"),
                "Proof scope differs",
            )
            check.require(
                record["evidenceLevel"] == proof["value"]["evidence"]["kind"],
                "Proof evidence differs",
                "REGISTRY",
            )
        records[ref], raw_records[ref] = record, raw
        total += len(raw)
    check.require(
        list(records) == sorted(required) and total <= 32 * MIB,
        "Registry ordering/coverage/budget differs",
        "REGISTRY",
    )
    if pins is not None:
        check.require(
            isinstance(pins, dict) and set(pins) == required,
            "Separate pins must cover exact required registry set",
            "PINS",
        )
        for ref in sorted(required):
            check.require(
                pins[ref] == raw_records[ref],
                "Registry differs from separately supplied bytes",
                "PINS",
            )
    return records


def row_index(rows, check, maximum=110000):
    check.require(
        isinstance(rows, list) and 1 <= len(rows) <= maximum,
        "Row count exceeds budget",
        "ROWS",
    )
    result, columns = {}, set(rows[0])
    for row in rows:
        check.require(
            isinstance(row, dict) and set(row) == columns,
            "Rows must have exact common columns",
            "ROWS",
        )
        key = (date(row["trade_date"], check), row["ts_code"])
        check.require(key not in result, "Duplicate row coordinates", "ROWS")
        result[key] = row
    return result


def check_package(package, source, records, scope, check):
    check.keys(
        package,
        {
            "format",
            "version",
            "inputRoot",
            "packRoot",
            "raw",
            "selection",
            "unitPolicy",
            "bindings",
        },
    )
    check.require(
        package["format"] == "atlas.quant.financial-input"
        and type(package["version"]) is int
        and package["version"] == 1,
        "Unknown financial package",
        "PACKAGE",
    )
    check.require(
        package["inputRoot"] == root(package["raw"])
        and package["packRoot"] == root(without(package, "packRoot")),
        "Financial package roots differ",
        "ROOT",
    )
    raw, selection = package["raw"], package["selection"]
    check.keys(raw, {"snapshots", "calendar", "sourceKind", "sourceProvider"})
    check.keys(
        selection,
        {"universe", "selectedStates", "announcementStart", "scope", "flowBasis"},
    )
    u = selection["universe"]
    check.keys(u, {"symbols", "start", "end"})
    symbols(u["symbols"], check)
    check.require(
        set(u["symbols"]).issubset(scope["symbols"])
        and u["start"] == scope["start"]
        and u["end"] == scope["end"],
        "Financial scope differs",
        "SCOPE",
    )
    states = selection["selectedStates"]
    check.require(
        isinstance(states, list)
        and 1 <= len(states) <= 16
        and len(set(states)) == len(states)
        and all(
            isinstance(s, str) and re.fullmatch(r"model_fin_[a-z_]+", s) for s in states
        ),
        "Invalid state selection",
        "PACKAGE",
    )
    check.require(
        selection["scope"] in {"consolidated", "parent"}
        and selection["flowBasis"] in {"ytd", "quarter"}
        and package["unitPolicy"] in {"verified_only", "allow_declared"},
        "Unknown financial policies",
        "PACKAGE",
    )
    date(selection["announcementStart"], check)
    calendar_record = records[source["calendarRef"]]
    check.require(
        calendar_record["kind"] == "calendar",
        "Financial calendar ref has wrong type",
        "REGISTRY",
    )
    check.equal(
        raw["calendar"],
        calendar_record["payload"],
        "Package calendar differs from registry",
    )
    snapshots = raw["snapshots"]
    check.require(
        raw["sourceKind"] in {"provider", "fixture"}
        and isinstance(raw["sourceProvider"], str),
        "Source kind/provider missing",
        "PACKAGE",
    )
    check.require(
        isinstance(snapshots, list) and 1 <= len(snapshots) <= 128,
        "Snapshot budget exceeded",
        "PACKAGE",
    )
    rows, ids, count = {}, [], 0
    for snap in snapshots:
        body = without(snap, "id", "rowCount", "byteLength")
        check.keys(
            body,
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
            },
        )
        check.require(
            snap["id"] == root(body)
            and snap["byteLength"] == len(encode(body))
            and snap["rowCount"] == len(body["rows"]),
            "Snapshot root/count/bytes differ",
            "ROOT",
        )
        check.require(
            body["sourceKind"] == raw["sourceKind"]
            and body["sourceProvider"] == raw["sourceProvider"]
            and body["representation"] == "normalized_provider_table_snapshot"
            and body["wireBytesAvailable"] is False
            and body["wireNumericLexemesAvailable"] is False,
            "Snapshot source/wire evidence differs",
            "PACKAGE",
        )
        check.require(
            body["endpoint"] in {"balancesheet", "income", "cashflow"},
            "Unknown statement endpoint",
            "PACKAGE",
        )
        check.require(
            isinstance(body["fields"], list)
            and len(set(body["fields"])) == len(body["fields"]),
            "Snapshot fields differ",
            "PACKAGE",
        )
        for row in body["rows"]:
            check.require(
                isinstance(row, dict)
                and set(row) == set(body["fields"])
                and all(
                    v is None or type(v) in {str, int, float, bool}
                    for v in row.values()
                ),
                "Snapshot is not a scalar table",
                "PACKAGE",
            )
            rh = root(
                {
                    "representation": "normalized_provider_table_row",
                    "endpoint": body["endpoint"],
                    "row": row,
                }
            )
            rows[(snap["id"], rh)] = (body["endpoint"], row)
        ids.append(snap["id"])
        count += len(body["rows"])
    check.require(
        ids == sorted(set(ids)) and count <= 20000,
        "Snapshot order/uniqueness/budget differs",
        "PACKAGE",
    )
    approved = set()
    for ref in source["proofRefs"]:
        check.require(
            records[ref]["kind"] == "unit_proof", "Proof ref has wrong kind", "REGISTRY"
        )
        approved.add(encode(records[ref]["payload"]))
    check.require(
        isinstance(package["bindings"], dict), "Bindings must be an object", "PACKAGE"
    )
    count = 0
    for field, bindings in package["bindings"].items():
        check.require(
            isinstance(bindings, list) and bindings, "Empty binding list", "PACKAGE"
        )
        for binding in bindings:
            count += 1
            check.keys(binding, {"type", "value"})
            v = binding["value"]
            check.require(
                v["field_id"] == field and v["provider"] == raw["sourceProvider"],
                "Binding field/provider differs",
                "PROOF",
            )
            if binding["type"] == "DeclaredUnitBinding":
                check.require(
                    v["input_root"] == package["inputRoot"]
                    and v["scope"] == "declared"
                    and v["evidence"]["verified"] is False
                    and v["evidence"]["kind"] == "user_declared_assumption",
                    "Declaration attempted trust upgrade",
                    "PROOF",
                )
            else:
                check.require(
                    binding["type"] in {"DocumentUnitBinding", "GlobalUnitBinding"}
                    and encode(binding) in approved,
                    "Binding lacks exact registered proof",
                    "PROOF",
                )
                if binding["type"] == "DocumentUnitBinding":
                    key = (v["source_snapshot"], v["normalized_row_hash"])
                    check.require(
                        key in rows, "Document proof has no source row", "PROOF"
                    )
                    endpoint, row = rows[key]
                    check.require(
                        field.startswith(endpoint + ".")
                        and field.split(".", 1)[1] in row,
                        "Proof field not in snapshot",
                        "PROOF",
                    )
                    for name, column in {
                        "symbol": "ts_code",
                        "period_end": "end_date",
                        "ann_date": "ann_date",
                        "f_ann_date": "f_ann_date",
                        "report_type": "report_type",
                        "company_type": "comp_type",
                    }.items():
                        check.equal(
                            v[name], row.get(column), "Proof source coordinate differs"
                        )
    check.require(count <= 20000, "Binding count exceeded", "BUDGET")
    return calendar_evidence(raw["calendar"], check), rows


def check_dependency(dep, event, package, source_rows, check):
    """Tie disclosed values and unit evidence back to frozen rows/bindings.

    The separate StatementRecord hash and formula arithmetic are intentionally
    outside this audit; they remain explicit limitations in every result.
    """
    identity(dep["record_hash"], check)
    field = dep["field_id"]
    check.require(
        isinstance(field, str) and "." in field,
        "Dependency field identity missing",
        "LINEAGE",
    )
    endpoint, column = field.split(".", 1)
    candidates = [
        (key, row)
        for key, (ep, row) in source_rows.items()
        if key[0] == dep["source_snapshot"]
        and ep == endpoint
        and row.get("ts_code") == event["symbol"]
        and row.get("end_date") == dep["period_end"]
        and row.get("report_type") == dep["report_type"]
        and row.get("comp_type") == dep["company_type"]
    ]
    check.require(
        bool(candidates), "Dependency lacks matching source coordinates", "LINEAGE"
    )
    matches = []
    for key, row in candidates:
        announced = max(
            (d for d in (row.get("ann_date"), row.get("f_ann_date")) if d), default=None
        )
        raw = row.get(column)
        raw_decimal = str(Decimal(str(raw))) if raw is not None else None
        if announced == dep["announcement_date"] and raw_decimal == dep["raw_decimal"]:
            matches.append((key, row))
    check.require(
        bool(matches), "Dependency raw value/disclosure differs from source", "LINEAGE"
    )
    snap = next(
        s for s in package["raw"]["snapshots"] if s["id"] == dep["source_snapshot"]
    )
    check.require(
        dep["source_kind"] == package["raw"]["sourceKind"]
        and dep["source_provider"] == package["raw"]["sourceProvider"]
        and dep["retrieved_at"] == snap["retrievedAt"],
        "Dependency source metadata differs",
        "LINEAGE",
    )
    calendar = package["raw"]["calendar"]
    announced = dep["announcement_date"]
    if announced is not None and dep["available_date"] is not None:
        position = bisect_right(calendar["sessions"], announced)
        check.require(
            position < len(calendar["sessions"])
            and dep["available_date"] == calendar["sessions"][position],
            "Dependency is not available on first strictly later session",
            "CAUSALITY",
        )
    bindings = {root(b["value"]): b for b in package["bindings"].get(field, [])}
    unit_scope = dep["unit_scope"]
    if unit_scope is None:
        check.require(
            dep["unit_verified"] is False,
            "Unscoped dependency cannot be verified",
            "PROOF",
        )
        return
    hashes = unit_scope["binding_hashes"]
    check.require(
        isinstance(hashes, list) and hashes and all(h in bindings for h in hashes),
        "Dependency unit scope references missing bindings",
        "PROOF",
    )
    retained = [bindings[h] for h in hashes]
    exemplar = retained[0]["value"]
    evidence = exemplar["evidence"]
    check.require(
        unit_scope["field_id"] == field
        and unit_scope["provider"] == dep["source_provider"],
        "Dependency unit scope differs",
        "PROOF",
    )
    for key, evidence_key in (
        ("raw_unit", "native_unit"),
        ("currency", "currency"),
        ("unit_evidence", "reference"),
        ("unit_evidence_kind", "kind"),
        ("evidence_level", "kind"),
    ):
        check.equal(
            dep[key],
            evidence[evidence_key],
            "Dependency unit evidence differs from binding",
        )
    check.equal(
        unit_scope["document_hashes"],
        [
            b["value"]["document_hash"]
            for b in retained
            if "document_hash" in b["value"]
        ],
        "Unit document references differ",
    )
    declared = [
        root(b["value"]) for b in retained if b["type"] == "DeclaredUnitBinding"
    ]
    check.equal(
        dep["declaration_hashes"], declared, "Dependency declaration references differ"
    )
    check.equal(
        unit_scope["declaration_hashes"],
        declared,
        "Unit scope declaration references differ",
    )
    if unit_scope["kind"] in {"document", "declared"}:
        check.require(
            (unit_scope["source_snapshot"], unit_scope["normalized_row_hash"])
            in {key for key, _ in matches},
            "Unit scope does not match retained source row",
            "PROOF",
        )
        check.require(
            unit_scope["symbol"] == event["symbol"]
            and unit_scope["period_end"] == dep["period_end"],
            "Unit scope coordinates differ",
            "PROOF",
        )
    if declared:
        check.require(
            unit_scope["input_root"] == package["inputRoot"]
            and dep["unit_verified"] is False,
            "Declaration upgraded to trusted units",
            "PROOF",
        )
    if dep["unit_verified"]:
        check.require(
            unit_scope["status"] == "matched"
            and evidence["verified"] is True
            and all(b["type"] != "DeclaredUnitBinding" for b in retained),
            "Dependency verification unsupported by binding",
            "PROOF",
        )


def check_prepared(package, prepared, source_rows, cal, check):
    check.keys(
        prepared, {"panel", "provenance", "stateEvents", "assignments", "coverage"}
    )
    p = prepared["provenance"]
    expected_root = root(
        {
            "inputRoot": package["inputRoot"],
            "unitPolicy": package["unitPolicy"],
            "selection": package["selection"],
            **without(prepared, "provenance"),
        }
    )
    check.require(
        p["preparedRoot"] == expected_root
        and p["inputRoot"] == package["inputRoot"]
        and p["packRoot"] == package["packRoot"],
        "Prepared root links differ",
        "ROOT",
    )
    check.equal(p["calendar"], cal, "Prepared calendar evidence differs")
    check.require(
        p["originalAsPublishedVerified"] is False
        and p["revisionTimeVerified"] is False,
        "Prepared source history cannot claim verification",
        "EVIDENCE",
    )
    selection = package["selection"]
    states, universe = selection["selectedStates"], selection["universe"]
    sessions = [
        d
        for d in package["raw"]["calendar"]["sessions"]
        if universe["start"] <= d <= universe["end"]
    ]
    grid = {(d, s) for s in universe["symbols"] for d in sessions}
    panel = row_index(prepared["panel"], check)
    check.require(
        set(panel) == grid,
        "Prepared panel lacks exact calendar/security grid",
        "COVERAGE",
    )
    expected_columns = (
        {"ts_code", "trade_date"}
        | set(states)
        | {s + "__available_date" for s in states}
    )
    check.require(
        set(prepared["panel"][0]) == expected_columns,
        "Panel state columns differ",
        "COVERAGE",
    )
    events = {}
    for event in prepared["stateEvents"]:
        check.keys(event, {"id", "symbol", "stateId", "computedAsOf", "result"})
        value = event["result"]
        check.require(
            event["id"] == root(without(event, "id")) and event["id"] not in events,
            "Event ID/hash repeats or differs",
            "LINEAGE",
        )
        check.require(
            value["lineageHash"] == root(without(value, "lineageHash")),
            "Lineage root differs",
            "LINEAGE",
        )
        check.require(
            event["symbol"] in universe["symbols"]
            and event["stateId"] in states
            and value["asOf"] == event["computedAsOf"]
            and event["computedAsOf"] in sessions,
            "Event scope/as-of differs",
            "LINEAGE",
        )
        check.equal(value["calendar"], cal, "Event calendar differs")
        deps = value["dependencies"]
        check.require(
            isinstance(deps, list), "Dependency collection missing", "LINEAGE"
        )
        for dep in deps:
            check_dependency(dep, event, package, source_rows, check)
            check.require(
                dep["source_snapshot"] in {key[0] for key in source_rows},
                "Dependency snapshot missing",
                "LINEAGE",
            )
            if dep["available_date"] is not None:
                check.require(
                    date(dep["available_date"], check) <= value["asOf"],
                    "Future dependency",
                    "CAUSALITY",
                )
            if (
                dep["announcement_date"] is not None
                and dep["available_date"] is not None
            ):
                check.require(
                    dep["announcement_date"] < dep["available_date"],
                    "Disclosure must precede available session",
                    "CAUSALITY",
                )
        check.equal(
            value["mappingVersions"],
            sorted({d["mapping_version"] for d in deps}),
            "Mapping versions omitted/reordered",
        )
        check.equal(
            value["qualityFlags"],
            sorted({v for d in deps for v in d["quality_flags"]}),
            "Quality flags differ",
        )
        check.equal(
            value["declarationHashes"],
            sorted({v for d in deps for v in d["declaration_hashes"]}),
            "Declaration lineage differs",
        )
        check.equal(
            value["unitEvidenceLevels"],
            sorted({d["evidence_level"] for d in deps}),
            "Unit evidence levels differ",
        )
        check.require(
            value["unitVerified"]
            is (bool(deps) and all(d["unit_verified"] for d in deps)),
            "Unit verification aggregation differs",
            "LINEAGE",
        )
        if value["status"] == "ok":
            try:
                number = Decimal(value["decimalValue"])
                finite = number.is_finite() and math.isfinite(float(number))
            except (InvalidOperation, ValueError, TypeError, OverflowError):
                finite = False
            check.require(
                finite
                and not value["reasonCodes"]
                and value["availableDate"] is not None
                and date(value["availableDate"], check) <= value["asOf"],
                "Invalid/future valid result",
                "CAUSALITY",
            )
        else:
            check.require(
                value["status"] == "missing"
                and value["decimalValue"] is None
                and value["reasonCodes"],
                "Invalid missing result",
                "LINEAGE",
            )
        events[event["id"]] = event
    by_symbol = {s: [] for s in universe["symbols"]}
    for assignment in prepared["assignments"]:
        check.keys(assignment, {"symbol", "from", "through", "states"})
        check.require(
            assignment["symbol"] in by_symbol
            and assignment["from"] in sessions
            and assignment["through"] in sessions
            and assignment["from"] <= assignment["through"]
            and set(assignment["states"]) == set(states),
            "Assignment coordinates differ",
            "ASSIGNMENT",
        )
        by_symbol[assignment["symbol"]].append(assignment)
    reason_counts = {s: Counter() for s in states}
    ok_counts, used = Counter(), set()
    for symbol, assignments in by_symbol.items():
        assignments.sort(key=lambda a: a["from"])
        cursor = 0
        for assignment in assignments:
            check.require(
                cursor < len(sessions) and assignment["from"] == sessions[cursor],
                "Assignment overlap/gap",
                "ASSIGNMENT",
            )
            through = bisect_right(sessions, assignment["through"])
            for state, eid in assignment["states"].items():
                check.require(
                    eid in events, "Assignment references missing event", "ASSIGNMENT"
                )
                event = events[eid]
                check.require(
                    event["symbol"] == symbol
                    and event["stateId"] == state
                    and event["computedAsOf"] <= assignment["from"],
                    "Assignment references wrong/future event",
                    "ASSIGNMENT",
                )
                used.add(eid)
                value = event["result"]
                expected = (
                    float(Decimal(value["decimalValue"]))
                    if value["status"] == "ok"
                    else None
                )
                available = value["availableDate"] if expected is not None else None
                for day in sessions[cursor:through]:
                    row = panel[(day, symbol)]
                    check.require(
                        row[state] == expected
                        and row[state + "__available_date"] == available,
                        "Panel differs from assigned event",
                        "ASSIGNMENT",
                    )
                    if expected is None:
                        reason_counts[state].update(value["reasonCodes"])
                    else:
                        ok_counts[state] += 1
            cursor = through
        check.require(
            cursor == len(sessions), "Assignment misses tail sessions", "ASSIGNMENT"
        )
    check.require(
        used == set(events), "Orphan event not represented by assignment", "ASSIGNMENT"
    )
    check.equal(
        prepared["coverage"],
        {
            s: {
                "okRows": ok_counts[s],
                "missingRows": len(panel) - ok_counts[s],
                "reasons": dict(reason_counts[s]),
            }
            for s in states
        },
        "Coverage counts differ from complete panel",
    )
    check.require(
        p["panelRows"] == len(panel) and p["stateEvents"] == len(events),
        "Prepared metadata counts differ",
        "COVERAGE",
    )
    return panel, events


def prepared_summary(package, prepared, check):
    """Recount every coverage field from events, assignments and the panel."""
    events = {event["id"]: event for event in prepared["stateEvents"]}
    selection = package["selection"]
    securities = []
    for symbol in sorted(selection["universe"]["symbols"]):
        rows = [r for r in prepared["panel"] if r["ts_code"] == symbol]
        assignments = sorted(
            (a for a in prepared["assignments"] if a["symbol"] == symbol),
            key=lambda a: a["from"],
        )
        starts = [a["from"] for a in assignments]
        details = []
        for state in selection["selectedStates"]:
            periods, reasons = set(), Counter()
            for row in rows:
                assignment = assignments[bisect_right(starts, row["trade_date"]) - 1]
                result = events[assignment["states"][state]]["result"]
                if result["status"] == "missing":
                    reasons.update(result["reasonCodes"])
                elif result["periodEnd"]:
                    periods.add(result["periodEnd"])
            valid = [r for r in rows if r[state] is not None]
            observed = [r["trade_date"] for r in valid]
            available = [r[state + "__available_date"] for r in valid]
            latest_period = max(periods, default=None)
            last_prepared = max(r["trade_date"] for r in rows)
            details.append(
                {
                    "stateId": state,
                    "status": "available" if valid else "missing",
                    "okRows": len(valid),
                    "missingRows": len(rows) - len(valid),
                    "firstAvailable": min(available, default=None),
                    "lastAvailable": max(available, default=None),
                    "firstObserved": min(observed, default=None),
                    "lastObserved": max(observed, default=None),
                    "latestPeriodEnd": latest_period,
                    "latestAvailableDate": max(available, default=None),
                    "lastPreparedDate": last_prepared,
                    "latestAgeCalendarDays": (
                        (
                            datetime.strptime(last_prepared, "%Y%m%d")
                            - datetime.strptime(latest_period, "%Y%m%d")
                        ).days
                        if latest_period
                        else None
                    ),
                    "periodEnds": sorted(periods),
                    "reasonCounts": dict(reasons),
                }
            )
        securities.append({"symbol": symbol, "rows": len(rows), "states": details})
    flags = sorted(
        {flag for event in events.values() for flag in event["result"]["qualityFlags"]}
    )
    check.equal(
        prepared["provenance"]["qualityFlags"],
        flags,
        "Prepared quality flags differ from events",
    )
    return {
        "schemaVersion": 1,
        "inputRoot": package["inputRoot"],
        "packRoot": package["packRoot"],
        "preparedRoot": prepared["provenance"]["preparedRoot"],
        "calendarRoot": prepared["provenance"]["calendar"]["root"],
        "unitPolicy": package["unitPolicy"],
        "selectedStateIds": selection["selectedStates"],
        "securities": securities,
        "stateEventCount": len(events),
        "assignmentCount": len(prepared["assignments"]),
        "qualityFlags": flags,
        "completeHistoricalVersionsVerified": False,
        "originalAsPublishedVerified": False,
        "revisionTimeVerified": False,
        "sourceScope": "selected_frozen_report_set",
    }


def audit_semantics(manifest, raw_payloads, check, pins):
    values = {name: decode(raw, TOTAL, check) for name, raw in raw_payloads.items()}
    records = registry_records(manifest, values["registryEvidence"], check, pins)
    market, joined = values["marketDataset"], values["researchRows"]
    for value in (market, joined):
        check.keys(value, {"schemaVersion", "rows", "provenance"})
        check.require(
            type(value["schemaVersion"]) is int and value["schemaVersion"] == 1,
            "Dataset row version differs",
            "TYPE",
        )
    scope = manifest["scope"]
    mcal = records[manifest["marketCalendarRef"]]
    check.require(
        mcal["kind"] == "calendar", "Market calendar ref has wrong kind", "REGISTRY"
    )
    calendar_evidence(mcal["payload"], check)
    sessions = [
        d for d in mcal["payload"]["sessions"] if scope["start"] <= d <= scope["end"]
    ]
    check.require(
        mcal["payload"]["coverage_start"] <= scope["start"]
        and mcal["payload"]["coverage_end"] >= scope["end"]
        and sessions,
        "Calendar does not cover scope",
        "CALENDAR",
    )
    check.equal(
        market["provenance"]["tradingDates"], sessions, "Market trading sessions differ"
    )
    for key in ("start", "end"):
        if key in market["provenance"]:
            check.equal(
                market["provenance"][key], scope[key], "Market provenance scope differs"
            )
    if "symbols" in market["provenance"]:
        symbols(market["provenance"]["symbols"], check)
        check.require(
            set(market["provenance"]["symbols"]) == set(scope["symbols"]),
            "Market metadata symbols differ",
            "SCOPE",
        )
    check.require(
        root(market) == manifest["roots"]["marketRoot"],
        "Market semantic root differs",
        "ROOT",
    )
    mi, ji = row_index(market["rows"], check), row_index(joined["rows"], check)
    check.require(
        set(mi) == set(ji) and list(ji) == sorted(ji),
        "Join created/deleted/reordered market rows",
        "JOIN",
    )
    check.require(
        all(d in sessions and s in scope["symbols"] for d, s in mi),
        "Market row outside scope",
        "SCOPE",
    )
    check.require(
        not any(c.startswith("model_fin_") for c in market["rows"][0])
        and not set(market["provenance"]).intersection(
            {
                "financialInputs",
                "financialDatasetRoot",
                "financialCompositionVersion",
                "preparedRoot",
            }
        ),
        "Financial namespace/evidence prefilled in market",
        "JOIN",
    )
    for key, row in mi.items():
        for column, value in row.items():
            if (
                column not in {"ts_code", "trade_date"}
                and not column.endswith("__available_date")
                and value is not None
            ):
                check.require(
                    type(value) is not bool, "Boolean is not a market number", "JOIN"
                )
                try:
                    value = float(value)
                except (TypeError, ValueError) as exc:
                    raise AuditError("JOIN", "Invalid market numeric value") from exc
            check.require(
                ji[key].get(column) == value and type(ji[key].get(column)) is not bool,
                "Join changed market field",
                "JOIN",
            )
    components = {c["componentId"]: c for c in manifest["components"]}
    financial_inputs, roots, ownership, panels, total_events = [], [], {}, {}, 0
    expected_external = dict(market["provenance"].get("externalFields", {}))
    for i, source in enumerate(manifest["financialSources"]):
        package, prepared = (
            values[f"financialInput{i}"],
            values[f"financialPrepared{i}"],
        )
        cal, raw_rows = check_package(package, source, records, scope, check)
        panel, events = check_prepared(package, prepared, raw_rows, cal, check)
        total_events += len(events)
        check.equal(
            components[f"financialInput{i}"]["semanticRoots"],
            {k: package[k] for k in ("inputRoot", "packRoot")},
            "Input descriptor roots differ",
        )
        check.equal(
            components[f"financialPrepared{i}"]["semanticRoots"],
            {
                "packRoot": package["packRoot"],
                "preparedRoot": prepared["provenance"]["preparedRoot"],
                "calendarRoot": cal["root"],
            },
            "Prepared descriptor roots differ",
        )
        selected = package["selection"]["selectedStates"]
        financial_inputs.append(
            {
                "packRoot": package["packRoot"],
                "preparedRoot": source["preparedRoot"],
                "calendarRoot": cal["root"],
                "unitPolicy": package["unitPolicy"],
                "selectedStateIds": selected,
            }
        )
        roots.append(package["packRoot"])
        panels[i] = panel
        for state in selected:
            for symbol in package["selection"]["universe"]["symbols"]:
                check.require(
                    (state, symbol) not in ownership,
                    "Overlapping financial state ownership",
                    "JOIN",
                )
                ownership[(state, symbol)] = i
            evidence = prepared["provenance"]["externalFields"][state]
            entry = {
                "symbols": sorted(package["selection"]["universe"]["symbols"]),
                "packRoot": package["packRoot"],
                "preparedRoot": source["preparedRoot"],
                "calendarRoot": cal["root"],
                "unitPolicy": package["unitPolicy"],
                "evidence": evidence,
            }
            if state not in expected_external:
                expected_external[state] = {
                    "source": "FROZEN_NATIVE_STATEMENT_PREPARATION",
                    "path": "financial_state/" + state,
                    "dataType": "number",
                    "unit": "ratio",
                    "availabilityPolicy": "point_in_time_asof",
                    "availableDateColumn": state + "__available_date",
                    "semanticKind": "native_statement_state",
                    "formulaId": state,
                    "formulaVersion": evidence["formulaVersion"],
                    "preparedInputs": [],
                    "authentication": "proof_and_calendar_authenticity_is_trusted_caller_responsibility",
                    "qualityFlags": [],
                }
            expected_external[state]["preparedInputs"].append(entry)
            expected_external[state]["qualityFlags"] = sorted(
                set(expected_external[state]["qualityFlags"])
                | set(evidence["qualityFlags"])
            )
    check.require(
        roots == sorted(set(roots)),
        "Financial source roots omitted/duplicated/reordered",
        "ROOT",
    )
    all_states = sorted({state for state, _ in ownership})
    expected_columns = (
        set(market["rows"][0])
        | set(all_states)
        | {s + "__available_date" for s in all_states}
    )
    check.require(
        set(joined["rows"][0]) == expected_columns, "Joined columns differ", "JOIN"
    )
    for key, row in ji.items():
        for state in all_states:
            owner = ownership.get((state, key[1]))
            source_row = panels[owner][key] if owner is not None else None
            for column in (state, state + "__available_date"):
                check.require(
                    row[column] == (source_row[column] if source_row else None),
                    "Financial left join differs",
                    "JOIN",
                )
    jp = joined["provenance"]
    check.equal(
        jp["externalFields"], expected_external, "Joined field evidence differs"
    )
    check.equal(
        jp["financialInputs"], financial_inputs, "Joined financial references differ"
    )
    computed = root(
        {
            "marketRoot": root(market),
            "financialInputs": financial_inputs,
            "rows": joined["rows"],
            "externalFields": expected_external,
            "compositionVersion": "financial_dataset_v1",
        }
    )
    check.require(
        jp["financialDatasetRoot"]
        == computed
        == manifest["roots"]["financialDatasetRoot"]
        and jp["marketRoot"] == root(market)
        and jp["financialCompositionVersion"] == "financial_dataset_v1",
        "Joined semantic root differs",
        "ROOT",
    )
    check.require(
        jp["rows"] == len(ji)
        and jp["source"] == "COMPOSED_FINANCIAL_DATASET"
        and jp["marketSource"] == market["provenance"].get("source")
        and jp["financialSourceScope"]
        == "selected_frozen_report_set_not_complete_filing_history",
        "Joined provenance count/source differs",
        "COVERAGE",
    )
    synthetic = bool(
        market["provenance"].get("synthetic")
        or str(market["provenance"].get("source", "")).upper().startswith("SYNTHETIC")
        or str(market["provenance"].get("classification", ""))
        .upper()
        .startswith("SYNTHETIC")
        or any(
            values[f"financialInput{i}"]["raw"]["sourceKind"] == "fixture"
            for i in range(len(financial_inputs))
        )
    )
    check.require(
        jp["synthetic"] is synthetic, "Synthetic origin evidence changed", "EVIDENCE"
    )
    check.equal(jp["tradingDates"], sessions, "Joined calendar changed")
    schema = values["schema"]
    check.keys(schema, {"columns", "externalFields", "calendarSessions"})
    check.require(
        isinstance(schema["columns"], list)
        and len(schema["columns"]) == len(expected_columns)
        and set(schema["columns"]) == expected_columns,
        "Schema columns do not cover joined rows",
        "SCHEMA",
    )
    check.equal(
        schema["externalFields"], expected_external, "Schema field evidence differs"
    )
    check.equal(schema["calendarSessions"], sessions, "Schema calendar differs")
    coverage = values["coverage"]
    check.keys(coverage, {"marketRows", "observedSymbols", "financial"})
    check.require(
        coverage["marketRows"] == len(ji), "Coverage market rows differ", "COVERAGE"
    )
    check.equal(
        coverage["observedSymbols"],
        sorted({s for _, s in ji}),
        "Coverage symbols differ",
    )
    check.require(
        len(coverage["financial"]) == len(financial_inputs),
        "Financial coverage omitted",
        "COVERAGE",
    )
    for i, summary in enumerate(coverage["financial"]):
        expected = prepared_summary(
            values[f"financialInput{i}"], values[f"financialPrepared{i}"], check
        )
        check.equal(
            summary,
            expected,
            "Complete financial coverage summary differs from retained evidence",
        )
    return {
        "marketRows": len(mi),
        "joinedRows": len(ji),
        "financialInputs": len(financial_inputs),
        "states": len(all_states),
        "events": total_events,
        "registryRecords": len(records),
        "roots": manifest["roots"],
        "financialRoots": financial_inputs,
        "preparedCoverage": [
            values[f"financialPrepared{i}"]["coverage"]
            for i in range(len(financial_inputs))
        ],
    }


def load_registry_pins(path):
    """Explicit UUID-to-file map, provided separately by an authorized caller."""
    path = Path(path)
    value = json.loads(read_file(path, MANIFEST), object_pairs_hook=_pin_pairs)
    if not isinstance(value, dict) or not 1 <= len(value) <= 2057:
        raise AuditError("PINS", "Pins require an exact UUID-to-local-file object")
    pins, total = {}, 0
    check = Checks()
    for ref, filename in value.items():
        identity(ref, check, True)
        check.require(
            isinstance(filename, str) and filename and "://" not in filename,
            "Pin must reference a local file, never a URL",
            "PINS",
        )
        target = Path(filename)
        if not target.is_absolute():
            target = path.parent / target
        raw = read_file(target, MANIFEST)
        total += len(raw)
        check.require(total <= 32 * MIB, "External pins exceed total budget", "PINS")
        pins[ref] = raw
    return pins


def _pin_pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise AuditError("PINS", "Duplicate external registry reference")
        value[key] = item
    return value


def audit_dataset(path, *, expected_root=None, registry_pins=None):
    check = Checks()
    try:
        raw, manifest, payloads = load_closure(path, check, expected_root)
        details = audit_semantics(manifest, payloads, check, registry_pins)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        raise AuditError(
            "SHAPE", "Malformed typed payload or semantic reference"
        ) from exc
    return {
        "status": "PASS",
        "auditor": "atlas.dataset.stdlib_audit/1",
        "checks": check.count,
        "datasetRoot": sha(raw),
        "datasetRootPinned": expected_root is not None,
        "format": manifest["format"],
        "profile": manifest["profile"],
        "componentCount": len(manifest["components"]),
        "partCount": sum(len(c["parts"]) for c in manifest["components"]),
        "closureBytes": len(raw) + sum(len(v) for v in payloads.values()),
        "scope": manifest["scope"],
        "integrityVerified": True,
        "semanticClosureVerified": True,
        "trustStatus": (
            "external_registry_bytes_matched"
            if registry_pins is not None
            else "unverified"
        ),
        "externalRegistryBytesMatched": registry_pins is not None,
        "sourceAuthorityVerified": False,
        "pdfAuthenticityVerified": False,
        "financialFormulasRecomputed": False,
        "modelFitted": False,
        "providerCalls": 0,
        "limitations": [
            "Caller must authorize external registry pins independently; this tool does not authenticate the operator or PDF.",
            "Financial formula arithmetic, normalized StatementRecord record_hash, and original as-published/revision history are not rederived by this closure audit.",
            "Historical provider dataFingerprint uses pandas rounding; it is not rederived. All market/joined payload bytes and semantic roots are checked.",
            "JSON nesting is limited to 64; peak Python RSS is not bounded by the 64MiB encoded-byte ceiling.",
        ],
        **details,
    }
