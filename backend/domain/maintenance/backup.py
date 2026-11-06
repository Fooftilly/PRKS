"""Library backup format 2: export one Library into a verified ``.prks-backup`` (L-B-1 #639; §11.3, §11.4).

:func:`write_backup` runs on its own connection (:mod:`.connection`):

1. **Start.** One transaction locks the Library's row ``FOR NO KEY UPDATE``,
   refuses a Library in maintenance (``MAINTENANCE``), and records a
   ``backup_runs`` row with a lease. R2 sets maintenance with an ``UPDATE`` of
   that row that is refused while a backup run is live, so a backup and a
   restore can never both pass their check.
2. **Objects lock.** ``pg_advisory_lock_shared`` on the Library's objects
   key, held until the run ends. Cleanup and the orphan sweep take that key
   exclusively with a try-lock and skip the batch, so no object the snapshot
   names can be deleted before it is copied (§11.3).
3. **Snapshot.** One ``REPEATABLE READ READ ONLY`` transaction, with the
   interchange settings pinned, reads everything the archive describes: the
   schema fingerprint and interchange contract, each content table as text
   ``COPY … TO STDOUT`` CSV of its stored columns in primary-key order, the
   attribution identities (users and devices named by content rows, never a
   login name or credential) and the list of objects the catalogue names.
   The table copies, the identities and the object list therefore agree.
   One connection reads the whole snapshot, so no exported snapshot
   (``pg_export_snapshot``) is needed to share it.
4. **Objects.** Each listed object is copied from the storage backend. A
   listed object that is missing, unreadable, or whose bytes do not match
   the catalogue's recorded size or hash fails the backup (transferred from
   the SQLite audit #963): an archive never silently omits an object it
   names.
5. **Manifest and verification.** The manifest records every member with
   its row count, size and SHA-256. The archive is then re-read and every
   member checked against it before the run is marked ``published``, with the
   archive's size and hash. Restore's staging check (R0/R1) is added to this
   verification when it exists.

Any failure marks the run ``failed`` with a reason code, removes the partial
archive and raises :class:`BackupFailed`. The archive is a ZIP with stored
(not deflated) members, like format 1.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import IO, Any, LiteralString, Optional, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from backend.domain.maintenance import tables
from backend.domain.maintenance.format import (
    BACKUP_EXTENSION,
    DEVICES_MEMBER,
    FORMAT_ID,
    FORMAT_VERSION,
    IO_CHUNK_SIZE,
    MANIFEST_NAME,
    USERS_MEMBER,
    object_references,
    schema_fingerprint,
    table_member,
)
from backend.domain.maintenance.archive import ArchiveRefused, check_schema, open_archive
from backend.domain.maintenance.connection import maintenance_connection, pin_interchange_settings
from backend.domain.maintenance.staging import drop_staging, stage
from backend.fs_durability import fsync_directory, fsync_open_file
from backend.storage.objects import (
    StorageBackend,
    StorageKey,
    StorageObjectError,
)

# A run renews its lease while it works; recovery fails a run whose lease
# expired (§11.4).
DEFAULT_LEASE_SECONDS = 300

_LIBRARY_ID_RE = re.compile(r"^LIB-[0-9A-F]{32}$")

# Phases a test can pause at (see ``hooks``).
PHASE_LOCKED = "locked"
PHASE_SNAPSHOT = "snapshot"
PHASE_OBJECTS = "objects"
PHASE_VERIFY = "verify"


class BackupFailed(Exception):
    """A backup that did not publish an archive; ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ObjectRef:
    """An object the snapshot's catalogue names, with what the catalogue recorded about it."""

    key: StorageKey
    sha256: Optional[str]
    size: Optional[int]

    @property
    def member(self) -> str:
        return f"objects/{self.key}"


@dataclass(frozen=True)
class BackupResult:
    run_id: uuid.UUID
    path: str
    size: int
    sha256: str
    manifest: dict[str, Any] = field(repr=False)


# -- run bookkeeping --------------------------------------------------------------------------

_START_SQL: LiteralString = "SELECT maintenance FROM prks.libraries WHERE id = %s FOR NO KEY UPDATE"
_INSERT_RUN_SQL: LiteralString = (
    "INSERT INTO prks.backup_runs (library_id, locked_until) "
    "VALUES (%s, now() + make_interval(secs => %s)) RETURNING id"
)
_RENEW_SQL: LiteralString = (
    "UPDATE prks.backup_runs SET locked_until = now() + make_interval(secs => %s) "
    "WHERE id = %s AND state = 'running'"
)
_PUBLISH_SQL: LiteralString = (
    "UPDATE prks.backup_runs SET state = 'published', finished_at = now(), archive_bytes = %s, "
    "archive_sha256 = %s WHERE id = %s AND state = 'running'"
)
_FAIL_SQL: LiteralString = (
    "UPDATE prks.backup_runs SET state = 'failed', finished_at = now(), error_code = %s, "
    "error_message = left(%s, 2000) WHERE id = %s AND state = 'running'"
)
_OBJECTS_LOCK_SHARED_SQL: LiteralString = (
    "SELECT pg_advisory_lock_shared(hashtextextended('prks.objects:' || %s, 0))"
)


def _start_run(conn: psycopg.Connection[Any], library_id: str, lease_seconds: int) -> uuid.UUID:
    with conn.transaction():
        row = conn.execute(_START_SQL, (library_id,)).fetchone()
        if row is None:
            raise BackupFailed("library_missing", "The Library does not exist.")
        if row[0] is not None:
            raise BackupFailed("MAINTENANCE", "The Library is under maintenance; try the backup again later.")
        run = conn.execute(_INSERT_RUN_SQL, (library_id, lease_seconds)).fetchone()
        assert run is not None
        return run[0]


class _Lease:
    """Renews the run's lease from a second connection while the snapshot transaction is open."""

    def __init__(self, conninfo: str, run_id: uuid.UUID, seconds: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._conninfo = conninfo
        self._run_id = run_id
        self._seconds = seconds
        self._clock = clock
        self._renewed = clock()

    def tick(self) -> None:
        if self._clock() - self._renewed < self._seconds / 3:
            return
        with psycopg.connect(self._conninfo, autocommit=True) as conn:
            updated = conn.execute(_RENEW_SQL, (self._seconds, self._run_id)).rowcount
        if updated != 1:
            raise BackupFailed("lease_lost", "The backup run lost its lease.")
        self._renewed = self._clock()


def _fail_run(conninfo: str, run_id: uuid.UUID, reason: str, message: str) -> None:
    """Mark the run failed on a fresh connection; best effort, recovery handles the rest."""
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute(_FAIL_SQL, (reason[:64], message, run_id))
    except psycopg.Error:
        pass


# -- snapshot ---------------------------------------------------------------------------------

_PRIMARY_KEY_SQL: LiteralString = """
SELECT a.attname
  FROM pg_index i
  JOIN LATERAL unnest(i.indkey) WITH ORDINALITY u(n, pos) ON true
  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = u.n
 WHERE i.indrelid = to_regclass(%s) AND i.indisprimary
 ORDER BY u.pos
"""



def _export_order(conn: psycopg.Connection[Any], table: str, columns: list[str]) -> list[str]:
    """The primary key, or every exported column for a table without one (such as ``roles``)."""
    rows = conn.execute(_PRIMARY_KEY_SQL, (table,)).fetchall()
    return [row[0] for row in rows] if rows else columns


def _copy_to(conn: psycopg.Connection[Any], query: sql.Composable, out: Sink) -> str:
    """Stream a text ``COPY … TO STDOUT`` CSV into ``out``; return its SHA-256."""
    digest = hashlib.sha256()
    statement = sql.SQL("COPY ({}) TO STDOUT WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')").format(query)
    with conn.cursor().copy(statement) as copy:
        for chunk in copy:
            data = bytes(chunk)
            digest.update(data)
            out.write(data)
    return digest.hexdigest()


def _table_query(conn: psycopg.Connection[Any], table: str, library_id: str) -> tuple[sql.Composable, list[str]]:
    schema, name = tables.split_name(table)
    columns = [c.name for c in tables.exported_columns(conn, table)]
    order = _export_order(conn, table, columns)
    query = sql.SQL("SELECT {} FROM {} WHERE library_id = {} ORDER BY {}").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.Identifier(schema, name),
        sql.Literal(library_id),
        sql.SQL(", ").join(sql.Identifier(c) for c in order),
    )
    return query, columns


def _identity_ids(conn: psycopg.Connection[Any], library_id: str) -> tuple[sql.Composable, sql.Composable]:
    """Subqueries of the user ids and device ids content rows of ``library_id`` name."""
    found = tables.identity_columns(conn)
    lib = sql.Literal(library_id)

    def union(parent: str) -> list[sql.Composable]:
        parts: list[sql.Composable] = []
        for fk in found[parent]:
            schema, name = tables.split_name(fk.table)
            (column,) = fk.columns
            parts.append(sql.SQL("SELECT {c} FROM {t} WHERE library_id = {lib} AND {c} IS NOT NULL").format(
                c=sql.Identifier(column), t=sql.Identifier(schema, name), lib=lib))
        return parts

    devices = union("prks.devices") or [sql.SQL("SELECT NULL::uuid WHERE false")]
    device_ids = sql.SQL(" UNION ").join(devices)
    # A device's user is an identity too, so a mapped device always has its user.
    users = union("prks.users") + [
        sql.SQL("SELECT user_id FROM prks.devices WHERE id IN ({})").format(device_ids)]
    return sql.SQL(" UNION ").join(users), device_ids


def _object_refs(conn: psycopg.Connection[Any], library_id: str) -> list[ObjectRef]:
    """One ref per key. A key may be named by several rows (Assets sharing bytes, or a
    processed upload's key becoming its Asset's); every recorded digest and size must agree,
    since the copied bytes can match only one of each."""
    hashes: dict[StorageKey, set[str]] = {}
    sizes: dict[StorageKey, set[int]] = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for row in cur.execute(object_references(), {"library_id": library_id}).fetchall():
            obj_key = StorageKey(row["namespace"], row["name"])
            key_hashes = hashes.setdefault(obj_key, set())
            key_sizes = sizes.setdefault(obj_key, set())
            if row["sha256"] is not None:
                key_hashes.add(row["sha256"])
            if row["size"] is not None:
                key_sizes.add(int(row["size"]))
            if len(key_hashes) > 1 or len(key_sizes) > 1:
                raise BackupFailed("object_changed",
                                   f"The catalogue records conflicting contents for the stored object {obj_key}.")
    refs = [ObjectRef(key, next(iter(hashes[key]), None), next(iter(sizes[key]), None)) for key in hashes]
    return sorted(refs, key=lambda ref: (ref.key.namespace, ref.key.name))


# -- archive ----------------------------------------------------------------------------------


class Sink(Protocol):
    def write(self, data: bytes, /) -> int: ...


class _HashingSink:
    """Counts and hashes what is written through it into a ZIP member."""

    def __init__(self, inner: IO[bytes], renew: Callable[[], None]) -> None:
        self._inner = inner
        self._renew = renew
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes, /) -> int:
        self._inner.write(data)
        self.digest.update(data)
        self.size += len(data)
        self._renew()  # a long COPY or object copy keeps the lease (cheap: renews every third of it)
        return len(data)


def _write_member(archive: zipfile.ZipFile, name: str, fill: Callable[[Sink], object],
                  renew: Callable[[], None] = lambda: None) -> tuple[int, str]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o100600 << 16
    with archive.open(info, "w", force_zip64=True) as member:
        out = _HashingSink(member, renew)
        fill(out)
    return out.size, out.digest.hexdigest()


def _copy_object(storage: StorageBackend, ref: ObjectRef, out: Sink) -> None:
    try:
        stat = storage.stat(ref.key)
    except (OSError, StorageObjectError):  # StorageObjectError: e.g. the path is a link or not a file
        raise BackupFailed("object_unreadable", f"The stored object {ref.key} cannot be read.") from None
    if stat is None:
        raise BackupFailed("object_missing", f"The stored object {ref.key} is missing.")
    unreadable = BackupFailed("object_unreadable", f"The stored object {ref.key} cannot be read.")
    try:
        handle = storage.open_read(ref.key)
    except Exception:
        raise unreadable from None
    try:
        while True:
            try:
                chunk = handle.read(IO_CHUNK_SIZE)
            except Exception:
                raise unreadable from None
            if not chunk:
                break
            # Outside the read's handler: a ZIP write or lease renewal failure keeps its own reason.
            out.write(chunk)
    except BaseException:
        # The failure already raised wins over any trouble closing the reader.
        with contextlib.suppress(Exception):
            handle.close()
        raise
    try:
        handle.close()
    except Exception:
        raise unreadable from None


def _verify_archive(path: str, manifest: dict[str, Any], renew: Callable[[], None] = lambda: None) -> None:
    """Re-read the archive and check every member against the manifest."""
    try:
        _check_archive(path, manifest, renew)
    except (zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
        raise BackupFailed("verify", "The written archive does not read back.") from exc


def _check_archive(path: str, manifest: dict[str, Any], renew: Callable[[], None]) -> None:
    expected: dict[str, tuple[int, str]] = {}
    for table, entry in manifest["tables"].items():
        expected[table_member(table)] = (entry["size"], entry["sha256"])
    for member, kind in ((USERS_MEMBER, "users"), (DEVICES_MEMBER, "devices")):
        entry = manifest["identities"][kind]
        expected[member] = (entry["size"], entry["sha256"])
    for text_key, entry in manifest["objects"].items():
        expected[f"objects/{text_key}"] = (entry["size"], entry["sha256"])
    with zipfile.ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist()]
        if len(names) != len(set(names)) or set(names) != set(expected) | {MANIFEST_NAME}:
            raise BackupFailed("verify", "The written archive's members do not match its manifest.")
        with archive.open(MANIFEST_NAME) as handle:
            if json.loads(handle.read()) != manifest:
                raise BackupFailed("verify", "The written archive's manifest does not read back.")
        for name, (size, sha256) in expected.items():
            digest = hashlib.sha256()
            total = 0
            with archive.open(name) as handle:
                while True:
                    chunk = handle.read(IO_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    total += len(chunk)
                    renew()
            if total != size or digest.hexdigest() != sha256:
                raise BackupFailed("verify", f"The archive member {name} does not match its manifest.")


_OWNER_SQL: LiteralString = (
    "SELECT user_id FROM prks.library_memberships WHERE library_id = %s AND role = 'owner' ORDER BY user_id LIMIT 1"
)


def _verify_by_staging(conn: psycopg.Connection[Any], path: str, run_id: uuid.UUID, library_id: str,
                       renew: Callable[[], None]) -> None:
    """Load the archive into a scratch schema exactly as restore stages it, then drop it (§11.4)."""
    try:
        archive = open_archive(path, staging_dir=None)  # stages tables only: no object space needed
        check_schema(conn, archive)
        owner = conn.execute(_OWNER_SQL, (library_id,)).fetchone()
        if owner is None:
            raise BackupFailed("verify", "The Library has no owner to map unknown identities to.")
        stage(conn, archive, run_id=run_id, target_library_id=library_id, owner_user_id=owner[0], renew=renew)
    except ArchiveRefused as exc:
        raise BackupFailed("verify", f"The written archive does not stage: {exc.message}") from exc
    finally:
        drop_staging(conn, run_id)


def _hash_file(path: str, renew: Callable[[], None] = lambda: None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(IO_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            renew()
    return size, digest.hexdigest()


# -- the run ----------------------------------------------------------------------------------


def write_backup(
    conninfo: str,
    storage: StorageBackend,
    library_id: str,
    dest_dir: str,
    *,
    prks_version: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    hooks: Optional[dict[str, Callable[[], None]]] = None,
) -> BackupResult:
    """Export ``library_id`` into a verified archive in ``dest_dir``; see the module docstring.

    ``dest_dir`` is a private directory of the data root's maintenance area;
    the archive is created there with exclusive create and named by the run.
    ``hooks`` maps a phase name to a callable run when the backup reaches it
    (tests pause there).
    """
    if not _LIBRARY_ID_RE.fullmatch(library_id):
        raise ValueError("invalid library id")
    hooks = hooks or {}
    with maintenance_connection(conninfo) as conn:
        run_id = _start_run(conn, library_id, lease_seconds)
        path = os.path.join(dest_dir, f"{run_id}{BACKUP_EXTENSION}")
        try:
            result = _run(conn, conninfo, storage, library_id, run_id, path,
                          prks_version=prks_version, lease_seconds=lease_seconds, hooks=hooks)
        except BaseException as exc:
            if isinstance(exc, BackupFailed):
                reason, message = exc.reason, exc.message
            elif isinstance(exc, psycopg.Error):
                reason, message = "database", "The backup failed in the database."
            elif isinstance(exc, (OSError, zipfile.BadZipFile, zipfile.LargeZipFile)):
                reason, message = "io", "The backup archive could not be written."
            else:
                reason, message = "internal", "The backup failed."
            _fail_run(conninfo, run_id, reason, message)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            if isinstance(exc, (BackupFailed, KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, psycopg.Error):
                raise BackupFailed("database", "The backup failed in the database.") from exc
            if reason == "io":
                raise BackupFailed(reason, message) from exc
            raise
        return result


def _run(conn: psycopg.Connection[Any], conninfo: str, storage: StorageBackend, library_id: str,
         run_id: uuid.UUID, path: str, *, prks_version: str, lease_seconds: int,
         hooks: dict[str, Callable[[], None]]) -> BackupResult:
    def reach(phase: str) -> None:
        hook = hooks.get(phase)
        if hook is not None:
            hook()

    lease = _Lease(conninfo, run_id, lease_seconds)
    conn.execute(_OBJECTS_LOCK_SHARED_SQL, (library_id,))
    reach(PHASE_LOCKED)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw:
        manifest = _write_archive(conn, storage, library_id, raw, prks_version=prks_version, lease=lease,
                                  reach=reach)
        # Only now is the ZIP complete: closing it wrote the central directory.
        raw.flush()
        fsync_open_file(raw.fileno())
    if not fsync_directory(os.path.dirname(path)):
        raise BackupFailed("io", "The backup archive could not be made durable.")
    reach(PHASE_VERIFY)
    _verify_archive(path, manifest, lease.tick)
    _verify_by_staging(conn, path, run_id, library_id, lease.tick)
    size, sha256 = _hash_file(path, lease.tick)
    with conn.transaction():
        if conn.execute(_PUBLISH_SQL, (size, sha256, run_id)).rowcount != 1:
            raise BackupFailed("lease_lost", "The backup run lost its lease.")
    return BackupResult(run_id=run_id, path=path, size=size, sha256=sha256, manifest=manifest)


def _write_archive(conn: psycopg.Connection[Any], storage: StorageBackend, library_id: str, raw: IO[bytes], *,
                   prks_version: str, lease: _Lease, reach: Callable[[str], None]) -> dict[str, Any]:
    """Write every member and the manifest, and close the ZIP (its central directory) into ``raw``."""
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        table_entries: dict[str, dict[str, Any]] = {}
        identity_entries: dict[str, dict[str, Any]] = {}
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            pin_interchange_settings(conn)
            reach(PHASE_SNAPSHOT)
            instance = conn.execute("SELECT id FROM prks.instance").fetchone()
            version = conn.execute("SELECT max(version) FROM prks.schema_migrations").fetchone()
            created_at = conn.execute("SELECT now()").fetchone()
            fingerprint = schema_fingerprint(conn)
            contract = tables.interchange_contract(conn)
            for table in tables.BACKUP_CONTENT_TABLES:
                query, _columns = _table_query(conn, table, library_id)
                count_row = conn.execute(
                    sql.SQL("SELECT count(*) FROM ({}) q").format(query)).fetchone()
                assert count_row is not None
                size, sha256 = _write_member(
                    archive, table_member(table), lambda out, q=query: _copy_to(conn, q, out), lease.tick)
                table_entries[table] = {"rows": int(count_row[0]), "size": size, "sha256": sha256}
                lease.tick()
            user_ids, device_ids = _identity_ids(conn, library_id)
            users = sql.SQL(
                "SELECT id AS archive_user_id, display_name FROM prks.users WHERE id IN ({}) ORDER BY id"
            ).format(user_ids)
            devices = sql.SQL(
                "SELECT id AS device_id, user_id AS archive_user_id, label, first_seen "
                "FROM prks.devices WHERE id IN ({}) ORDER BY id"
            ).format(device_ids)
            for member, kind, query in ((USERS_MEMBER, "users", users), (DEVICES_MEMBER, "devices", devices)):
                count_row = conn.execute(sql.SQL("SELECT count(*) FROM ({}) q").format(query)).fetchone()
                assert count_row is not None
                size, sha256 = _write_member(archive, member, lambda out, q=query: _copy_to(conn, q, out),
                                             lease.tick)
                identity_entries[kind] = {"rows": int(count_row[0]), "size": size, "sha256": sha256}
            refs = _object_refs(conn, library_id)
        reach(PHASE_OBJECTS)
        object_entries: dict[str, dict[str, Any]] = {}
        for ref in refs:
            size, sha256 = _write_member(archive, ref.member, lambda out, r=ref: _copy_object(storage, r, out),
                                         lease.tick)
            if (ref.size is not None and size != ref.size) or (ref.sha256 is not None and sha256 != ref.sha256):
                raise BackupFailed("object_changed",
                                   f"The stored object {ref.key} does not match its catalogue entry.")
            object_entries[str(ref.key)] = {"size": size, "sha256": sha256}
            lease.tick()
        assert version is not None and created_at is not None
        manifest: dict[str, Any] = {
            "format": FORMAT_ID,
            "format_version": FORMAT_VERSION,
            "created_at": _iso(created_at[0]),
            "prks_version": prks_version,
            "schema_version": int(version[0]),
            "schema_fingerprint": fingerprint,
            "library_id": library_id,
            # None only before bootstrap (a test database); restore never needs it.
            "instance_id": None if instance is None else instance[0],
            "interchange": contract,
            "identities": identity_entries,
            "tables": table_entries,
            "objects": object_entries,
        }
        payload = json.dumps(manifest, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8")
        _write_member(archive, MANIFEST_NAME, lambda out: out.write(payload))
    return manifest


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def members(manifest: dict[str, Any]) -> Iterable[str]:
    """Every member name a manifest describes, the manifest included."""
    yield MANIFEST_NAME
    yield USERS_MEMBER
    yield DEVICES_MEMBER
    for table in manifest["tables"]:
        yield table_member(table)
    for text_key in manifest["objects"]:
        yield f"objects/{text_key}"
