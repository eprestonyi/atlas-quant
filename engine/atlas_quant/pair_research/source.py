"""Local fixed market_dataset/1 adapter. No acquisition or hosted admission.

The caller supplies every domain pin separately. Matching those assertions to
bytes establishes consistency, never ownership or historical authorization.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math

from ..market_acquisition.reader import MarketSourceReader
from .contract import _domain, digest, keys, prepare_price_input, require

MAX_SYMBOLS = 50
MAX_CALENDAR_DAYS = 366
MAX_SOURCE_ROWS = MAX_SYMBOLS * MAX_CALENDAR_DAYS
# Local allocation limits, not capacity/profile declarations.
MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_NORMALIZED_BYTES = 8 * 1024 * 1024
SOURCE_VERSION = "explicit_pair_market_projection/1"


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class MarketPairSource:
    """Owned, detached local values; not a serialized authorization credential.

    Obtain through read_market_source. Bytes/tuples keep returned DataFrames and
    metadata from mutating a prepared source. Arbitrary Python callers are not a
    security boundary; no server accepts this object as an admission token.
    """
    _price_bytes: bytes
    _evidence_bytes: bytes
    _rows: tuple
    fields: tuple
    feature_source_root: str

    @property
    def price_input(self):
        return json.loads(self._price_bytes)

    @property
    def domain(self):
        return self.price_input["sourceDomain"]

    @property
    def evidence(self):
        return json.loads(self._evidence_bytes)

    def observed_rows(self):
        columns = ("trade_date", "ts_code", *self.fields)
        return [dict(zip(columns, row)) for row in self._rows]

    def frame(self):
        """Fresh complete U x source calendar; missing sessions stay missing."""
        import pandas as pd

        domain = self.domain
        frame = pd.DataFrame(self.observed_rows(),
                             columns=["trade_date", "ts_code", *self.fields])
        index = pd.MultiIndex.from_product(
            [domain["calendar"], sorted(domain["symbols"])],
            names=["trade_date", "ts_code"])
        return frame.set_index(["trade_date", "ts_code"]).reindex(index).astype(float)


def validate_source_binding(source):
    """Detect stale/replaced in-process feature values, without authenticating them."""
    require(isinstance(source, MarketPairSource), "PAIR_SOURCE_READER", "Prepared local source required")
    price = source.price_input
    require(source.feature_source_root == digest({
        "version": SOURCE_VERSION, "sourceDomainRoot": price["sourceDomainRoot"],
        "fields": list(source.fields), "observedRows": [list(r) for r in source._rows],
    }), "PAIR_SOURCE_BINDING", "Prepared feature values differ from their bound source root")


def read_market_source(reader, *, expected_domain):
    """Verify the fixed archive against explicit caller pins, without a fetch.

    A real MarketSourceReader may be injected with in-memory byte callbacks.
    No default infers the expected owner/root/scope from that same source.
    """
    domain = _domain(expected_domain)
    require(len(domain["symbols"]) <= MAX_SYMBOLS,
            "PAIR_RESEARCH_BUDGET", "Complete U exceeds the local 50-symbol envelope; never truncate")
    require(isinstance(reader, MarketSourceReader), "PAIR_SOURCE_READER",
            "A fixed market_dataset/1 reader is required; no cache/forecast fallback")
    manifest, scope = reader.manifest, reader.scope
    require(reader.dataset_root == domain["marketDatasetRef"]["datasetRoot"]
            and manifest["universeScopeRef"] == domain["universeScopeRef"],
            "PAIR_SOURCE_BINDING", "Dataset/scope root differs from explicit caller pins")
    require(all(manifest["scope"][k] == domain[k] == scope[k]
                for k in ("symbols", "start", "end"))
            and manifest["calendar"] == domain["calendar"]
            and scope["membershipPolicy"] == domain["membershipPolicy"],
            "PAIR_SOURCE_BINDING", "Complete U, date range and source calendar must match exactly")
    require(manifest["rowCount"] <= MAX_SOURCE_ROWS
            and manifest["rawArchive"]["byteLength"] <= MAX_RAW_BYTES
            and sum(c["byteLength"] for c in manifest["collections"].values()) <= MAX_NORMALIZED_BYTES,
            "PAIR_RESEARCH_BUDGET", "Complete source exceeds local allocation envelope")

    # One full reconstruction, then every part remains hash checked on read.
    verified = reader.verify_integrity()
    rows = list(reader.records("rows"))
    provenance = list(reader.records("provenance"))
    fields = tuple(manifest["fields"])
    require(set(("open", "close")) <= set(fields) and len(rows) == manifest["rowCount"]
            and len(provenance) == 1, "PAIR_SOURCE_SHAPE", "Complete market rows/provenance required")
    require(provenance[0]["tradingDates"] == domain["calendar"]
            and provenance[0]["adjustment"] ==
            "OHLC multiplied by adj_factor / first observed adj_factor per symbol",
            "PAIR_SOURCE_BINDING", "Calendar or adjusted-share basis differs")
    seen, values, projection = set(), [], []
    for row in rows:
        keys(row, {"trade_date", "ts_code", *fields}, "market row")
        key = (row["trade_date"], row["ts_code"])
        require(key not in seen and key[0] in domain["calendar"]
                and key[1] in domain["symbols"], "PAIR_SOURCE_DOMAIN", "Duplicate/out-of-domain market row")
        seen.add(key)
        cells = []
        for field in fields:
            val = row[field]
            require(val is None or (type(val) in (float, int) and math.isfinite(val)),
                    "PAIR_SOURCE_SHAPE", "Finite market cells or explicit null required")
            cells.append(None if val is None else float(val))
        values.append((*key, *cells))
        projection.append({"date": key[0], "symbol": key[1],
                           "open": row["open"], "close": row["close"]})
    price_input = prepare_price_input(domain, projection)
    values.sort(key=lambda r: (r[0], r[1]))
    feature_root = digest({"version": SOURCE_VERSION,
                           "sourceDomainRoot": price_input["sourceDomainRoot"],
                           "fields": list(fields), "observedRows": [list(r) for r in values]})
    evidence = {
        **deepcopy(verified), "version": SOURCE_VERSION,
        "sourceKind": manifest["sourceKind"], "sourceDomainRoot": price_input["sourceDomainRoot"],
        "priceProjectionRoot": price_input["priceProjectionRoot"],
        "featureSourceRoot": feature_root, "planRoot": manifest["planRoot"],
        "universeScopeRef": domain["universeScopeRef"], "observedRows": len(rows),
        "gridRows": len(domain["symbols"]) * len(domain["calendar"]),
        "missingRows": len(domain["symbols"]) * len(domain["calendar"]) - len(rows),
        "contentPinsMatched": True, "pinAuthority": "caller_assertion_not_authenticated",
        # Neither value is present in the market manifest. They bind this local
        # experiment, but cannot be authenticated by matching source bytes.
        "unverifiedCallerIdentityFields": ["ownerKey", "marketDatasetRef.datasetId"],
        "sourceAuthorityVerified": False, "hostedOwnerAuthorizationVerified": False,
        "historicalMembershipVerified": False, "historicalRevisionVintageVerified": False,
        "originalProviderWireVerified": False,
    }
    return MarketPairSource(_bytes(price_input), _bytes(evidence), tuple(values), fields, feature_root)
