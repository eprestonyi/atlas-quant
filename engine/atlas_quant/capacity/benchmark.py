"""Fixed synthetic capacity case, not market data, a stock screen or an alpha test."""

import numpy as np
import pandas as pd

SEED = 20261008
EXPRESSIONS = (
    "returns(close,5)",
    "rank(returns(close,5))",
    "returns(close,20)",
    "zscore(returns(close,20))",
    "returns(close,60)",
    "ts_std(returns(close,1),20)",
    "ts_rank(close,20)",
    "close/ts_mean(close,20)-1",
    "delta(close,5)/ts_std(close,20)",
    "vol/ts_mean(vol,20)",
    "zscore(ts_mean(vol,5))",
    "amount/ts_mean(amount,20)",
    "rank(ts_mean(returns(close,5),20))",
    "ts_mean(rank(returns(close,5)),20)",
    "(high-low)/close",
    "ts_std(returns(close,1),60)",
)


def benchmark_strategy():
    return {
        "schemaVersion": 2,
        "name": "Predeclared 300 synthetic pooled Ridge capacity benchmark",
        "universe": {
            "symbols": [f"{100000+i:06d}.SZ" for i in range(300)],
            "start": "20230101",
            "end": "20251231",
        },
        "research": {"mode": "statistical_quant", "observationDays": 1},
        "target": {"kind": "asset_price", "horizonSessions": 5},
        "model": {
            "family": "mean_reversion",
            "estimator": "ridge",
            "trainWindow": 504,
            "refitDays": 20,
        },
        "validation": {
            "innerFolds": 2,
            "outerFolds": 2,
            "holdoutFraction": 0.2,
            "minTrainDates": 80,
        },
        "execution": {"enabled": False},
        "factors": [
            {
                "id": f"capacity_{i+1:02d}",
                "expression": expression,
                "direction": 1,
                "role": "predictor",
            }
            for i, expression in enumerate(EXPRESSIONS)
        ],
    }


def make_benchmark_data(strategy):
    dates = (
        pd.bdate_range(strategy["universe"]["start"], strategy["universe"]["end"])
        .strftime("%Y%m%d")
        .tolist()
    )
    symbols = sorted(strategy["universe"]["symbols"])
    shape = (len(dates), len(symbols))
    rng = np.random.default_rng(SEED)
    common = rng.normal(0, 0.006, (shape[0], 1))
    specific = rng.normal(0, 0.011, shape)
    close = np.exp(
        np.log(np.linspace(20, 80, shape[1]))[None, :]
        + np.cumsum(common + specific, axis=0)
    )
    opens = close * np.exp(rng.normal(0, 0.002, shape))
    high = np.maximum(opens, close) * 1.005
    low = np.minimum(opens, close) * 0.995
    volume = rng.lognormal(10, 0.4, shape)
    frame = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, shape[1]),
            "ts_code": np.tile(symbols, shape[0]),
            "open": opens.ravel(),
            "high": high.ravel(),
            "low": low.ravel(),
            "close": close.ravel(),
            "raw_close": close.ravel(),
            "vol": volume.ravel(),
            "amount": (volume * close / 10).ravel(),
            "adj_factor": np.ones(shape[0] * shape[1]),
        }
    )
    from ..provider import canonical_hash, _records

    provenance = {
        "source": "SYNTHETIC_CAPACITY_FIXTURE",
        "synthetic": True,
        "classification": "RESOURCE_BENCHMARK_ONLY",
        "seed": SEED,
        "symbols": symbols,
        "tradingDates": dates,
        "dataFingerprint": canonical_hash(_records(frame)),
        "calendar": "Synthetic weekdays; holidays deliberately not claimed as official sessions",
        "symbolIdentities": "300 synthetic codes; not historical CSI300 constituents",
        "providerCalls": 0,
        "membershipVerified": False,
    }
    return frame, provenance
