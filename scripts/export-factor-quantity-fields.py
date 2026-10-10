"""Freeze field units for the browser/edge; definitions only, no observations."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from atlas_quant.context_sources import FIELDS as CONTEXT_FIELDS, FOREIGN_APIS
from atlas_quant.connectors import FINANCIAL_ALIASES
from atlas_quant.factors import FIELDS
from atlas_quant.financial_statements.recipes import RECIPES
from atlas_quant.statistical_quant.typed_preprocessing import field_quantity


def registry():
    fields = {}
    for name in sorted(set(FIELDS) | set(CONTEXT_FIELDS) | set(FINANCIAL_ALIASES) | set(RECIPES)):
        quantity = field_quantity(name)
        entry = {"kind": quantity.kind, "unit": quantity.unit}
        if name in CONTEXT_FIELDS:
            source = CONTEXT_FIELDS[name]
            entry.update(sourceIdentity=source["api"] + ":" + source["ts_code"],
                         foreign=source["api"] in FOREIGN_APIS)
        fields[name] = entry
    return {"schema": "factor-quantity-fields/1", "fields": fields}


if __name__ == "__main__":
    target = ROOT / "engine/atlas_quant/factor_quantity_fields.json"
    expected = json.dumps(registry(), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    if "--check" in sys.argv:
        if not target.exists() or target.read_text() != expected:
            raise SystemExit("Factor quantity fields differ from Python definitions; regenerate before build.")
    else:
        target.write_text(expected)
    print(json.dumps({"fields": len(registry()["fields"]), "bytes": len(expected.encode()), "check": "--check" in sys.argv}))
