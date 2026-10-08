"""Auditable native-statement state core. No provider, catalog or engine wiring."""

from .contracts import (
    FIELDS,
    POLICY_VERSION,
    FORMULA_VERSION,
    ContractError,
    StatementRecord,
    SourceRef,
    UnitEvidence,
    UnitScope,
    TradingCalendar,
)
from .store import build_store, StatementStore
from .periods import quarter, ttm, point, average_assets
from .recipes import RECIPES, compute_states
from .results import ValueResult
from .prepare import (
    prepare_statement_states,
    AdapterBudget,
    AdapterError,
    AdapterResult,
)
from .package import (
    freeze_package,
    validate_package,
    decode_package,
    prepare_package,
    raw_input,
)
from .unit_bindings import DeclaredUnitBinding, DocumentUnitBinding

__all__ = [
    "FIELDS",
    "RECIPES",
    "POLICY_VERSION",
    "FORMULA_VERSION",
    "ContractError",
    "StatementRecord",
    "SourceRef",
    "UnitEvidence",
    "UnitScope",
    "TradingCalendar",
    "build_store",
    "StatementStore",
    "quarter",
    "ttm",
    "point",
    "average_assets",
    "compute_states",
    "ValueResult",
    "prepare_statement_states",
    "AdapterBudget",
    "AdapterError",
    "AdapterResult",
    "freeze_package",
    "validate_package",
    "decode_package",
    "prepare_package",
    "raw_input",
    "DeclaredUnitBinding",
    "DocumentUnitBinding",
]
