"""R0: validate a format-2 archive before anything is staged (L-B-1 #639; §11.4, §13.3).

:func:`open_archive` checks an uploaded ``.prks-backup`` without writing
anything:

- **Resource bounds** (format 1's, kept): the entry count, the declared
  uncompressed size, each member's compression ratio, and free space on the
  staging filesystem for the declared size plus a margin. Format 2 stores its
  members (no compression), so a valid archive of highly compressible
  content always has ratio 1 and passes, while a hostile deflated member over
  the ratio limit is refused (transferred from the SQLite audit #986: the
  policy never rejects its own output, and decompression bombs stay out).
- **Member names**: normalized relative POSIX paths only (no absolute or
  drive path, ``.``/``..``, backslash, NUL, empty component), no directory,
  symlink or special entry, no duplicate.
- **Layout**: exactly ``manifest.json``, the two identity files, one
  ``db/<table>.csv`` per content table and one ``objects/<key>`` per object
  with a valid key; every member listed in the manifest and every manifest
  entry present.
- **Format**: a format-1 (SQLite) archive is refused with a clear message, a
  newer format too.
- **Hashes**: every member is read once and must match the manifest's size
  and SHA-256; reads stop at the declared size, so a member that inflates
  beyond it is refused.

The archive is never extracted: staging (R1) streams each member straight
from the archive into ``COPY`` or the storage backend, so no archive path is
ever resolved on the filesystem. :func:`check_schema` is the database half of
R0: the archive's schema fingerprint and interchange columns must match the
live database (before 1.0, §11.4 "Staging schema").
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as stat_module
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO, Any, Optional

import psycopg

from backend.domain.maintenance import tables
from backend.domain.maintenance.format import (
    DEVICES_MEMBER,
    FORMAT_ID,
    FORMAT_VERSION,
    IO_CHUNK_SIZE,
    MANIFEST_NAME,
    USERS_MEMBER,
    schema_fingerprint,
    table_member,
)
from backend.storage.objects import InvalidStorageKey, StorageKey

# Format 1's bounds (backend/backup_restore.py), kept for format 2 (§11.4).
DEFAULT_MAX_ZIP_ENTRIES = 500_000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 200
DISK_MARGIN_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LIBRARY_ID_RE = re.compile(r"^LIB-[0-9A-F]{32}$")
_UNIX_SPECIAL = frozenset({stat_module.S_IFIFO, stat_module.S_IFCHR, stat_module.S_IFBLK,
                           stat_module.S_IFSOCK, stat_module.S_IFLNK, stat_module.S_IFDIR})


class ArchiveRefused(Exception):
    """An archive R0 refuses; ``reason`` is a stable code, ``message`` safe to show."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Limits:
    max_entries: int = DEFAULT_MAX_ZIP_ENTRIES
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO
    disk_margin_bytes: int = DISK_MARGIN_BYTES


@dataclass(frozen=True)
class Member:
    name: str
    size: int
    sha256: str
    rows: Optional[int] = None


@dataclass(frozen=True)
class Archive:
    """An archive that passed R0's structural checks and hashes."""

    path: str
    manifest: dict[str, Any]
    tables: dict[str, Member]
    users: Member
    devices: Member
    objects: dict[StorageKey, Member]

    @property
    def library_id(self) -> str:
        return str(self.manifest["library_id"])

    @contextmanager
    def open_member(self, member: Member) -> Iterator[BoundedReader]:
        """A stream of one member that never yields more than its declared size."""
        with zipfile.ZipFile(self.path) as archive, archive.open(member.name) as handle:
            yield BoundedReader(handle, member.size)


class BoundedReader:
    """Reads a member, refusing one that holds more than its declared size."""

    def __init__(self, inner: IO[bytes], limit: int) -> None:
        self._inner = inner
        self._left = limit

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            if self._inner.read(1):
                raise ArchiveRefused("member_size", "An archive member is larger than its manifest says.")
            return b""
        want = self._left if size < 0 else min(size, self._left)
        data = self._inner.read(want)
        self._left -= len(data)
        return data


def _unsafe() -> ArchiveRefused:
    return ArchiveRefused("unsafe_archive_path", "The backup archive contains an unsafe path.")


def normalize_member_name(name: str) -> str:
    """The member name if it is a normalized relative POSIX file path; refuses anything else."""
    if not name or "\x00" in name or "\\" in name or name.endswith("/"):
        raise _unsafe()
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":") or "//" in name:
        raise _unsafe()
    posix = PurePosixPath(name)
    if posix.is_absolute() or posix.anchor or any(part in {".", ".."} for part in posix.parts):
        raise _unsafe()
    if posix.as_posix() != name:
        raise _unsafe()
    return name


def _is_regular(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat_module.S_IFMT(mode) if mode else 0
    return kind not in _UNIX_SPECIAL and kind in (0, stat_module.S_IFREG)


def _manifest_member(entry: Any, name: str, *, rows: bool) -> Member:
    if not isinstance(entry, dict):
        raise ArchiveRefused("manifest", "The backup manifest is malformed.")
    size, sha256 = entry.get("size"), entry.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 \
            or not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise ArchiveRefused("manifest", "The backup manifest is malformed.")
    count: Optional[int] = None
    if rows:
        count = entry.get("rows")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ArchiveRefused("manifest", "The backup manifest is malformed.")
    return Member(name=name, size=size, sha256=sha256, rows=count)


def _read_manifest(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ArchiveRefused("manifest", "The backup manifest is too large.")
    with archive.open(info) as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ArchiveRefused("manifest", "The backup manifest is too large.")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ArchiveRefused("manifest", "The backup manifest is not valid JSON.") from None
    if not isinstance(manifest, dict):
        raise ArchiveRefused("manifest", "The backup manifest is malformed.")
    return manifest


def _check_format(manifest: dict[str, Any]) -> None:
    if manifest.get("format") != FORMAT_ID:
        raise ArchiveRefused("not_a_backup", "This file is not a PRKS backup.")
    version = manifest.get("format_version")
    if version == 1:
        raise ArchiveRefused(
            "format_1",
            "This backup was made by a PRKS version that stored its library in SQLite (backup format 1). "
            "This PRKS version restores only format 2 backups.")
    if version != FORMAT_VERSION:
        raise ArchiveRefused("format_newer" if isinstance(version, int) and version > FORMAT_VERSION
                             else "manifest",
                             f"This backup uses format {version!r}; this PRKS version restores format "
                             f"{FORMAT_VERSION}.")
    if not isinstance(manifest.get("library_id"), str) or not _LIBRARY_ID_RE.fullmatch(manifest["library_id"]):
        raise ArchiveRefused("manifest", "The backup manifest is malformed.")


def _layout(manifest: dict[str, Any]) -> tuple[dict[str, Member], Member, Member, dict[StorageKey, Member]]:
    tables_entry, identities, objects_entry = (manifest.get("tables"), manifest.get("identities"),
                                               manifest.get("objects"))
    if not isinstance(tables_entry, dict) or not isinstance(identities, dict) \
            or not isinstance(objects_entry, dict):
        raise ArchiveRefused("manifest", "The backup manifest is malformed.")
    if set(tables_entry) != set(tables.BACKUP_CONTENT_TABLES):
        raise ArchiveRefused("schema_mismatch", "The backup's tables do not match this PRKS version's.")
    content = {table: _manifest_member(tables_entry[table], table_member(table), rows=True)
               for table in tables.BACKUP_CONTENT_TABLES}
    if set(identities) != {"users", "devices"}:
        raise ArchiveRefused("manifest", "The backup manifest is malformed.")
    users = _manifest_member(identities["users"], USERS_MEMBER, rows=True)
    devices = _manifest_member(identities["devices"], DEVICES_MEMBER, rows=True)
    objects: dict[StorageKey, Member] = {}
    for text_key, entry in objects_entry.items():
        namespace, _, name = str(text_key).partition("/")
        try:
            obj_key = StorageKey(namespace, name)
        except (InvalidStorageKey, ValueError):
            raise ArchiveRefused("manifest", "The backup manifest names an invalid object key.") from None
        objects[obj_key] = _manifest_member(entry, f"objects/{obj_key}", rows=False)
    return content, users, devices, objects


def _free_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def open_archive(path: str, *, staging_dir: str, limits: Limits = Limits(),
                 free_bytes: Callable[[str], int] = _free_bytes) -> Archive:
    """R0's archive checks (see the module docstring); raises :class:`ArchiveRefused`."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                raise ArchiveRefused("too_many_entries", "The backup archive has too many entries.")
            declared = 0
            by_name: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                name = normalize_member_name(info.filename)
                if not _is_regular(info):
                    raise ArchiveRefused("unsafe_archive_entry", "The backup archive contains a link or "
                                                                 "special entry.")
                if name in by_name:
                    raise ArchiveRefused("duplicate_entry", "The backup archive contains a duplicate entry.")
                by_name[name] = info
                declared += info.file_size
                if info.compress_size > 0 and info.file_size / info.compress_size > limits.max_compression_ratio:
                    raise ArchiveRefused("compression_ratio", "A backup archive member is compressed beyond "
                                                              "the allowed ratio.")
                if info.compress_size == 0 and info.file_size > 0:
                    raise ArchiveRefused("compression_ratio", "A backup archive member is compressed beyond "
                                                              "the allowed ratio.")
            if declared > limits.max_uncompressed_bytes:
                raise ArchiveRefused("too_large", "The backup archive is too large to restore.")
            if declared + limits.disk_margin_bytes > free_bytes(staging_dir):
                raise ArchiveRefused("disk_space", "There is not enough free space to restore this backup.")
            manifest_info = by_name.get(MANIFEST_NAME)
            if manifest_info is None:
                raise ArchiveRefused("not_a_backup", "This file is not a PRKS backup.")
            manifest = _read_manifest(archive, manifest_info)
            _check_format(manifest)
            content, users, devices, objects = _layout(manifest)
            expected = {m.name: m for m in [*content.values(), users, devices, *objects.values()]}
            if set(by_name) != set(expected) | {MANIFEST_NAME}:
                raise ArchiveRefused("layout", "The backup archive's members do not match its manifest.")
            for name, member in expected.items():
                if by_name[name].file_size != member.size:
                    raise ArchiveRefused("hash_mismatch", "A backup archive member does not match its manifest.")
                digest = hashlib.sha256()
                with archive.open(by_name[name]) as handle:
                    bounded = BoundedReader(handle, member.size)
                    while True:
                        chunk = bounded.read(IO_CHUNK_SIZE)
                        if not chunk:
                            break
                        digest.update(chunk)
                if digest.hexdigest() != member.sha256:
                    raise ArchiveRefused("hash_mismatch", "A backup archive member does not match its manifest.")
    except zipfile.BadZipFile:
        raise ArchiveRefused("not_a_backup", "This file is not a readable PRKS backup.") from None
    except (zipfile.LargeZipFile, NotImplementedError, EOFError):
        raise ArchiveRefused("not_a_backup", "This file is not a readable PRKS backup.") from None
    return Archive(path=path, manifest=manifest, tables=content, users=users, devices=devices, objects=objects)


def check_schema(conn: psycopg.Connection[Any], archive: Archive) -> None:
    """R0's database half: the archive must come from this exact schema (before 1.0)."""
    live_contract = tables.interchange_contract(conn)
    theirs = archive.manifest.get("interchange")
    if not isinstance(theirs, dict):
        raise ArchiveRefused("manifest", "The backup manifest is malformed.")
    for table in tables.BACKUP_CONTENT_TABLES:
        if theirs.get(table) != live_contract[table]:
            raise ArchiveRefused(
                "schema_mismatch",
                f"The backup was made with database schema version {archive.manifest.get('schema_version')!r}, "
                f"and its {table} columns differ from this database's. Restore it with the PRKS version "
                "that made it.")
    if archive.manifest.get("schema_fingerprint") != schema_fingerprint(conn):
        raise ArchiveRefused(
            "schema_mismatch",
            f"The backup was made with a different database schema (version "
            f"{archive.manifest.get('schema_version')!r}) than this database's. Restore it with the PRKS "
            "version that made it.")
