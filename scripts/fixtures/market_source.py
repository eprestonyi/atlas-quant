"""Explicit SYNTHETIC whole-market fixture; zero provider I/O or real constituents."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import random
import uuid

from atlas_quant.market_acquisition.protocol import encode, sha, LIMITS, BASE_FIELDS
from atlas_quant.market_acquisition.provider import RawResponse
from atlas_quant.capacity.benchmark import benchmark_strategy

ROOT = Path(__file__).resolve().parents[2]


def source_plan(count=2, *, estimator="ridge"):
    assert type(count) is int and 1 <= count <= 1000 and estimator in {"ridge", "auto"}
    base = json.loads((ROOT / "contracts/fixtures/market-plan-v1.json").read_text())
    scope = json.loads((ROOT / "contracts/fixtures/market-scope-v1.json").read_text())
    symbols = [f"{100000+i:06d}.SZ" for i in range(count)]
    scope.update(symbols=symbols, symbolCount=count, start="20240101", end="20241231")
    scope["selection"]["includeSymbols"] = symbols
    scope_root = sha(encode(scope))
    ref = {
        **base["universeScopeRef"],
        "scopeId": str(
            uuid.uuid5(uuid.NAMESPACE_URL, "SYNTHETIC_MARKET_SCOPE:" + scope_root)
        ),
        "scopeRoot": scope_root,
    }
    plan = {
        **base,
        "universeScopeRef": ref,
        "scope": {k: scope[k] for k in ("symbols", "symbolCount", "start", "end")},
        "fields": sorted(BASE_FIELDS),
        "authorizationScope": "SYNTHETIC_MARKET_PREVIEW",
        "requests": [],
    }
    plan["scope"]["scopeRoot"] = scope_root
    definitions = [
        (
            "trade_cal",
            {"exchange": "SZSE"},
            "exchange,cal_date,is_open,pretrade_date",
            65536,
        )
    ]
    for symbol in symbols:
        definitions.extend(
            [
                (
                    "daily",
                    {"ts_code": symbol},
                    "ts_code,trade_date,open,high,low,close,vol,amount",
                    131072,
                ),
                (
                    "adj_factor",
                    {"ts_code": symbol},
                    "ts_code,trade_date,adj_factor",
                    65536,
                ),
            ]
        )
    for i, (api, params, fields, budget) in enumerate(definitions):
        definition = {
            "provider": "TUSHARE_PRO",
            "authorizationScope": plan["authorizationScope"],
            "apiName": api,
            "params": {
                **params,
                "start_date": scope["start"],
                "end_date": scope["end"],
            },
            "fields": fields,
            "responseBytes": budget,
            "maxAttempts": 1,
        }
        plan["requests"].append(
            {"ordinal": i, **definition, "requestKey": sha(encode(definition))}
        )
    plan["budget"] = {
        **LIMITS,
        "calendarDays": 366,
        "declaredRequests": len(definitions),
        "materializedRequests": len(definitions),
        "rawResponseCeilingBytes": sum(r["responseBytes"] for r in plan["requests"]),
    }
    plan["planRoot"] = sha(encode({k: v for k, v in plan.items() if k != "planRoot"}))
    strategy = benchmark_strategy()
    strategy.update(
        name=f"SYNTHETIC {count} security whole-source HTTP {estimator} acceptance"
    )
    strategy["universe"] = {k: scope[k] for k in ("symbols", "start", "end")}
    strategy["model"].update(estimator=estimator, trainWindow=120, refitDays=20)
    strategy["validation"]["minTrainDates"] = 40
    return scope, plan, strategy


class SyntheticMarketProvider:
    """Picklable one-call adapter; deliberately no socket or requests dependency."""

    def __init__(self, config):
        if (
            config.get("allow_market_fixtures") is not True
            or config.get("provider_access")
            or config.get("tushare_token")
        ):
            raise ValueError("Explicit fixture-only configuration required")

    def call_once(self, request, *, deadline=None, maximum_bytes=None):
        start = datetime.strptime(request["params"]["start_date"], "%Y%m%d")
        end = datetime.strptime(request["params"]["end_date"], "%Y%m%d")
        symbol = request["params"].get("ts_code", "100000.SZ")
        seed = int(symbol[:6]) + 20261008
        rng = random.Random(seed)
        values = []
        level = 20 + (int(symbol[:6]) % 97) * 0.3
        previous = None
        for i in range((end - start).days + 1):
            d = start + timedelta(days=i)
            day = d.strftime("%Y%m%d")
            opened = d.weekday() < 5
            if request["apiName"] == "trade_cal":
                row = {
                    "exchange": request["params"]["exchange"],
                    "cal_date": day,
                    "is_open": int(opened),
                    "pretrade_date": previous,
                }
                if opened:
                    previous = day
            elif not opened:
                continue
            else:
                level *= math.exp(rng.gauss(0, 0.009))
                factor = 2.0 if i < 180 else 4.0
                close = level * 2 / factor
                open_price = close * math.exp(rng.gauss(0, 0.002))
                vol = 10000 + int(rng.random() * 10000)
                row = {
                    "ts_code": symbol,
                    "trade_date": day,
                    "open": open_price,
                    "close": close,
                    "high": max(close, open_price) * 1.005,
                    "low": min(close, open_price) * 0.995,
                    "vol": vol,
                    "amount": vol * close / 10,
                    "adj_factor": factor,
                }
                if (
                    i == 7
                    and int(symbol[:6]) % 37 == 0
                    and request["apiName"] == "daily"
                ):
                    continue
            values.append([row[k] for k in request["fields"].split(",")])
        raw = encode(
            {
                "code": 0,
                "data": {"fields": request["fields"].split(","), "items": values},
            }
        )
        if maximum_bytes is not None and len(raw) > maximum_bytes:
            raise ValueError("Fixture exceeds fixed response ceiling")
        return RawResponse(raw, 200, "2026-10-08T00:00:00Z", "fixture")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=int, default=2)
    parser.add_argument("--estimator", choices=["ridge", "auto"], default="ridge")
    args = parser.parse_args()
    scope, plan, strategy = source_plan(args.symbols, estimator=args.estimator)
    print(
        json.dumps(
            {"scope": scope, "plan": plan, "strategy": strategy}, ensure_ascii=False
        )
    )
