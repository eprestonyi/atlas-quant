"""Exclusive private export with retained directory handles; failed partials survive."""
from contextlib import ExitStack
from pathlib import Path
import os
import shutil
import stat
from .contract import *
from .view import derive_view

RESERVE = 500 * 1024**2
WRITE_CHUNK = 64 * 1024


def _identity(info):
    return info.st_dev, info.st_ino


def _space(fd, remaining):
    require(shutil.disk_usage(fd).free >= RESERVE + remaining,
            'SOURCE_DISK', '500 MiB reserve plus all remaining output required')


class _Directories:
    """Anchor every ancestor and child with no-follow directory descriptors."""
    def __init__(self, stack):
        require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'),
                'SOURCE_PATH', 'No-follow directory descriptor support required')
        self.stack = stack
        self.flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
        self.links = []
        self.created = []
        self.files = []

    def open(self, name, parent=None):
        fd = os.open(name, self.flags, dir_fd=parent)
        self.stack.callback(os.close, fd)
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode), 'SOURCE_PATH', 'Directory required')
        if parent is not None:
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(entry.st_mode) and _identity(entry) == _identity(info),
                    'SOURCE_PATH', 'Directory changed during open')
            self.links.append((parent, name, fd, _identity(info)))
        return fd

    def verify(self):
        for parent, name, fd, identity in self.links:
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(entry.st_mode) and _identity(entry) == identity
                    and _identity(os.fstat(fd)) == identity,
                    'SOURCE_PATH', 'An anchored directory was replaced')
        for parent, name, identity, size in self.files:
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISREG(entry.st_mode) and _identity(entry) == identity
                    and entry.st_size == size and entry.st_uid == os.geteuid()
                    and entry.st_mode & 0o077 == 0,
                    'SOURCE_PATH', 'An exclusive output file was replaced or changed')

    def create(self, parent, name, remaining):
        self.verify()
        _space(parent, remaining)
        # Existing entries, including symlinks, are never adopted or overwritten.
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError as exc:
            raise SourceError('SOURCE_PATH', 'Output entry already exists; no overwrite') from exc
        fd = self.open(name, parent)
        info = os.fstat(fd)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                'SOURCE_PATH', 'New output directory is not private and owned')
        self.created.append((parent, fd))
        self.verify()
        _space(parent, remaining)
        os.fsync(fd)
        os.fsync(parent)
        self.verify()
        return fd


def export_view(view, directory, *, limits=DEFAULT_LIMITS):
    m = decode(view.manifest_bytes, limits.descriptor_bytes, canonical=True)
    checked = derive_view(view.source, view.scope_bytes, m['transform']['universeScopeRef'],
                          mode=m['transform']['mode'], limits=limits)
    require(checked == view, 'SOURCE_INTEGRITY', 'View must equal fresh full-source derivation')
    payloads = {'source.json': view.source.descriptor_bytes, 'view.json': view.manifest_bytes,
                'filter-scope.json': view.scope_bytes, 'market.json': view.market_bytes,
                **{'originals/'+n: raw for n, raw in view.source.originals}}
    root = Path(directory).absolute()
    require(root.name and '..' not in root.parts, 'SOURCE_PATH', 'New absolute directory without parent traversal required')
    subdirs = sorted({p for name in payloads for p in Path(name).parents if p != Path('.')},
                     key=lambda p: (len(p.parts), str(p)))
    with ExitStack() as stack:
        dirs = _Directories(stack)
        parent = dirs.open(root.anchor)
        for name in root.parent.parts[1:]:
            parent = dirs.open(name, parent)
        usage = os.fstatvfs(parent)
        block = max(4096, usage.f_frsize or usage.f_bsize)
        # Round each payload to filesystem allocation blocks and reserve two more
        # blocks per new entry. Actual free space is rechecked before each write.
        budgets = {name: ((len(raw)+block-1)//block+2)*block for name, raw in payloads.items()}
        remaining = sum(budgets.values()) + 2*block*(len(subdirs)+1)
        anchor = dirs.create(parent, root.name, remaining)
        remaining -= 2*block
        handles = {Path('.'): anchor}
        for path in subdirs:
            handles[path] = dirs.create(handles[path.parent], path.name, remaining)
            remaining -= 2*block
        for name, raw in payloads.items():
            path = Path(name)
            directory_fd = handles[path.parent]
            dirs.verify()
            _space(anchor, remaining)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
            fd = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
            try:
                initial = os.fstat(fd)
                require(stat.S_ISREG(initial.st_mode) and initial.st_uid == os.geteuid()
                        and initial.st_mode & 0o077 == 0, 'SOURCE_PATH', 'Private regular output required')
                for offset in range(0, len(raw), WRITE_CHUNK):
                    dirs.verify()
                    _space(anchor, remaining)
                    chunk = memoryview(raw)[offset:offset+WRITE_CHUNK]
                    written = os.write(fd, chunk)
                    require(written == len(chunk), 'SOURCE_WRITE', 'Short output write; partial file retained')
                require(os.fstat(fd).st_size == len(raw), 'SOURCE_WRITE', 'Incomplete output file')
                _space(anchor, remaining)
                os.fsync(fd)
                dirs.files.append((directory_fd, path.name, _identity(initial), len(raw)))
                dirs.verify()
                _space(anchor, remaining)
                os.fsync(directory_fd)
            finally:
                os.close(fd)
            remaining -= budgets[name]
        # Flush each created directory and its parent entry, deepest first. An
        # fsync failure propagates; no cleanup, overwrite, or success is returned.
        for parent_fd, directory_fd in reversed(dirs.created):
            dirs.verify()
            _space(anchor, remaining)
            os.fsync(directory_fd)
            os.fsync(parent_fd)
        dirs.verify()
        _space(anchor, 0)
    return {'viewRoot': view.view_root, 'sourceRoot': view.source.source_root,
            'files': len(payloads), 'bytes': sum(map(len, payloads.values())),
            'ownerGrantVerified': False, 'modelAdmissionRegistered': False,
            'providerCalls': 0, 'researchFits': 0}
