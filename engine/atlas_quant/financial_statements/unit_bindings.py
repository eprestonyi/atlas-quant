"""Explicit unit-proof scopes for normalized provider tables, never wire bytes.

These contracts validate identities and scope. Document authenticity and the
human/source verification decision remain external evidence responsibilities.
"""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import re

from .contracts import ContractError, FIELDS, UnitEvidence, UnitScope, parse_date
from .results import canonical_hash


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ContractError("binding requires nonempty lowercase SHA256 identities")


def _identity(evidence, field_id, provider, kind):
    if (
        not isinstance(evidence, UnitEvidence)
        or evidence.kind != kind
        or evidence.scope is not None
    ):
        raise ContractError(
            "binding requires unbound UnitEvidence of its declared evidence kind"
        )
    if field_id not in FIELDS or not isinstance(provider, str) or not provider.strip():
        raise ContractError(
            "unit binding requires registered field ID and exact provider"
        )


@dataclass(frozen=True)
class FixtureUnitBinding:
    evidence: UnitEvidence
    field_id: str
    provider: str
    scope: str = "fixture"

    def __post_init__(self):
        _identity(self.evidence, self.field_id, self.provider, "fixture")
        if self.scope != "fixture":
            raise ContractError("fixture unit scope must be explicit")


@dataclass(frozen=True)
class GlobalUnitBinding:
    evidence: UnitEvidence
    field_id: str
    provider: str
    document_hash: str
    scope: str  # Required keyword/value: no implied global coverage.

    def __post_init__(self):
        _identity(self.evidence, self.field_id, self.provider, "source_contract")
        _digest(self.document_hash)
        if self.scope != "global":
            raise ContractError("source contract must explicitly declare global scope")


@dataclass(frozen=True)
class DocumentUnitBinding:
    evidence: UnitEvidence
    field_id: str
    provider: str
    symbol: str
    period_end: str
    ann_date: str | None
    f_ann_date: str | None
    report_type: str
    company_type: str
    source_snapshot: str
    normalized_row_hash: str
    document_hash: str
    scope: str = "document"

    def __post_init__(self):
        _identity(self.evidence, self.field_id, self.provider, "source_document")
        if self.scope != "document" or not re.fullmatch(r"\d{6}\.(SH|SZ)", self.symbol):
            raise ContractError(
                "document unit scope requires an exact supported security"
            )
        parse_date(self.period_end)
        for day in (self.ann_date, self.f_ann_date):
            if day is not None:
                parse_date(day)
        if not isinstance(self.company_type, str) or not self.company_type:
            raise ContractError("document unit scope requires company type")
        if not isinstance(self.report_type, str) or not self.report_type:
            raise ContractError("document unit scope requires report type")
        for value in (
            self.source_snapshot,
            self.normalized_row_hash,
            self.document_hash,
        ):
            _digest(value)


@dataclass(frozen=True)
class DeclaredUnitBinding:
    """An explicit user interpretation of one field in an exact frozen input.

    The raw input root excludes declarations and execution policy, avoiding a
    self-referential hash. No declaration authenticates vendor units or history.
    """

    evidence: UnitEvidence
    field_id: str
    provider: str
    input_root: str
    declared_by: str
    declared_at: str
    statement: str
    scope: str = "declared"

    def __post_init__(self):
        _identity(
            self.evidence, self.field_id, self.provider, "user_declared_assumption"
        )
        _digest(self.input_root)
        if self.scope != "declared" or self.evidence.verified:
            raise ContractError("declarations are explicitly unverified")
        for value, maximum in ((self.declared_by, 200), (self.statement, 2000)):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ContractError(
                    "declarations require a bounded author and explicit statement"
                )
        try:
            if (
                datetime.fromisoformat(self.declared_at.replace("Z", "+00:00")).tzinfo
                is None
            ):
                raise ValueError()
        except (AttributeError, TypeError, ValueError) as error:
            raise ContractError("declaration time requires a timezone") from error

    @property
    def declaration_hash(self):
        return canonical_hash(asdict(self))


def normalized_row_hash(endpoint, row):
    """Hash the adapter's normalized row; this is NOT raw JSON or HTTP identity."""
    if endpoint not in {"income", "balancesheet", "cashflow"} or not isinstance(
        row, dict
    ):
        raise ContractError(
            "normalized row hash requires endpoint and tabular row mapping"
        )
    return canonical_hash(
        {
            "representation": "normalized_provider_table_row",
            "endpoint": endpoint,
            "row": row,
        }
    )


def validate_unit_contract(contract, source_kind):
    if not isinstance(contract, dict) or set(contract) - set(FIELDS):
        raise ContractError("unit contract must use registered field IDs")
    normalized = {}
    for field_id, value in contract.items():
        candidates = tuple(value) if isinstance(value, (list, tuple)) else (value,)
        if not candidates:
            raise ContractError("unit binding collections cannot be empty")
        for binding in candidates:
            if not isinstance(
                binding,
                (
                    FixtureUnitBinding,
                    GlobalUnitBinding,
                    DocumentUnitBinding,
                    DeclaredUnitBinding,
                ),
            ):
                raise ContractError(
                    "unit contract requires explicit scoped bindings, not bare UnitEvidence"
                )
            if binding.field_id != field_id:
                raise ContractError("unit contract key must match binding field ID")
            if source_kind == "provider" and isinstance(binding, FixtureUnitBinding):
                raise ContractError(
                    "fixture unit evidence cannot certify provider input"
                )
        kinds = {binding.scope for binding in candidates}
        if len(kinds) != 1:
            raise ContractError(
                "do not mix fixture, global and document scopes for one field"
            )
        normalized[field_id] = candidates
    return normalized


def resolve_unit(
    candidates, field_id, provider, source_snapshot, endpoint, row, *, input_root=None
):
    if not candidates:
        return None
    row_hash = normalized_row_hash(endpoint, row)
    observed = {
        "symbol": row["ts_code"],
        "period_end": row["end_date"],
        "ann_date": row["ann_date"],
        "f_ann_date": row["f_ann_date"],
        "report_type": row["report_type"],
        "company_type": row["comp_type"],
        "source_snapshot": source_snapshot,
        "normalized_row_hash": row_hash,
    }
    matched = [
        binding
        for binding in candidates
        if binding.provider == provider
        and binding.field_id == field_id
        and (
            not isinstance(binding, DocumentUnitBinding)
            or all(getattr(binding, key) == value for key, value in observed.items())
        )
        and (
            not isinstance(binding, DeclaredUnitBinding)
            or binding.input_root == input_root
        )
    ]
    # Compare declared units/signs, not document references. Multiple sources may
    # substantiate the same observation; conflicting units never pick a winner.
    signatures = {
        (
            b.evidence.native_unit,
            b.evidence.currency,
            b.evidence.verified,
            b.evidence.positive_outflow,
        )
        for b in matched
    }
    status = (
        "mismatch"
        if not matched
        else "conflicting" if len(signatures) > 1 else "matched"
    )
    retained = matched if matched else candidates
    exemplar = retained[0]
    scope = UnitScope(
        kind=exemplar.scope,
        field_id=field_id,
        provider=provider,
        status=status,
        binding_hashes=tuple(canonical_hash(asdict(binding)) for binding in retained),
        document_hashes=tuple(
            b.document_hash for b in retained if hasattr(b, "document_hash")
        ),
        **(observed if exemplar.scope in {"document", "declared"} else {}),
        **(
            {
                "input_root": exemplar.input_root,
                "declaration_hashes": tuple(b.declaration_hash for b in retained),
                "policy_version": "statement_unit_scope_v2",
            }
            if exemplar.scope == "declared"
            else {}
        ),
    )
    return replace(
        exemplar.evidence,
        verified=exemplar.evidence.verified and status == "matched",
        scope=scope,
    )


class CompiledUnitBindings:
    """Pre-hash each immutable proof once and index exact matching scopes.

    Row resolution performs a dictionary lookup, not a scan of all candidates.
    Digest results are reused across row scopes. Expanded serialized dependencies
    remain charged separately by the preparation budget before allocation.
    """

    def __init__(self, contract, *, max_bytes, max_bindings=20000):
        self.fields = {}
        self.binding_bytes = self.binding_count = 0
        for field_id, candidates in contract.items():
            groups, all_entries = {}, []
            for binding in candidates:
                self.binding_count += 1
                if self.binding_count > max_bindings:
                    raise ContractError("compiled unit bindings exceed count budget")
                value = asdict(binding)
                size = len(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                )
                # Include the field key and package type/value/list wrappers,
                # not only the binding dataclass payload.
                self.binding_bytes += size + len(field_id.encode()) + 128
                if self.binding_bytes > max_bytes:
                    raise ContractError(
                        "compiled unit bindings exceed preparation byte budget"
                    )
                # A declaration hash and its binding hash commit to the same
                # immutable dataclass; do not serialize/hash it a second time.
                entry = (binding, canonical_hash(value))
                all_entries.append(entry)
                groups.setdefault(self._binding_key(binding), []).append(entry)
            self.fields[field_id] = {
                "scope": candidates[0].scope,
                "groups": {
                    key: self._group(entries) for key, entries in groups.items()
                },
                "fallback": self._group(all_entries),
            }

    @staticmethod
    def _binding_key(binding):
        if isinstance(binding, DocumentUnitBinding):
            return (
                binding.provider,
                binding.source_snapshot,
                binding.normalized_row_hash,
                binding.symbol,
                binding.period_end,
                binding.ann_date,
                binding.f_ann_date,
                binding.report_type,
                binding.company_type,
            )
        if isinstance(binding, DeclaredUnitBinding):
            return binding.provider, binding.input_root
        return (binding.provider,)

    @staticmethod
    def _group(entries):
        exemplar = entries[0][0]
        signatures = {
            (
                b.evidence.native_unit,
                b.evidence.currency,
                b.evidence.verified,
                b.evidence.positive_outflow,
            )
            for b, _ in entries
        }
        return {
            "exemplar": exemplar,
            "bindingHashes": tuple(sorted({h for _, h in entries})),
            "documentHashes": tuple(
                sorted(
                    {b.document_hash for b, _ in entries if hasattr(b, "document_hash")}
                )
            ),
            "declarationHashes": tuple(
                sorted({h for b, h in entries if isinstance(b, DeclaredUnitBinding)})
            ),
            "status": "conflicting" if len(signatures) > 1 else "matched",
            "evidenceBytes": len(
                json.dumps(
                    asdict(exemplar.evidence), ensure_ascii=False, separators=(",", ":")
                ).encode()
            ),
        }

    def select(
        self,
        field_id,
        provider,
        source_snapshot,
        endpoint,
        row,
        *,
        input_root,
        row_hash
    ):
        field = self.fields[field_id]
        observed = {
            "symbol": row["ts_code"],
            "period_end": row["end_date"],
            "ann_date": row["ann_date"],
            "f_ann_date": row["f_ann_date"],
            "report_type": row["report_type"],
            "company_type": row["comp_type"],
            "source_snapshot": source_snapshot,
            "normalized_row_hash": row_hash,
        }
        if field["scope"] == "document":
            key = (
                provider,
                source_snapshot,
                row_hash,
                row["ts_code"],
                row["end_date"],
                row["ann_date"],
                row["f_ann_date"],
                row["report_type"],
                row["comp_type"],
            )
        elif field["scope"] == "declared":
            key = provider, input_root
        else:
            key = (provider,)
        matched = field["groups"].get(key)
        return matched or field["fallback"], observed, bool(matched)

    @staticmethod
    def estimated_unit_bytes(selection):
        """Conservative serialized bound without iterating any hash tuple."""
        group, observed, _ = selection
        count = sum(
            len(group[key])
            for key in ("bindingHashes", "documentHashes", "declarationHashes")
        )
        return (
            group["evidenceBytes"]
            + len(json.dumps(observed, ensure_ascii=False).encode())
            + 1024
            + 70 * count
        )

    @staticmethod
    def resolve(selection, field_id, provider):
        group, observed, matched = selection
        exemplar = group["exemplar"]
        status = group["status"] if matched else "mismatch"
        scope = UnitScope(
            kind=exemplar.scope,
            field_id=field_id,
            provider=provider,
            status=status,
            binding_hashes=group["bindingHashes"],
            document_hashes=group["documentHashes"],
            **(observed if exemplar.scope in {"document", "declared"} else {}),
            **(
                {
                    "input_root": exemplar.input_root,
                    "declaration_hashes": group["declarationHashes"],
                    "policy_version": "statement_unit_scope_v2",
                }
                if exemplar.scope == "declared"
                else {}
            ),
        )
        return replace(
            exemplar.evidence,
            verified=exemplar.evidence.verified and status == "matched",
            scope=scope,
        )


def dependency_size_bound(dependency):
    """Bound expanded JSON before dataclasses.asdict duplicates proof arrays."""
    scalar = {
        key: value
        for key, value in vars(dependency).items()
        if key not in {"unit_scope", "declaration_hashes"}
    }
    size = (
        len(json.dumps(scalar, ensure_ascii=False, separators=(",", ":")).encode())
        + 128
    )
    size += 70 * len(dependency.declaration_hashes)
    scope = dependency.unit_scope
    if scope is not None:
        scalar_scope = {
            key: value
            for key, value in vars(scope).items()
            if key not in {"binding_hashes", "document_hashes", "declaration_hashes"}
        }
        size += (
            len(
                json.dumps(
                    scalar_scope, ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            + 128
        )
        size += 70 * (
            len(scope.binding_hashes)
            + len(scope.document_hashes)
            + len(scope.declaration_hashes)
        )
    return size
