"""Versioned native-statement contracts; no provider calls or unit inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping

POLICY_VERSION = "statement_asof_v1"
FORMULA_VERSION = "financial_states_v1"
DECIMAL_PRECISION = 34
DECIMAL_ROUNDING = "ROUND_HALF_EVEN"
MAX_INPUT_DIGITS = 100
MAX_INPUT_EXPONENT = 100
REVISION_LIMITATION = "PROVIDER_ORIGINAL_AS_PUBLISHED_VERSIONS_UNVERIFIED"


class ContractError(ValueError):
    """Malformed input, distinct from an otherwise valid missing observation."""


def parse_date(value: str):
    if (
        not isinstance(value, str)
        or len(value) != 8
        or not value.isascii()
        or not value.isdigit()
    ):
        raise ContractError("dates must be YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise ContractError("invalid calendar date") from error


def is_quarter_end(value: str) -> bool:
    parse_date(value)
    return value[4:] in {"0331", "0630", "0930", "1231"}


def decimal(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ContractError(
            "statement values must be numeric or null, never numeric strings"
        )
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ContractError("invalid numeric statement value") from error
    if not result.is_finite():
        raise ContractError("statement values must be finite")
    components = result.as_tuple()
    if (
        len(components.digits) > MAX_INPUT_DIGITS
        or abs(components.exponent) > MAX_INPUT_EXPONENT
        or abs(result.adjusted()) > MAX_INPUT_EXPONENT
    ):
        raise ContractError(
            "statement Decimal exceeds the supported 100-digit/exponent range"
        )
    return result


@dataclass(frozen=True)
class FieldSpec:
    id: str
    endpoint: str
    provider_field: str
    alias: str
    period_kind: str
    positive_outflow: bool = False
    canonical_unit: str = "CNY"
    mapping_version: str = "native_statement_fields_v1"


def _field(endpoint, name, kind, *, alias=None, positive_outflow=False):
    prefix = {"income": "income", "balancesheet": "balance", "cashflow": "cashflow"}[
        endpoint
    ]
    return FieldSpec(
        f"{endpoint}.{name}",
        endpoint,
        name,
        alias or f"fd_{prefix}_{name}",
        kind,
        positive_outflow,
    )


FIELDS = MappingProxyType(
    {
        field.id: field
        for field in [
            *[
                _field("income", name, "flow")
                for name in ("revenue", "operate_profit", "n_income", "n_income_attr_p")
            ],
            *[
                _field("balancesheet", name, "point")
                for name in (
                    "total_assets",
                    "total_liab",
                    "total_cur_assets",
                    "total_cur_liab",
                    "money_cap",
                    "accounts_receiv",
                    "goodwill",
                    "st_borr",
                    "lt_borr",
                )
            ],
            _field("cashflow", "n_cashflow_act", "flow", alias="fd_cashflow_operating"),
            _field(
                "cashflow",
                "c_pay_acq_const_fiolta",
                "flow",
                alias="fd_cashflow_capex_cash",
                positive_outflow=True,
            ),
        ]
    }
)

# Scope/basis are declared by the source report type. Original and revised records
# share a semantic coordinate; update_flag is never a disclosure clock.
REPORT_TYPES = MappingProxyType(
    {
        "1": ("consolidated", "ytd"),
        "2": ("consolidated", "quarter"),
        "3": ("consolidated", "quarter"),
        "4": ("consolidated", "ytd"),
        "5": ("consolidated", "ytd"),
        "6": ("parent", "ytd"),
        "7": ("parent", "quarter"),
        "8": ("parent", "quarter"),
        "9": ("parent", "ytd"),
        "10": ("parent", "ytd"),
        "12": ("parent", "ytd"),
    }
)
UNIT_SCALES = MappingProxyType(
    {"CNY": Decimal(1), "CNY_1000": Decimal(1000), "CNY_10000": Decimal(10000)}
)


@dataclass(frozen=True)
class UnitScope:
    """Frozen declared scope; adapter verification is retained in result lineage."""

    kind: str
    field_id: str
    provider: str
    status: str
    binding_hashes: tuple[str, ...]
    symbol: str | None = None
    period_end: str | None = None
    ann_date: str | None = None
    f_ann_date: str | None = None
    report_type: str | None = None
    company_type: str | None = None
    source_snapshot: str | None = None
    normalized_row_hash: str | None = None
    document_hashes: tuple[str, ...] = ()
    policy_version: str = "statement_unit_scope_v1"
    input_root: str | None = None
    declaration_hashes: tuple[str, ...] = ()

    def __post_init__(self):
        import re

        if self.kind not in {"fixture", "global", "document", "declared"}:
            raise ContractError(
                "unit scope must be explicit fixture, global, document or declared"
            )
        if (
            self.field_id not in FIELDS
            or not isinstance(self.provider, str)
            or not self.provider
        ):
            raise ContractError("unit scope requires registered field and provider")
        if self.policy_version not in {
            "statement_unit_scope_v1",
            "statement_unit_scope_v2",
        }:
            raise ContractError("unsupported unit scope policy")
        if self.status not in {"matched", "mismatch", "conflicting"}:
            raise ContractError("unit scope must declare match status")
        for name in ("binding_hashes", "document_hashes"):
            hashes = tuple(getattr(self, name))
            if not hashes and (
                name == "binding_hashes" or self.kind in {"document", "global"}
            ):
                raise ContractError("unit scope requires binding/document hashes")
            if any(
                not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h)
                for h in hashes
            ):
                raise ContractError("unit scope identities must be SHA256")
            object.__setattr__(self, name, tuple(sorted(set(hashes))))
        if self.kind == "declared":
            if self.policy_version != "statement_unit_scope_v2":
                raise ContractError(
                    "declared units require the explicit v2 scope policy"
                )
            hashes = tuple(self.declaration_hashes)
            if not hashes or any(
                not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h)
                for h in hashes
            ):
                raise ContractError("declared units require declaration SHA256")
            if not isinstance(self.input_root, str) or not re.fullmatch(
                r"[a-f0-9]{64}", self.input_root
            ):
                raise ContractError("declared units require an exact raw input root")
            object.__setattr__(self, "declaration_hashes", tuple(sorted(set(hashes))))
        elif self.input_root is not None or self.declaration_hashes:
            raise ContractError("only declared scope carries declaration identities")
        if self.kind in {"document", "declared"}:
            if not self.symbol or not self.report_type or not self.company_type:
                raise ContractError("document scope requires security and report type")
            parse_date(self.period_end)
            for date in (self.ann_date, self.f_ann_date):
                if date is not None:
                    parse_date(date)
            for digest in (self.source_snapshot, self.normalized_row_hash):
                if not isinstance(digest, str) or not re.fullmatch(
                    r"[a-f0-9]{64}", digest
                ):
                    raise ContractError(
                        "document scope requires normalized snapshot and row SHA256"
                    )


@dataclass(frozen=True)
class UnitEvidence:
    native_unit: str
    currency: str
    verified: bool
    kind: str
    reference: str
    positive_outflow: bool | None = None
    scope: UnitScope | None = None

    def __post_init__(self):
        if self.scope is not None and not isinstance(self.scope, UnitScope):
            raise ContractError("unit evidence scope must be a frozen UnitScope")
        if self.kind not in {
            "fixture",
            "source_contract",
            "source_document",
            "user_declared_assumption",
        }:
            raise ContractError(
                "unit evidence kind must distinguish fixtures from source evidence"
            )
        expected_scope = {
            "fixture": "fixture",
            "source_contract": "global",
            "source_document": "document",
            "user_declared_assumption": "declared",
        }[self.kind]
        if self.scope is not None and self.scope.kind != expected_scope:
            raise ContractError(
                "unit evidence kind cannot be promoted to a different scope"
            )
        if (
            not isinstance(self.verified, bool)
            or not isinstance(self.reference, str)
            or not self.reference.strip()
        ):
            raise ContractError(
                "unit evidence requires an explicit verification flag and reference"
            )
        if not isinstance(self.native_unit, str) or not isinstance(self.currency, str):
            raise ContractError("unit and currency must be explicit strings")
        if self.positive_outflow is not None and not isinstance(
            self.positive_outflow, bool
        ):
            raise ContractError(
                "cash outflow sign evidence must be a boolean or unknown"
            )
        if self.kind == "user_declared_assumption" and self.verified:
            raise ContractError("a user declaration can never claim verified units")


@dataclass(frozen=True)
class SourceRef:
    provider: str
    snapshot_id: str
    retrieved_at: str
    kind: str

    def __post_init__(self):
        if self.kind not in {"fixture", "provider"}:
            raise ContractError("source kind must distinguish fixture from provider")
        if not all(
            isinstance(x, str) and x.strip()
            for x in (self.provider, self.snapshot_id, self.retrieved_at)
        ):
            raise ContractError("source provenance is required")
        try:
            moment = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError()
        except ValueError as error:
            raise ContractError("retrieved_at requires a timezone") from error


@dataclass(frozen=True)
class StatementRecord:
    symbol: str
    endpoint: str
    period_end: str
    ann_date: str | None
    f_ann_date: str | None
    report_type: str
    company_type: str
    values: Mapping[str, Decimal | int | float | None]
    units: Mapping[str, UnitEvidence]
    source: SourceRef
    update_flag: str | None = None
    fiscal_year_end: str = "1231"
    requested_fields: tuple[str, ...] | None = None

    def __post_init__(self):
        if not isinstance(self.values, Mapping) or not isinstance(self.units, Mapping):
            raise ContractError("values and units must be field mappings")
        if not all(
            isinstance(value, str)
            for value in (
                self.endpoint,
                self.report_type,
                self.company_type,
                self.fiscal_year_end,
            )
        ):
            raise ContractError("statement identity and scope codes must be strings")
        if self.update_flag is not None and not isinstance(self.update_flag, str):
            raise ContractError("update flag must be a source string or null")
        if self.endpoint not in {"income", "balancesheet", "cashflow"}:
            raise ContractError("unsupported statement endpoint")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ContractError("explicit security identity required")
        parse_date(self.period_end)
        for value in (self.ann_date, self.f_ann_date):
            if value is not None:
                parse_date(value)
        if not isinstance(self.source, SourceRef):
            raise ContractError("SourceRef required")
        allowed = {
            f.provider_field for f in FIELDS.values() if f.endpoint == self.endpoint
        }
        if set(self.values) - allowed or set(self.units) - allowed:
            raise ContractError("field is outside this versioned statement contract")
        requested = (
            tuple(self.values)
            if self.requested_fields is None
            else tuple(self.requested_fields)
        )
        if (
            any(not isinstance(field, str) for field in requested)
            or len(set(requested)) != len(requested)
            or set(requested) - allowed
            or set(self.values) - set(requested)
        ):
            raise ContractError(
                "requested fields must uniquely describe this response projection"
            )
        object.__setattr__(self, "requested_fields", tuple(sorted(requested)))
        if any(not isinstance(unit, UnitEvidence) for unit in self.units.values()):
            raise ContractError("UnitEvidence required")
        if self.source.kind == "provider" and any(
            u.kind == "fixture" for u in self.units.values()
        ):
            raise ContractError("provider records cannot use fixture unit evidence")
        object.__setattr__(
            self,
            "values",
            MappingProxyType({k: decimal(v) for k, v in self.values.items()}),
        )
        object.__setattr__(self, "units", MappingProxyType(dict(self.units)))


@dataclass(frozen=True)
class TradingCalendar:
    sessions: tuple[str, ...]
    coverage_start: str
    coverage_end: str
    complete: bool
    evidence_reference: str
    kind: str

    def __post_init__(self):
        parse_date(self.coverage_start)
        parse_date(self.coverage_end)
        sessions = tuple(self.sessions)
        for date in sessions:
            parse_date(date)
        if self.coverage_start > self.coverage_end or sessions != tuple(
            sorted(set(sessions))
        ):
            raise ContractError(
                "calendar must have ordered unique sessions and a valid coverage interval"
            )
        if any(
            not self.coverage_start <= date <= self.coverage_end for date in sessions
        ):
            raise ContractError("calendar sessions outside declared coverage")
        if (
            not isinstance(self.complete, bool)
            or not isinstance(self.evidence_reference, str)
            or not self.evidence_reference.strip()
            or len(self.evidence_reference) > 2000
        ):
            raise ContractError("calendar completeness and evidence must be explicit")
        if self.kind not in {"fixture", "official"}:
            raise ContractError("calendar source must be explicit")
        object.__setattr__(self, "sessions", sessions)
