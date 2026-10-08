"""Create a small, explicit synthetic input for local financial workspace QA.
No credentials, providers, company disclosures or market calendars are read.
"""
from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from atlas_quant.financial_statements import TradingCalendar
from atlas_quant.financial_statements.package import freeze_package
from atlas_quant.financial_runner.protocol import encode, sha
from atlas_quant.financial_runner.trust import calendar_scope

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "private/financial-http-synthetic-canary")
args = parser.parse_args()
os.umask(0o077)
args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
provider = "SYNTHETIC_HTTP_CANARY_NOT_PROVIDER_EVIDENCE"
calendar = TradingCalendar(
    ("20240426", "20240429", "20240430", "20240501", "20240502", "20240503"),
    "20240426", "20240503", True, "SYNTHETIC_WEEKDAYS_NOT_EXCHANGE_CALENDAR", "fixture",
)
row = {"ts_code": "600000.SH", "ann_date": "20240426", "f_ann_date": "20240426",
       "end_date": "20231231", "report_type": "1", "comp_type": "1", "update_flag": "0",
       "money_cap": 2, "total_assets": 10}
body = {"endpoint": "balancesheet", "params": {"ts_code": "600000.SH", "period": "20231231"},
        "fields": list(row), "rows": [row], "retrievedAt": "2026-10-08T00:00:00+00:00",
        "sourceKind": "fixture", "sourceProvider": provider,
        "representation": "normalized_provider_table_snapshot",
        "wireBytesAvailable": False, "wireNumericLexemesAvailable": False}
snapshot = {**body, "id": sha(encode(body)), "rowCount": 1, "byteLength": len(encode(body))}
package = freeze_package(
    [snapshot], calendar,
    {"universe": {"symbols": ["600000.SH"], "start": "20240426", "end": "20240503"}},
    ["model_fin_cash_asset_share"], {}, announcement_start="20240426",
    source_kind="fixture", source_provider=provider, unit_policy="verified_only",
)
registry = {"kind": "calendar", "registryVersion": 1,
            "evidenceLevel": "EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE",
            "payload": asdict(calendar), "scope": calendar_scope(asdict(calendar))}
selection = package["selection"]
revision = {"expectedPackRoot": package["packRoot"],
    "selection": {**selection["universe"], "announcementStart": selection["announcementStart"],
                  "selectedStateIds": selection["selectedStates"], "scope": selection["scope"],
                  "flowBasis": selection["flowBasis"]},
    "unitPolicy": "allow_declared", "declarations": [
        {"fieldId": field, "inputRoot": package["inputRoot"], "nativeUnit": "CNY", "currency": "CNY",
         "positiveOutflow": None,
         "statement": "Explicit synthetic assumption for this exact frozen input; not company evidence."}
        for field in ("balancesheet.money_cap", "balancesheet.total_assets")]}
files = {"strict-unbound-package.json": package, "calendar-registry.json": registry,
         "ui-revision-body-template.json": revision}
# Refuse overwrite: an existing folder may contain retained acceptance evidence.
for name in files:
    if (args.output / name).exists():
        raise SystemExit(f"Refusing to replace retained fixture: {args.output / name}")
for name, value in files.items():
    with (args.output / name).open("xb") as stream:
        stream.write(encode(value))
print(json.dumps({"output": str(args.output.resolve()), "classification": "EXPLICIT_SYNTHETIC",
                  "providerCalls": 0, "files": list(files), "inputRoot": package["inputRoot"],
                  "strictExpected": "6 missing rows without unit assumptions",
                  "declaredExpected": "5 valid rows at 2/10=0.2; 1 missing; unitVerified remains false"}))
