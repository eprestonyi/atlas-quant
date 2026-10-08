"""Pure computation and bounded publication projection; never network or files."""

from bisect import bisect_right
from collections import Counter
from dataclasses import replace
from datetime import datetime
import math

from ..financial_statements.contracts import ContractError
from ..financial_statements.package import _encoded, prepare_package
from ..financial_statements.prepare import AdapterBudget, required_fields
from .protocol import (
    CHUNK_BYTES,
    CHUNK_COUNT,
    MANIFEST_BYTES,
    PACKAGE_BYTES,
    RESULT_BYTES,
    encode,
    fail,
    sha,
)
from .trust import resolve_input, revise_input


class PublicationWriter:
    """At most one encoded chunk is buffered; budget checks precede callbacks."""

    def __init__(self, write_chunk):
        if not callable(write_chunk):
            fail("FINANCIAL_PROTOCOL", "需要有界分片写入回调。")
        self.write_chunk = write_chunk
        self.collections = {}
        self.byte_count = 0
        self.chunk_count = 0

    def _write(self, name, descriptor, raw, start, count):
        if (
            not raw
            or len(raw) > CHUNK_BYTES
            or self.byte_count + len(raw) > RESULT_BYTES
            or self.chunk_count + 1 > CHUNK_COUNT
        ):
            fail("PREPARED_BYTE_BUDGET", "财务结果超过分片或累计发布预算。")
        part = {
            "ordinal": len(descriptor["chunks"]),
            "startRow": start,
            "rowCount": count,
            "byteLength": len(raw),
            "sha256": sha(raw),
        }
        self.write_chunk(name, part["ordinal"], raw)
        descriptor["chunks"].append(part)
        descriptor["byteLength"] += len(raw)
        self.byte_count += len(raw)
        self.chunk_count += 1

    def package(self, raw):
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= PACKAGE_BYTES:
            fail("FINANCIAL_BYTE_BUDGET", "规范化财务输入超过包预算。")
        # The complete known collection must fit before its first callback.
        parts = math.ceil(len(raw) / CHUNK_BYTES)
        if (
            self.byte_count + len(raw) > RESULT_BYTES
            or self.chunk_count + parts > CHUNK_COUNT
        ):
            fail("PREPARED_BYTE_BUDGET", "规范化输入超过剩余发布预算。")
        descriptor = {
            "encoding": "bytes",
            "rowCount": None,
            "byteLength": 0,
            "sha256": sha(raw),
            "chunks": [],
        }
        for offset in range(0, len(raw), CHUNK_BYTES):
            self._write(
                "package", descriptor, raw[offset : offset + CHUNK_BYTES], None, None
            )
        self.collections["package"] = descriptor

    def records(self, name, records):
        row_limits = {
            "panel": 110000,
            "events": 10000,
            "dependencies": 500000,
            "assignments": 500000,
            "coverage": 800,
        }
        if name not in row_limits or name in self.collections:
            fail("FINANCIAL_PROTOCOL", "财务记录集合未知或重复。")
        descriptor = {
            "encoding": "json_records",
            "rowCount": 0,
            "byteLength": 0,
            "chunks": [],
        }
        pending, size = [], 2

        def flush():
            nonlocal pending, size
            if pending:
                self._write(
                    name,
                    descriptor,
                    b"[" + b",".join(pending) + b"]",
                    descriptor["rowCount"],
                    len(pending),
                )
                descriptor["rowCount"] += len(pending)
                pending, size = [], 2

        for row in records:
            if descriptor["rowCount"] + len(pending) >= row_limits[name]:
                fail("PREPARED_ROW_BUDGET", "财务记录集合超过已声明行数上限。")
            raw = encode(row)
            if len(raw) + 2 > CHUNK_BYTES:
                fail(
                    "FINANCIAL_RECORD_BYTE_BUDGET",
                    "单条完整财务证据超过分片上限，不能截断。",
                )
            if len(pending) == 500 or size + len(raw) + bool(pending) > CHUNK_BYTES:
                flush()
            prospective = size + len(raw) + bool(pending)
            if (
                self.byte_count + prospective > RESULT_BYTES
                or self.chunk_count >= CHUNK_COUNT
            ):
                fail("PREPARED_BYTE_BUDGET", "展开财务记录超过累计发布预算。")
            pending.append(raw)
            size = prospective
        flush()
        self.collections[name] = descriptor


def _panel_rows(panel):
    columns = list(panel.columns)
    for values in panel.itertuples(index=False, name=None):
        yield {
            key: (
                None
                if value is None or isinstance(value, float) and math.isnan(value)
                else value
            )
            for key, value in zip(columns, values)
        }


def coverage_rows(package, prepared):
    """Same exact date and day-weighted coverage semantics as the offline bridge."""
    events = {event["id"]: event for event in prepared.state_events}
    states = package["selection"]["selectedStates"]
    for symbol, panel in prepared.panel.groupby("ts_code", sort=True):
        intervals = sorted(
            (a for a in prepared.assignments if a["symbol"] == symbol),
            key=lambda a: a["from"],
        )
        starts = [a["from"] for a in intervals]
        reasons = {state: Counter() for state in states}
        periods = {state: set() for state in states}
        for row in panel.itertuples(index=False):
            position = bisect_right(starts, row.trade_date) - 1
            if position < 0 or row.trade_date > intervals[position]["through"]:
                fail("FINANCIAL_ASSIGNMENT_GAP", "准备面板缺少对应的证据区间。")
            for state in states:
                result = events[intervals[position]["states"][state]]["result"]
                if result["status"] == "missing":
                    reasons[state].update(result["reasonCodes"])
                elif result["periodEnd"]:
                    periods[state].add(result["periodEnd"])
        for state in states:
            mask = panel[state].notna()
            valid = panel.loc[mask]
            period = max(periods[state], default=None)
            last = panel.trade_date.max()
            availability = valid[state + "__available_date"]
            yield {
                "symbol": symbol,
                "stateId": state,
                "status": "available" if mask.any() else "missing",
                "okRows": int(mask.sum()),
                "missingRows": int((~mask).sum()),
                "firstAvailable": availability.min() if len(valid) else None,
                "firstObserved": valid.trade_date.min() if len(valid) else None,
                "lastObserved": valid.trade_date.max() if len(valid) else None,
                "lastAvailable": availability.max() if len(valid) else None,
                "latestPeriodEnd": period,
                "latestAvailableDate": availability.max() if len(valid) else None,
                "lastPreparedDate": last,
                "latestAgeCalendarDays": (
                    (
                        datetime.strptime(last, "%Y%m%d")
                        - datetime.strptime(period, "%Y%m%d")
                    ).days
                    if period
                    else None
                ),
                "periodEnds": sorted(periods[state]),
                "reasonCounts": dict(sorted(reasons[state].items())),
            }


def _input_summary(package):
    raw, selection = package["raw"], package["selection"]
    declared = any(
        binding["type"] == "DeclaredUnitBinding"
        for bindings in package["bindings"].values()
        for binding in bindings
    )
    return {
        "source": {
            "kind": raw["sourceKind"],
            "provider": raw["sourceProvider"],
            "snapshotRepresentation": "normalized_provider_table_snapshot",
        },
        "selection": {
            **selection["universe"],
            "announcementStart": selection["announcementStart"],
            "selectedStateIds": selection["selectedStates"],
            "scope": selection["scope"],
            "flowBasis": selection["flowBasis"],
        },
        "unitPolicy": package["unitPolicy"],
        "counts": {
            "snapshots": len(raw["snapshots"]),
            "sourceRows": sum(len(snapshot["rows"]) for snapshot in raw["snapshots"]),
            "bindings": sum(len(entries) for entries in package["bindings"].values()),
        },
        "evidence": {
            "availabilityEvidenceLevel": (
                "synthetic_disclosure_dates"
                if raw["sourceKind"] == "fixture"
                else "vendor_reported_disclosure_dates"
            ),
            "originalAsPublishedVerified": False,
            "revisionTimeVerified": False,
            "unitAssumptions": declared,
        },
    }


def _project_events(events):
    offset = 0
    for event in events:
        dependencies = event["result"]["dependencies"]
        yield {
            **event,
            "result": {k: v for k, v in event["result"].items() if k != "dependencies"},
            "dependencyStart": offset if dependencies else 0,
            "dependencyCount": len(dependencies),
        }
        offset += len(dependencies)


def _project_dependencies(events):
    for event in events:
        for index, dependency in enumerate(event["result"]["dependencies"]):
            yield {"eventId": event["id"], "index": index, "dependency": dependency}


def compute_publication(job, input_meta, source_bytes, registry_bytes, write_chunk):
    """Verify/compute once and return a manifest only after every chunk succeeds.

    The callback may create a durable encrypted staging file. No manifest is
    returned on a failed budget, core validation or callback; callers must never
    publish those partial chunks as a complete financial result.
    """
    try:
        trusted = resolve_input(job, input_meta, source_bytes, registry_bytes)
        package = (
            revise_input(trusted, input_meta["operation"])
            if job["kind"] == "financial_revise"
            else trusted.package
        )
        prepared = None
        if job["kind"] == "financial_prepare":
            prepared = prepare_package(
                package,
                trusted_unit_proofs=trusted.reviewed_proofs,
                budget=replace(AdapterBudget(), max_prepared_bytes=RESULT_BYTES),
            )
        summary = {
            "input": _input_summary(package),
            "validation": {
                "status": "passed",
                "issues": [],
                "missingPrerequisites": [],
            },
        }
        missing_fields = sorted(
            set(required_fields(package["selection"]["selectedStates"]))
            - set(package["bindings"])
        )
        summary["validation"]["missingPrerequisites"] = [
            {"code": "UNIT_BINDING_ABSENT", "fieldId": field}
            for field in missing_fields
        ]
        if prepared is not None:
            p = prepared.provenance
            summary["preparation"] = {
                key: p[key]
                for key in (
                    "formulaVersion",
                    "policyVersion",
                    "qualityFlags",
                    "availabilityEvidenceLevel",
                    "originalAsPublishedVerified",
                    "revisionTimeVerified",
                    "resourceAccounting",
                )
            }
            summary["hasUsableStates"] = any(
                value["okRows"] > 0 for value in prepared.coverage.values()
            )
        if len(encode(summary)) > 32 * 1024:
            fail("FINANCIAL_MANIFEST_BUDGET", "财务摘要超过有限元数据预算。")
        writer = PublicationWriter(write_chunk)
        writer.package(_encoded(package))
        if prepared is not None:
            writer.records("panel", _panel_rows(prepared.panel))
            writer.records("events", _project_events(prepared.state_events))
            writer.records("dependencies", _project_dependencies(prepared.state_events))
            writer.records("assignments", iter(prepared.assignments))
            writer.records("coverage", coverage_rows(package, prepared))
        manifest = {
            "format": "atlas.quant.financial-result",
            "version": 1,
            "kind": {
                "financial_validate": "validated",
                "financial_revise": "revised",
                "financial_prepare": "prepared",
            }[job["kind"]],
            "inputId": job["inputId"],
            "roots": {
                "inputRoot": package["inputRoot"],
                "packRoot": package["packRoot"],
                "preparedRoot": (
                    prepared.provenance["preparedRoot"] if prepared else None
                ),
                "calendarRoot": trusted.calendar_root,
            },
            "summary": summary,
            "collections": writer.collections,
        }
        if len(encode(manifest)) > MANIFEST_BYTES:
            fail("FINANCIAL_MANIFEST_BUDGET", "财务分片清单超过元数据预算。")
        return manifest
    except ContractError as error:
        fail(getattr(error, "code", "FINANCIAL_PACKAGE_INVALID"), str(error)[:700])
