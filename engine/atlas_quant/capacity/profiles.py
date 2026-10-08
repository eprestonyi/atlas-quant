"""Explicit admission profiles. Default production validators remain unchanged."""

from dataclasses import dataclass, asdict
from datetime import datetime

PROFILE_ID = "pooled_asset_300_v1"
FULL_FILTER_PROFILE_ID = "pooled_asset_1000_v1"


@dataclass(frozen=True)
class CapacityProfile:
    id: str = PROFILE_ID
    max_symbols: int = 300
    max_rows: int = 250_000
    max_samples: int = 250_000
    max_forecasts: int = 60_000
    max_factors: int = 16
    max_calendar_days: int = 366 * 3
    max_cache_bytes: int = 400 * 1024 * 1024
    max_rss_bytes: int = 3 * 1024**3
    max_wall_seconds: int = 1800

    def validate_strategy(self, s):
        from ..statistical_quant.schema import fail

        u = s["universe"]
        span = (
            datetime.strptime(u["end"], "%Y%m%d")
            - datetime.strptime(u["start"], "%Y%m%d")
        ).days
        if (
            s["target"]["kind"] != "asset_price"
            or s["model"]["estimator"] != "ridge"
            or s["execution"]["enabled"]
            or len(s["factors"]) > self.max_factors
            or span > self.max_calendar_days
            or s["model"]["refitDays"] < 20
            or s["validation"]["innerFolds"] != 2
            or s["validation"]["outerFolds"] != 2
            or (
                self.id == FULL_FILTER_PROFILE_ID
                and s["model"]["family"] not in {"mean_reversion", "trend"}
            )
        ):
            fail(
                "CAPACITY_PROFILE",
                f"{self.id} requires asset_price, Ridge, forecast-only, <=16 factors, <= {self.max_calendar_days} calendar days, refit>=20 and exactly 2 inner/outer folds; the 1000 profile currently supports mean_reversion/trend only",
            )

    def to_dict(self):
        return asdict(self)


def get_profile(profile_id):
    from ..statistical_quant.schema import fail

    if profile_id == FULL_FILTER_PROFILE_ID:
        return CapacityProfile(
            id=FULL_FILTER_PROFILE_ID,
            max_symbols=1000,
            max_rows=300_000,
            max_samples=300_000,
            max_forecasts=80_000,
            max_calendar_days=366,
            max_wall_seconds=900,
        )
    if profile_id != PROFILE_ID:
        fail(
            "CAPACITY_PROFILE", "Unknown or missing explicit research capacity profile"
        )
    return CapacityProfile()
