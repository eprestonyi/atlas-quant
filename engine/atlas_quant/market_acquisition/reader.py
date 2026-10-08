"""Provider-free, immutable market-source readback and exact recomposition.

The manifest is an identity, not proof of provider authorization. Callers must
pin its root through the owner-scoped server admission before a research run.
"""

from copy import deepcopy
from pathlib import Path
import os
import stat

from .normalize import build_publication
from .protocol import (
    CHUNK_BYTES,
    DATA_BYTES,
    MANIFEST_BYTES,
    MAX_CHUNKS,
    META_BYTES,
    RAW_BYTES,
    RAW_CHUNK_BYTES,
    RAW_CHUNKS,
    PROFILE,
    decode,
    encode,
    digest,
    identifier,
    require,
    sha,
    validate_plan,
    output_collections,
)


def _keys(value, names):
    require(
        isinstance(value, dict) and set(value) == set(names),
        "MARKET_SOURCE_SHAPE",
        "Unexpected market source fields",
    )


def validate_source_manifest(raw, expected_root=None):
    """Validate all descriptor budgets before any part is read."""
    m = decode(raw, limit=MANIFEST_BYTES)
    _keys(
        m,
        {
            "format",
            "version",
            "profile",
            "planRoot",
            "universeScopeRef",
            "scope",
            "calendar",
            "fields",
            "rowCount",
            "sourceKind",
            "collections",
            "rawArchive",
        },
    )
    require(
        encode(m) == raw, "MARKET_SOURCE_CANONICAL", "Manifest must be canonical JSON"
    )
    require(
        expected_root is None or sha(raw) == digest(expected_root),
        "MARKET_SOURCE_ROOT",
        "Market source root differs from admission",
    )
    require(
        m["format"] == "atlas.quant.market_dataset"
        and type(m["version"]) is int
        and m["version"] == 1
        and m["profile"] == PROFILE
        and m["sourceKind"] in {"fixture", "provider"},
        "MARKET_SOURCE_FORMAT",
        "Unregistered market source format",
    )
    digest(m["planRoot"])
    _keys(m["universeScopeRef"], {"scopeId", "scopeRoot", "format", "version"})
    identifier(m["universeScopeRef"]["scopeId"])
    digest(m["universeScopeRef"]["scopeRoot"])
    require(
        m["universeScopeRef"]["format"] == "atlas.quant.universe_scope"
        and type(m["universeScopeRef"]["version"]) is int
        and m["universeScopeRef"]["version"] == 1,
        "MARKET_SOURCE_FORMAT",
        "Unregistered universe scope",
    )
    _keys(m["collections"], {"rows", "provenance", "receipts"})
    require(
        type(m["rowCount"]) is int and 1 <= m["rowCount"] <= 300000,
        "MARKET_SOURCE_BUDGET",
        "Observed row budget exceeded",
    )
    counts, total = 0, 0
    for name, c in output_collections(m).items():
        raw_collection = name == "raw"
        _keys(
            c,
            {"chunks", "byteLength", "receiptCount" if raw_collection else "rowCount"},
        )
        require(
            isinstance(c["chunks"], list) and c["chunks"],
            "MARKET_SOURCE_SHAPE",
            "Missing required collection",
        )
        require(
            len(c["chunks"]) <= (RAW_CHUNKS if raw_collection else MAX_CHUNKS),
            "MARKET_SOURCE_BUDGET",
            "Too many chunks",
        )
        length, rows = 0, 0
        for i, p in enumerate(c["chunks"]):
            _keys(
                p,
                {"ordinal", "sha256", "byteLength"}
                | (set() if raw_collection else {"rowCount"}),
            )
            require(
                type(p["ordinal"]) is int and p["ordinal"] == i,
                "MARKET_SOURCE_SHAPE",
                "Noncontiguous chunk ordinals",
            )
            digest(p["sha256"])
            require(
                type(p["byteLength"]) is int
                and 1
                <= p["byteLength"]
                <= (RAW_CHUNK_BYTES if raw_collection else CHUNK_BYTES),
                "MARKET_SOURCE_BUDGET",
                "Chunk byte budget exceeded",
            )
            length += p["byteLength"]
            if not raw_collection:
                require(
                    type(p["rowCount"]) is int and 1 <= p["rowCount"] <= 10000,
                    "MARKET_SOURCE_BUDGET",
                    "Chunk record budget exceeded",
                )
                rows += p["rowCount"]
        require(
            type(c["byteLength"]) is int and length == c["byteLength"],
            "MARKET_SOURCE_BUDGET",
            "Collection byte total differs",
        )
        if raw_collection:
            require(
                length <= RAW_BYTES
                and type(c["receiptCount"]) is int
                and 1 <= c["receiptCount"] <= 3002,
                "MARKET_SOURCE_BUDGET",
                "Raw parent budget exceeded",
            )
        else:
            require(
                type(c["rowCount"]) is int
                and rows == c["rowCount"]
                and rows <= {"rows": 300000, "receipts": 3002, "provenance": 1}[name],
                "MARKET_SOURCE_BUDGET",
                "Collection row total differs",
            )
            counts += len(c["chunks"])
            total += length
    require(
        counts <= MAX_CHUNKS
        and total <= DATA_BYTES
        and m["rowCount"] == m["collections"]["rows"]["rowCount"]
        and m["rawArchive"]["receiptCount"] == m["collections"]["receipts"]["rowCount"],
        "MARKET_SOURCE_BUDGET",
        "Complete market source budget differs",
    )
    return m


class MarketSourceReader:
    """Read fixed source bytes through a bounded callback; no network/provider."""

    def __init__(
        self, manifest_bytes, plan_bytes, scope_bytes, read_part, *, expected_root=None
    ):
        self._manifest_bytes = bytes(manifest_bytes)
        self._plan_bytes = bytes(plan_bytes)
        self._scope_bytes = bytes(scope_bytes)
        self._read_part = read_part
        m = validate_source_manifest(self._manifest_bytes, expected_root)
        p = decode(self._plan_bytes, limit=META_BYTES)
        scope = decode(self._scope_bytes, limit=MANIFEST_BYTES)
        require(
            encode(p) == self._plan_bytes and encode(scope) == self._scope_bytes,
            "MARKET_SOURCE_CANONICAL",
            "Plan and scope must preserve canonical bytes",
        )
        require(
            sha(self._scope_bytes) == m["universeScopeRef"]["scopeRoot"],
            "MARKET_SOURCE_SCOPE",
            "Original frozen scope root differs",
        )
        fake_job = {
            "id": "00000000-0000-0000-0000-000000000000",
            "planId": "00000000-0000-0000-0000-000000000001",
            "planRoot": m["planRoot"],
        }
        validate_plan(
            {
                "job": {
                    "id": fake_job["id"],
                    "kind": "market_acquire",
                    "planId": fake_job["planId"],
                },
                "plan": p,
                "publicationLimits": {
                    "manifestBytes": MANIFEST_BYTES,
                    "chunkBytes": CHUNK_BYTES,
                    "chunks": MAX_CHUNKS,
                    "totalBytes": DATA_BYTES,
                    "rawChunkBytes": RAW_CHUNK_BYTES,
                    "rawChunks": RAW_CHUNKS,
                    "rawBytes": RAW_BYTES,
                },
            },
            fake_job,
            p.get("authorizationScope"),
        )
        require(
            all(
                m[k] == p[k]
                for k in ("planRoot", "universeScopeRef", "scope", "fields")
            ),
            "MARKET_SOURCE_SCOPE",
            "Manifest and request plan differ",
        )
        require(
            m["scope"]["scopeRoot"] == m["universeScopeRef"]["scopeRoot"]
            and all(
                p["catalog"].get(k) == scope.get(k)
                for k in (
                    "snapshotHash",
                    "resolutionHash",
                    "historicalMembershipVerified",
                )
            )
            and p["catalog"].get("historicalMembershipVerified") is False,
            "MARKET_SOURCE_SCOPE",
            "Scope and catalog evidence roots conflict",
        )
        require(
            scope.get("format") == "atlas.quant.universe_scope"
            and scope.get("version") == 1
            and scope.get("membershipPolicy") == "complete_filtered_set"
            and scope.get("historicalMembershipVerified") is False
            and all(
                scope.get(k) == m["scope"].get(k)
                for k in ("symbols", "start", "end", "symbolCount")
            ),
            "MARKET_SOURCE_SCOPE",
            "Full original membership and dates must match",
        )
        self._manifest, self._plan, self._scope = m, p, scope

    @property
    def dataset_root(self):
        return sha(self._manifest_bytes)

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    @property
    def plan(self):
        return deepcopy(self._plan)

    @property
    def scope(self):
        return deepcopy(self._scope)

    def part(self, name, ordinal):
        require(
            name in output_collections(self._manifest) and type(ordinal) is int,
            "MARKET_SOURCE_PART",
            "Unregistered part",
        )
        parts = output_collections(self._manifest)[name]["chunks"]
        require(
            0 <= ordinal < len(parts),
            "MARKET_SOURCE_PART",
            "Part ordinal outside manifest",
        )
        p = parts[ordinal]
        raw = self._read_part(name, ordinal)
        require(
            isinstance(raw, bytes)
            and len(raw) == p["byteLength"]
            and sha(raw) == p["sha256"],
            "MARKET_SOURCE_INTEGRITY",
            "Market part bytes differ from frozen root",
        )
        return raw

    def records(self, name):
        require(
            name in self._manifest["collections"],
            "MARKET_SOURCE_PART",
            "Raw bodies are not records",
        )
        for p in self._manifest["collections"][name]["chunks"]:
            rows = decode(self.part(name, p["ordinal"]), limit=CHUNK_BYTES)
            require(
                isinstance(rows, list) and len(rows) == p["rowCount"],
                "MARKET_SOURCE_SHAPE",
                "Part record count differs",
            )
            yield from rows

    def verify_integrity(self):
        """Recompute every normalized byte from retained endpoint responses."""
        receipts = list(self.records("receipts"))
        require(
            len(receipts) == len(self._plan["requests"]),
            "MARKET_SOURCE_RECEIPTS",
            "Incomplete request closure",
        )
        cache_ordinal, cache_raw = None, None

        def read_receipt(request):
            nonlocal cache_ordinal, cache_raw
            r = receipts[request["ordinal"]]
            loc = r["rawLocation"]
            if loc["ordinal"] != cache_ordinal:
                cache_raw = self.part("raw", loc["ordinal"])
                cache_ordinal = loc["ordinal"]
            return {
                **r,
                "raw": cache_raw[loc["offset"] : loc["offset"] + loc["byteLength"]],
            }

        ordinal, offset = 0, 0
        raw_chunks = self._manifest["rawArchive"]["chunks"]
        for request, receipt in zip(self._plan["requests"], receipts):
            _keys(
                receipt,
                {
                    "requestKey",
                    "receiptId",
                    "sha256",
                    "byteLength",
                    "httpStatus",
                    "retrievedAt",
                    "sourceKind",
                    "rawLocation",
                },
            )
            identifier(receipt["receiptId"])
            digest(receipt["sha256"])
            loc = receipt["rawLocation"]
            _keys(loc, {"ordinal", "offset", "byteLength"})
            require(
                all(type(loc[k]) is int for k in loc)
                and type(receipt["byteLength"]) is int
                and 0 < receipt["byteLength"] <= request["responseBytes"],
                "MARKET_SOURCE_RECEIPTS",
                "Invalid raw response coordinates",
            )
            if offset == raw_chunks[ordinal]["byteLength"]:
                ordinal, offset = ordinal + 1, 0
            require(
                ordinal < len(raw_chunks)
                and loc
                == {
                    "ordinal": ordinal,
                    "offset": offset,
                    "byteLength": receipt["byteLength"],
                }
                and offset + loc["byteLength"] <= raw_chunks[ordinal]["byteLength"]
                and receipt["requestKey"] == request["requestKey"]
                and receipt["sourceKind"] == self._manifest["sourceKind"],
                "MARKET_SOURCE_RECEIPTS",
                "Raw closure has gaps, overlap, or mismatched requests",
            )
            offset += loc["byteLength"]
        require(
            ordinal == len(raw_chunks) - 1 and offset == raw_chunks[-1]["byteLength"],
            "MARKET_SOURCE_RECEIPTS",
            "Unreferenced raw response bytes",
        )

        seen = set()

        def compare(name, i, raw):
            require(
                (name, i) not in seen and self.part(name, i) == raw,
                "MARKET_SOURCE_RECOMPOSE",
                "Normalized values or missingness differ from raw responses",
            )
            seen.add((name, i))

        recomposed = build_publication({}, self._plan, read_receipt, compare)
        require(
            encode(recomposed) == self._manifest_bytes,
            "MARKET_SOURCE_RECOMPOSE",
            "Recomposed manifest differs from source identity",
        )
        return {
            "datasetRoot": self.dataset_root,
            "transportVerified": True,
            "normalizationRecomputed": True,
            "sourceAuthorityVerified": False,
            "rowCount": self._manifest["rowCount"],
            "symbolCount": self._manifest["scope"]["symbolCount"],
            "rawReceipts": len(receipts),
            "providerCalls": 0,
        }

    def research_input(self):
        """Fresh owned frame after verification; no fills or symbol truncation."""
        self.verify_integrity()
        import pandas as pd

        return pd.DataFrame(self.records("rows")), next(self.records("provenance"))


class DirectoryMarketSourceReader(MarketSourceReader):
    def __init__(self, directory, *, expected_root=None):
        directory = Path(directory)
        require(
            not directory.is_symlink() and directory.is_dir(),
            "MARKET_SOURCE_PATH",
            "Source directory must be a real directory",
        )
        self.directory = directory.resolve(strict=True)

        def read(name, limit):
            path = self.directory / name
            require(
                all(
                    not parent.is_symlink()
                    for parent in [path, *path.parents]
                    if parent != self.directory.parent
                ),
                "MARKET_SOURCE_PATH",
                "Source links are not permitted",
            )
            fd = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                require(
                    stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit,
                    "MARKET_SOURCE_BUDGET",
                    "Source file exceeds its bounded descriptor",
                )
                return stream.read(limit + 1)

        super().__init__(
            read("manifest.json", MANIFEST_BYTES),
            read("plan.json", META_BYTES),
            read("scope.json", MANIFEST_BYTES),
            lambda n, i: read(
                f"parts/{n}/{i}.bin", RAW_CHUNK_BYTES if n == "raw" else CHUNK_BYTES
            ),
            expected_root=expected_root,
        )
