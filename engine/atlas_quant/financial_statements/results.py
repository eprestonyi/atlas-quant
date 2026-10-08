"""Decimal results and dependency evidence, independent of the full source store."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json

from .contracts import (
    DECIMAL_PRECISION,
    DECIMAL_ROUNDING,
    POLICY_VERSION,
    REVISION_LIMITATION,
    UnitScope,
)


def canonical_hash(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def arithmetic(function):
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        return +function()


@dataclass(frozen=True)
class CalendarEvidence:
    root: str
    sessions_hash: str
    coverage_start: str
    coverage_end: str
    complete: bool
    kind: str
    evidence_reference: str


@dataclass(frozen=True)
class Dependency:
    field_id: str
    record_hash: str
    source_snapshot: str
    source_kind: str
    source_provider: str
    retrieved_at: str
    period_end: str
    announcement_date: str | None
    available_date: str | None
    report_type: str
    company_type: str
    scope: str | None
    basis: str | None
    raw_decimal: str | None
    raw_unit: str | None
    currency: str | None
    unit_evidence: str | None
    unit_evidence_kind: str | None
    mapping_version: str
    unit_scope: UnitScope | None = None
    unit_verified: bool = False
    evidence_level: str = "unverified"
    quality_flags: tuple[str, ...] = ()
    declaration_hashes: tuple[str, ...] = ()


def dependencies(results):
    unique = {
        (d.field_id, d.record_hash): d
        for result in results
        for d in result.dependencies
    }
    return tuple(unique[key] for key in sorted(unique))


@dataclass(frozen=True)
class ValueResult:
    value: Decimal | None
    unit: str
    period_end: str | None
    available_date: str | None
    dependencies: tuple[Dependency, ...] = ()
    reason_codes: tuple[str, ...] = ()
    formula: str = "statement_value_v1"
    as_of: str | None = None
    scope: str = "consolidated"
    basis: str = "point"
    calendar_evidence: CalendarEvidence | None = None

    @property
    def status(self):
        return "ok" if self.value is not None and not self.reason_codes else "missing"

    @property
    def quality_flags(self):
        return tuple(
            sorted({flag for dep in self.dependencies for flag in dep.quality_flags})
        )

    @property
    def declaration_hashes(self):
        return tuple(
            sorted({h for dep in self.dependencies for h in dep.declaration_hashes})
        )

    def to_dict(self):
        data = {
            "status": self.status,
            "decimalValue": str(self.value) if self.status == "ok" else None,
            "decimalPrecision": DECIMAL_PRECISION,
            "rounding": DECIMAL_ROUNDING,
            "valueRepresentation": (
                "source_exact_decimal_normalization"
                if self.formula == "statement_value_v1"
                else "derived_decimal34"
            ),
            "unit": self.unit,
            "periodEnd": self.period_end,
            "availableDate": self.available_date,
            "reasonCodes": list(self.reason_codes),
            "dependencies": [asdict(d) for d in self.dependencies],
            "formulaVersion": self.formula,
            "policyVersion": POLICY_VERSION,
            "calendar": (
                asdict(self.calendar_evidence) if self.calendar_evidence else None
            ),
            "mappingVersions": sorted({d.mapping_version for d in self.dependencies}),
            "asOf": self.as_of,
            "scope": self.scope,
            "basis": self.basis,
            "revisionHistory": REVISION_LIMITATION,
            "qualityFlags": list(self.quality_flags),
            "unitEvidenceLevels": sorted({d.evidence_level for d in self.dependencies}),
            "declarationHashes": list(self.declaration_hashes),
            "unitVerified": bool(self.dependencies)
            and all(d.unit_verified for d in self.dependencies),
        }
        data["lineageHash"] = canonical_hash(data)
        return data


def missing(
    reason,
    *,
    period_end=None,
    as_of=None,
    scope="consolidated",
    basis="point",
    deps=(),
    formula="statement_value_v1",
    unit="CNY",
    calendar_evidence=None,
):
    reasons = (reason,) if isinstance(reason, str) else tuple(sorted(set(reason)))
    return ValueResult(
        None,
        unit,
        period_end,
        None,
        tuple(deps),
        reasons,
        formula,
        as_of,
        scope,
        basis,
        calendar_evidence,
    )


def derive(
    results,
    function,
    *,
    formula,
    period_end,
    as_of,
    scope,
    basis,
    unit="CNY",
    reasons=(),
    calendar_evidence=None,
):
    result_deps = dependencies(results)
    failures = set(reasons)
    calendars = {r.calendar_evidence for r in results if r.calendar_evidence}
    if calendar_evidence:
        calendars.add(calendar_evidence)
    if len(calendars) > 1:
        failures.add("MIXED_CALENDAR_CONTRACT")
    elif calendars:
        calendar_evidence = next(iter(calendars))
    failures.update(reason for result in results for reason in result.reason_codes)
    if len({d.company_type for d in result_deps}) > 1:
        failures.add("COMPANY_TYPE_MISMATCH")
    if any(result.status != "ok" for result in results) and not failures:
        failures.add("DEPENDENCY_MISSING")
    if failures:
        return missing(
            failures,
            period_end=period_end,
            as_of=as_of,
            scope=scope,
            basis=basis,
            deps=result_deps,
            formula=formula,
            unit=unit,
            calendar_evidence=calendar_evidence,
        )
    value = arithmetic(lambda: function(*[r.value for r in results]))
    if not value.is_finite():
        return missing(
            "NONFINITE_DERIVED_VALUE",
            period_end=period_end,
            as_of=as_of,
            scope=scope,
            basis=basis,
            deps=result_deps,
            formula=formula,
            unit=unit,
            calendar_evidence=calendar_evidence,
        )
    return ValueResult(
        value,
        unit,
        period_end,
        max((r.available_date for r in results if r.available_date), default=None),
        result_deps,
        (),
        formula,
        as_of,
        scope,
        basis,
        calendar_evidence,
    )
