"""Bounded encrypted child inputs, avoiding a large blocking spawn argument pipe."""

import re

from ..financial_runner.spool import _private, _sync
from .protocol import LIMITS, decode, digest, encode, integer, keys, require, sha

NAME = re.compile(
    r"market-manifest|market-snapshot|financial[0-7]|registry-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
)
INDEX_BYTES = 512 * 1024


def cache_identity(spool, job):
    key = sha((job["id"] + "\0" + job["leaseToken"]).encode())
    return spool.root / ("sources-" + key), spool.aad + b":sources:" + key.encode()


def ceiling(name):
    if name == "market-manifest":
        return LIMITS["sourceManifestBytes"]
    if name == "market-snapshot":
        return LIMITS["sourceSnapshotBytes"]
    return (
        LIMITS["registryEntryBytes"]
        if name.startswith("registry-")
        else LIMITS["packageBytes"]
    )


def store_sources(spool, job, inputs, check):
    meta, manifest, snapshot, packages, registry = inputs
    spool.remember_input(job, meta)
    values = {
        "market-manifest": manifest,
        "market-snapshot": snapshot,
        **packages,
        **{"registry-" + k: v for k, v in registry.items()},
    }
    require(len(values) == 2 + len(packages) + len(registry), "DATASET_INPUT_IDENTITY")
    entries, total = [], len(encode(meta))
    for name, raw in values.items():
        require(isinstance(name, str) and NAME.fullmatch(name), "DATASET_SOURCE_CACHE")
        require(
            isinstance(raw, bytes) and 1 <= len(raw) <= ceiling(name),
            "DATASET_INPUT_BUDGET",
        )
        total += len(raw)
        entries.append({"name": name, "sha256": sha(raw), "byteLength": len(raw)})
    index = encode({"version": 1, "entries": entries})
    require(
        len(entries) <= 2 + LIMITS["financialInputs"] + LIMITS["registryEntries"]
        and len(index) <= INDEX_BYTES
        and total + len(index) <= LIMITS["closureBytes"],
        "DATASET_INPUT_BUDGET",
    )
    root, aad = cache_identity(spool, job)
    _private(root)
    for name, raw in [*values.items(), ("index", index)]:
        check()
        path, domain = root / (name + ".enc"), aad + b":" + name.encode()
        if path.exists() or path.is_symlink():
            require(
                spool._read(
                    path, INDEX_BYTES if name == "index" else ceiling(name), domain
                )
                == raw,
                "DATASET_INPUT_CHANGED",
            )
        else:
            spool._write(path, raw, domain)
    check()


def read_sources(spool, job, check):
    root, aad = cache_identity(spool, job)
    require(root.is_dir() and not root.is_symlink(), "DATASET_SPOOL_INTEGRITY")
    path, metadata_aad = spool._input_path(job)
    metadata_raw = spool._read(path, LIMITS["inputMetadataBytes"], metadata_aad)
    meta = decode(metadata_raw, limit=LIMITS["inputMetadataBytes"])
    raw_index = spool._read(root / "index.enc", INDEX_BYTES, aad + b":index")
    index = decode(raw_index, limit=INDEX_BYTES)
    keys(index, {"version", "entries"})
    require(
        type(index["version"]) is int
        and index["version"] == 1
        and isinstance(index["entries"], list)
        and len(index["entries"])
        <= 2 + LIMITS["financialInputs"] + LIMITS["registryEntries"],
        "DATASET_SPOOL_INTEGRITY",
    )
    wanted = {"market-manifest", "market-snapshot"}
    wanted.update(v["sourceId"] for v in meta["sources"]["financial"])
    refs = {meta["plan"]["marketCalendarRef"]}
    for item in meta["sources"]["financial"]:
        refs.add(item["calendarRef"])
        refs.update(item["proofRefs"])
    wanted.update("registry-" + ref for ref in refs)
    found, total = set(), len(metadata_raw) + len(raw_index)
    for entry in index["entries"]:
        keys(entry, {"name", "sha256", "byteLength"})
        name = entry["name"]
        require(
            isinstance(name, str)
            and NAME.fullmatch(name)
            and name in wanted
            and name not in found,
            "DATASET_SPOOL_INTEGRITY",
        )
        found.add(name)
        digest(entry["sha256"])
        integer(entry["byteLength"], 1, ceiling(name))
        total += entry["byteLength"]
    require(
        found == wanted
        and total <= LIMITS["closureBytes"]
        and {p.name for p in root.iterdir()}
        == {name + ".enc" for name in wanted} | {"index.enc"},
        "DATASET_SPOOL_INTEGRITY",
    )
    values = {}
    for entry in index["entries"]:
        check()
        name = entry["name"]
        raw = spool._read(
            root / (name + ".enc"), entry["byteLength"], aad + b":" + name.encode()
        )
        require(
            len(raw) == entry["byteLength"] and sha(raw) == entry["sha256"],
            "DATASET_SPOOL_INTEGRITY",
        )
        values[name] = raw
    check()
    return (
        meta,
        values.pop("market-manifest"),
        values.pop("market-snapshot"),
        {k: v for k, v in values.items() if k.startswith("financial")},
        {
            k.removeprefix("registry-"): v
            for k, v in values.items()
            if k.startswith("registry-")
        },
    )


def cleanup_sources(spool, job):
    root, _ = cache_identity(spool, job)
    require(not root.is_symlink(), "DATASET_SPOOL_INTEGRITY")
    if not root.exists():
        return
    require(root.is_dir(), "DATASET_SPOOL_INTEGRITY")
    for path in root.iterdir():
        require(
            path.is_file()
            and not path.is_symlink()
            and (
                (
                    path.suffix == ".enc"
                    and (path.stem == "index" or NAME.fullmatch(path.stem))
                )
                or re.fullmatch(r"write-[a-f0-9]{32}\.tmp", path.name)
            ),
            "DATASET_SPOOL_INTEGRITY",
        )
    for path in root.iterdir():
        path.unlink()
    root.rmdir()
    _sync(spool.root)
