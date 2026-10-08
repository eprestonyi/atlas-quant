"""Dataset/3 archive with exact bounded USTAR; old dataset parsers stay closed."""
import os,stat,tempfile,shutil
from pathlib import Path
from ..archive import BLOCK,header,entries,_sync,_publish,_ArchiveReader
from ..codec import require
from ..profile import DEFAULT_PROFILE,check_profile
from .manifest import validate_manifest
from .dataset import GraphDatasetReader,DirectoryGraphDatasetReader


def export_graph_dataset_archive(reader, destination):
    require(
        isinstance(reader, GraphDatasetReader), "DATASET_READER", "Typed reader required"
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
            "format": "atlas-graph-dataset-ustar-v1",
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


def extract_graph_dataset_archive(
    archive, destination, *, expected_root=None, profile=DEFAULT_PROFILE
):
    check_profile(profile)
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
        result = DirectoryGraphDatasetReader(
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
            directory=str(destination), archiveFormat="atlas-graph-dataset-ustar-v1"
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
