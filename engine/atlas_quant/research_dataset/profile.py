"""The offline financial slice cannot borrow the larger pooled-market profile."""

from dataclasses import dataclass, fields

from .codec import require

FORMAT = "atlas.quant.research_dataset"
VERSION = 1
PROFILE_ID = "financial_compose_50_v1"
VIEW_PROFILE_ID = "financial_snapshot_view_50_v1"
VIEW_VERSION = 2


@dataclass(frozen=True)
class DatasetProfile:
    total_bytes: int = 64 * 1024 * 1024
    manifest_bytes: int = 256 * 1024
    part_bytes: int = 512 * 1024
    max_parts: int = 256
    max_components: int = 32
    max_depth: int = 3
    market_bytes: int = 24 * 1024 * 1024
    package_bytes: int = 24 * 1024 * 1024
    joined_bytes: int = 24 * 1024 * 1024
    registry_bytes: int = 256 * 1024
    registries_bytes: int = 32 * 1024 * 1024
    max_inputs: int = 8
    max_rows: int = 110000
    max_symbols: int = 50

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            require(
                type(value) is int and 1 <= value <= field.default,
                "DATASET_PROFILE",
                "Budgets may be reduced, never expanded",
            )


DEFAULT_PROFILE = DatasetProfile()


def check_profile(profile):
    require(
        type(profile) is DatasetProfile,
        "DATASET_PROFILE",
        "An explicit registered DatasetProfile is required",
    )
    # Also recheck values at public boundaries. There is no client-provided
    # capability that can raise these frozen resource ceilings.
    profile.__post_init__()
    return profile
