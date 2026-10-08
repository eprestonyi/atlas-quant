"""Deterministic private USTAR for the typed dataset format, not bundle/1."""

import ctypes
import errno
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

from .codec import DatasetError, require, sha
from .manifest import validate_manifest
from .profile import DEFAULT_PROFILE
from .reader import DatasetReader, DirectoryDatasetReader

BLOCK = 512


def header(name, size):
    require(
        isinstance(name, str) and len(name.encode("ascii")) < 100,
        "DATASET_ARCHIVE_PATH",
        "Archive path exceeds registered format",
    )
    out = bytearray(BLOCK)

    def put(offset, value):
        raw = value.encode("ascii")
        out[offset : offset + len(raw)] = raw

    def octal(offset, width, value):
        digits = format(value, "o")
        require(len(digits) < width, "DATASET_ARCHIVE_HEADER", "Header size overflow")
        put(offset, digits.zfill(width - 1) + "\0")

    put(0, name)
    octal(100, 8, 0o600)
    octal(108, 8, 0)
    octal(116, 8, 0)
    octal(124, 12, size)
    octal(136, 12, 0)
    out[148:156] = b" " * 8
    put(156, "0")
    put(257, "ustar\0")
    put(263, "00")
    put(148, format(sum(out), "o").zfill(6) + "\0 ")
    return bytes(out)


def entries(manifest):
    for component in manifest["components"]:
        for part in component["parts"]:
            yield f"parts/{component['componentId']}/{part['ordinal']}.bin", component[
                "componentId"
            ], part


def _sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(source, destination):
    """Atomic no-replace publication, including a racing empty destination."""
    if sys.platform == "win32":
        os.rename(source, destination)
        return
    lib = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(lib, "renamex_np"):
        fn = lib.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        result = fn(os.fsencode(source), os.fsencode(destination), 4)
    elif sys.platform.startswith("linux") and hasattr(lib, "renameat2"):
        fn = lib.renameat2
        fn.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        fn.restype = ctypes.c_int
        result = fn(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise DatasetError("DATASET_PUBLISH", "No supported atomic no-replace rename")
    if result:
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise DatasetError("DATASET_OUTPUT_EXISTS", "Existing output is preserved")
        raise OSError(code, os.strerror(code))


def export_dataset_archive(reader, destination):
    require(
        isinstance(reader, DatasetReader), "DATASET_READER", "Typed reader required"
    )
    reader.verify_integrity()
    destination = Path(destination).absolute()
    require(
        not os.path.lexists(destination),
        "DATASET_OUTPUT_EXISTS",
        "Archive output must be new",
    )
    # Write privately to a sibling and publish only after EOF and fsync. No partial
    # valid-looking destination is visible after failure or a racing publication.
    fd, temporary = tempfile.mkstemp(
        prefix=".atlas-dataset-tar-", dir=destination.parent
    )
    temporary = Path(temporary)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:

            def member(name, raw):
                stream.write(header(name, len(raw)))
                stream.write(raw)
                stream.write(bytes((-len(raw)) % BLOCK))

            member("manifest.json", reader.manifest_bytes)
            for name, component_id, part in entries(reader.manifest):
                member(name, reader.part(component_id, part["ordinal"]))
            stream.write(bytes(2 * BLOCK))
            stream.flush()
            os.fsync(stream.fileno())
        size = temporary.stat().st_size
        _publish(temporary, destination)
        published = True
        result = {
            "datasetRoot": reader.dataset_root,
            "archiveBytes": size,
            "format": "atlas-dataset-ustar-v1",
            "transportVerified": True,
            "sourceAuthorityVerified": False,
        }
        try:
            _sync(destination.parent)
        except OSError:
            result["warnings"] = ["PARENT_DIRECTORY_FSYNC_UNAVAILABLE"]
        return result
    finally:
        if not published:
            temporary.unlink(missing_ok=True)


class _ArchiveReader:
    def __init__(self, stream, profile):
        self.stream, self.profile = stream, profile
        self.consumed = 0
        self.maximum = profile.total_bytes + (profile.max_parts + 1) * 1023 + 1024

    def exact(self, size):
        require(
            type(size) is int
            and 0
            <= size
            <= max(BLOCK * 2, self.profile.part_bytes, self.profile.manifest_bytes)
            and self.consumed + size <= self.maximum,
            "DATASET_ARCHIVE_BUDGET",
            "Archive read exceeds its bounded profile",
        )
        chunks, left = [], size
        while left:
            raw = self.stream.read(left)
            require(
                raw and len(raw) <= left,
                "DATASET_ARCHIVE_TRUNCATED",
                "Archive ended inside an entry",
            )
            chunks.append(raw)
            left -= len(raw)
        self.consumed += size
        return b"".join(chunks)

    def member(self, name, expected_size=None, expected_hash=None):
        raw_header = self.exact(BLOCK)
        try:
            size = int(raw_header[124:135].decode("ascii"), 8)
        except (ValueError, UnicodeError) as exc:
            raise DatasetError(
                "DATASET_ARCHIVE_HEADER", "Invalid regular-file size"
            ) from exc
        limit = (
            self.profile.manifest_bytes
            if name == "manifest.json"
            else self.profile.part_bytes
        )
        require(
            0 < size <= limit and (expected_size is None or size == expected_size),
            "DATASET_ARCHIVE_BUDGET",
            "Archive entry exceeds exact manifest boundary",
        )
        require(
            raw_header == header(name, size),
            "DATASET_ARCHIVE_HEADER",
            "Only exact registered regular-file headers are accepted; no links, PAX, paths or extensions",
        )
        raw = self.exact(size)
        require(
            self.exact((-size) % BLOCK) == bytes((-size) % BLOCK),
            "DATASET_ARCHIVE_PADDING",
            "Nonzero archive padding",
        )
        if expected_hash is not None:
            require(
                sha(raw) == expected_hash,
                "DATASET_ARCHIVE_HASH",
                "Archive part hash differs",
            )
        return raw


def extract_dataset_archive(
    archive, destination, *, expected_root=None, profile=DEFAULT_PROFILE
):
    destination = Path(destination).absolute()
    require(
        not os.path.lexists(destination),
        "DATASET_OUTPUT_EXISTS",
        "Output must not exist",
    )
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    temporary = Path(tempfile.mkdtemp(prefix=".atlas-dataset-extract-", dir=parent))
    published = False
    fd = None
    try:
        fd = os.open(
            archive,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode),
            "DATASET_ARCHIVE_FILE",
            "Archive must be a regular file",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            reader = _ArchiveReader(stream, profile)
            require(
                info.st_size <= reader.maximum,
                "DATASET_ARCHIVE_BUDGET",
                "Archive total size exceeds budget",
            )
            raw_manifest = reader.member("manifest.json")
            manifest = validate_manifest(
                raw_manifest, expected_root=expected_root, profile=profile
            )

            def write(name, raw):
                target = temporary / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with target.open("xb") as output:
                    os.chmod(target, 0o600)
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())

            write("manifest.json", raw_manifest)
            for name, _, part in entries(manifest):
                write(name, reader.member(name, part["byteLength"], part["sha256"]))
            require(
                reader.exact(BLOCK * 2) == bytes(BLOCK * 2) and stream.read(1) == b"",
                "DATASET_ARCHIVE_EOF",
                "Archive requires exactly two EOF blocks, no trailing bytes",
            )
        result = DirectoryDatasetReader(
            temporary, expected_root=expected_root, profile=profile
        ).verify_integrity()
        for folder in sorted(
            (p for p in temporary.rglob("*") if p.is_dir()), reverse=True
        ):
            _sync(folder)
        _sync(temporary)
        _publish(temporary, destination)
        published = True
        result.update(
            directory=str(destination), archiveFormat="atlas-dataset-ustar-v1"
        )
        try:
            _sync(parent)
        except OSError:
            result["warnings"] = ["PARENT_DIRECTORY_FSYNC_UNAVAILABLE"]
        return result
    finally:
        if fd is not None:
            os.close(fd)
        if not published:
            shutil.rmtree(temporary)
