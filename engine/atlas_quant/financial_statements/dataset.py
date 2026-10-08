"""Pure financial-package composition into a frozen market dataset.

No metadata string grants trust. Every package is revalidated and prepared;
reviewed proofs remain an explicit out-of-band trusted-caller responsibility.
"""

from bisect import bisect_right
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
import math

import pandas as pd

from ..provider import _records, _validate_panel, validate_universe
from .contracts import ContractError
from .package import _encoded, _private_content, decode_package, prepare_package
from .prepare import AdapterBudget, AdapterError, AdapterResult, _safe_rows
from .results import canonical_hash


@dataclass(frozen=True)
class DatasetBudget:
    max_inputs: int = 8
    max_input_bytes: int = 24 * 1024 * 1024
    max_output_bytes: int = 24 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_rows: int = 110000

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise ContractError("dataset budgets must be positive integers")


@dataclass(frozen=True)
class FinancialArtifact:
    package: dict
    prepared: AdapterResult
    summary: dict


@dataclass(frozen=True)
class FinancialDatasetResult:
    data: pd.DataFrame
    provenance: dict
    financial_artifacts: tuple[FinancialArtifact, ...]

    def to_dataset(self):
        """Small metadata and joined rows; private sidecars are separate."""
        return {
            "schemaVersion": 1,
            "rows": _safe_rows(self.data),
            "provenance": deepcopy(self.provenance),
        }


def _fail(code, message):
    raise AdapterError(code, message)


def _bounded_rows(frame, maximum):
    """Retain rows incrementally; never first allocate an oversized records list."""
    rows, total = [], 2
    columns = list(frame.columns)
    for values in frame.itertuples(index=False, name=None):
        row = {
            key: (
                None
                if value is None or isinstance(value, float) and math.isnan(value)
                else value
            )
            for key, value in zip(columns, values)
        }
        total += len(_encoded(row)) + 1
        if total > maximum:
            _fail(
                "FINANCIAL_DATASET_BUDGET",
                "Expanded joined rows exceed the dataset budget",
            )
        rows.append(row)
    return rows


def summarize_prepared(package, prepared):
    """Coverage per security, without raw statement values or full dependencies."""
    events = {event["id"]: event for event in prepared.state_events}
    states = package["selection"]["selectedStates"]
    securities = []
    for symbol, panel in prepared.panel.groupby("ts_code", sort=True):
        intervals = sorted(
            (a for a in prepared.assignments if a["symbol"] == symbol),
            key=lambda a: a["from"],
        )
        starts = [a["from"] for a in intervals]
        counts = {state: Counter() for state in states}
        periods = {state: set() for state in states}
        for row in panel.itertuples(index=False):
            position = bisect_right(starts, row.trade_date) - 1
            if position < 0 or row.trade_date > intervals[position]["through"]:
                _fail("FINANCIAL_ASSIGNMENT_GAP", "Prepared row has no audit interval")
            assignment = intervals[position]
            for state in states:
                result = events[assignment["states"][state]]["result"]
                if result["status"] == "missing":
                    counts[state].update(result["reasonCodes"])
                elif result["periodEnd"]:
                    periods[state].add(result["periodEnd"])
        summaries = []
        for state in states:
            valid = panel[state].notna()
            observed = panel.loc[valid]
            latest_period = max(periods[state], default=None)
            last_prepared = panel.trade_date.max()
            summaries.append(
                {
                    "stateId": state,
                    "status": "available" if valid.any() else "missing",
                    "okRows": int(valid.sum()),
                    "missingRows": int((~valid).sum()),
                    "firstAvailable": (
                        observed[state + "__available_date"].min()
                        if len(observed)
                        else None
                    ),
                    "firstObserved": (
                        observed.trade_date.min() if len(observed) else None
                    ),
                    "lastObserved": (
                        observed.trade_date.max() if len(observed) else None
                    ),
                    "lastAvailable": (
                        observed[state + "__available_date"].max()
                        if len(observed)
                        else None
                    ),
                    "latestPeriodEnd": latest_period,
                    "latestAvailableDate": (
                        observed[state + "__available_date"].max()
                        if len(observed)
                        else None
                    ),
                    "lastPreparedDate": last_prepared,
                    "latestAgeCalendarDays": (
                        (
                            datetime.strptime(last_prepared, "%Y%m%d")
                            - datetime.strptime(latest_period, "%Y%m%d")
                        ).days
                        if latest_period
                        else None
                    ),
                    "periodEnds": sorted(periods[state]),
                    "reasonCounts": dict(sorted(counts[state].items())),
                }
            )
        securities.append({"symbol": symbol, "rows": len(panel), "states": summaries})
    return {
        "schemaVersion": 1,
        "inputRoot": package["inputRoot"],
        "packRoot": package["packRoot"],
        "preparedRoot": prepared.provenance["preparedRoot"],
        "calendarRoot": prepared.provenance["calendar"]["root"],
        "unitPolicy": package["unitPolicy"],
        "selectedStateIds": list(states),
        "securities": securities,
        "stateEventCount": len(prepared.state_events),
        "assignmentCount": len(prepared.assignments),
        "qualityFlags": prepared.provenance["qualityFlags"],
        "completeHistoricalVersionsVerified": False,
        "originalAsPublishedVerified": False,
        "revisionTimeVerified": False,
        "sourceScope": "selected_frozen_report_set",
    }


def compose_financial_dataset(
    strategy,
    market_dataset,
    financial_packages,
    *,
    trusted_unit_proofs=False,
    budget=None
):
    """Validate, prepare and join immutable inputs; never fetch or impute prices.

    Public callers must not expose ``trusted_unit_proofs`` as an upload option.
    The integration layer must authenticate calendars and resolve proof refs.
    This function verifies structure/scope and integrity, not reviewer identity.
    """
    if type(trusted_unit_proofs) is not bool:
        raise ContractError("trusted unit proofs is an out-of-band boolean contract")
    budget = budget or DatasetBudget()
    if not isinstance(budget, DatasetBudget):
        raise ContractError("explicit DatasetBudget required")
    if (
        not isinstance(financial_packages, (list, tuple))
        or not 1 <= len(financial_packages) <= budget.max_inputs
    ):
        _fail(
            "FINANCIAL_INPUT_BUDGET",
            "Provide a bounded collection of financial packages",
        )
    if not isinstance(market_dataset, dict) or set(market_dataset) - {
        "schemaVersion",
        "rows",
        "provenance",
    }:
        _fail(
            "INVALID_MARKET_DATASET",
            "Frozen market dataset requires rows and provenance",
        )
    _private_content(market_dataset)
    market_raw = _encoded(market_dataset)
    encoded, input_bytes = [], 0
    for value in financial_packages:
        raw = value if isinstance(value, bytes) else _encoded(value)
        input_bytes += len(raw)
        if (
            input_bytes > budget.max_input_bytes
            or len(market_raw) + input_bytes >= budget.max_total_bytes
        ):
            _fail(
                "FINANCIAL_DATASET_BUDGET",
                "Known frozen inputs exceed the common dataset budget",
            )
        encoded.append(raw)
    retained = len(market_raw) + input_bytes
    if len(market_raw) > budget.max_output_bytes:
        _fail(
            "FINANCIAL_DATASET_BUDGET",
            "Known frozen inputs exceed the common dataset budget",
        )
    meta = market_dataset.get("provenance")
    rows = market_dataset.get("rows")
    if (
        not isinstance(meta, dict)
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= budget.max_rows
    ):
        _fail(
            "INVALID_MARKET_DATASET", "Bounded market rows and provenance are required"
        )
    reserved = {
        "financialInputs",
        "financialDatasetRoot",
        "financialCompositionVersion",
        "preparedRoot",
    }
    registry = meta.get("externalFields", {})
    if (
        set(meta) & reserved
        or not isinstance(registry, dict)
        or any(str(field).startswith("model_fin_") for field in registry)
        or any(
            isinstance(row, dict)
            and any(str(field).startswith("model_fin_") for field in row)
            for row in rows
        )
    ):
        _fail(
            "FINANCIAL_FIELD_COLLISION",
            "Reserved financial evidence must come from revalidated packages",
        )
    symbols, start, end = validate_universe(strategy)
    market = _validate_panel(strategy, rows, external_fields=registry)
    dates = meta.get("tradingDates")
    if (
        not isinstance(dates, list)
        or not dates
        or dates != sorted(set(dates))
        or not set(market.trade_date).issubset(dates)
    ):
        _fail(
            "FINANCIAL_CALENDAR_MISMATCH",
            "An explicit complete matching market calendar is required",
        )
    packages = [
        decode_package(raw, trusted_unit_proofs=trusted_unit_proofs) for raw in encoded
    ]
    if len({p["packRoot"] for p in packages}) != len(packages):
        _fail(
            "DUPLICATE_FINANCIAL_INPUT",
            "Duplicate package roots are not separate evidence",
        )
    packages.sort(key=lambda p: p["packRoot"])
    claimed = set()
    for package in packages:
        scope = package["selection"]["universe"]
        sessions = package["raw"]["calendar"]["sessions"]
        selected_dates = [d for d in sessions if start <= d <= end]
        if (
            scope["start"] != start
            or scope["end"] != end
            or not set(scope["symbols"]).issubset(symbols)
            or selected_dates != dates
        ):
            _fail(
                "FINANCIAL_CALENDAR_MISMATCH",
                "Package scope/calendar must exactly match the research interval",
            )
        for symbol in scope["symbols"]:
            for state in package["selection"]["selectedStates"]:
                if (symbol, state) in claimed:
                    _fail(
                        "FINANCIAL_FIELD_COLLISION",
                        "Overlapping financial sources require an explicit new combined package",
                    )
                claimed.add((symbol, state))
    output = market.copy()
    artifacts, field_sources = [], {}
    for package in packages:
        remaining = budget.max_total_bytes - retained
        prepared = prepare_package(
            package,
            trusted_unit_proofs=trusted_unit_proofs,
            budget=replace(AdapterBudget(), max_prepared_bytes=remaining),
        )
        private_body = {
            "panel": _safe_rows(prepared.panel),
            "provenance": prepared.provenance,
            "stateEvents": prepared.state_events,
            "assignments": prepared.assignments,
            "coverage": prepared.coverage,
        }
        retained += len(_encoded(private_body))
        if retained >= budget.max_total_bytes:
            _fail(
                "FINANCIAL_DATASET_BUDGET",
                "Prepared financial evidence exceeds the common dataset budget",
            )
        summary = summarize_prepared(package, prepared)
        artifacts.append(FinancialArtifact(package, prepared, summary))
        right = prepared.panel.set_index(["ts_code", "trade_date"])
        index = pd.MultiIndex.from_frame(output[["ts_code", "trade_date"]])
        package_symbols = set(package["selection"]["universe"]["symbols"])
        mask = output.ts_code.isin(package_symbols)
        for state in package["selection"]["selectedStates"]:
            for column in (state, state + "__available_date"):
                if column not in output:
                    output[column] = None
                output.loc[mask, column] = right[column].reindex(index[mask]).to_numpy()
            field_sources.setdefault(state, []).append(
                {
                    "symbols": sorted(package_symbols),
                    "packRoot": package["packRoot"],
                    "preparedRoot": prepared.provenance["preparedRoot"],
                    "calendarRoot": summary["calendarRoot"],
                    "unitPolicy": package["unitPolicy"],
                    "evidence": prepared.provenance["externalFields"][state],
                }
            )
    external = deepcopy(registry)
    for state, sources in field_sources.items():
        external[state] = {
            "source": "FROZEN_NATIVE_STATEMENT_PREPARATION",
            "path": "financial_state/" + state,
            "dataType": "number",
            "unit": "ratio",
            "availabilityPolicy": "point_in_time_asof",
            "availableDateColumn": state + "__available_date",
            "semanticKind": "native_statement_state",
            "formulaId": state,
            "formulaVersion": sources[0]["evidence"]["formulaVersion"],
            "preparedInputs": sources,
            "authentication": "proof_and_calendar_authenticity_is_trusted_caller_responsibility",
            "qualityFlags": sorted(
                {f for item in sources for f in item["evidence"]["qualityFlags"]}
            ),
        }
    records = _bounded_rows(
        output, min(budget.max_output_bytes, budget.max_total_bytes - retained)
    )
    output = _validate_panel(strategy, records, external_fields=external)
    financial_inputs = [
        {
            key: artifact.summary[key]
            for key in (
                "packRoot",
                "preparedRoot",
                "calendarRoot",
                "unitPolicy",
                "selectedStateIds",
            )
        }
        for artifact in artifacts
    ]
    provenance = deepcopy(meta)
    provenance.update(
        {
            "source": "COMPOSED_FINANCIAL_DATASET",
            "marketRoot": canonical_hash(market_dataset),
            "marketSource": meta.get("source"),
            "financialCompositionVersion": "financial_dataset_v1",
            "financialInputs": financial_inputs,
            "externalFields": external,
            "rows": len(output),
            "dataFingerprint": canonical_hash(_records(output)),
            "synthetic": bool(
                meta.get("synthetic")
                or str(meta.get("source", "")).upper().startswith("SYNTHETIC")
                or str(meta.get("classification", "")).upper().startswith("SYNTHETIC")
                or any(p["raw"]["sourceKind"] == "fixture" for p in packages)
            ),
            "financialSourceScope": "selected_frozen_report_set_not_complete_filing_history",
        }
    )
    provenance["financialDatasetRoot"] = canonical_hash(
        {
            "marketRoot": provenance["marketRoot"],
            "financialInputs": financial_inputs,
            "rows": _safe_rows(output),
            "externalFields": external,
            "compositionVersion": "financial_dataset_v1",
        }
    )
    result = FinancialDatasetResult(output, provenance, tuple(artifacts))
    output_bytes = len(_encoded(result.to_dataset()))
    if (
        output_bytes > budget.max_output_bytes
        or retained + output_bytes > budget.max_total_bytes
    ):
        _fail(
            "FINANCIAL_DATASET_BUDGET",
            "Joined dataset exceeds the output/common budget",
        )
    from .admission import _register_composed

    _register_composed(output, provenance)
    return result
