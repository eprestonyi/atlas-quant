"""Versioned estimator admission, distinct from immutable dataset composition."""
from datetime import datetime
from .codec import require

SOURCE_PROFILES = {1: "financial_compose_50_v1", 2: "financial_snapshot_view_50_v1"}
AUTO_PROFILE = "financial_fundamental_auto_50_v1"


def admit_profile(profile, dataset_version):
    require(type(dataset_version) is int and dataset_version in SOURCE_PROFILES,
            "DATASET_RESEARCH_PROFILE", "Unknown financial dataset version")
    profile = SOURCE_PROFILES[dataset_version] if profile is None else profile
    require(profile == SOURCE_PROFILES[dataset_version] or dataset_version == 2 and profile == AUTO_PROFILE,
            "DATASET_RESEARCH_PROFILE", "Research admission does not match the registered source version")
    return profile


def validate_research_profile(strategy, scope, *, research_profile=None, dataset_version=2):
    from ..statistical_quant.schema import validate
    profile = admit_profile(research_profile, dataset_version)
    require(isinstance(strategy, dict) and strategy.get("schemaVersion") == 2
            and isinstance(strategy.get("execution"), dict) and strategy["execution"].get("enabled") is False,
            "DATASET_FORECAST_ONLY", "Financial research requires explicit forecast-only schema 2")
    normalized = validate(strategy)
    estimator = "auto" if profile == AUTO_PROFILE else "ridge"
    require(normalized["research"]["mode"] == "statistical_quant"
            and normalized["target"]["kind"] == "asset_price"
            and normalized["model"]["family"] == "fundamental"
            and normalized["model"]["estimator"] == estimator,
            "DATASET_RESEARCH_PROFILE", "Financial estimator differs from its explicit registered admission")
    require(not normalized["universe"].get("selection")
            and {key: normalized["universe"][key] for key in ("symbols", "start", "end")} == scope,
            "DATASET_SCOPE", "Research must use the exact frozen dataset universe and interval")
    require(all(f["role"] == "predictor" for f in normalized["factors"])
            and not any(normalized["dataBindings"].values()), "DATASET_RESEARCH_PROFILE",
            "Financial research accepts predictor factors and no temporary bindings")
    if profile == AUTO_PROFILE:
        span = (datetime.strptime(scope["end"], "%Y%m%d")-datetime.strptime(scope["start"], "%Y%m%d")).days
        require(len(scope["symbols"]) <= 50 and span <= 366 and len(normalized["factors"]) <= 16
                and normalized["validation"]["innerFolds"] == normalized["validation"]["outerFolds"] == 2
                and normalized["model"]["refitDays"] >= 20,
                "DATASET_RESEARCH_PROFILE", "Financial auto/1 requires <=50 symbols, <=16 factors, <=366 days, 2x2 folds and refit>=20")
    return normalized
