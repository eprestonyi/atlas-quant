"""Bounded byte verification and fresh-process financial recomposition."""

import os
from pathlib import Path
import stat
from types import MappingProxyType

from .codec import DatasetError, decode, encode, keys, require, sha, uuid
from .compose import FinancialSource, compose_dataset_components
from .manifest import validate_manifest
from .profile import DEFAULT_PROFILE


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, MappingProxyType):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class DatasetReader:
    def __init__(
        self, manifest_bytes, read_part, *, expected_root=None, profile=DEFAULT_PROFILE
    ):
        manifest = validate_manifest(
            manifest_bytes, expected_root=expected_root, profile=profile
        )
        self._profile = profile
        self._manifest_bytes = manifest_bytes
        self._manifest = _freeze(manifest)
        self._dataset_root = sha(manifest_bytes)
        self._read_part = read_part
        self._components = MappingProxyType(
            {c["componentId"]: c for c in self._manifest["components"]}
        )

    @property
    def manifest_bytes(self):
        return self._manifest_bytes

    @property
    def dataset_root(self):
        return self._dataset_root

    @property
    def profile(self):
        return self._profile

    @property
    def manifest(self):
        # Callers may inspect/edit their own projection, never the trusted graph.
        return _thaw(self._manifest)

    @property
    def components(self):
        return _thaw(self._components)

    def part(self, component_id, ordinal):
        require(
            component_id in self._components
            and type(ordinal) is int
            and 0 <= ordinal < len(self._components[component_id]["parts"]),
            "DATASET_PART",
            "Unregistered component part",
        )
        descriptor = self._components[component_id]["parts"][ordinal]
        try:
            raw = self._read_part(component_id, ordinal)
        except (KeyError, FileNotFoundError) as exc:
            raise DatasetError(
                "DATASET_PART_MISSING", "Required immutable component part is absent"
            ) from exc
        require(
            isinstance(raw, bytes)
            and len(raw) == descriptor["byteLength"]
            and sha(raw) == descriptor["sha256"],
            "DATASET_PART",
            "Component part differs from its immutable receipt",
        )
        return raw

    def payload(self, component_id):
        item = self._components[component_id]
        raw = b"".join(self.part(component_id, i) for i in range(len(item["parts"])))
        require(
            len(raw) == item["byteLength"] and sha(raw) == item["payloadSha256"],
            "DATASET_PAYLOAD",
            "Reconstructed payload identity differs",
        )
        return raw

    def verify_integrity(self):
        """No trust or numerical claim: exact complete typed transport only."""
        for name in self._components:
            decode(self.payload(name), self.profile.total_bytes)
        return {
            "datasetRoot": self.dataset_root,
            "transportVerified": True,
            "sourceAuthorityVerified": False,
            "financialRecomputed": False,
            "components": len(self._components),
            "parts": sum(len(c["parts"]) for c in self._components.values()),
        }


def _read_file(path, maximum):
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum,
            "DATASET_FILE",
            "Expected a bounded regular file",
        )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        require(
            len(raw) == info.st_size and len(raw) <= maximum,
            "DATASET_FILE",
            "File changed while reading or exceeded budget",
        )
        return raw
    finally:
        os.close(descriptor)


class DirectoryDatasetReader(DatasetReader):
    def __init__(self, directory, *, expected_root=None, profile=DEFAULT_PROFILE):
        self.directory = Path(directory).absolute()
        require(
            self.directory.is_dir() and not self.directory.is_symlink(),
            "DATASET_PATH",
            "Dataset directory cannot be a symlink",
        )
        raw = _read_file(self.directory / "manifest.json", profile.manifest_bytes)
        super().__init__(
            raw, self._part_file, expected_root=expected_root, profile=profile
        )
        require(
            {p.name for p in self.directory.iterdir()} == {"manifest.json", "parts"},
            "DATASET_PATH",
            "Unregistered archive entries",
        )
        parts = self.directory / "parts"
        require(
            parts.is_dir()
            and not parts.is_symlink()
            and {p.name for p in parts.iterdir()} == set(self._components),
            "DATASET_PATH",
            "Missing or unregistered component directories",
        )
        for name, component in self._components.items():
            folder = parts / name
            require(
                folder.is_dir()
                and not folder.is_symlink()
                and {p.name for p in folder.iterdir()}
                == {f"{i}.bin" for i in range(len(component["parts"]))},
                "DATASET_PATH",
                "Missing or extra component parts",
            )

    def _part_file(self, name, ordinal):
        return _read_file(
            self.directory / "parts" / name / f"{ordinal}.bin", self.profile.part_bytes
        )


def restore_dataset(reader, authorized_registry):
    """Recompute offline from separately authorized complete frozen evidence.

    This returns a new process-local admitted object, never registers serialized
    rows directly. Caller authority authenticates registry bytes, not PDF truth.
    """
    require(
        isinstance(reader, DatasetReader), "DATASET_READER", "Typed reader required"
    )
    reader.verify_integrity()
    registry = decode(reader.payload("registryEvidence"), reader.profile.total_bytes)
    keys(registry, {"entries"})
    require(
        isinstance(registry["entries"], list) and len(registry["entries"]) <= 2057,
        "DATASET_REGISTRY",
        "Registry descriptor count exceeds profile",
    )
    stored = {}
    for entry in registry["entries"]:
        keys(entry, {"ref", "sha256", "byteLength", "rawText"})
        ref = uuid(entry["ref"])
        require(
            ref not in stored and isinstance(entry["rawText"], str),
            "DATASET_REGISTRY",
            "Duplicate or malformed registry record",
        )
        raw = entry["rawText"].encode("utf-8")
        require(
            type(entry["byteLength"]) is int
            and len(raw) == entry["byteLength"]
            and len(raw) <= reader.profile.registry_bytes
            and sha(raw) == entry["sha256"],
            "DATASET_REGISTRY",
            "Registry bytes differ from receipt",
        )
        stored[ref] = raw
    require(
        isinstance(authorized_registry, dict) and stored == authorized_registry,
        "DATASET_AUTHORITY",
        "Archive cannot authorize itself; exact external registry pins required",
    )
    sources = [
        FinancialSource(
            reader.payload(s["componentId"]),
            s["preparedRoot"],
            s["calendarRef"],
            tuple(s["proofRefs"]),
        )
        for s in reader.manifest["financialSources"]
    ]

    def compare(name, ordinal, raw):
        require(
            raw == reader.part(name, ordinal),
            "DATASET_RECOMPUTATION",
            "Recomputed source, numbers, missing masks or lineage differ from archive",
        )

    publication = compose_dataset_components(
        reader.manifest["scope"],
        reader.payload("marketDataset"),
        sources,
        authorized_registry,
        compare,
        market_calendar_ref=reader.manifest["marketCalendarRef"],
        profile=reader.profile,
    )
    require(
        publication.manifest_bytes == reader.manifest_bytes,
        "DATASET_RECOMPUTATION",
        "Recomputed typed closure differs; rehashing forged rows cannot grant admission",
    )
    return publication.result


def restore_dataset_for_research(strategy, reader, authorized_registry):
    from .snapshot import validate_research_profile

    validate_research_profile(strategy, reader.manifest["scope"])
    return restore_dataset(reader, authorized_registry)
