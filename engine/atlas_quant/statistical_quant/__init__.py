"""Public numerical API for version-2 forecast-first statistical research."""
from .core import run_statistical_quant, execute_forecasts
from .schema import validate as validate_statistical_quant

__all__ = ["run_statistical_quant", "execute_forecasts", "validate_statistical_quant"]
