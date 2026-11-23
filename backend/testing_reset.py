"""``prks_app.py --testing --reset-db``: empty the testing instance, then start on a new one.

A testing database whose applied schema file has since changed cannot start
(``backend.db.migrate``: before v1.0.0 the instance is recreated, never
upgraded). This command empties the testing instance this ``--testing`` run
would use, after the person types :data:`CONFIRM_WORD`, and the normal start
then applies the schema to the empty database and bootstraps a new owner and
Library on the empty data root.

It only ever touches a testing instance:

- the database must pass :func:`backend.db.testing_guard.prove_testing_database`
  (marked for testing; any instance row says ``testing``);
- the data root must pass the ``--testing`` path check (never ``/data`` or the
  repository ``data/``) and be the repository ``data_testing/`` tree, an empty
  or missing directory, or a root whose marker names this database's testing
  instance. Any other root is refused before anything is deleted;
- the confirmation is read from a terminal only, so a script or a pipe can
  never answer it.

The database itself is kept (with its testing mark, owner and locale); every
schema in it other than ``public`` and the system schemas is dropped in one
transaction, and the database must then be as empty as the schema runner
requires. The data root keeps only its lock files. A running PRKS process on
the root holds ``root.lock`` and makes the reset refuse before it deletes
anything; one that holds database locks makes the drop time out and roll back.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional, TextIO

import psycopg
from psycopg import sql

from backend.db.migrate import MIGRATION_LOCK_KEY, is_empty_database
from backend.db.testing_guard import TestingDatabaseRefused, prove_testing_database
from backend.storage import paths
from backend.storage.errors import StorageRootRefused
from backend.storage.file_lock import ExclusiveFileLock, LockBusy, LockUnavailable
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
_SYSTEM_SCHEMAS = ("public", "information_schema")


class ResetRefused(Exception):
    """The reset did not run, or stopped; ``message`` says why and what to do."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ResetPlan:
    """What a confirmed reset deletes."""

    conninfo: str
    database_label: str
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


def _instance_id(conninfo: str) -> Optional[str]:
    """The database's ``prks.instance`` id, if it has one (read-only)."""
    try:
        with psycopg.connect(conninfo, application_name=_APPLICATION_NAME) as conn:
            conn.read_only = True
            row = conn.execute("SELECT to_regclass('prks.instance') IS NOT NULL").fetchone()
            if not (row and row[0]):
                return None
            found = conn.execute("SELECT id FROM prks.instance").fetchone()
            return str(found[0]) if found else None
    except psycopg.Error:
        raise ResetRefused(
            "database_unavailable", "Could not read the testing database. Nothing was deleted."
        ) from None


def _is_empty_dir(path: str) -> bool:
    with os.scandir(path) as entries:
        return next(entries, None) is None


def check_root(root: str, *, instance_id: Optional[str]) -> str:
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
    if root_real == os.path.realpath(os.path.join(paths.repo_root(), "data_testing")):
        return root_real
    if _is_empty_dir(root_real):
        return root_real
    try:
        marker = read_marker(root_real)
    except StorageRootRefused:
        marker = None
    if marker is not None and instance_id is not None and marker.instance_id == instance_id:
        return root_real
    raise ResetRefused(
        "root_not_paired",
        f"--reset-db only empties the repository data_testing/ tree or a data root that belongs to "
        f"this testing database, and {root} is neither. Nothing was deleted. Choose that root "
        "with --storage-root or PRKS_STORAGE, or delete it yourself if it is disposable.",
    )


def plan_reset(conninfo: str, root: str) -> ResetPlan:
    """Check that both halves are a testing instance; deletes nothing."""
    try:
        prove_testing_database(conninfo)
    except TestingDatabaseRefused as exc:
        raise ResetRefused(exc.reason, f"{exc.message} Nothing was deleted.") from None
    root_real = check_root(root, instance_id=_instance_id(conninfo))
    return ResetPlan(conninfo=conninfo, database_label=database_label(conninfo), root=root, root_real=root_real)


def warning_text(plan: ResetPlan) -> str:
    return (
        "--reset-db permanently deletes this testing instance:\n"
        f"  Database:  {plan.database_label}. Every PRKS schema, table and row in it is dropped.\n"
        f"  Data root: {plan.root}. Every file in it is deleted (PDFs, thumbnails, people images, logs).\n"
        "This cannot be undone. Nothing is backed up and the data cannot be recovered.\n"
        "When the reset is done, PRKS starts on a new, empty testing database and data root,\n"
        "with a new owner and an empty Library.\n"
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


def _drop_schemas(conninfo: str) -> None:
    try:
        with psycopg.connect(conninfo, application_name=_APPLICATION_NAME, autocommit=True) as conn:
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
                    schemas = [
                        str(name)
                        for (name,) in conn.execute(
                            "SELECT nspname FROM pg_catalog.pg_namespace "
                            "WHERE nspname <> ALL(%s) AND nspname !~ '^pg_' ORDER BY nspname",
                            (list(_SYSTEM_SCHEMAS),),
                        ).fetchall()
                    ]
                    for name in schemas:
                        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
                    if not is_empty_database(conn):
                        raise ResetRefused(
                            "database_not_empty",
                            "The testing database holds objects outside PRKS's schemas (for example in "
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


def _remove(path: str) -> None:
    st = os.lstat(path)
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _empty_root(root_real: str) -> None:
    """Delete everything in the root except its lock files, which other processes may open."""
    for entry in os.listdir(root_real):
        path = os.path.join(root_real, entry)
        if entry == MAINTENANCE_DIRNAME and os.path.isdir(path) and not os.path.islink(path):
            for inner in os.listdir(path):
                if inner not in _LOCK_FILES:
                    _remove(os.path.join(path, inner))
            continue
        _remove(path)


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
    """Empty the database, then the data root, holding the root's lease throughout."""
    lease = _lease(plan.root_real)
    try:
        _drop_schemas(plan.conninfo)
        if os.path.isdir(plan.root_real):
            try:
                _empty_root(plan.root_real)
            except OSError as exc:
                raise ResetRefused(
                    "root_not_emptied",
                    "The testing database was emptied, but the data root could not be "
                    f"({exc.strerror or 'error'}). Delete {plan.root} yourself before the next start.",
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
