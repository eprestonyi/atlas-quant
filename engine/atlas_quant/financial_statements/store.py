"""Immutable disclosure versions selected at one observation date."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, asdict
from decimal import Decimal
from types import MappingProxyType

from .contracts import (
    FIELDS,
    REPORT_TYPES,
    UNIT_SCALES,
    StatementRecord,
    TradingCalendar,
    ContractError,
    is_quarter_end,
    parse_date,
)
from .results import CalendarEvidence, Dependency, ValueResult, canonical_hash, missing


@dataclass(frozen=True)
class Observation:
    symbol: str
    field_id: str
    period: str
    effective_announcement: str | None
    available: str | None
    scope: str | None
    basis: str | None
    value: object
    reasons: tuple[str, ...]
    dependency: Dependency


def _normalize(record, calendar, unit_policy, input_root):
    raw = {
        "symbol": record.symbol,
        "endpoint": record.endpoint,
        "period": record.period_end,
        "annDate": record.ann_date,
        "fAnnDate": record.f_ann_date,
        "reportType": record.report_type,
        "companyType": record.company_type,
        "fiscalYearEnd": record.fiscal_year_end,
        "updateFlag": record.update_flag,
        "requestedFields": record.requested_fields,
        "source": asdict(record.source),
        "values": {
            k: str(v) if v is not None else None for k, v in record.values.items()
        },
        "units": {k: asdict(v) for k, v in record.units.items()},
    }
    identity = canonical_hash(raw)
    scope, basis = REPORT_TYPES.get(record.report_type, (None, None))
    common = []
    if scope is None:
        common.append("UNSUPPORTED_REPORT_TYPE")
    if record.company_type not in {"1", "2", "3", "4", "7"}:
        common.append("UNKNOWN_COMPANY_TYPE")
    if record.fiscal_year_end != "1231" or not is_quarter_end(record.period_end):
        common.append("UNSUPPORTED_REPORT_PERIOD")
    dates = [d for d in (record.ann_date, record.f_ann_date) if d]
    announced = max(dates) if dates else None
    available = None
    if not announced:
        common.append("DISCLOSURE_DATE_MISSING")
    elif any(d < record.period_end for d in dates):
        common.append("DISCLOSURE_BEFORE_PERIOD_END")
    elif not calendar.complete:
        common.append("CALENDAR_INCOMPLETE")
    elif record.source.kind == "provider" and calendar.kind != "official":
        common.append("CALENDAR_SOURCE_UNVERIFIED")
    elif not calendar.coverage_start <= announced <= calendar.coverage_end:
        common.append("CALENDAR_OUTSIDE_COVERAGE")
    else:
        index = bisect_right(calendar.sessions, announced)
        if index == len(calendar.sessions):
            common.append("NEXT_SESSION_UNAVAILABLE")
        else:
            available = calendar.sessions[index]
    for field in FIELDS.values():
        if (
            field.endpoint != record.endpoint
            or field.provider_field not in record.requested_fields
        ):
            continue
        unit = record.units.get(field.provider_field)
        raw_value = record.values.get(field.provider_field)
        reasons = list(common)
        if field.provider_field not in record.values:
            reasons.append("FIELD_MISSING")
        elif raw_value is None:
            reasons.append("NULL_VALUE")
        if unit and unit.scope:
            if unit.scope.status != "matched":
                reasons.append(
                    "UNIT_SCOPE_CONFLICT"
                    if unit.scope.status == "conflicting"
                    else "UNIT_SCOPE_MISMATCH"
                )
            if (
                unit.scope.field_id != field.id
                or unit.scope.provider != record.source.provider
            ):
                reasons.append("UNIT_SCOPE_MISMATCH")
            if unit.scope.kind in {"document", "declared"} and any(
                (
                    unit.scope.symbol != record.symbol,
                    unit.scope.period_end != record.period_end,
                    unit.scope.ann_date != record.ann_date,
                    unit.scope.f_ann_date != record.f_ann_date,
                    unit.scope.report_type != record.report_type,
                    unit.scope.company_type != record.company_type,
                    unit.scope.source_snapshot != record.source.snapshot_id,
                )
            ):
                reasons.append("UNIT_SCOPE_MISMATCH")
            if unit.scope.kind == "declared" and unit.scope.input_root != input_root:
                reasons.append("UNIT_SCOPE_MISMATCH")
        elif unit and (record.source.kind == "provider" or unit.kind != "fixture"):
            reasons.append("UNIT_SCOPE_UNVERIFIED")
        declared = bool(unit and unit.kind == "user_declared_assumption")
        admitted_declaration = bool(
            declared
            and unit_policy == "allow_declared"
            and unit.scope
            and unit.scope.kind == "declared"
            and unit.scope.status == "matched"
            and unit.scope.input_root == input_root
        )
        if (unit is None or not unit.verified) and not admitted_declaration:
            reasons.append("UNIT_UNVERIFIED")
        if unit and unit.currency != "CNY":
            reasons.append("UNSUPPORTED_CURRENCY")
        if unit and unit.native_unit not in UNIT_SCALES:
            reasons.append("UNSUPPORTED_UNIT")
        if field.positive_outflow:
            if unit is None or unit.positive_outflow is not True:
                reasons.append("OUTFLOW_SIGN_UNVERIFIED")
            if raw_value is not None and raw_value < 0:
                reasons.append("NEGATIVE_OUTFLOW")
        actual_basis = "point" if field.period_kind == "point" else basis
        value = None
        if not reasons:
            # Registered scales are exact powers of ten. Move the exponent
            # without rounding before comparing same-disclosure versions.
            sign, digits, exponent = raw_value.as_tuple()
            value = Decimal(
                (sign, digits, exponent + UNIT_SCALES[unit.native_unit].adjusted())
            )
        dependency = Dependency(
            field.id,
            identity,
            record.source.snapshot_id,
            record.source.kind,
            record.source.provider,
            record.source.retrieved_at,
            record.period_end,
            announced,
            available,
            record.report_type,
            record.company_type,
            scope,
            actual_basis,
            str(raw_value) if raw_value is not None else None,
            unit.native_unit if unit else None,
            unit.currency if unit else None,
            unit.reference if unit else None,
            unit.kind if unit else None,
            field.mapping_version,
            unit.scope if unit else None,
            bool(
                unit
                and unit.verified
                and not {
                    "UNIT_SCOPE_UNVERIFIED",
                    "UNIT_SCOPE_MISMATCH",
                    "UNIT_SCOPE_CONFLICT",
                }.intersection(reasons)
            ),
            unit.kind if unit else "unverified",
            ("USER_DECLARED_UNIT_ASSUMPTION",) if declared else (),
            unit.scope.declaration_hashes if unit and unit.scope else (),
        )
        yield Observation(
            record.symbol,
            field.id,
            record.period_end,
            announced,
            available,
            scope,
            actual_basis,
            value,
            tuple(sorted(set(reasons))),
            dependency,
        )


@dataclass(frozen=True)
class StatementStore:
    calendar: TradingCalendar
    observations: tuple[Observation, ...]
    _index: object
    calendar_evidence: CalendarEvidence

    def observation_issue(self, as_of):
        parse_date(as_of)
        if not self.calendar.complete:
            return "CALENDAR_INCOMPLETE"
        if not self.calendar.coverage_start <= as_of <= self.calendar.coverage_end:
            return "OBSERVATION_OUTSIDE_CALENDAR"
        if as_of not in self.calendar.sessions:
            return "OBSERVATION_NOT_SESSION"
        return None

    def latest_period(
        self, symbol, field_id, as_of, *, scope="consolidated", basis="ytd"
    ):
        if field_id not in FIELDS:
            raise ContractError("unknown field")
        if self.observation_issue(as_of):
            return None
        periods = [
            o.period
            for o in self.observations
            if o.symbol == symbol
            and o.field_id == field_id
            and o.scope == scope
            and o.basis == basis
            and o.available
            and o.available <= as_of
        ]
        return max(periods, default=None)

    def value(
        self, symbol, field_id, period_end, as_of, *, scope="consolidated", basis="ytd"
    ):
        if (
            field_id not in FIELDS
            or scope not in {"consolidated", "parent"}
            or basis not in {"ytd", "quarter", "point"}
        ):
            raise ContractError("unknown field, scope or basis")
        parse_date(period_end)
        args = {
            "period_end": period_end,
            "as_of": as_of,
            "scope": scope,
            "basis": basis,
            "calendar_evidence": self.calendar_evidence,
        }
        issue = self.observation_issue(as_of)
        if issue:
            return missing(issue, **args)
        rows = self._index.get((symbol, field_id, period_end, scope, basis), ())
        eligible = [o for o in rows if o.available and o.available <= as_of]
        if not eligible:
            known = [
                o
                for o in rows
                if o.effective_announcement and o.effective_announcement <= as_of
            ]
            reasons = {reason for o in known for reason in o.reasons} or {
                "PERIOD_NOT_AVAILABLE"
            }
            return missing(reasons, deps=tuple(o.dependency for o in known), **args)
        latest = max(o.effective_announcement for o in eligible)
        selected = [o for o in eligible if o.effective_announcement == latest]
        deps = tuple(
            o.dependency
            for o in sorted(selected, key=lambda o: o.dependency.record_hash)
        )
        reasons = {reason for o in selected for reason in o.reasons}
        signatures = {
            (o.value, o.dependency.company_type, o.dependency.currency)
            for o in selected
        }
        if len(signatures) > 1:
            reasons.add("CONFLICTING_DISCLOSURES")
        if reasons:
            return missing(reasons, deps=deps, **args)
        return ValueResult(
            selected[0].value,
            "CNY",
            period_end,
            max(o.available for o in selected),
            deps,
            (),
            "statement_value_v1",
            as_of,
            scope,
            basis,
            self.calendar_evidence,
        )


def build_store(records, calendar, *, unit_policy="verified_only", input_root=None):
    if not isinstance(calendar, TradingCalendar):
        raise ContractError("TradingCalendar required")
    if not isinstance(unit_policy, str) or unit_policy not in {
        "verified_only",
        "allow_declared",
    }:
        raise ContractError("unit policy must be verified_only or allow_declared")
    unique = {}
    for record in records:
        if not isinstance(record, StatementRecord):
            raise ContractError("StatementRecord required")
        for observation in _normalize(record, calendar, unit_policy, input_root):
            unique[(observation.field_id, observation.dependency.record_hash)] = (
                observation
            )
    observations = tuple(unique[k] for k in sorted(unique))
    index = {}
    for row in observations:
        key = (row.symbol, row.field_id, row.period, row.scope, row.basis)
        index.setdefault(key, []).append(row)
    evidence = {
        "sessions_hash": canonical_hash(calendar.sessions),
        "coverage_start": calendar.coverage_start,
        "coverage_end": calendar.coverage_end,
        "complete": calendar.complete,
        "kind": calendar.kind,
        "evidence_reference": calendar.evidence_reference,
    }
    return StatementStore(
        calendar,
        observations,
        MappingProxyType({k: tuple(v) for k, v in index.items()}),
        CalendarEvidence(root=canonical_hash(evidence), **evidence),
    )
