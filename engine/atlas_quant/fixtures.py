"""Explicit synthetic educational prices. Never an automatic provider fallback."""
from __future__ import annotations

import hashlib
import numpy as np
import pandas as pd

from .provider import validate_universe, canonical_hash, _records, ProviderError


def make_demo_data(strategy):
    symbols, start, end = validate_universe(strategy)
    dates = pd.bdate_range(start=pd.to_datetime(start), end=pd.to_datetime(end))
    if not len(dates):
        raise ProviderError("INSUFFICIENT_DEMO_DATA", "所选日期范围没有合成工作日。")
    seed = int.from_bytes(hashlib.sha256((start+end).encode()).digest()[:8], "big")
    market_rng = np.random.default_rng(seed)
    market = market_rng.normal(0.00015, 0.008, len(dates))
    rows = []
    for symbol in symbols:
        rng = np.random.default_rng(int.from_bytes(hashlib.sha256(("atlas-synthetic-v1"+symbol+start+end).encode()).digest()[:8], "big"))
        previous = 20.0 + rng.uniform(0, 80)
        for index, day in enumerate(dates):
            slow = 0.0008 * np.sin(index / 35 + rng.uniform(-0.1, 0.1))
            op = previous * np.exp(rng.normal(0, 0.002))
            close = op * np.exp(0.8 * market[index] + slow + rng.normal(0, 0.009))
            high = max(op, close) * (1 + rng.uniform(0.001, 0.015))
            low = min(op, close) * (1 - rng.uniform(0.001, 0.015))
            vol = float(rng.integers(20000, 120000))
            rows.append({"ts_code": symbol, "trade_date": day.strftime("%Y%m%d"), "open": op, "high": high,
                         "low": low, "close": close, "raw_close": close, "vol": vol,
                         "amount": vol*100*(op+close)/2/1000, "adj_factor": 1.0})
            previous = close
    frame = pd.DataFrame(rows).sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    provenance = {"source": "SYNTHETIC", "classification": "SYNTHETIC_EDUCATIONAL_ONLY", "synthetic": True,
                  "generatorVersion": "atlas-synthetic-v1", "seed": str(seed), "symbols": symbols,
                  "start": start, "end": end, "tradingDates": [d.strftime("%Y%m%d") for d in dates],
                  "rows": len(frame), "dataFingerprint": canonical_hash(_records(frame)), "adjustment": "none",
                  "warnings": ["全部价格为确定性生成的合成数据，不是真实行情，结果不能证明投资有效性。",
                               "合成日历仅排除周末，未模拟中国法定节假日、停牌与涨跌停。"]}
    return frame, provenance
