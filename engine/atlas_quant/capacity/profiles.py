"""Explicit admission profiles. Default production validators remain unchanged."""

from dataclasses import dataclass, asdict
from datetime import datetime

PROFILE_ID = "pooled_asset_300_v1"
FULL_FILTER_PROFILE_ID = "pooled_asset_1000_v1"
AUTO_FILTER_CANDIDATE_ID = "pooled_asset_1000_auto_candidate_v1"
TREND_AUTO_PROFILE_ID = "pooled_asset_1000_trend_auto_v1"
AUTO_FILTER_PROFILES = frozenset({AUTO_FILTER_CANDIDATE_ID, TREND_AUTO_PROFILE_ID})
FULL_FILTER_PROFILES = AUTO_FILTER_PROFILES | {FULL_FILTER_PROFILE_ID}


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
        automatic = self.id in AUTO_FILTER_PROFILES
        family = {
            AUTO_FILTER_CANDIDATE_ID: "mean_reversion",
            TREND_AUTO_PROFILE_ID: "trend",
        }.get(self.id)
        if self.id in FULL_FILTER_PROFILES:
            span += 1  # New source profile explicitly includes both endpoints.
        if (
            s["target"]["kind"] != "asset_price"
            or s["model"]["estimator"]
            != ("auto" if automatic else "ridge")
            or s["execution"]["enabled"]
            or len(s["factors"]) > self.max_factors
            or span > self.max_calendar_days
            or s["model"]["refitDays"] < 20
            or s["validation"]["innerFolds"] != 2
            or s["validation"]["outerFolds"] != 2
            or (
                self.id in FULL_FILTER_PROFILES
                and s["model"]["family"] not in {"mean_reversion", "trend"}
            )
            or (family is not None and s["model"]["family"] != family)
        ):
            fail(
                "CAPACITY_PROFILE",
                f"{self.id} requires asset_price, {'auto' if automatic else 'Ridge'}, forecast-only, <=16 factors, <= {self.max_calendar_days} calendar days, refit>=20 and exactly 2 inner/outer folds; registered auto family={family}",
            )

    def to_dict(self):
        return asdict(self)


def get_profile(profile_id):
    from ..statistical_quant.schema import fail

    if profile_id in FULL_FILTER_PROFILES:
        return CapacityProfile(
            id=profile_id,
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
