"""Export the installed financial formula contract; never fetch observations."""
from dataclasses import asdict
import json
import sys
from pathlib import Path
from atlas_quant.financial_statements.contracts import FIELDS, UNIT_SCALES, FORMULA_VERSION, POLICY_VERSION, REPORT_TYPES
from atlas_quant.financial_statements.recipes import RECIPES
from atlas_quant.financial_statements.prepare import required_fields
root = Path(__file__).resolve().parents[1]
value = {
    'formulaVersion': FORMULA_VERSION, 'policyVersion': POLICY_VERSION,
    'supportedSelection': {'scope': sorted({v[0] for v in REPORT_TYPES.values()}), 'flowBasis': sorted({v[1] for v in REPORT_TYPES.values()})},
    'items': [{'id':r.id,'name':r.name,'expression':r.id,'definition':asdict(r),
               'requiredFields':list(required_fields([r.id])),
               'availability':{'status':'definition_only'}} for r in RECIPES.values()],
    'fields': [asdict(f) for f in FIELDS.values()],
    'unitOptions': [{'nativeUnit':k,'currency':'CNY','scale':str(v)} for k,v in UNIT_SCALES.items()],
}
expected=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
target=root/'edge/financial/definitions.json'
if '--check' in sys.argv:
    if target.read_text()!=expected:
        raise SystemExit('Financial definitions differ from installed Python core; re-export before build.')
else:
    target.write_text(expected)
