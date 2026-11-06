"""Restore a format-2 archive over a Library: R0 to R5 and recovery (L-B-1 #639; §11.4).

:func:`restore_library` runs every phase on one dedicated maintenance
connection (:func:`connection.maintenance_connection`; never pooled):

- **Setup.** Under the Library row lock: the Library exists, the restoring
  user owns it and it is not in maintenance; a ``restore_runs`` row with a
  lease is inserted. A second autocommit connection renews the lease (and
  records the phase) while the main connection sits in R1's long
  transaction; a lease that has already expired is never renewed, so a run
  that stalled past it is fenced (``lease_lost``).
- **R0 / R1.** :func:`archive.open_archive`, :func:`archive.check_schema` and
  :func:`staging.stage` (objects included). Nothing is visible to others.
- **R2 Enter.** One transaction sets ``maintenance = 'restore'`` and
  ``maintenance_run`` only if no maintenance is active, no backup run holds a
  live lease and this run still holds its own.
- **R3 Drain.** One short transaction with ``lock_timeout`` takes the
  Library's maintenance lock and then its objects lock, both exclusive and
  session level; the run must still own the flag; every staged key is
  re-checked for existence and size and a missing one is rewritten from the
  archive.
- **R4 Commit.** One SERIALIZABLE transaction (bounded retry on ``40001`` and
  ``40P01``): ownership and the live lease under ``FOR UPDATE``; the derived
  and content rows of the Library are replaced by the staged ones (devices
  marked new first); every object the old catalogue named and the new one
  does not gets a ``hold`` claim; the Library's jobs are cancelled; the epoch
  advances and the flag clears; ``derived.rebuild``, a cleanup pass and the
  orphan sweep are enqueued; the run is marked committed.

  Cleanup claims: R4 replaces the Library's ordinary claims with the
  archive's, but **keeps every existing ``hold`` claim**, merging by key
  (``hold`` wins). A hold is released only by an explicit discard decision,
  so restoring an older archive a second time never frees the objects an
  earlier restore set aside.
- **R5 Release.** On an abort after R2, one owner-fenced transaction clears
  the flag (epoch unchanged) and fails the run; before R2 the run is simply
  failed. Then the staging schema is dropped, advisory locks released and
  the connection closed, on every path.

:func:`recover` is the startup and maintenance-reaper half (§11.4, "Crash
recovery"): it fails runs whose lease expired, clearing a restore's flag
only under ``pg_try_advisory_xact_lock`` on the Library key (a restore that
reached R3 holds that lock, so it is left alone), and drops every
``restore_<id>`` schema whose run is no longer live.

The archive is read by path in R1 and again in R3, so the caller passes a
private copy in the maintenance area, never a path another user can swap.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

import psycopg
from psycopg import errors, sql

from backend.db.database import Isolation, Transaction
from backend.domain.maintenance import tables
from backend.domain.maintenance.archive import Archive, ArchiveRefused, Limits, Member, open_archive, check_schema
from backend.domain.maintenance.connection import maintenance_connection
from backend.domain.maintenance.format import IO_CHUNK_SIZE
from backend.domain.maintenance.staging import STAGING_PREFIX, drop_staging, stage
from backend.domain.objects import jobs as object_jobs
from backend.domain.search import rebuild
from backend.storage.objects import ObjectExists, StorageBackend, StorageKey

DEFAULT_LEASE_SECONDS = 300
DEFAULT_DRAIN_TIMEOUT_SECONDS = 30.0
SERIALIZATION_RETRIES = 3
SUPERSEDED = "restore_superseded"

PHASE_VALIDATE = "validate"
PHASE_STAGE = "stage"
PHASE_ENTER = "enter"
PHASE_DRAIN = "drain"
PHASE_COMMIT = "commit"
PHASE_RELEASE = "release"

# Hook names (tests pause or fail there).
HOOK_STAGED = "staged"          # after R1, before R2
HOOK_ENTERED = "entered"        # after R2, before R3
HOOK_DRAINED = "drained"        # after R3, before R4
HOOK_COMMITTING = "committing"  # inside R4, before the run is marked committed

_RETRYABLE = (errors.SerializationFailure, errors.DeadlockDetected)


class RestoreFailed(Exception):
    """A restore that did not commit; ``reason`` is a short code, ``message`` safe to show the owner."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class RestoreResult:
    run_id: uuid.UUID
    library_id: str
    epoch: int
    new_devices: int
    objects_written: int
    objects_reused: int
    objects_held: int


# -- run bookkeeping --------------------------------------------------------------------------

_START_SQL: LiteralString = """
SELECT l.maintenance,
       EXISTS (SELECT 1 FROM prks.library_memberships m
                WHERE m.library_id = l.id AND m.user_id = %(user_id)s AND m.role = 'owner') AS is_owner
  FROM prks.libraries l WHERE l.id = %(library_id)s FOR NO KEY UPDATE
"""
_INSERT_RUN_SQL: LiteralString = (
    "INSERT INTO prks.restore_runs (library_id, locked_until) "
    "VALUES (%s, now() + make_interval(secs => %s)) RETURNING id"
)
_RENEW_SQL: LiteralString = (
    "UPDATE prks.restore_runs SET locked_until = now() + make_interval(secs => %s), phase = %s "
    "WHERE id = %s AND state = 'running' AND locked_until > now()"
)
_FAIL_RUN_SQL: LiteralString = (
    "UPDATE prks.restore_runs SET state = 'failed', finished_at = now(), error_code = %s, "
    "error_message = left(%s, 2000) WHERE id = %s AND state = 'running'"
)


def _start_run(conn: psycopg.Connection[Any], library_id: str, owner_user_id: str,
               lease_seconds: int) -> uuid.UUID:
    with conn.transaction():
        row = conn.execute(_START_SQL, {"library_id": library_id, "user_id": owner_user_id}).fetchone()
        if row is None:
            raise RestoreFailed("library_missing", "The Library does not exist.")
        if not row[1]:
            raise RestoreFailed("forbidden", "Only an owner of the Library can restore it.")
        if row[0] is not None:
            raise RestoreFailed("MAINTENANCE", "The Library is under maintenance; try the restore again later.")
        run = conn.execute(_INSERT_RUN_SQL, (library_id, lease_seconds)).fetchone()
        assert run is not None
        return run[0]


class _Lease:
    """Renews the run's lease (and records its phase) on a second autocommit connection."""

    def __init__(self, conn: psycopg.Connection[Any], run_id: uuid.UUID, seconds: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._conn = conn
        self._run_id = run_id
        self._seconds = seconds
        self._clock = clock
        self._renewed = clock()
        self.phase = PHASE_VALIDATE

    def enter(self, phase: str) -> None:
        self.phase = phase
        self._renew()

    def tick(self) -> None:
        if self._clock() - self._renewed >= self._seconds / 3:
            self._renew()

    def _renew(self) -> None:
        if self._conn.execute(_RENEW_SQL, (self._seconds, self.phase, self._run_id)).rowcount != 1:
            raise RestoreFailed("lease_lost", "The restore run lost its lease.")
        self._renewed = self._clock()


# -- R2 ---------------------------------------------------------------------------------------

_ENTER_SQL: LiteralString = """
UPDATE prks.libraries SET maintenance = 'restore', maintenance_run = %(run)s
 WHERE id = %(library_id)s AND maintenance IS NULL
   AND NOT EXISTS (SELECT 1 FROM prks.backup_runs b
                    WHERE b.library_id = %(library_id)s AND b.state = 'running' AND b.locked_until > now())
   AND EXISTS (SELECT 1 FROM prks.restore_runs r
                WHERE r.id = %(run)s AND r.state = 'running' AND r.locked_until > now())
"""
_ENTER_REFUSAL_SQL: LiteralString = """
SELECT l.maintenance IS NOT NULL AS in_maintenance,
       EXISTS (SELECT 1 FROM prks.backup_runs b
                WHERE b.library_id = l.id AND b.state = 'running' AND b.locked_until > now()) AS backing_up
  FROM prks.libraries l WHERE l.id = %(library_id)s
"""


def _enter(conn: psycopg.Connection[Any], library_id: str, run_id: uuid.UUID) -> None:
    params = {"run": run_id, "library_id": library_id}
    with conn.transaction():
        if conn.execute(_ENTER_SQL, params).rowcount == 1:
            return
        row = conn.execute(_ENTER_REFUSAL_SQL, params).fetchone()
    if row is None:
        raise RestoreFailed("library_missing", "The Library does not exist.")
    if row[0]:
        raise RestoreFailed("MAINTENANCE", "The Library is under maintenance; try the restore again later.")
    if row[1]:
        raise RestoreFailed("backup_running", "A backup of the Library is running; try the restore again later.")
    raise RestoreFailed("lease_lost", "The restore run lost its lease.")


# -- R3 ---------------------------------------------------------------------------------------

_LIBRARY_LOCK_SQL: LiteralString = "SELECT pg_advisory_lock(hashtextextended('prks.library:' || %s, 0))"
_OBJECTS_LOCK_SQL: LiteralString = "SELECT pg_advisory_lock(hashtextextended('prks.objects:' || %s, 0))"
_OWNS_FLAG_SQL: LiteralString = (
    "SELECT 1 FROM prks.libraries WHERE id = %s AND maintenance = 'restore' AND maintenance_run = %s"
)
_STAGED_KEYS_SQL: LiteralString = (
    "SELECT namespace, name FROM prks.restore_run_objects WHERE run_id = %s ORDER BY namespace, name"
)


def _drain(conn: psycopg.Connection[Any], library_id: str, run_id: uuid.UUID, timeout_seconds: float) -> None:
    try:
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(
                sql.Literal(f"{max(1, int(timeout_seconds * 1000))}ms")))
            conn.execute(_LIBRARY_LOCK_SQL, (library_id,))
            conn.execute(_OBJECTS_LOCK_SQL, (library_id,))
    except errors.LockNotAvailable:
        raise RestoreFailed("drain_timeout", "Work on the Library did not finish in time; "
                                             "try the restore again later.") from None
    if conn.execute(_OWNS_FLAG_SQL, (library_id, run_id)).fetchone() is None:
        raise RestoreFailed("lease_lost", "The restore run lost its lease.")


def _recheck_objects(conn: psycopg.Connection[Any], archive: Archive, storage: StorageBackend,
                     run_id: uuid.UUID, renew: Callable[[], None]) -> None:
    """Every staged key exists with the archive's size; a missing one is written again (§11.4 R3)."""
    for namespace, name in conn.execute(_STAGED_KEYS_SQL, (run_id,)).fetchall():
        obj_key = StorageKey(namespace, name)
        member = archive.objects.get(obj_key)
        if member is None:
            raise RestoreFailed("internal", "A staged object is not in the backup.")
        info = storage.stat(obj_key)
        if info is None:
            _rewrite(archive, storage, obj_key, member, renew)
        elif info.size != member.size:
            raise RestoreFailed("object_conflict", "A stored object of the backup changed during the restore.")
        renew()


def _rewrite(archive: Archive, storage: StorageBackend, obj_key: StorageKey, member: Member,
             renew: Callable[[], None]) -> None:
    def write(stream: Any) -> None:
        with archive.open_member(member) as handle:
            while True:
                chunk = handle.read(IO_CHUNK_SIZE)
                if not chunk:
                    break
                stream.write(chunk)
                renew()

    try:
        info = storage.put_new(obj_key, write, hash=True)
    except ObjectExists:
        found = storage.stat(obj_key)
        if found is None or found.size != member.size:
            raise RestoreFailed("object_conflict",
                                "A stored object of the backup changed during the restore.") from None
        return
    if info.sha256 != member.sha256 or info.size != member.size:
        raise RestoreFailed("object_conflict", "A backup object did not write back intact.")


# -- R4 ---------------------------------------------------------------------------------------

_LOCK_FOR_COMMIT_SQL: LiteralString = """
SELECT l.epoch
  FROM prks.libraries l
 WHERE l.id = %(library_id)s AND l.maintenance = 'restore' AND l.maintenance_run = %(run)s
   FOR UPDATE
"""
_LOCK_RUN_SQL: LiteralString = """
SELECT 1 FROM prks.restore_runs
 WHERE id = %(run)s AND state = 'running' AND locked_until > now() FOR UPDATE
"""
# The Library's object keys, by the catalogue columns the reference check reads.
_KEYS_SQL: LiteralString = """
SELECT 'asset-objects' AS namespace, storage_locator AS name FROM {s}.assets
 WHERE library_id = {lib} AND storage_locator IS NOT NULL
UNION
SELECT 'asset-objects', source_locator FROM {s}.assets WHERE library_id = {lib} AND source_locator IS NOT NULL
UNION
SELECT 'asset-objects', object_locator FROM {s}.processing_items
 WHERE library_id = {lib} AND object_locator IS NOT NULL
UNION
SELECT 'portraits', portrait_locator FROM {s}.persons WHERE library_id = {lib} AND portrait_locator IS NOT NULL
"""
_CLAIMS_TABLE = "prks.object_cleanup_claims"
_CANCEL_JOBS_SQL: LiteralString = """
UPDATE prks.jobs SET state = 'cancelled', lease_id = NULL, locked_by = NULL, locked_until = NULL,
       finished_at = now(), last_error = %(error)s
 WHERE library_id = %(library_id)s AND state IN ('queued', 'running')
"""
_ADVANCE_SQL: LiteralString = """
UPDATE prks.libraries SET epoch = epoch + 1, maintenance = NULL, maintenance_run = NULL, search_folding = NULL
 WHERE id = %(library_id)s RETURNING epoch
"""
_COMMIT_RUN_SQL: LiteralString = (
    "UPDATE prks.restore_runs SET state = 'committed', phase = 'commit', finished_at = now() "
    "WHERE id = %s AND state = 'running'"
)
_FORGET_STAGED_SQL: LiteralString = "DELETE FROM prks.restore_run_objects WHERE run_id = %s"


def _columns(conn: psycopg.Connection[Any], table: str) -> sql.Composable:
    return sql.SQL(", ").join(sql.Identifier(c.name) for c in tables.exported_columns(conn, table))


def _replace_rows(conn: psycopg.Connection[Any], schema: str, library_id: str) -> None:
    lib = sql.Literal(library_id)
    for table in sorted(tables.BACKUP_DERIVED_TABLES):
        conn.execute(sql.SQL("DELETE FROM {} WHERE library_id = {}").format(
            sql.Identifier(*tables.split_name(table)), lib))
    for table in reversed(tables.BACKUP_CONTENT_TABLES):
        target = sql.Identifier(*tables.split_name(table))
        if table == _CLAIMS_TABLE:
            # Holds outlive every restore (module docstring).
            conn.execute(sql.SQL("DELETE FROM {} WHERE library_id = {} AND NOT hold").format(target, lib))
        else:
            conn.execute(sql.SQL("DELETE FROM {} WHERE library_id = {}").format(target, lib))
    conn.execute(sql.SQL(
        "INSERT INTO prks.devices (id, user_id, label, first_seen, last_seen) "
        "SELECT device_id, user_id, label, first_seen, first_seen FROM {} WHERE is_new").format(
            sql.Identifier(schema, "_map_devices")))
    for table in tables.BACKUP_CONTENT_TABLES:
        _, name = tables.split_name(table)
        columns = _columns(conn, table)
        staged = sql.Identifier(schema, name)
        if table == _CLAIMS_TABLE:
            conn.execute(sql.SQL(
                "INSERT INTO prks.object_cleanup_claims ({c}) SELECT {c} FROM {s} "
                "ON CONFLICT (namespace, name) DO UPDATE SET "
                "hold = prks.object_cleanup_claims.hold OR EXCLUDED.hold").format(c=columns, s=staged))
        else:
            conn.execute(sql.SQL("INSERT INTO {t} ({c}) SELECT {c} FROM {s}").format(
                t=sql.Identifier(*tables.split_name(table)), c=columns, s=staged))


def _hold_replaced(conn: psycopg.Connection[Any], library_id: str) -> int:
    """A ``hold`` claim for every key the old catalogue named and the restored one does not."""
    new_keys = sql.SQL(_KEYS_SQL).format(s=sql.Identifier("prks"), lib=sql.Literal(library_id))
    cursor = conn.execute(sql.SQL(
        "INSERT INTO prks.object_cleanup_claims (namespace, name, library_id, hold) "
        "SELECT o.namespace, o.name, {lib}, true FROM _restore_old_keys o "
        "WHERE (o.namespace, o.name) NOT IN (SELECT namespace, name FROM ({new}) n) "
        "ON CONFLICT (namespace, name) DO UPDATE SET hold = true, library_id = EXCLUDED.library_id").format(
            lib=sql.Literal(library_id), new=new_keys))
    return cursor.rowcount


def _commit(conn: psycopg.Connection[Any], schema: str, library_id: str, run_id: uuid.UUID,
            hook: Callable[[], None]) -> tuple[int, int]:
    params = {"library_id": library_id, "run": run_id}
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        row = conn.execute(_LOCK_FOR_COMMIT_SQL, params).fetchone()
        if row is None or conn.execute(_LOCK_RUN_SQL, params).fetchone() is None:
            raise RestoreFailed("lease_lost", "The restore run lost its lease.")
        old_keys = sql.SQL(_KEYS_SQL).format(s=sql.Identifier("prks"), lib=sql.Literal(library_id))
        conn.execute(sql.SQL("CREATE TEMP TABLE _restore_old_keys ON COMMIT DROP AS {}").format(old_keys))
        _replace_rows(conn, schema, library_id)
        held = _hold_replaced(conn, library_id)
        conn.execute(_CANCEL_JOBS_SQL, {"library_id": library_id, "error": SUPERSEDED})
        advanced = conn.execute(_ADVANCE_SQL, params).fetchone()
        assert advanced is not None
        epoch = int(advanced[0])
        tx = Transaction(conn, isolation=Isolation.SERIALIZABLE, read_only=False, attempt=1)
        try:
            rebuild.enqueue_for_host(tx, library_id, epoch)
            object_jobs.enqueue_cleanup(tx)
            object_jobs.enqueue_sweep(tx)
        finally:
            tx.close()
        conn.execute(_FORGET_STAGED_SQL, (run_id,))
        hook()
        conn.execute(_COMMIT_RUN_SQL, (run_id,))
    return epoch, held


def _commit_with_retry(conn: psycopg.Connection[Any], schema: str, library_id: str, run_id: uuid.UUID,
                       hook: Callable[[], None], renew: Callable[[], None]) -> tuple[int, int]:
    for attempt in range(1, SERIALIZATION_RETRIES + 1):
        try:
            return _commit(conn, schema, library_id, run_id, hook)
        except _RETRYABLE:
            if attempt == SERIALIZATION_RETRIES:
                raise
            renew()
    raise AssertionError("unreachable")


# -- R5 ---------------------------------------------------------------------------------------

_FINALIZE_LOCK_SQL: LiteralString = """
SELECT maintenance = 'restore' AND maintenance_run IS NOT DISTINCT FROM %(run)s AS owned
  FROM prks.libraries WHERE id = %(library_id)s FOR UPDATE
"""
_CLEAR_FLAG_SQL: LiteralString = (
    "UPDATE prks.libraries SET maintenance = NULL, maintenance_run = NULL WHERE id = %(library_id)s"
)


def _finalize_abort(conn: psycopg.Connection[Any], library_id: str, run_id: uuid.UUID, entered: bool,
                    reason: str, message: str) -> None:
    """Abort finalization: clear the flag only if this run owns it, and fail the run (§11.4)."""
    with conn.transaction():
        if entered:
            row = conn.execute(_FINALIZE_LOCK_SQL, {"library_id": library_id, "run": run_id}).fetchone()
            if row is not None and row[0]:
                conn.execute(_CLEAR_FLAG_SQL, {"library_id": library_id})
        conn.execute(_FAIL_RUN_SQL, (reason[:64], message, run_id))


def _failure(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, RestoreFailed):
        return exc.reason, exc.message
    if isinstance(exc, ArchiveRefused):
        return exc.reason, exc.message
    if isinstance(exc, psycopg.Error):
        code = exc.sqlstate or "database"
        return code, f"The restore failed in the database ({code}); the Library was not changed."
    return "internal", "The restore failed; the Library was not changed."


# -- the run ----------------------------------------------------------------------------------


def restore_library(
    conninfo: str,
    storage: StorageBackend,
    archive_path: str,
    library_id: str,
    *,
    owner_user_id: str,
    staging_dir: str,
    limits: Limits = Limits(),
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
    hooks: Optional[dict[str, Callable[[], None]]] = None,
) -> RestoreResult:
    """Restore ``archive_path`` over ``library_id``; see the module docstring.

    Raises :class:`RestoreFailed` (the run is marked failed with the same
    reason) unless R4 committed. ``hooks`` maps a ``HOOK_*`` name to a
    callable run when the restore reaches it.
    """
    hooks = hooks or {}

    def reach(name: str) -> None:
        hook = hooks.get(name)
        if hook is not None:
            hook()

    with maintenance_connection(conninfo) as conn, _side_connection(conninfo) as side:
        run_id = _start_run(conn, library_id, owner_user_id, lease_seconds)
        lease = _Lease(side, run_id, lease_seconds)
        entered = False
        archive: Optional[Archive] = None
        try:
            archive = open_archive(archive_path, staging_dir=staging_dir, limits=limits, renew=lease.tick)
            check_schema(conn, archive)
            lease.enter(PHASE_STAGE)
            staged = stage(conn, archive, run_id=run_id, target_library_id=library_id,
                           owner_user_id=owner_user_id, storage=storage, renew=lease.tick)
            reach(HOOK_STAGED)
            lease.enter(PHASE_ENTER)
            _enter(conn, library_id, run_id)
            entered = True
            reach(HOOK_ENTERED)
            lease.enter(PHASE_DRAIN)
            _drain(conn, library_id, run_id, drain_timeout_seconds)
            _recheck_objects(conn, archive, storage, run_id, lease.tick)
            reach(HOOK_DRAINED)
            lease.enter(PHASE_COMMIT)
            epoch, held = _commit_with_retry(conn, staged.schema, library_id, run_id,
                                             lambda: reach(HOOK_COMMITTING), lease.tick)
        except BaseException as exc:
            reason, message = _failure(exc)
            try:
                _finalize_abort(conn, library_id, run_id, entered, reason, message)
            except psycopg.Error:
                pass  # The flag (if set) stays until the lease expires; recover() clears it.
            _release(conn, run_id)
            if isinstance(exc, (RestoreFailed, KeyboardInterrupt, SystemExit)):
                raise
            raise RestoreFailed(reason, message) from exc
        finally:
            if archive is not None:
                archive.close()  # the validated ZipFile R0 kept open
        _release(conn, run_id)
        return RestoreResult(run_id=run_id, library_id=library_id, epoch=epoch,
                             new_devices=staged.new_devices, objects_written=staged.objects_written,
                             objects_reused=staged.objects_reused, objects_held=held)


@contextmanager
def _side_connection(conninfo: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(conninfo, autocommit=True, application_name="prks-maintenance-lease") as conn:
        yield conn


def _release(conn: psycopg.Connection[Any], run_id: uuid.UUID) -> None:
    """R5: drop the staging schema and release the advisory locks; best effort."""
    try:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        drop_staging(conn, run_id)
        conn.execute("SELECT pg_advisory_unlock_all()")
    except psycopg.Error:
        pass  # Closing the connection releases the locks; recover() drops the schema.


# -- recovery ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recovered:
    restores_failed: int
    flags_cleared: int
    backups_failed: int
    schemas_dropped: int


_EXPIRED_RESTORES_SQL: LiteralString = (
    "SELECT id, library_id FROM prks.restore_runs WHERE state = 'running' AND locked_until <= now() ORDER BY id"
)
_TRY_LIBRARY_LOCK_SQL: LiteralString = (
    "SELECT pg_try_advisory_xact_lock(hashtextextended('prks.library:' || %s, 0))"
)
_EXPIRE_RESTORE_SQL: LiteralString = (
    "UPDATE prks.restore_runs SET state = 'failed', finished_at = now(), error_code = 'lease_expired', "
    "error_message = 'The restore stopped before it finished.' "
    "WHERE id = %s AND state = 'running' AND locked_until <= now()"
)
_EXPIRE_BACKUPS_SQL: LiteralString = (
    "UPDATE prks.backup_runs SET state = 'failed', finished_at = now(), error_code = 'lease_expired', "
    "error_message = 'The backup stopped before it finished.' "
    "WHERE state = 'running' AND locked_until <= now()"
)
_STAGING_SCHEMAS_SQL: LiteralString = (
    "SELECT nspname FROM pg_namespace WHERE nspname ~ '^restore_[0-9a-f]{32}$' ORDER BY nspname"
)
_LIVE_RUN_SQL: LiteralString = """
SELECT EXISTS (SELECT 1 FROM prks.restore_runs WHERE id = %(run)s AND state = 'running')
    OR EXISTS (SELECT 1 FROM prks.backup_runs WHERE id = %(run)s AND state = 'running')
"""


def _expire_restore(conn: psycopg.Connection[Any], run_id: uuid.UUID, library_id: str) -> tuple[bool, bool]:
    """(failed, cleared): fail one expired run, clearing its flag only under the Library's lock."""
    with conn.transaction():
        locked = conn.execute(_TRY_LIBRARY_LOCK_SQL, (library_id,)).fetchone()
        if locked is None or not locked[0]:
            return False, False  # A restore past R3 still holds it: leave the run alone.
        row = conn.execute(_FINALIZE_LOCK_SQL, {"library_id": library_id, "run": run_id}).fetchone()
        if conn.execute(_EXPIRE_RESTORE_SQL, (run_id,)).rowcount != 1:
            return False, False
        cleared = row is not None and bool(row[0])
        if cleared:
            conn.execute(_CLEAR_FLAG_SQL, {"library_id": library_id})
        return True, cleared


def recover(conn: psycopg.Connection[Any]) -> Recovered:
    """Startup and maintenance-reaper recovery on an autocommit connection; idempotent."""
    failed = cleared = 0
    for run_id, library_id in conn.execute(_EXPIRED_RESTORES_SQL).fetchall():
        did_fail, did_clear = _expire_restore(conn, run_id, library_id)
        failed += did_fail
        cleared += did_clear
    with conn.transaction():
        backups = conn.execute(_EXPIRE_BACKUPS_SQL).rowcount
    dropped = 0
    for (schema,) in conn.execute(_STAGING_SCHEMAS_SQL).fetchall():
        run_id = uuid.UUID(hex=schema[len(STAGING_PREFIX):])
        live = conn.execute(_LIVE_RUN_SQL, {"run": run_id}).fetchone()
        if live is not None and live[0]:
            continue
        drop_staging(conn, run_id)
        dropped += 1
    return Recovered(restores_failed=failed, flags_cleared=cleared, backups_failed=backups,
                     schemas_dropped=dropped)


__all__ = [
    "RestoreFailed", "RestoreResult", "Recovered", "restore_library", "recover",
    "DEFAULT_LEASE_SECONDS", "DEFAULT_DRAIN_TIMEOUT_SECONDS",
]
