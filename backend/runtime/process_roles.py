"""The PostgreSQL process roles started from ``prks_app.py`` (§12.2, §12.3; L-D-1 #630).

``prks_app.py migrate`` and ``prks_app.py worker`` are the one-shot schema
runner and the job worker of the Compose topology. Both act on the storage
root that ``prks_app.py`` selected (``--storage-root``, ``PRKS_STORAGE`` or
the bootstrap file), so every role of one deployment names its root the same
way.

``migrate`` applies the schema files (§4.4), then runs the §11.1 start check
and bump for its own start under the shared ``root.lock``: every starting
``migrate``, ``web`` and ``worker`` bumps the start generation once. The
runner's schema work never reads or writes the root; the check and the bump
run after it. While setup is not ``ready`` there is nothing to pair yet, so
``migrate`` leaves the root untouched (§11.1, "Root claim").

Output is fixed text: no connection string, path, token or driver message.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any, Optional, TextIO

from backend.db.config import load_database_config
from backend.db.database import Database
from backend.db.errors import DatabaseConfigError, DatabaseError
from backend.db.migrate import SCHEMA_DIR, MigrationError, migrate
from backend.domain.identity import root_pairing
from backend.domain.identity.errors import RootPairingRefused
from backend.storage.errors import StorageRootError
from backend.storage.root_binding import acquire_shared_root_lease
from backend.storage.root_marker import binding_refusal, read_marker

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

LOGGER = logging.getLogger(__name__)

DatabaseFactory = Callable[[Mapping[str, str]], Database]
Faults = Callable[[str], None]


def _open_database(environ: Mapping[str, str]) -> Database:
    # One short-lived pool for the setup-state read; the runner and the start
    # check each open their own dedicated connection (§4.4, §11.1).
    return Database(load_database_config(environ, role="web"))


def _no_faults(_point: str) -> None:
    return None


def _setup_state(db: Database) -> Optional[str]:
    row = db.query(lambda tx: tx.execute("SELECT setup_state FROM prks.instance").fetchone())
    return None if row is None else str(row["setup_state"])


def _check_bound(root: str) -> None:
    """Refuse a missing or unbound root before anything is created in it."""
    if not os.path.isdir(root):
        raise RootPairingRefused("root_missing", "The storage root does not exist.")
    try:
        marker = read_marker(root)
    except StorageRootError as exc:
        raise RootPairingRefused(exc.reason, f"The storage root cannot be used ({exc.reason}).") from None
    if marker is None:
        raise RootPairingRefused(
            "root_unmarked",
            "This database is set up, but the storage root has no prks-root.json. "
            "A root is never adopted by a database that is not empty.",
        )
    refusal = binding_refusal(marker)
    if refusal is not None:
        raise RootPairingRefused(refusal.reason, f"The storage root cannot be used ({refusal.reason}).")


def start_after_migrate(
    db: Database, *, root: str, testing: bool, faults: Faults = _no_faults
) -> root_pairing.StartResult:
    """The §11.1 start check and bump of a ``migrate`` run, after its schema work.

    Returns ``SETUP_PENDING`` without touching the root while setup is not
    ``ready``. Raises :class:`RootPairingRefused` when this database must not
    run against ``root``.
    """
    if _setup_state(db) != root_pairing.SETUP_READY:
        return root_pairing.StartResult(root_pairing.StartStatus.SETUP_PENDING)
    _check_bound(root)
    try:
        lease = acquire_shared_root_lease(root)
    except StorageRootError as exc:
        raise RootPairingRefused(exc.reason, f"The storage root cannot be used ({exc.reason}).") from None
    try:
        return root_pairing.start_process(
            db, root=root, lease=lease, testing=testing, bump=True, faults=faults
        )
    finally:
        lease.release()


def run_migrate(
    *,
    root: str,
    testing: bool,
    environ: Optional[Mapping[str, str]] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    open_database: DatabaseFactory = _open_database,
    schema_dir: str = SCHEMA_DIR,
    faults: Faults = _no_faults,
) -> int:
    """``prks_app.py migrate``: apply the schema, then check and bump this start.

    ``root`` is the real path of the selected storage root. Exit codes: 0
    done, 1 refused (the schema or the root pairing), 2 configuration.
    """
    env = os.environ if environ is None else environ
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    try:
        db = open_database(env)
    except DatabaseConfigError as exc:
        print(f"migrate: {exc.message}", file=err)
        return EXIT_USAGE
    except DatabaseError:
        print("migrate: the database cannot be reached.", file=err)
        return EXIT_REFUSED
    with db:
        try:
            result = migrate(db.config.conninfo, directory=schema_dir)
        except MigrationError as exc:
            print(f"migrate: {exc.message}", file=err)
            return EXIT_REFUSED
        for filename in result.applied:
            print(f"migrate: applied {filename}", file=out)
        if not result.applied:
            print("migrate: schema is up to date", file=out)
        try:
            started = start_after_migrate(db, root=root, testing=testing, faults=faults)
        except RootPairingRefused as exc:
            print(f"migrate: refused to start: {exc.message}", file=err)
            return EXIT_REFUSED
        except DatabaseError:
            print("migrate: the database cannot be reached.", file=err)
            return EXIT_REFUSED
    if started.status is root_pairing.StartStatus.SETUP_PENDING:
        print("migrate: setup is not complete; the storage root was not checked", file=out)
    else:
        note = " (reconciled an interrupted start)" if started.reconciled else ""
        print(f"migrate: start generation {started.start_generation}{note}", file=out)
    return EXIT_OK


def run_worker(*, root: str, testing: bool, **kwargs: Any) -> int:
    """``prks_app.py worker``: the job worker on the selected root (``backend.jobs.worker``)."""
    from backend.jobs import worker

    argv = ["--root", root] + (["--testing"] if testing else [])
    return worker.main(argv, **kwargs)
