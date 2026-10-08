"""Pure preparation of frozen statement inputs; no network or credential access."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
import re

import pandas as pd

from .contracts import (
    FIELDS,
    FORMULA_VERSION,
    POLICY_VERSION,
    REVISION_LIMITATION,
    ContractError,
    StatementRecord,
    SourceRef,
    TradingCalendar,
    parse_date,
)
from .recipes import RECIPES, compute_states
from .results import canonical_hash
from .store import build_store
from .unit_bindings import (
    CompiledUnitBindings,
    dependency_size_bound,
    normalized_row_hash,
    validate_unit_contract,
    resolve_unit,
)

METADATA_FIELDS = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "update_flag",
)


@dataclass(frozen=True)
class AdapterBudget:
    max_requests: int = 128
    max_source_rows: int = 20000
    max_interval_days: int = 366
    max_panel_rows: int = 110000
    max_state_events: int = 10000
    max_source_bytes: int = 32 * 1024 * 1024
    max_audit_bytes: int = 32 * 1024 * 1024
    max_panel_bytes: int = 32 * 1024 * 1024
    max_prepared_bytes: int = 64 * 1024 * 1024
    response_row_limit: int = 1000

    def __post_init__(self):
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in vars(self).values()
        ):
            raise ContractError("adapter budgets must be explicit positive integers")


class AdapterError(ContractError):
    def __init__(self, code, message, partial=None):
        super().__init__(message)
        self.code = code
        self.partial = partial or {"status": "failed", "panelPublished": False}


@dataclass(frozen=True)
class AdapterResult:
    panel: pd.DataFrame
    snapshots: tuple[dict, ...]
    state_events: tuple[dict, ...]
    assignments: tuple[dict, ...]
    provenance: dict
    coverage: dict


def required_fields(selected_ids):
    ids = tuple(selected_ids)
    if (
        not ids
        or any(not isinstance(id, str) for id in ids)
        or len(ids) != len(set(ids))
        or any(id not in RECIPES for id in ids)
    ):
        raise ContractError("select unique registered financial states")
    fields = {RECIPES[id].anchor_field for id in ids}
    for id in ids:
        recipe = RECIPES[id]
        fields.update(leg[1] for leg in (*recipe.numerator, recipe.denominator))
    return tuple(sorted(fields))


def _safe_rows(frame):
    rows = []
    for record in frame.to_dict("records"):
        row = {}
        for key, value in record.items():
            if value is None or (isinstance(value, float) and math.isnan(value)):
                row[key] = None
            elif isinstance(value, (str, int, float, bool)):
                if isinstance(value, float) and not math.isfinite(value):
                    raise ContractError("provider returned a nonfinite value")
                row[key] = value
            else:
                raise ContractError("provider returned a non-scalar statement cell")
        rows.append(row)
    return rows


def _json_bytes(value):
    import json

    return len(
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    )


def validate_selection(
    strategy,
    selected_ids,
    calendar,
    *,
    announcement_start,
    source_kind="provider",
    scope="consolidated",
    flow_basis="ytd",
    unit_policy="verified_only",
    budget=None,
):
    """Validate research bounds without inferring source/calendar authenticity."""
    budget = budget or AdapterBudget()
    if not isinstance(strategy, dict) or not isinstance(budget, AdapterBudget):
        raise ContractError("explicit strategy and AdapterBudget required")
    required_fields(selected_ids)
    if not isinstance(source_kind, str) or source_kind not in {"fixture", "provider"}:
        raise ContractError("source kind must be fixture or provider")
    if not isinstance(unit_policy, str) or unit_policy not in {
        "verified_only",
        "allow_declared",
    }:
        raise ContractError("unit policy must be verified_only or allow_declared")
    if (
        not isinstance(scope, str)
        or not isinstance(flow_basis, str)
        or scope not in {"consolidated", "parent"}
        or flow_basis not in {"ytd", "quarter"}
    ):
        raise ContractError(
            "explicit supported statement scope and flow basis required"
        )
    universe = strategy.get("universe")
    if not isinstance(universe, dict) or set(universe) != {"symbols", "start", "end"}:
        raise ContractError(
            "financial preparation universe requires exactly symbols/start/end"
        )
    symbols, start, end = universe["symbols"], universe["start"], universe["end"]
    if (
        not isinstance(symbols, list)
        or not 1 <= len(symbols) <= 50
        or any(
            not isinstance(s, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", s)
            for s in symbols
        )
        or len(set(symbols)) != len(symbols)
    ):
        raise ContractError(
            "financial preparation requires 1–50 unique supported securities"
        )
    for day in (start, end, announcement_start):
        parse_date(day)
    if not announcement_start <= start <= end:
        raise ContractError(
            "announcement history must begin before the research interval"
        )
    if not isinstance(calendar, TradingCalendar):
        raise AdapterError(
            "CALENDAR_EVIDENCE_REQUIRED",
            "An explicit frozen calendar contract is required",
        )
    if (
        not calendar.complete
        or calendar.coverage_start > announcement_start
        or calendar.coverage_end < end
    ):
        raise AdapterError(
            "CALENDAR_COVERAGE",
            "Calendar must cover the declared history and research interval",
        )
    if source_kind == "provider" and calendar.kind != "official":
        raise AdapterError(
            "CALENDAR_SOURCE_UNVERIFIED",
            "Provider statements require an official calendar contract",
        )
    dates = [day for day in calendar.sessions if start <= day <= end]
    if not dates:
        raise AdapterError("NO_OBSERVATION_SESSIONS", "No covered research sessions")
    if len(dates) * len(symbols) > budget.max_panel_rows:
        raise AdapterError(
            "PANEL_BUDGET", "Requested daily panel exceeds its explicit budget"
        )
    return budget


def prepare_statement_states(
    snapshots,
    strategy,
    selected_ids,
    calendar,
    unit_contract,
    *,
    announcement_start,
    budget=None,
    source_kind="provider",
    source_provider="TUSHARE_PRO",
    scope="consolidated",
    flow_basis="ytd",
    unit_policy="verified_only",
    requests=(),
):
    """Compute states entirely from frozen inputs, without any client/read callback.

    Verified proof and calendar contracts are trusted caller inputs. This library
    never authenticates documents; public package import defaults to declarations
    only in :mod:`package`.
    """
    from .package import raw_input

    selected_ids = tuple(selected_ids)
    budget = validate_selection(
        strategy,
        selected_ids,
        calendar,
        announcement_start=announcement_start,
        source_kind=source_kind,
        scope=scope,
        flow_basis=flow_basis,
        unit_policy=unit_policy,
        budget=budget,
    )
    units = validate_unit_contract(unit_contract, source_kind)
    raw, input_root = raw_input(
        snapshots,
        calendar,
        source_kind=source_kind,
        source_provider=source_provider,
        max_source_rows=budget.max_source_rows,
        max_source_bytes=budget.max_source_bytes,
    )
    # The source and calendar are already known. Reject before compiling proof
    # hashes or expanding any per-row evidence if they cannot fit the parent.
    input_bytes = _json_bytes(raw)
    if input_bytes > budget.max_prepared_bytes:
        raise AdapterError(
            "PREPARED_BYTE_BUDGET", "Frozen input exceeds the preparation budget"
        )
    try:
        compiled = CompiledUnitBindings(
            units, max_bytes=budget.max_prepared_bytes - input_bytes
        )
    except ContractError as error:
        raise AdapterError(
            "PREPARED_BYTE_BUDGET", "Unit bindings exceed the preparation budget"
        ) from error
    input_bytes += compiled.binding_bytes
    expanded_record_bytes = 0
    snapshots = raw["snapshots"]
    symbols = set(strategy["universe"]["symbols"])
    fields = required_fields(selected_ids)
    by_endpoint = {}
    for field_id in fields:
        field = FIELDS[field_id]
        by_endpoint.setdefault(field.endpoint, []).append(field)
    records = []
    for snapshot in snapshots:
        endpoint, params = snapshot["endpoint"], snapshot["params"]
        if params["ts_code"] not in symbols:
            raise AdapterError(
                "PROVIDER_IDENTITY",
                "Snapshot security is outside the selected universe",
            )
        requested = tuple(
            field.provider_field for field in by_endpoint.get(endpoint, ())
        )
        if not set(requested).issubset(snapshot["fields"]):
            raise AdapterError(
                "RESPONSE_COLUMNS",
                "Selected financial columns are absent; this is not a null disclosure",
            )
        for row in snapshot["rows"]:
            if row["ts_code"] != params["ts_code"]:
                raise AdapterError(
                    "PROVIDER_IDENTITY",
                    "Frozen row security differs from its actual request",
                )
            announced = row["ann_date"]
            if announced is not None:
                parse_date(announced)
                if (
                    "start_date" in params
                    and not params["start_date"] <= announced <= params["end_date"]
                ) or ("ann_date" in params and announced != params["ann_date"]):
                    raise AdapterError(
                        "PROVIDER_DATE_RANGE",
                        "Frozen announcement lies outside its actual request",
                    )
            for parameter, column in (
                ("period", "end_date"),
                ("report_type", "report_type"),
                ("comp_type", "comp_type"),
            ):
                if parameter in params and params[parameter] != row[column]:
                    raise AdapterError(
                        "PROVIDER_SELECTOR",
                        "Frozen row contradicts an explicit request selector",
                    )
            if not requested:
                continue
            row_hash = normalized_row_hash(endpoint, row)
            selections = {
                field.id: compiled.select(
                    field.id,
                    source_provider,
                    snapshot["id"],
                    endpoint,
                    row,
                    input_root=input_root,
                    row_hash=row_hash,
                )
                for field in by_endpoint[endpoint]
                if field.id in units
            }
            # Charge every source row before constructing UnitScope/record
            # objects. Later store deduplication cannot erase this expansion.
            record_bound = (
                _json_bytes(row)
                + _json_bytes(source_provider)
                + _json_bytes(snapshot["retrievedAt"])
                + 2048
                + sum(compiled.estimated_unit_bytes(s) for s in selections.values())
            )
            if (
                input_bytes + expanded_record_bytes + record_bound
                > budget.max_prepared_bytes
            ):
                raise AdapterError(
                    "PREPARED_BYTE_BUDGET",
                    "Expanded source-record evidence exceeds the preparation budget",
                )
            expanded_record_bytes += record_bound
            try:
                records.append(
                    StatementRecord(
                        row["ts_code"],
                        endpoint,
                        row["end_date"],
                        announced,
                        row["f_ann_date"],
                        row["report_type"],
                        row["comp_type"],
                        {field: row[field] for field in requested},
                        {
                            field.provider_field: compiled.resolve(
                                selections[field.id],
                                field.id,
                                source_provider,
                            )
                            for field in by_endpoint[endpoint]
                            if field.id in units
                        },
                        SourceRef(
                            source_provider,
                            snapshot["id"],
                            snapshot["retrievedAt"],
                            source_kind,
                        ),
                        row["update_flag"],
                        requested_fields=requested,
                    )
                )
            except ContractError as error:
                raise AdapterError(
                    "RECORD_CONTRACT",
                    "Frozen row violates the statement/date/numeric contract",
                ) from error
    return _compute_prepared(
        records,
        snapshots,
        strategy,
        selected_ids,
        calendar,
        announcement_start=announcement_start,
        budget=budget,
        source_kind=source_kind,
        source_provider=source_provider,
        scope=scope,
        flow_basis=flow_basis,
        requests=requests,
        unit_policy=unit_policy,
        input_root=input_root,
        retained_input_bytes=input_bytes + expanded_record_bytes,
        expanded_record_bytes=expanded_record_bytes,
        binding_bytes=compiled.binding_bytes,
    )


def _compute_prepared(
    records,
    snapshots,
    strategy,
    selected_ids,
    calendar,
    *,
    announcement_start,
    budget,
    source_kind,
    source_provider,
    scope,
    flow_basis,
    requests,
    unit_policy,
    input_root,
    retained_input_bytes,
    expanded_record_bytes,
    binding_bytes,
):
    symbols = strategy["universe"]["symbols"]
    start, end = strategy["universe"]["start"], strategy["universe"]["end"]
    dates = [day for day in calendar.sessions if start <= day <= end]
    source_rows = sum(item["rowCount"] for item in snapshots)
    source_bytes = sum(item["byteLength"] for item in snapshots)

    def fail(code, message):
        raise AdapterError(
            code,
            message,
            {
                "status": "failed",
                "panelPublished": False,
                "requests": list(requests),
                "sourceRows": source_rows,
                "snapshotMetadata": [
                    {
                        key: item[key]
                        for key in (
                            "id",
                            "endpoint",
                            "params",
                            "retrievedAt",
                            "rowCount",
                            "byteLength",
                        )
                    }
                    for item in snapshots
                ],
            },
        )

    store = build_store(
        records, calendar, unit_policy=unit_policy, input_root=input_root
    )
    panel, events, assignments = [], [], []
    coverage = {
        id: {"okRows": 0, "missingRows": 0, "reasons": Counter()} for id in selected_ids
    }
    audit_bytes = panel_bytes = assignment_bytes = 0

    def check_total(additional=0):
        if (
            retained_input_bytes
            + audit_bytes
            + panel_bytes
            + assignment_bytes
            + additional
            > budget.max_prepared_bytes
        ):
            fail(
                "PREPARED_BYTE_BUDGET",
                "Inputs, expanded evidence, panel and audit exceed the preparation budget",
            )

    for symbol in symbols:
        transitions = sorted(
            {
                date
                for row in store.observations
                if row.symbol == symbol and row.available
                for date in [row.available]
                if dates[0] < date <= dates[-1]
            }
        )
        event_dates = [dates[0], *transitions]
        event_index, current, current_assignment = 0, None, None
        for day in dates:
            if current is None or (
                event_index < len(event_dates) and event_dates[event_index] <= day
            ):
                while (
                    event_index < len(event_dates) and event_dates[event_index] <= day
                ):
                    event_index += 1
                if len(events) + len(selected_ids) > budget.max_state_events:
                    fail(
                        "STATE_EVENT_BUDGET",
                        "State audit event budget exceeded; no partial panel published",
                    )
                current = compute_states(
                    store, symbol, day, selected_ids, scope=scope, flow_basis=flow_basis
                )
                references = {}
                for id, value in current.items():
                    # Bound repeated dependency/hash lists before asdict and
                    # result-level aggregation allocate their expanded copies.
                    event_bound = (
                        8192
                        + _json_bytes(
                            asdict(value.calendar_evidence)
                            if value.calendar_evidence
                            else None
                        )
                        + sum(dependency_size_bound(d) for d in value.dependencies)
                        + 70
                        * sum(len(d.declaration_hashes) for d in value.dependencies)
                    )
                    if audit_bytes + event_bound > budget.max_audit_bytes:
                        fail(
                            "AUDIT_BYTE_BUDGET",
                            "Expanded audit evidence exceeds its byte budget",
                        )
                    check_total(event_bound)
                    event = {
                        "symbol": symbol,
                        "stateId": id,
                        "computedAsOf": day,
                        "result": value.to_dict(),
                    }
                    audit_bytes += (
                        _json_bytes(event) + 73
                    )  # id and collection separator
                    if audit_bytes > budget.max_audit_bytes:
                        fail(
                            "AUDIT_BYTE_BUDGET",
                            "State audit evidence exceeds its explicit byte budget",
                        )
                    identity = canonical_hash(event)
                    check_total()
                    events.append({"id": identity, **event})
                    references[id] = identity
                current_assignment = {
                    "symbol": symbol,
                    "from": day,
                    "through": day,
                    "states": references,
                }
                assignment_bytes += _json_bytes(current_assignment)
                check_total()
                assignments.append(current_assignment)
            current_assignment["through"] = day
            row = {"ts_code": symbol, "trade_date": day}
            for id, value in current.items():
                number = float(value.value) if value.status == "ok" else None
                if number is not None and (
                    not math.isfinite(number) or (number == 0 and value.value != 0)
                ):
                    fail(
                        "FLOAT_CONVERSION",
                        "State cannot be represented by the engine numeric type",
                    )
                row[id] = number
                row[id + "__available_date"] = (
                    value.available_date if value.status == "ok" else None
                )
                coverage[id]["okRows" if value.status == "ok" else "missingRows"] += 1
                if value.status == "missing":
                    coverage[id]["reasons"].update(value.reason_codes)
            panel_bytes += _json_bytes(row)
            if panel_bytes > budget.max_panel_bytes:
                fail(
                    "PANEL_BYTE_BUDGET",
                    "Prepared daily state panel exceeds its explicit byte budget",
                )
            check_total()
            panel.append(row)
    # Provenance repeats a bounded subset of the event evidence. Reserve that
    # aggregate before constructing its sets/lists; counting over retained
    # event arrays does not duplicate the proofs themselves.
    metadata_bound = (
        8192 * (len(selected_ids) + 1)
        + _json_bytes(requests)
        + _json_bytes(asdict(store.calendar_evidence))
        + 140 * sum(len(event["result"]["declarationHashes"]) for event in events)
    )
    check_total(metadata_bound)
    external = {
        id: {
            "source": (
                "TUSHARE_STATEMENT_DERIVED"
                if source_kind == "provider"
                else "HAND_STATEMENT_FIXTURE"
            ),
            "path": FORMULA_VERSION + ":" + id,
            "dataType": "number",
            "unit": "ratio",
            "availabilityPolicy": "point_in_time_asof",
            "availabilityEvidenceLevel": (
                "synthetic_disclosure_dates"
                if source_kind == "fixture"
                else "vendor_reported_disclosure_dates"
            ),
            "originalAsPublishedVerified": False,
            "revisionTimeVerified": False,
            "availableDateColumn": id + "__available_date",
            "formulaId": id,
            "formulaVersion": FORMULA_VERSION,
            "formulaDefinition": asdict(RECIPES[id]),
            "unitPolicy": unit_policy,
            "qualityFlags": sorted(
                {
                    flag
                    for event in events
                    if event["stateId"] == id
                    for flag in event["result"]["qualityFlags"]
                }
            ),
            "unitEvidenceLevels": sorted(
                {
                    level
                    for event in events
                    if event["stateId"] == id
                    for level in event["result"]["unitEvidenceLevels"]
                }
            ),
            "declarationHashes": sorted(
                {
                    h
                    for event in events
                    if event["stateId"] == id
                    for h in event["result"]["declarationHashes"]
                }
            ),
        }
        for id in selected_ids
    }
    provenance = {
        "adapterVersion": "statement_adapter_v1",
        "policyVersion": POLICY_VERSION,
        "formulaVersion": FORMULA_VERSION,
        "sourceKind": source_kind,
        "provider": source_provider,
        "announcementCoverage": {
            "start": announcement_start,
            "end": end,
            "completeHistoricalVersionsVerified": False,
        },
        "financialRevisionHistory": REVISION_LIMITATION,
        "availabilityEvidenceLevel": (
            "synthetic_disclosure_dates"
            if source_kind == "fixture"
            else "vendor_reported_disclosure_dates"
        ),
        "originalAsPublishedVerified": False,
        "revisionTimeVerified": False,
        "availabilityLimitation": "Aligns the returned values by declared disclosure dates; does not establish when a specific revised value was historically known",
        "unitEvidencePolicy": "statement_unit_scope_v2",
        "unitPolicy": unit_policy,
        "inputRoot": input_root,
        "qualityFlags": sorted(
            {flag for event in events for flag in event["result"]["qualityFlags"]}
        ),
        "declarationHashes": sorted(
            {h for event in events for h in event["result"]["declarationHashes"]}
        ),
        "externalFields": external,
        "sourceSnapshots": [item["id"] for item in snapshots],
        "requests": requests,
        "calendar": store.calendar_evidence.__dict__,
        "snapshotRepresentation": "normalized_provider_table_snapshot",
        "wireEvidence": {
            "availability": "UNKNOWN",
            "wireBytesAvailable": False,
            "wireNumericLexemesAvailable": False,
        },
        "inputNumericPrecision": "Client JSON decoding/pandas may already coerce values to binary float; Decimal does not restore lost source precision",
        "numericConversion": "Normalized table values to Decimal34-derived states to finite IEEE754 float; audit is not original wire numeric text",
        "stateEvents": len(events),
        "sourceRows": source_rows,
        "sourceBytes": source_bytes,
        "auditBytes": audit_bytes,
        "panelRows": len(panel),
        "panelBytes": panel_bytes,
        "assignmentBytes": assignment_bytes,
        "bindingBytes": binding_bytes,
        "expandedRecordByteBound": expanded_record_bytes,
        "resourceAccounting": "conservative_serialized_expansion_v1_not_rss",
    }
    coverage = {
        id: {**counts, "reasons": dict(counts["reasons"])}
        for id, counts in coverage.items()
    }
    prepared = {
        "inputRoot": input_root,
        "unitPolicy": unit_policy,
        "selection": {
            "universe": strategy["universe"],
            "selectedStates": list(selected_ids),
            "scope": scope,
            "flowBasis": flow_basis,
            "announcementStart": announcement_start,
        },
        "panel": panel,
        "stateEvents": events,
        "assignments": assignments,
        "coverage": coverage,
    }
    # Final envelope accounting includes collection delimiters and metadata.
    if (
        _json_bytes(prepared) + _json_bytes(provenance) + retained_input_bytes + 82
        > budget.max_prepared_bytes
    ):
        fail("PREPARED_BYTE_BUDGET", "Frozen preparation exceeds its total byte budget")
    provenance["preparedRoot"] = canonical_hash(prepared)
    return AdapterResult(
        pd.DataFrame(panel),
        tuple(snapshots),
        tuple(events),
        tuple(assignments),
        provenance,
        coverage,
    )
