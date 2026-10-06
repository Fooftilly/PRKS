"""The ``StorageBackend`` boundary (storage-architecture §10).

A small, blob-shaped contract: exclusive create, atomic replace, open for
read, stat, idempotent delete, verify, and list for maintenance only. It knows
nothing about Works, Manifestations or Assets (#60 owns those); callers name
objects by :class:`StorageKey`, never by path.

Phase A adds the contract and the local implementation only. **No production
operation is routed through it yet** -- managed PDFs, portraits, import and
backup enumeration move onto it in Phase B. The shared contract suite in
``tests/storage_backend_contract.py`` is what any later backend (for example an
S3-compatible one, Phase F) must also pass.

``LocalFilesystemStorage`` is built from the existing primitives rather than
new ones: ``paths.resolved_child_path`` (containment and drive-qualified
refusal) turns a key into a path, and ``fs_durability`` (content barrier before
publication, best-effort directory sync after) makes writes crash-durable.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterator, Mapping, Optional, Protocol, runtime_checkable

from backend.fs_durability import fsync_directory, fsync_open_file
from backend.storage import paths
from backend.storage.file_lock import is_link_or_reparse_point

LOGGER = logging.getLogger("prks.storage")

NAMESPACE_ASSET_OBJECTS = "asset-objects"
NAMESPACE_PORTRAITS = "portraits"
NAMESPACES = frozenset({NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS})
# Layout 1 (§4.2): existing directory names are kept; the namespace decouples
# code from them.
LAYOUT_1_NAMESPACE_DIRS: Mapping[str, str] = {
    NAMESPACE_ASSET_OBJECTS: "pdfs",
    NAMESPACE_PORTRAITS: "people",
}

MAX_KEY_NAME_BYTES = 255
# Temporaries the storage layer and its neighbors create beside objects. A name
# with one of these prefixes is never a key, so it can never collide with an
# in-flight temporary, and maintenance listings skip them.
RESERVED_NAME_PREFIXES = (".prks-", ".linearized_")
IO_CHUNK_SIZE = 64 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# ``os.link`` failures that mean "this filesystem has no hard links" rather
# than "the name is taken" or a real I/O error.
_NO_HARDLINK_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, "EPERM", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOSYS", None),
        getattr(errno, "EMLINK", None),
        getattr(errno, "EXDEV", None),
    )
    if code is not None
)


class StorageObjectError(Exception):
    """Base class for storage-backend failures."""


class InvalidStorageKey(StorageObjectError, ValueError):
    """The key is not valid. Raised before storage is touched."""


class ObjectExists(StorageObjectError):
    """``put_new`` found the key already present; nothing was written."""


class ObjectNotFound(StorageObjectError):
    """The operation requires the key to exist and it does not."""


class ObjectIntegrityError(StorageObjectError):
    """The key names something that is not a plain object (a link, a directory)."""


class ExclusivePublishUnsupported(StorageObjectError):
    """The filesystem offers no atomic no-overwrite publication; nothing was written."""


_AT_FDCWD = -100
_LINUX_RENAME_NOREPLACE = 0x1
_DARWIN_RENAME_EXCL = 0x4
_NOREPLACE_UNSUPPORTED_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, "EINVAL", None),
        getattr(errno, "ENOSYS", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if code is not None
)


def rename_noreplace(src: str, dst: str) -> None:
    """Atomically rename ``src`` to ``dst`` only if ``dst`` does not exist.

    Linux ``renameat2(RENAME_NOREPLACE)`` or macOS ``renamex_np(RENAME_EXCL)``.
    Raises ``FileExistsError`` when ``dst`` exists and
    ``ExclusivePublishUnsupported`` when neither the platform nor the
    filesystem provides the primitive.
    """
    import ctypes

    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:  # pragma: no cover - no C library handle
        raise ExclusivePublishUnsupported("no-replace rename unavailable") from exc
    src_b, dst_b = os.fsencode(src), os.fsencode(dst)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        func = libc.renameat2
        func.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        func.restype = ctypes.c_int
        rc = func(_AT_FDCWD, src_b, _AT_FDCWD, dst_b, _LINUX_RENAME_NOREPLACE)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):  # pragma: no cover - macOS
        func = libc.renamex_np
        func.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        func.restype = ctypes.c_int
        rc = func(src_b, dst_b, _DARWIN_RENAME_EXCL)
    else:
        raise ExclusivePublishUnsupported("no-replace rename unavailable on this platform")
    if rc == 0:
        return
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError(code, os.strerror(code), dst)
    if code in _NOREPLACE_UNSUPPORTED_ERRNOS:
        raise ExclusivePublishUnsupported("filesystem refuses no-replace rename")
    raise OSError(code, os.strerror(code), dst)


def validate_key_name(name: object) -> str:
    """The backend-neutral name grammar (§10.2).

    The rules ``safe_pdf_path_under_dir`` applies to a decoded managed name,
    plus a byte bound and the reserved temporary prefixes. Names are stored
    names: they are never URL-decoded here. Legacy names those rules accept
    stay valid.
    """
    if not isinstance(name, str):
        raise InvalidStorageKey("key name must be a string")
    if (
        not name
        or name != name.strip()
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise InvalidStorageKey("key name is not a single plain segment")
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidStorageKey("key name is not valid UTF-8") from exc
    if len(encoded) > MAX_KEY_NAME_BYTES:
        raise InvalidStorageKey("key name is too long")
    if name.startswith(RESERVED_NAME_PREFIXES):
        raise InvalidStorageKey("key name uses a reserved prefix")
    return name


@dataclass(frozen=True)
class StorageKey:
    """``(namespace, name)``: one object within a backend. Text form ``namespace/name``."""

    namespace: str
    name: str

    def __post_init__(self) -> None:
        if self.namespace not in NAMESPACES:
            raise InvalidStorageKey("unknown storage namespace")
        validate_key_name(self.name)

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"


@dataclass(frozen=True)
class ObjectInfo:
    key: StorageKey
    size: int
    # Opaque; compare for equality within one request, never store as identity.
    version: str
    # Set only when this call computed it (put_new/replace with hash=True).
    sha256: Optional[str] = None


Writer = Callable[[BinaryIO], None]


@runtime_checkable
class StorageBackend(Protocol):
    """The whole generic surface (§10.1). Semantics: §10.2."""

    backend_type: str

    def put_new(self, key: StorageKey, write: Writer, *, hash: bool = True) -> ObjectInfo: ...

    def replace(self, key: StorageKey, write: Writer, *, hash: bool = True) -> ObjectInfo: ...

    def open_read(self, key: StorageKey) -> BinaryIO: ...

    def stat(self, key: StorageKey) -> Optional[ObjectInfo]: ...

    def delete(self, key: StorageKey) -> bool: ...

    def verify(self, key: StorageKey, sha256: str) -> Optional[bool]: ...

    def iter_keys(self, namespace: str) -> Iterator[StorageKey]: ...


@runtime_checkable
class LocalPathCapable(Protocol):
    """Local-only extension (§10.3) for infrastructure that needs a real file.

    Use the path only inside the ``with`` block; never store, log or return it.
    """

    def local_path(self, key: StorageKey): ...


def mint_name(namespace: str, hint: str) -> str:
    """A fresh key name for ``namespace`` (today's ``mint_managed_pdf_filename`` rules).

    Only ``asset-objects`` names are minted. Portrait names are derived from a
    person ID and an image-URL hash, never minted.
    """
    if namespace == NAMESPACE_ASSET_OBJECTS:
        from backend.db_manager import mint_managed_pdf_filename

        return validate_key_name(mint_managed_pdf_filename(hint))
    if namespace in NAMESPACES:
        raise InvalidStorageKey("names in this namespace are not minted")
    raise InvalidStorageKey("unknown storage namespace")


def _check_key(key: object) -> StorageKey:
    if not isinstance(key, StorageKey):
        raise InvalidStorageKey("expected a StorageKey")
    if key.namespace not in NAMESPACES:
        raise InvalidStorageKey("unknown storage namespace")
    validate_key_name(key.name)
    return key


class _HashingWriter:
    """The stream a ``write`` callback receives: counts and (optionally) hashes."""

    def __init__(self, handle: BinaryIO, digest: Optional["hashlib._Hash"]) -> None:
        self._handle = handle
        self._digest = digest
        self.size = 0

    def write(self, data: bytes) -> int:
        view = memoryview(data).cast("B")
        written = self._handle.write(view)
        if self._digest is not None:
            self._digest.update(view)
        self.size += len(view)
        return written if written is not None else len(view)

    def flush(self) -> None:
        self._handle.flush()

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False


def _version_token(st: os.stat_result) -> str:
    return f"{st.st_mtime_ns}:{st.st_size}:{st.st_ino}"


class LocalFilesystemStorage:
    """``StorageBackend`` over one data root, layout 1 (§10.3).

    The root is resolved **once**, at construction (bind time), and every key
    is resolved beneath that snapshot. Nothing inside the root is followed
    through a link: a link where a namespace directory or an object should be
    is an ``ObjectIntegrityError``.
    """

    backend_type = "local"

    def __init__(
        self,
        root: str,
        *,
        namespace_dirs: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._root_real = os.path.realpath(root)
        dirs = dict(LAYOUT_1_NAMESPACE_DIRS if namespace_dirs is None else namespace_dirs)
        if set(dirs) != set(NAMESPACES):
            raise ValueError("every namespace needs exactly one directory")
        for rel in dirs.values():
            validate_key_name(rel)
        self._namespace_dirs = dirs

    @classmethod
    def for_config(cls, config) -> "LocalFilesystemStorage":
        """The backend for a bound ``StorageConfig`` (layout 1 under ``config.root``)."""
        return cls(config.root)

    # -- key -> path ----------------------------------------------------------------

    def _namespace_dir(self, namespace: str, *, create: bool) -> Optional[str]:
        rel = self._namespace_dirs[namespace]
        if paths.resolved_child_path(self._root_real, rel) is None:
            raise InvalidStorageKey("namespace directory escapes the storage root")
        directory = os.path.join(self._root_real, rel)
        try:
            st = os.lstat(directory)
        except FileNotFoundError:
            if not create:
                return None
            try:
                os.mkdir(directory, 0o700)
            except FileExistsError:
                pass
            if not fsync_directory(self._root_real):
                LOGGER.warning("storage_dir_sync_failed op=mkdir")
            st = os.lstat(directory)
        if is_link_or_reparse_point(st) or not stat.S_ISDIR(st.st_mode):
            raise ObjectIntegrityError("namespace directory is not a plain directory")
        return directory

    def _object_path(self, directory: str, key: StorageKey) -> str:
        lexical = os.path.join(directory, key.name)
        if paths.resolved_child_path(directory, key.name) is None:
            if os.path.islink(lexical):
                raise ObjectIntegrityError("object is a link")
            raise InvalidStorageKey("key name cannot be placed beneath the namespace")
        return lexical

    def _existing(self, key: StorageKey) -> Optional[tuple[str, os.stat_result]]:
        directory = self._namespace_dir(key.namespace, create=False)
        if directory is None:
            return None
        path = self._object_path(directory, key)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        if is_link_or_reparse_point(st) or not stat.S_ISREG(st.st_mode):
            raise ObjectIntegrityError("object is not a regular file")
        return path, st

    # -- writes ---------------------------------------------------------------------

    def _write_temporary(
        self, directory: str, write: Writer, *, hash: bool
    ) -> tuple[str, int, Optional[str]]:
        fd, tmp = tempfile.mkstemp(prefix=".prks-write-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                sink = _HashingWriter(handle, hashlib.sha256() if hash else None)
                write(sink)  # type: ignore[arg-type]
                handle.flush()
                # Content barrier before anything can publish the bytes.
                fsync_open_file(handle.fileno())
                digest = sink._digest.hexdigest() if sink._digest is not None else None
                return tmp, sink.size, digest
        except BaseException:
            _remove_quietly(tmp)
            raise

    def _publish_exclusive(self, tmp: str, final: str) -> None:
        """Give ``tmp``'s bytes the name ``final`` only if ``final`` does not exist.

        Every path is one atomic, no-overwrite step, so a reader sees no object
        or the whole object and a crash leaves nothing under the key: POSIX
        ``link`` (then the temporary is dropped), Windows ``rename`` (which
        refuses an existing target), or -- on a filesystem without hard links
        -- the kernel's no-replace rename. Where none exists, publication fails
        closed rather than reserving the key with an empty file.
        """
        if os.name == "nt":  # pragma: no cover - Windows only
            try:
                os.rename(tmp, final)
            except FileExistsError as exc:
                raise ObjectExists("key already exists") from exc
            return
        try:
            os.link(tmp, final)
        except FileExistsError as exc:
            raise ObjectExists("key already exists") from exc
        except OSError as exc:
            if exc.errno not in _NO_HARDLINK_ERRNOS:
                raise
            try:
                rename_noreplace(tmp, final)
            except FileExistsError as exists:
                raise ObjectExists("key already exists") from exists
            return
        _remove_quietly(tmp)

    def put_new(self, key: StorageKey, write: Writer, *, hash: bool = True) -> ObjectInfo:
        key = _check_key(key)
        directory = self._namespace_dir(key.namespace, create=True)
        assert directory is not None
        final = self._object_path(directory, key)
        if os.path.lexists(final):
            raise ObjectExists("key already exists")
        tmp, size, digest = self._write_temporary(directory, write, hash=hash)
        try:
            self._publish_exclusive(tmp, final)
        finally:
            _remove_quietly(tmp)
        if not fsync_directory(directory):
            # Same rule as the managed-PDF create path: the bytes are durable,
            # only the directory entry is weaker; report, do not discard.
            LOGGER.warning("storage_dir_sync_failed op=put_new")
        st = os.lstat(final)
        return ObjectInfo(key=key, size=size, version=_version_token(st), sha256=digest)

    def replace(self, key: StorageKey, write: Writer, *, hash: bool = True) -> ObjectInfo:
        key = _check_key(key)
        found = self._existing(key)
        if found is None:
            raise ObjectNotFound("key does not exist")
        final, _ = found
        directory = os.path.dirname(final)
        tmp, size, digest = self._write_temporary(directory, write, hash=hash)
        try:
            if self._existing(key) is None:
                raise ObjectNotFound("key does not exist")
            os.replace(tmp, final)
        except BaseException:
            _remove_quietly(tmp)
            raise
        if not fsync_directory(directory):
            LOGGER.warning("storage_dir_sync_failed op=replace")
        st = os.lstat(final)
        return ObjectInfo(key=key, size=size, version=_version_token(st), sha256=digest)

    # -- reads ----------------------------------------------------------------------

    def open_read(self, key: StorageKey) -> BinaryIO:
        key = _check_key(key)
        found = self._existing(key)
        if found is None:
            raise ObjectNotFound("key does not exist")
        path, st = found
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ObjectNotFound("key does not exist") from exc
        except OSError as exc:
            if exc.errno == getattr(errno, "ELOOP", None):
                raise ObjectIntegrityError("object is a link") from exc
            raise
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (
            os.name == "posix" and (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino)
        ):
            os.close(fd)
            raise ObjectIntegrityError("object changed while it was opened")
        return os.fdopen(fd, "rb")

    def stat(self, key: StorageKey) -> Optional[ObjectInfo]:
        key = _check_key(key)
        found = self._existing(key)
        if found is None:
            return None
        _, st = found
        return ObjectInfo(key=key, size=st.st_size, version=_version_token(st), sha256=None)

    def delete(self, key: StorageKey) -> bool:
        key = _check_key(key)
        found = self._existing(key)
        if found is None:
            return True
        path, _ = found
        try:
            os.remove(path)
        except FileNotFoundError:
            return True
        if not fsync_directory(os.path.dirname(path)):
            LOGGER.warning("storage_dir_sync_failed op=delete")
        return True

    def verify(self, key: StorageKey, sha256: str) -> Optional[bool]:
        key = _check_key(key)
        expected = str(sha256 or "").lower()
        if not _SHA256_RE.fullmatch(expected):
            raise ValueError("sha256 must be 64 hex characters")
        try:
            handle = self.open_read(key)
        except (ObjectNotFound, ObjectIntegrityError, OSError):
            return None
        digest = hashlib.sha256()
        try:
            with handle:
                for chunk in iter(lambda: handle.read(IO_CHUNK_SIZE), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest() == expected

    def iter_keys(self, namespace: str) -> Iterator[StorageKey]:
        """Maintenance only (audit, orphan sweep, backup, relocation). Never on a request path."""
        if namespace not in NAMESPACES:
            raise InvalidStorageKey("unknown storage namespace")
        directory = self._namespace_dir(namespace, create=False)
        if directory is None:
            return
        with os.scandir(directory) as it:
            names = sorted(entry.name for entry in it if entry.is_file(follow_symlinks=False))
        for name in names:
            try:
                yield StorageKey(namespace, name)
            except InvalidStorageKey:
                continue  # temporaries, reserved and unrepresentable names

    # -- local-only extension (§10.3) -------------------------------------------------

    @contextmanager
    def local_path(self, key: StorageKey) -> Iterator[str]:
        key = _check_key(key)
        found = self._existing(key)
        if found is None:
            raise ObjectNotFound("key does not exist")
        yield found[0]


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
