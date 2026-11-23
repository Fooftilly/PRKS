"""``prks_app.py --testing --reset-db``: empty the testing instance, then start on a new one.

A testing database whose applied schema file has since changed cannot start
(``backend.db.migrate``: before v1.0.0 the instance is recreated, never
upgraded). This command empties the testing instance this ``--testing`` run
would use, after the person types :data:`CONFIRM_WORD`, and the normal start
then applies the schema to the empty database and sets it up as on a first
start, on the empty data root.

It only ever touches one testing instance, the database and the data root
paired with it:

- the database must pass :func:`backend.db.testing_guard.prove_testing_connection`
  (marked for testing; any instance row says ``testing``). The proof runs
  twice: once to plan, and again in the transaction that drops the schemas,
  which also refuses when the instance or the schema list changed since the
  person confirmed them;
- the data root must pass the ``--testing`` path check (never ``/data`` or the
  repository ``data/``) and be missing, empty, or a root whose marker names
  this database's instance (both ``instance_id`` and ``storage_root_id``). A
  root without a marker, one paired with another instance, and one that holds
  a pre-0.8 SQLite catalogue are refused, even the default ``data_testing/``;
- under the root's exclusive lease and before the database is touched, the
  tree must hold no mount point, link or directory on another device
  (``root_binding.check_no_foreign_tree``), and the delete re-checks each
  directory before descending into it;
- the confirmation is read from a terminal only, so a script or a pipe can
  never answer it.

The database itself is kept (with its testing mark, owner and locale); every
schema in it other than ``public`` and the system schemas is dropped in one
transaction (the prompt lists them), and the database must then be as empty
as the schema runner requires. The data root keeps only its lock files. A
running PRKS process on the root holds ``root.lock`` and makes the reset
refuse before it deletes anything; one that holds database locks makes the
drop time out and roll back.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional, TextIO

import psycopg
from psycopg import sql

from backend.db.migrate import MIGRATION_LOCK_KEY, is_empty_database
from backend.db.testing_guard import TestingDatabaseRefused, prove_testing_connection
from backend.storage import paths
from backend.storage.errors import StorageRootError
from backend.storage.file_lock import ExclusiveFileLock, LockBusy, LockUnavailable
from backend.storage.root_binding import check_no_foreign_tree
from backend.storage.root_marker import (
    CLAIM_LOCK_NAME,
    MAINTENANCE_DIRNAME,
    OPEN_LOCK_NAME,
    ROOT_LOCK_NAME,
    lease_path,
    read_marker,
)

CONFIRM_WORD = "RESET"

_APPLICATION_NAME = "prks-testing-reset"
_LOCK_TIMEOUT = "5s"
_LOCK_FILES = frozenset({ROOT_LOCK_NAME, CLAIM_LOCK_NAME, OPEN_LOCK_NAME})
_SCHEMAS_SQL = (
    "SELECT nspname FROM pg_catalog.pg_namespace "
    "WHERE nspname <> ALL(ARRAY['public', 'information_schema']) AND nspname !~ '^pg_' "
    "ORDER BY nspname"
)


class ResetRefused(Exception):
    """The reset did not run, or stopped; ``message`` says why and what to do."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Instance:
    id: str
    storage_root_id: str


@dataclass(frozen=True)
class DatabaseState:
    """What the database holds, read in one transaction."""

    instance: Optional[Instance]
    schemas: tuple[str, ...]


@dataclass(frozen=True)
class ResetPlan:
    """What a confirmed reset deletes."""

    conninfo: str
    database_label: str
    database: DatabaseState
    root: str
    root_real: str


def database_label(conninfo: str) -> str:
    """``<dbname> on <host>:<port>``, never the user or password."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        parts = conninfo_to_dict(conninfo)
    except psycopg.Error:
        return "the configured database"
    name = str(parts.get("dbname") or "the configured database")
    host = str(parts.get("host") or "the local socket")
    port = parts.get("port")
    return f"{name} on {host}:{port}" if port else f"{name} on {host}"


def _read_state(conn: psycopg.Connection[Any]) -> DatabaseState:
    """Prove ``conn`` is a testing database and read its instance and schemas."""
    try:
        prove_testing_connection(conn)
    except TestingDatabaseRefused as exc:
        raise ResetRefused(exc.reason, f"{exc.message} Nothing was deleted.") from None
    instance = None
    row = conn.execute("SELECT to_regclass('prks.instance') IS NOT NULL").fetchone()
    if row and row[0]:
        found = conn.execute("SELECT id, storage_root_id FROM prks.instance").fetchone()
        if found:
            instance = Instance(id=str(found[0]), storage_root_id=str(found[1]))
    schemas = tuple(str(name) for (name,) in conn.execute(_SCHEMAS_SQL).fetchall())
    return DatabaseState(instance=instance, schemas=schemas)


def _plan_database(conninfo: str) -> DatabaseState:
    try:
        with psycopg.connect(conninfo, application_name=_APPLICATION_NAME) as conn:
            conn.read_only = True
            return _read_state(conn)
    except psycopg.Error:
        raise ResetRefused(
            "database_unavailable",
            "Cannot read the testing database. For --testing, start the local database with "
            "scripts/dev-postgres. Nothing was deleted.",
        ) from None


def _is_empty_dir(path: str) -> bool:
    with os.scandir(path) as entries:
        return next(entries, None) is None


def _holds_sqlite_catalogue(root_real: str) -> bool:
    from backend.domain.objects.jobs import root_refusal

    return root_refusal(root_real) is not None


def check_root(root: str, *, instance: Optional[Instance]) -> str:
    """The real path of ``root`` when the reset may empty it; :class:`ResetRefused` otherwise."""
    try:
        paths.assert_safe_testing_path(root, testing=True, what="storage root")
    except RuntimeError as exc:
        raise ResetRefused("unsafe_root", f"{exc}. Nothing was deleted.") from None
    root_real = os.path.realpath(root)
    if not os.path.lexists(root_real):
        return root_real
    if not os.path.isdir(root_real):
        raise ResetRefused(
            "root_not_a_directory", f"The testing data root {root} is not a directory. Nothing was deleted."
        )
    if _is_empty_dir(root_real):
        return root_real
    if _holds_sqlite_catalogue(root_real):
        raise ResetRefused(
            "sqlite_catalogue",
            f"The testing data root {root} holds a pre-0.8 SQLite Library, which --reset-db never "
            "deletes. Nothing was deleted. Move it aside, or delete it yourself if it is disposable.",
        )
    try:
        marker = read_marker(root_real)
    except StorageRootError:
        marker = None
    if (
        marker is not None
        and instance is not None
        and marker.instance_id == instance.id
        and marker.storage_root_id.lower() == instance.storage_root_id.lower()
    ):
        return root_real
    raise ResetRefused(
        "root_not_paired",
        f"The data root {root} does not belong to this testing database (it has no marker, or "
        "its marker names another PRKS instance), so --reset-db leaves both alone. Nothing was "
        "deleted. If that root's own database is gone (for example after scripts/dev-postgres "
        "reset), delete the folder yourself and run --reset-db again; otherwise choose the root "
        "that belongs to this database with --storage-root or PRKS_STORAGE.",
    )


def plan_reset(conninfo: str, root: str) -> ResetPlan:
    """Check that both halves are one testing instance; deletes nothing."""
    database = _plan_database(conninfo)
    root_real = check_root(root, instance=database.instance)
    return ResetPlan(
        conninfo=conninfo,
        database_label=database_label(conninfo),
        database=database,
        root=root,
        root_real=root_real,
    )


def warning_text(plan: ResetPlan) -> str:
    schemas = ", ".join(plan.database.schemas) or "none"
    return (
        "--reset-db permanently deletes this testing instance:\n"
        f"  Database:  {plan.database_label}. Every schema in it except public is dropped, "
        f"with every table and row ({schemas}).\n"
        f"  Data root: {plan.root}. Every file in it is deleted (PDFs, thumbnails, people images, logs).\n"
        "This cannot be undone. Nothing is backed up and the data cannot be recovered.\n"
        "When the reset is done, PRKS starts on a new, empty testing database and data root,\n"
        "set up as on a first start.\n"
    )


def confirm(
    plan: ResetPlan,
    *,
    stdin: TextIO,
    stdout: TextIO,
    read: Optional[Callable[[], str]] = None,
) -> bool:
    """Show what is deleted and return whether the person typed :data:`CONFIRM_WORD`.

    Refuses without asking when ``stdin`` is not a terminal.
    """
    if read is None and not stdin.isatty():
        raise ResetRefused(
            "no_terminal",
            "--reset-db asks for confirmation and needs a terminal. Nothing was deleted.",
        )
    reader: Callable[[], str] = read if read is not None else stdin.readline
    stdout.write(warning_text(plan))
    stdout.write(f"Type {CONFIRM_WORD} to confirm that you understand and want to delete it: ")
    stdout.flush()
    try:
        answer = reader()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    stdout.write("\n" if not answer.endswith("\n") else "")
    return answer.strip() == CONFIRM_WORD


def _drop_schemas(plan: ResetPlan) -> None:
    """Prove again and drop the confirmed schemas, in one transaction under the schema runner's lock."""
    try:
        with psycopg.connect(plan.conninfo, application_name=_APPLICATION_NAME, autocommit=True) as conn:
            locked = conn.execute("SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)).fetchone()
            if not (locked and locked[0]):
                raise ResetRefused(
                    "database_busy",
                    "Another PRKS process is applying the schema to the testing database. "
                    "Stop it and run --reset-db again. Nothing was deleted.",
                )
            try:
                with conn.transaction():
                    conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(_LOCK_TIMEOUT)))
                    if _read_state(conn) != plan.database:
                        raise ResetRefused(
                            "database_changed",
                            "The testing database changed after you confirmed (its PRKS instance or "
                            "its schemas). Nothing was deleted. Run --reset-db again.",
                        )
                    for name in plan.database.schemas:
                        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
                    if not is_empty_database(conn):
                        raise ResetRefused(
                            "database_not_empty",
                            "The testing database holds objects outside its schemas (for example in "
                            "the public schema), so it cannot be emptied safely. Nothing was deleted. "
                            "Drop them yourself, or use a new database.",
                        )
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    except psycopg.errors.LockNotAvailable:
        raise ResetRefused(
            "database_busy",
            "Another process is using the testing database. Stop every PRKS process on it and run "
            "--reset-db again. Nothing was deleted.",
        ) from None
    except psycopg.Error:
        raise ResetRefused(
            "database_unavailable",
            "Could not empty the testing database. Nothing was deleted.",
        ) from None


def _remove(path: str, root_dev: int) -> None:
    """Delete ``path``; never follow a link or descend into another device or a mount point."""
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(path)
        return
    if st.st_dev != root_dev or os.path.ismount(path):
        raise OSError(f"{path} is a mount point")
    for name in os.listdir(path):
        _remove(os.path.join(path, name), root_dev)
    os.rmdir(path)


def _empty_root(root_real: str) -> None:
    """Delete everything in the root except its lock files, which other processes may open."""
    root_dev = os.stat(root_real).st_dev
    for entry in os.listdir(root_real):
        path = os.path.join(root_real, entry)
        if entry == MAINTENANCE_DIRNAME and os.path.isdir(path) and not os.path.islink(path):
            for inner in os.listdir(path):
                if inner not in _LOCK_FILES:
                    _remove(os.path.join(path, inner), root_dev)
            continue
        _remove(path, root_dev)


def _lease(root_real: str) -> Optional[ExclusiveFileLock]:
    """The root's lease, held exclusive, or ``None`` when the root has no maintenance directory yet."""
    if not os.path.isdir(os.path.join(root_real, MAINTENANCE_DIRNAME)):
        return None
    try:
        return ExclusiveFileLock.acquire(lease_path(root_real))
    except LockBusy:
        raise ResetRefused(
            "root_in_use",
            "A PRKS process is running on the testing data root. Stop it (and its worker) and run "
            "--reset-db again. Nothing was deleted.",
        ) from None
    except LockUnavailable:
        raise ResetRefused(
            "root_lock_unavailable", "Could not lock the testing data root. Nothing was deleted."
        ) from None


def execute(plan: ResetPlan) -> None:
    """Check the tree, empty the database, then the data root, holding the root's lease throughout."""
    lease = _lease(plan.root_real)
    try:
        if os.path.isdir(plan.root_real):
            try:
                check_no_foreign_tree(plan.root_real)
            except StorageRootError as exc:
                raise ResetRefused(
                    "root_not_plain", f"{exc.message} --reset-db never deletes past it. Nothing was deleted."
                ) from None
        _drop_schemas(plan)
        if os.path.isdir(plan.root_real):
            try:
                _empty_root(plan.root_real)
            except OSError:
                raise ResetRefused(
                    "root_not_emptied",
                    "The testing database was emptied, but not all of the data root could be "
                    f"deleted. Delete {plan.root} yourself before the next start.",
                ) from None
    finally:
        if lease is not None:
            lease.release()


def run(
    conninfo: str,
    root: str,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    read: Optional[Callable[[], str]] = None,
) -> bool:
    """Plan, confirm and run the reset. ``False`` when the person cancelled; raises :class:`ResetRefused`."""
    plan = plan_reset(conninfo, root)
    if not confirm(plan, stdin=stdin, stdout=stdout, read=read):
        return False
    execute(plan)
    return True


def main_reset(conninfo: str, root: str, **kwargs: Any) -> None:
    """The ``--reset-db`` step of ``prks_app.py``: returns after a reset, exits otherwise."""
    try:
        done = run(conninfo, root, **kwargs)
    except ResetRefused as exc:
        print(f"PRKS did not reset the testing instance: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    if not done:
        print("Reset cancelled. Nothing was deleted.", file=sys.stderr)
        raise SystemExit(1)
    print("The testing instance was reset. Starting PRKS on a new, empty testing instance.", flush=True)
