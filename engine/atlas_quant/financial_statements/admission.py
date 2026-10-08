"""Process-local composition checks, never document or publication authentication.

There is no serialized token. A caller must recompose immutable financial inputs
in each process. In particular, a JSON snapshot cannot inherit this admission.
The registry holds only weak references and hashes; it does not retain datasets.
"""

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from threading import RLock
import weakref

from .recipes import RECIPES
from .results import canonical_hash


PREFIX = "model_fin_"
FUNDAMENTAL_FIELDS = frozenset(
    "pb pe pe_ttm ps ps_ttm dv_ratio dv_ttm total_mv circ_mv".split()
)
_REGISTRY = {}
_LOCK = RLock()


def is_registered_statement_state(field):
    return isinstance(field, str) and field in RECIPES


def is_fundamental_field(field):
    """Structural eligibility only; observed financial states require admission."""
    return (
        field.startswith(("fd_", "pcd_"))
        or field in FUNDAMENTAL_FIELDS
        or is_registered_statement_state(field)
    )


def _json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _frame_hash(frame):
    """Exact scalar bytes, row order, column order and dtypes; bounded per row."""
    digest = sha256(
        _json(
            {"columns": list(frame.columns), "dtypes": [str(v) for v in frame.dtypes]}
        )
    )
    for row in frame.itertuples(index=True, name=None):
        values = [
            (
                None
                if value is None or isinstance(value, float) and math.isnan(value)
                else value
            )
            for value in row
        ]
        digest.update(b"\n")
        digest.update(_json(values))
    return digest.hexdigest()


def _commitment(provenance):
    # This serializable closure commitment, not the registry, enters the forecast
    # fingerprint. The hashes bind the prepared inputs, policy, proofs and calendar.
    return deepcopy(
        {
            key: provenance[key]
            for key in (
                "financialCompositionVersion",
                "marketRoot",
                "financialDatasetRoot",
                "financialInputs",
            )
        }
    )


@dataclass(frozen=True)
class _Admission:
    reference: weakref.ReferenceType
    frame_hash: str
    provenance_hash: str
    fields: frozenset


def _register_composed(frame, provenance):
    """Private bridge hook after revalidation, preparation and all budget checks."""
    key = id(frame)

    def discard(reference):
        with _LOCK:
            entry = _REGISTRY.get(key)
            if entry is not None and entry.reference is reference:
                del _REGISTRY[key]

    fields = frozenset(field for field in frame.columns if field in RECIPES)
    entry = _Admission(
        weakref.ref(frame, discard),
        _frame_hash(frame),
        canonical_hash(provenance),
        fields,
    )
    with _LOCK:
        _REGISTRY[key] = entry


def assert_composed(original, frozen_copy, provenance, factor_fields, strategy):
    """Check the exact original object against the copy the engine will consume.

    Metadata, values or field substitutions invalidate the check. Trusted Python
    callers resolve proof/calendar authenticity out of band; this check does not.
    """
    registry = provenance.get("externalFields", {})
    reserved = {
        field
        for field in (
            *frozen_copy.columns,
            *factor_fields,
            *(registry if isinstance(registry, dict) else ()),
        )
        if isinstance(field, str) and field.startswith(PREFIX)
    }
    roots_present = any(
        key in provenance
        for key in (
            "financialInputs",
            "financialDatasetRoot",
            "financialCompositionVersion",
        )
    )
    if not reserved and not roots_present:
        return None

    from ..engine import ResearchError

    if (
        strategy.get("schemaVersion") != 2
        or strategy.get("research", {}).get("mode") != "statistical_quant"
        or strategy.get("execution", {}).get("enabled") is not False
    ):
        raise ResearchError(
            "FINANCIAL_FORECAST_ONLY_REQUIRED",
            "财务组合数据目前仅支持 schemaVersion=2 的 statistical_quant 且明确 execution.enabled=false。",
        )

    def reject():
        raise ResearchError(
            "FINANCIAL_RECOMPOSITION_REQUIRED",
            "财务状态须在本进程从冻结输入重新 compose；复制、JSON 导入或数据/来源修改不能继承来源检查。",
        )

    with _LOCK:
        entry = _REGISTRY.get(id(original))
        if entry is None or entry.reference() is not original:
            reject()
    names = {field.removesuffix("__available_date") for field in reserved}
    if not names.issubset(entry.fields):
        reject()
    try:
        matches = (
            _frame_hash(frozen_copy) == entry.frame_hash
            and canonical_hash(provenance) == entry.provenance_hash
        )
    except (ValueError, TypeError):
        reject()
    if not matches:
        reject()
    return _commitment(provenance)
