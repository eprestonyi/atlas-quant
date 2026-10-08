"""Content-verified private float64 arrays, atomic publication, no pickle loading."""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid
import numpy as np

from ..statistical_quant.schema import digest, fail

STORE_VERSION = "float64_panel_v1"


def private_dir(path):
    path = Path(path).absolute()
    if path.is_symlink():
        fail("CAPACITY_CACHE", "Cache directory cannot be a symbolic link")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        fail("CAPACITY_CACHE", "Cache directories require private 0700 permissions")
    return path


def file_hash(path):
    if path.is_symlink() or not path.is_file():
        fail("CAPACITY_CACHE", "Cache entries must be regular files")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    with open(path, "xb") as f:
        os.chmod(path, 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        fail("CAPACITY_CACHE", "Invalid cache manifest")
    try:
        return json.loads(path.read_bytes())
    except (ValueError, OSError):
        fail("CAPACITY_CACHE", "Invalid cache manifest")


def write_array(path, values):
    array = np.asarray(values, dtype=np.float64)
    # NaN payloads and signed zero are not semantic source distinctions.
    canonical = np.array(array, dtype="<f8", order="C", copy=True)
    canonical[np.isnan(canonical)] = np.nan
    canonical[canonical == 0] = 0.0
    with path.open("xb") as f:
        os.chmod(path, 0o600)
        np.save(f, canonical, allow_pickle=False)
        f.flush()
        os.fsync(f.fileno())
    return {
        "file": path.name,
        "sha256": file_hash(path),
        "bytes": path.stat().st_size,
        "shape": list(canonical.shape),
        "dtype": "<f8",
    }


def open_array(root, info):
    if (
        not isinstance(info, dict)
        or not re.fullmatch(r"[a-z0-9_-]+\.npy", str(info.get("file", "")))
        or info.get("dtype") != "<f8"
    ):
        fail("CAPACITY_CACHE", "Invalid array descriptor")
    path = root / info["file"]
    if file_hash(path) != info.get("sha256") or path.stat().st_size != info.get(
        "bytes"
    ):
        fail("CAPACITY_CACHE", "Cache array bytes changed")
    try:
        array = np.load(path, mmap_mode="r", allow_pickle=False)
    except (ValueError, OSError):
        fail("CAPACITY_CACHE", "Cache array cannot be decoded")
    if array.dtype.str != "<f8" or list(array.shape) != info.get("shape"):
        fail("CAPACITY_CACHE", "Cache array dimensions changed")
    return array


class PanelStore:
    def __init__(self, root, manifest):
        self.root, self.manifest = Path(root), manifest
        self.identity = manifest["identity"]
        self.dates, self.symbols = manifest["dates"], manifest["symbols"]
        self._arrays = {}

    @classmethod
    def prepare(cls, data, strategy, provenance, cache_dir, *, profile_id):
        from ..engine import _prepare_data
        from .profiles import get_profile

        profile = get_profile(profile_id)
        from ..statistical_quant.schema import validate

        strategy = validate(strategy, capacity_profile=profile_id)
        panel, dates, audit = _prepare_data(
            data, strategy, provenance, capacity_profile=profile_id
        )
        store = cls.from_prepared(
            panel,
            dates,
            sorted(strategy["universe"]["symbols"]),
            audit,
            provenance,
            cache_dir,
            max_bytes=profile.max_cache_bytes,
        )
        return store, audit

    @classmethod
    def from_prepared(
        cls, panel, dates, symbols, audit, provenance, cache_dir, *, max_bytes
    ):
        """Internal prepared-panel boundary; public callers should use prepare()."""
        if (
            list(panel.index.names) != ["trade_date", "ts_code"]
            or not panel.index.is_unique
            or list(panel.index) != [(d, s) for d in dates for s in symbols]
        ):
            fail(
                "CAPACITY_AXIS",
                "Prepared panel must use the complete ordered date/security grid",
            )
        root = private_dir(cache_dir)
        temp = private_dir(root / ("stage_" + uuid.uuid4().hex))
        descriptors = {}
        try:
            for index, name in enumerate(sorted(panel.columns)):
                descriptors[name] = write_array(
                    temp / f"field_{index}.npy",
                    panel[name].to_numpy().reshape(len(dates), len(symbols)),
                )
                if sum(v["bytes"] for v in descriptors.values()) > max_bytes:
                    fail("CAPACITY_DISK", "Panel exceeds cache byte budget")
            content = {
                "version": STORE_VERSION,
                "dates": list(dates),
                "symbols": list(symbols),
                "fields": descriptors,
                "dataAudit": audit,
                "provenanceHash": digest(provenance),
                "nullPolicy": "full_calendar_grid_nan_no_fill",
                "dtype": "float64",
            }
            identity = digest(content)
            manifest = dict(content, identity=identity)
            write_json(temp / "manifest.json", manifest)
            for info in descriptors.values():
                open_array(temp, info)
            sync_directory(temp)
            target = root / identity
            if target.exists():
                existing = cls.open(target)
                if existing.manifest != manifest:
                    fail("CAPACITY_CACHE", "Existing content-addressed panel disagrees")
                shutil.rmtree(temp)  # Only this newly created staging directory.
                return existing
            os.rename(temp, target)
            sync_directory(root)
            return cls.open(target)
        except BaseException:
            # No source or previously committed cache is removed on failure.
            if temp.exists():
                shutil.rmtree(temp)
            raise

    @classmethod
    def open(cls, path):
        root = Path(path)
        manifest = read_json(root / "manifest.json")
        if (
            manifest.get("version") != STORE_VERSION
            or root.name != manifest.get("identity")
            or manifest.get("identity")
            != digest({k: v for k, v in manifest.items() if k != "identity"})
        ):
            fail("CAPACITY_CACHE", "Panel manifest identity changed")
        if not isinstance(manifest.get("fields"), dict):
            fail("CAPACITY_CACHE", "Panel fields are missing")
        store = cls(root, manifest)
        for name in manifest["fields"]:
            array = store.field(name)
            if array.shape != (len(store.dates), len(store.symbols)):
                fail(
                    "CAPACITY_AXIS", "Panel array does not match the complete pool axes"
                )
        return store

    def field(self, name):
        if name not in self.manifest["fields"]:
            fail("MISSING_FACTOR_DATA", "Panel field is unavailable: " + name)
        if name not in self._arrays:
            self._arrays[name] = open_array(self.root, self.manifest["fields"][name])
        return self._arrays[name]
