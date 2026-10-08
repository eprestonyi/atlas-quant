"""Strict, bounded import of Atlas's deterministic USTAR reproduction archives.

No general tar extraction is used: extension records must never cause an
unbounded read before their type or size is checked. The independent bundle
auditor is the only semantic verifier; the research engine is not imported.
"""

from contextlib import contextmanager
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile

from bundle_audit import (
    BundleAudit,
    LIMIT_CHUNK,
    LIMIT_MANIFEST,
    LIMIT_TOTAL,
    PATHS,
    audit_bundle,
    canonical,
    decode,
)

BLOCK = 512
MAX_CHUNKS = 256
MAX_FILES = MAX_CHUNKS + 1
MAX_ARCHIVE_BYTES = (
    LIMIT_TOTAL + LIMIT_MANIFEST + MAX_FILES * (BLOCK * 2 - 1) + BLOCK * 2
)
READ_BLOCK = 1024 * 1024


class ArchiveError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise ArchiveError(code, message)


class BoundedReader:
    def __init__(self, stream):
        self.stream = stream
        self.consumed = 0

    def exact(self, size):
        require(
            type(size) is int and 0 <= size <= LIMIT_CHUNK,
            "ARCHIVE_SIZE",
            "Archive read exceeds the single-read budget",
        )
        require(
            self.consumed + size <= MAX_ARCHIVE_BYTES,
            "ARCHIVE_SIZE",
            "Archive exceeds the total byte budget",
        )
        pieces = []
        remaining = size
        while remaining:
            value = self.stream.read(remaining)
            require(
                bool(value),
                "ARCHIVE_TRUNCATED",
                "Archive ended before its declared boundary",
            )
            pieces.append(value)
            remaining -= len(value)
        self.consumed += size
        return b"".join(pieces)

    def padding(self, size):
        length = (-size) % BLOCK
        require(
            self.exact(length) == bytes(length),
            "ARCHIVE_PADDING",
            "Archive body padding must be all zero bytes",
        )

    def finish(self):
        require(
            self.exact(BLOCK * 2) == bytes(BLOCK * 2),
            "ARCHIVE_END",
            "Archive must end with exactly two zero blocks",
        )
        require(
            self.stream.read(1) == b"",
            "ARCHIVE_END",
            "Trailing records or extra padding are not part of the export format",
        )


def _expected_header(name, size):
    """Independent implementation of the server's fixed, non-extension header."""
    header = bytearray(BLOCK)

    def put(offset, value):
        raw = value.encode("ascii")
        header[offset : offset + len(raw)] = raw

    def octal(offset, width, value):
        digits = format(value, "o")
        require(len(digits) < width, "ARCHIVE_HEADER", "Numeric header field overflow")
        put(offset, digits.zfill(width - 1) + "\0")

    put(0, name)
    octal(100, 8, 0o600)
    octal(108, 8, 0)
    octal(116, 8, 0)
    octal(124, 12, size)
    octal(136, 12, 0)
    header[148:156] = b" " * 8
    put(156, "0")
    put(257, "ustar\0")
    put(263, "00")
    put(148, format(sum(header), "o").zfill(6) + "\0 ")
    return bytes(header)


def _header(reader, expected_name, expected_size=None):
    raw = reader.exact(BLOCK)
    try:
        info = tarfile.TarInfo.frombuf(raw, encoding="ascii", errors="strict")
    except (tarfile.HeaderError, UnicodeError, ValueError) as error:
        raise ArchiveError(
            "ARCHIVE_HEADER", "Invalid USTAR header or checksum"
        ) from error
    require(
        raw[156:157] == b"0" and info.type == tarfile.REGTYPE,
        "ARCHIVE_TYPE",
        "Only type-0 regular files are allowed; extensions, links and directories are forbidden",
    )
    require(
        info.name == expected_name,
        "ARCHIVE_PATH",
        "Archive member is duplicated, out of order, unknown, or has an unsafe path",
    )
    limit = LIMIT_MANIFEST if expected_name == "manifest.json" else LIMIT_CHUNK
    require(
        0 < info.size <= limit, "ARCHIVE_SIZE", "Archive member exceeds its body budget"
    )
    if expected_size is not None:
        require(
            info.size == expected_size,
            "ARCHIVE_SIZE",
            "Archive member size differs from manifest",
        )
    require(
        info.uid == info.gid == info.mtime == 0
        and info.mode == 0o600
        and not info.linkname
        and not info.uname
        and not info.gname,
        "ARCHIVE_HEADER",
        "Archive ownership, mode, timestamp or link fields are not canonical",
    )
    require(
        raw == _expected_header(expected_name, info.size),
        "ARCHIVE_HEADER",
        "Only the exact Atlas USTAR header encoding is supported",
    )
    return info.size


def _sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_body(reader, path, size, expected_hash=None):
    checksum = hashlib.sha256()
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        remaining = size
        while remaining:
            value = reader.exact(min(remaining, READ_BLOCK))
            stream.write(value)
            checksum.update(value)
            remaining -= len(value)
        stream.flush()
        os.fsync(stream.fileno())
    reader.padding(size)
    if expected_hash is not None:
        require(
            checksum.hexdigest() == expected_hash,
            "ARCHIVE_HASH",
            "Archive chunk body differs from the declared SHA-256",
        )


@contextmanager
def _open_archive(path):
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode),
            "ARCHIVE_FILE",
            "Archive input must be a regular file",
        )
        require(
            info.st_size <= MAX_ARCHIVE_BYTES,
            "ARCHIVE_SIZE",
            "Archive file exceeds total budget",
        )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            yield BoundedReader(stream)
    finally:
        os.close(descriptor)


def _manifest_plan(directory):
    raw = (directory / "manifest.json").read_bytes()
    value = decode(raw)
    require(isinstance(value, dict), "ARCHIVE_MANIFEST", "Manifest must be an object")
    require(
        canonical(value) == raw, "ARCHIVE_MANIFEST", "Manifest must use canonical JSON"
    )
    # This validates descriptor bounds before any chunk body is read. The full
    # independent audit runs again only after all registered files are present.
    with sqlite3.connect(":memory:") as database:
        parsed = BundleAudit(directory, database)
        manifest = parsed.manifest
    entries = []
    for collection in manifest["collections"]:
        require(collection["id"] in PATHS, "ARCHIVE_PATH", "Unknown collection")
        for chunk in collection["chunks"]:
            name = f"chunks/{collection['id']}/{chunk['ordinal']}.json"
            entries.append((name, chunk["byteLength"], chunk["sha256"]))
    require(len(entries) <= MAX_CHUNKS, "ARCHIVE_SIZE", "Too many archive members")
    return manifest, entries


def _publish(source, destination):
    """Atomic publication that cannot replace a racing empty output directory."""
    if sys.platform == "win32":
        # Windows os.rename raises FileExistsError rather than replacing dst.
        os.rename(source, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(library, "renamex_np"):
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        rename = library.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise ArchiveError(
            "ARCHIVE_PUBLISH", "This platform has no supported atomic no-replace rename"
        )
    if result:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise ArchiveError(
                "ARCHIVE_OUTPUT_EXISTS",
                "Output appeared during import and was preserved",
            )
        raise OSError(error, os.strerror(error), str(destination))


def extract_bundle_archive(archive, destination, *, source_bundle=None):
    """Validate into a private sibling directory, audit, then publish exactly once."""
    destination = Path(destination).absolute()
    require(
        not os.path.lexists(destination),
        "ARCHIVE_OUTPUT_EXISTS",
        "Output directory must be new; existing files and directories are never replaced",
    )
    parent = destination.parent.resolve(strict=True)
    require(
        parent.is_dir(), "ARCHIVE_OUTPUT", "Output parent must be an existing directory"
    )
    destination = parent / destination.name
    temporary = Path(tempfile.mkdtemp(prefix=".atlas-bundle-extract-", dir=parent))
    os.chmod(temporary, 0o700)
    published = False
    try:
        with _open_archive(archive) as reader:
            size = _header(reader, "manifest.json")
            _write_body(reader, temporary / "manifest.json", size)
            manifest, entries = _manifest_plan(temporary)
            if manifest["kind"] == "execution":
                require(
                    source_bundle is not None,
                    "ARCHIVE_SOURCE_REQUIRED",
                    "An execution archive requires its original forecast bundle, including frozen market data",
                )
                with sqlite3.connect(":memory:") as database:
                    source = BundleAudit(source_bundle, database)
                    require(
                        source.manifest["kind"] == "forecast",
                        "ARCHIVE_SOURCE_REQUIRED",
                        "The source must be a forecast bundle containing its snapshot",
                    )
            else:
                require(
                    source_bundle is None,
                    "ARCHIVE_SOURCE",
                    "--source-bundle is only used when importing an execution archive",
                )
            (temporary / "chunks").mkdir(mode=0o700)
            for name, declared_size, checksum in entries:
                _header(reader, name, declared_size)
                path = temporary / name
                path.parent.mkdir(mode=0o700, exist_ok=True)
                _write_body(reader, path, declared_size, checksum)
            reader.finish()
            archive_bytes = reader.consumed
        result = audit_bundle(temporary, source_bundle)
        result = {
            **result,
            "archive": {
                "format": "atlas-ustar-v1",
                "files": len(entries) + 1,
                "bytes": archive_bytes,
            },
            "directory": str(destination),
            "published": True,
        }
        for path in sorted(temporary.rglob("*"), reverse=True):
            if path.is_dir():
                _sync_directory(path)
        _sync_directory(temporary)
        _publish(temporary, destination)
        published = True
        try:
            _sync_directory(parent)
        except OSError:
            # Publication is already committed. Do not misreport a valid output
            # as a failed extraction or try to remove the user's new directory.
            result["warnings"] = ["PARENT_DIRECTORY_FSYNC_UNAVAILABLE"]
        return result
    except (
        KeyError,
        TypeError,
        AttributeError,
        sqlite3.DatabaseError,
        RecursionError,
    ) as error:
        raise ArchiveError(
            "ARCHIVE_AUDIT", "Invalid bundle structure or audit input: " + str(error)
        ) from error
    finally:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
