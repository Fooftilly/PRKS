"""Database ↔ data-root pairing (``docs/postgresql-architecture.md`` §9.2, §11.1).

One PostgreSQL database runs against one data root. This module is the only
code that compares or advances the pairing; the schema runner never reads or
writes the root (§4.4).

**Root claim (first boot).** :func:`bootstrap` runs §9.2's three durable
steps, and a retry resumes from wherever a crash stopped them:

1. :func:`begin_bootstrap`: with no instance row, one transaction checks the
   root is a new installation and commits the row as ``bootstrapping`` (new
   ``id``, the root's ``storage_root_id``, ``start_generation`` 1, a new
   ``start_token``). With a ``bootstrapping`` row it creates nothing and
   reuses the row only when the bound root's ``storage_root_id`` equals the
   row's, so one bootstrapping database never claims a second root.
2. :func:`claim_bootstrap_root`: ``root_marker.claim_root`` under the
   exclusive ``root.claim`` lock.
3. :func:`finish_bootstrap`: one SERIALIZABLE transaction re-checks the
   claim, creates the owner, the Library and the membership and marks setup
   ``ready``; repeating it returns the existing state.

**Start check and bump.** Every process that serves, schedules work or
changes data calls :func:`start_process` with ``bump=True`` before it does
anything else; a read-only admin command passes ``bump=False``. Under a
session-level advisory lock on :data:`START_LOCK_KEY`, taken on a dedicated
connection that is unlocked and closed on every path, it refuses a database
paired with another root or instance, a database behind the marker, a
database ahead of it that is not exactly one reconcilable bump ahead, and
equal generations with different tokens; it reconciles a bump whose marker
write never happened; then it bumps the database (committed) and the marker,
in that order. While setup is not ``ready`` it does nothing and reports
:attr:`StartStatus.SETUP_PENDING`, so a ``worker`` started before bootstrap
calls it again once setup is ready. The process that performed the claim
counts the claim as its bump and passes ``bump=False`` for its first start.

Concurrent database copies against one root are not detected (risk R20):
a copy carries the same ``instance.id``, and the generation counts starts,
not writes.

Every refusal is a :class:`RootPairingRefused` with a stable ``reason`` and
fixed text that names at most instance and storage root IDs, never a path,
a token, the connection string or a driver message.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from backend import entity_ids
from backend.db.database import Database, Isolation, Transaction
from backend.db.errors import DatabaseError
from backend.storage.errors import StorageRootError
from backend.storage.file_lock import SharedFileLock
from backend.storage.objects import root_holds_objects
from backend.storage.root_marker import (
    RootMarker,
    StartState,
    binding_refusal,
    bump_start_generation,
    claim_root,
    marker_is_durable,
    new_start_token,
    read_marker,
    require_shared_lease,
)

# Session advisory-lock key of the start check and bump ("PRKSSTRT" in ASCII),
# distinct from the schema runner's MIGRATION_LOCK_KEY.
START_LOCK_KEY = 0x50524B5353545254

# Bounded wait for the start key (§11.1: 60 s, not the pool's 5 s). A start
# that times out stays not ready and retries.
DEFAULT_START_LOCK_TIMEOUT_MS = 60_000

START_APPLICATION_NAME = "prks-start"

SETUP_UNCONFIGURED = "unconfigured"
SETUP_BOOTSTRAPPING = "bootstrapping"
SETUP_READY = "ready"

# Fault-injection points (tests only): a ``faults`` callback is called with
# one of these names at the matching point and may raise or exit there.
FAULT_AFTER_INSTANCE_COMMIT = "bootstrap.after_instance_commit"
FAULT_CLAIM_BEFORE_WRITE = "bootstrap.claim_before_write"
FAULT_AFTER_CLAIM = "bootstrap.after_claim"
FAULT_AFTER_DATABASE_BUMP = "start.after_database_bump"

Faults = Callable[[str], None]

_RECOVERY_HINT = (
    "Use whole-instance disaster recovery (this root restored together with its "
    "own database), or a new data root and a library backup restore."
)


class RootPairingRefused(Exception):
    """A bootstrap or start was refused; nothing that should not change did.

    ``retryable`` is true when the same process may try again later and
    expect a different answer (the start key was busy, the database or the
    marker could not be written); the process stays not ready meanwhile.
    """

    def __init__(self, reason: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.retryable = retryable


class StartStatus(enum.Enum):
    READY = "ready"
    SETUP_PENDING = "setup_pending"


@dataclass(frozen=True)
class StartResult:
    status: StartStatus
    instance_id: Optional[str] = None
    start_generation: Optional[int] = None
    reconciled: bool = False
    bumped: bool = False


@dataclass(frozen=True)
class BootstrapClaim:
    """The ``bootstrapping`` instance row a bootstrap resumes from."""

    instance_id: str
    storage_root_id: str
    start_token: str

    def __repr__(self) -> str:  # the token stays out of logs and tracebacks
        return f"BootstrapClaim(instance_id={self.instance_id!r})"


@dataclass(frozen=True)
class OwnerSpec:
    """The owner account and personal Library that bootstrap creates (§9.2)."""

    login_name: str
    display_name: str
    library_name: str
    password_hash: Optional[str] = None

    def __repr__(self) -> str:  # never print a password hash
        return f"OwnerSpec(login_name={self.login_name!r})"


@dataclass(frozen=True)
class BootstrapResult:
    instance_id: str
    user_id: str
    library_id: str
    created: bool  # False when setup was already ready (a repeated step 3)


def _no_faults(_point: str) -> None:
    return None


def _from_storage(exc: StorageRootError) -> RootPairingRefused:
    return RootPairingRefused(exc.reason, str(exc))


def _bound_marker(root: str) -> RootMarker:
    try:
        marker = read_marker(root)
    except StorageRootError as exc:
        raise _from_storage(exc) from exc
    if marker is None:
        raise RootPairingRefused(
            "root_unmarked",
            "The storage root has no prks-root.json; PRKS must bind it first.",
        )
    refusal = binding_refusal(marker)
    if refusal is not None:
        raise _from_storage(refusal)
    return marker


def _lease(root: str, lease: SharedFileLock) -> None:
    try:
        require_shared_lease(root, lease)
    except StorageRootError as exc:
        raise _from_storage(exc) from exc


# --- bootstrap: step 1 -------------------------------------------------------------


def _instance_row(tx: Transaction, *, lock: bool) -> Optional[dict[str, Any]]:
    suffix = " FOR UPDATE" if lock else ""
    return tx.execute(
        "SELECT id, storage_root_id, setup_state, testing, start_generation, "
        "start_token, prev_start_token FROM prks.instance" + suffix
    ).fetchone()


def begin_bootstrap(
    db: Database,
    *,
    root: str,
    lease: SharedFileLock,
    testing: bool,
    holds_objects: Optional[Callable[[], bool]] = None,
) -> BootstrapClaim:
    """Step 1: commit (or find) the ``bootstrapping`` instance row (§11.1)."""
    _lease(root, lease)
    marker = _bound_marker(root)
    check_objects = holds_objects or (lambda: root_holds_objects(root))
    # File checks happen once, before the transaction; the transaction body
    # must be safe to run again on a serialization retry.
    root_is_new = (
        marker.instance_id is None
        and marker.start_state.generation == 0
        and not check_objects()
    )
    instance_id = entity_ids.generate("IN")
    token = new_start_token()

    def step(tx: Transaction) -> BootstrapClaim:
        # Serializes every step 1 of this database (the lock conflicts with
        # itself), so a double-submitted bootstrap sees the other's committed
        # row instead of racing it to the singleton index.
        tx.execute("LOCK TABLE prks.instance IN SHARE ROW EXCLUSIVE MODE")
        row = _instance_row(tx, lock=False)
        if row is None:
            if not root_is_new:
                raise RootPairingRefused(
                    "root_used",
                    "This storage root belongs to an earlier database (it was claimed, "
                    "has run, or holds stored files). " + _RECOVERY_HINT,
                )
            tx.execute(
                "INSERT INTO prks.instance (id, storage_root_id, setup_state, testing, "
                "start_generation, start_token) VALUES (%s, %s, 'bootstrapping', %s, 1, %s)",
                (instance_id, marker.storage_root_id, testing, token),
            )
            return BootstrapClaim(instance_id, marker.storage_root_id, token)
        return _resume(row, marker=marker, testing=testing)

    try:
        return db.command(step)
    except DatabaseError as exc:
        raise RootPairingRefused(
            "database_unavailable", "The database could not record the bootstrap.",
            retryable=True,
        ) from exc


def _resume(row: dict[str, Any], *, marker: RootMarker, testing: bool) -> BootstrapClaim:
    state = row["setup_state"]
    if state == SETUP_READY:
        raise RootPairingRefused(
            "already_ready", "This PRKS instance is already set up; bootstrap is closed."
        )
    if state != SETUP_BOOTSTRAPPING:
        raise RootPairingRefused(
            "instance_state_invalid", "The instance row is in an unexpected setup state."
        )
    if row["storage_root_id"] != marker.storage_root_id:
        raise RootPairingRefused(
            "bootstrap_root_mismatch",
            f"This database started bootstrapping against storage root "
            f"{row['storage_root_id']}, not {marker.storage_root_id}. Point it at the "
            "root it started with.",
        )
    if bool(row["testing"]) != testing:
        raise RootPairingRefused(
            "testing_mismatch",
            "A testing database is only used by a testing process, and vice versa.",
        )
    if row["start_generation"] != 1 or not row["start_token"]:
        raise RootPairingRefused(
            "instance_state_invalid", "The bootstrapping instance row is not at generation 1."
        )
    return BootstrapClaim(row["id"], row["storage_root_id"], row["start_token"])


# --- bootstrap: step 2 -------------------------------------------------------------


def claim_bootstrap_root(
    root: str,
    claim: BootstrapClaim,
    *,
    lease: SharedFileLock,
    holds_objects: Optional[Callable[[], bool]] = None,
    faults: Faults = _no_faults,
) -> RootMarker:
    """Step 2: write the claim into the root marker, or accept a finished one."""
    try:
        return claim_root(
            root,
            instance_id=claim.instance_id,
            start_token=claim.start_token,
            lease=lease,
            holds_objects=holds_objects or (lambda: root_holds_objects(root)),
            before_write=lambda: faults(FAULT_CLAIM_BEFORE_WRITE),
        )
    except StorageRootError as exc:
        raise _from_storage(exc) from exc
    except OSError as exc:
        raise RootPairingRefused(
            "marker_write_failed",
            "The storage root marker prks-root.json could not be written.",
            retryable=True,
        ) from exc


# --- bootstrap: step 3 -------------------------------------------------------------


def finish_bootstrap(
    db: Database,
    claim: BootstrapClaim,
    owner: OwnerSpec,
    *,
    root: str,
    lease: SharedFileLock,
) -> BootstrapResult:
    """Step 3: create the owner, the Library and the membership; mark ``ready``.

    Re-reads the marker first and requires the claim exactly as step 2 left
    it: a root that moved on since is refused here too.
    """
    _lease(root, lease)
    marker = _bound_marker(root)
    if marker.instance_id != claim.instance_id or marker.start_state != StartState(
        1, claim.start_token
    ):
        raise RootPairingRefused(
            "root_not_claimed",
            f"The storage root is not claimed by instance {claim.instance_id} at its "
            "first start; run bootstrap again from the start.",
        )
    user_id = entity_ids.generate("U")
    library_id = entity_ids.generate("LIB")

    def step(tx: Transaction) -> BootstrapResult:
        row = _instance_row(tx, lock=True)
        if row is None or row["id"] != claim.instance_id:
            raise RootPairingRefused(
                "instance_state_invalid", "The instance row does not match this bootstrap."
            )
        if row["setup_state"] == SETUP_READY:
            existing = tx.execute(
                "SELECT library_id, user_id FROM prks.library_memberships "
                "WHERE role = 'owner' ORDER BY library_id, user_id LIMIT 1"
            ).fetchone()
            if existing is None:
                raise RootPairingRefused(
                    "instance_state_invalid", "The ready instance has no owner."
                )
            return BootstrapResult(row["id"], existing["user_id"], existing["library_id"], False)
        if row["setup_state"] != SETUP_BOOTSTRAPPING or row["start_token"] != claim.start_token:
            raise RootPairingRefused(
                "instance_state_invalid", "The instance row does not match this bootstrap."
            )
        tx.execute(
            "INSERT INTO prks.users (id, login_name, display_name, password_hash) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, owner.login_name, owner.display_name, owner.password_hash),
        )
        tx.execute(
            "INSERT INTO prks.libraries (id, name) VALUES (%s, %s)",
            (library_id, owner.library_name),
        )
        tx.execute(
            "INSERT INTO prks.library_memberships (library_id, user_id, role) "
            "VALUES (%s, %s, 'owner')",
            (library_id, user_id),
        )
        tx.execute("UPDATE prks.instance SET setup_state = 'ready' WHERE id = %s", (row["id"],))
        return BootstrapResult(row["id"], user_id, library_id, True)

    try:
        return db.command(step, isolation=Isolation.SERIALIZABLE)
    except DatabaseError as exc:
        raise RootPairingRefused(
            "database_unavailable", "The database could not finish the bootstrap.",
            retryable=True,
        ) from exc


def bootstrap(
    db: Database,
    owner: OwnerSpec,
    *,
    root: str,
    lease: SharedFileLock,
    testing: bool,
    holds_objects: Optional[Callable[[], bool]] = None,
    faults: Faults = _no_faults,
) -> BootstrapResult:
    """Run the three steps; a retry after any crash resumes where it stopped.

    The calling process counts the claim as its start-generation bump: its
    own first :func:`start_process` afterwards passes ``bump=False``.
    """
    claim = begin_bootstrap(
        db, root=root, lease=lease, testing=testing, holds_objects=holds_objects
    )
    faults(FAULT_AFTER_INSTANCE_COMMIT)
    claim_bootstrap_root(root, claim, lease=lease, holds_objects=holds_objects, faults=faults)
    faults(FAULT_AFTER_CLAIM)
    return finish_bootstrap(db, claim, owner, root=root, lease=lease)


# --- start check and bump ----------------------------------------------------------


def _connect_for_start(conninfo: str, lock_timeout_ms: int) -> psycopg.Connection[Any]:
    try:
        conn = psycopg.connect(conninfo, autocommit=True, application_name=START_APPLICATION_NAME)
    except psycopg.Error:
        raise RootPairingRefused(
            "database_unavailable", "Cannot connect to the database.", retryable=True
        ) from None
    try:
        conn.execute("SELECT set_config('search_path', '', false)")
        conn.execute("SELECT set_config('lock_timeout', %s, false)", (str(int(lock_timeout_ms)),))
        conn.execute("SELECT set_config('statement_timeout', '0', false)")
        conn.execute("SELECT set_config('transaction_timeout', '0', false)")
    except psycopg.Error:
        conn.close()
        raise RootPairingRefused(
            "database_unavailable", "Cannot configure the database session.", retryable=True
        ) from None
    return conn


def _take_start_key(conn: psycopg.Connection[Any]) -> None:
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (START_LOCK_KEY,))
    except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled):
        raise RootPairingRefused(
            "start_lock_timeout",
            "Another PRKS process of this database is still starting; try again.",
            retryable=True,
        ) from None
    except psycopg.Error:
        raise RootPairingRefused(
            "database_unavailable",
            "Lost the database connection while waiting for the start lock.",
            retryable=True,
        ) from None


def _release(conn: psycopg.Connection[Any]) -> None:
    try:
        if not conn.closed:
            conn.execute("SELECT pg_advisory_unlock(%s)", (START_LOCK_KEY,))
    except psycopg.Error:
        pass  # closing the session below releases it anyway
    finally:
        conn.close()


def start_process(
    db: Database,
    *,
    root: str,
    lease: SharedFileLock,
    testing: bool,
    bump: bool,
    lock_timeout_ms: int = DEFAULT_START_LOCK_TIMEOUT_MS,
    faults: Faults = _no_faults,
) -> StartResult:
    """The start check, the reconcile and the bump of one process (§11.1).

    Runs before the process serves, schedules work or changes data. Raises
    :class:`RootPairingRefused` when the process must not run against this
    root; the start key is released and the dedicated connection closed on
    every path.
    """
    _lease(root, lease)
    conn = _connect_for_start(db.config.conninfo, lock_timeout_ms)
    try:
        _take_start_key(conn)
        return _check_and_bump(conn, root=root, lease=lease, testing=testing, bump=bump, faults=faults)
    finally:
        _release(conn)


def _read_instance(conn: psycopg.Connection[Any]) -> Optional[dict[str, Any]]:
    try:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id, storage_root_id, setup_state, testing, start_generation, "
                "start_token, prev_start_token FROM prks.instance"
            )
            return cursor.fetchone()
    except psycopg.Error:
        raise RootPairingRefused(
            "database_unavailable", "Cannot read the instance row.", retryable=True
        ) from None


def _marker_write(
    root: str,
    *,
    lease: SharedFileLock,
    instance_id: str,
    expected: StartState,
    to: StartState,
) -> None:
    try:
        _marker, durable = bump_start_generation(
            root, lease=lease, instance_id=instance_id, expected=expected, to=to
        )
    except StorageRootError as exc:
        raise _from_storage(exc) from exc
    except OSError as exc:
        raise RootPairingRefused(
            "marker_write_failed",
            "The storage root marker prks-root.json could not be written; this process "
            "stays not ready and the next start reconciles it.",
            retryable=True,
        ) from exc
    if not durable:
        # The marker may roll back on a crash; a process never serves on a
        # pairing that is not durable. The next start retries the sync.
        raise _not_durable()


def _not_durable() -> RootPairingRefused:
    return RootPairingRefused(
        "marker_not_durable",
        "The storage root marker prks-root.json could not be made crash-safe; this "
        "process stays not ready and the next start retries.",
        retryable=True,
    )


def _check_and_bump(
    conn: psycopg.Connection[Any],
    *,
    root: str,
    lease: SharedFileLock,
    testing: bool,
    bump: bool,
    faults: Faults,
) -> StartResult:
    row = _read_instance(conn)
    if row is None or row["setup_state"] != SETUP_READY:
        return StartResult(StartStatus.SETUP_PENDING)
    instance_id = row["id"]
    if bool(row["testing"]) != testing:
        raise RootPairingRefused(
            "testing_mismatch",
            "A testing database is only served by a testing process, and vice versa.",
        )
    marker = _bound_marker(root)
    if marker.storage_root_id != row["storage_root_id"]:
        raise RootPairingRefused(
            "root_pairing_mismatch",
            f"This database belongs to storage root {row['storage_root_id']}, but the "
            f"selected root is {marker.storage_root_id}.",
        )
    if marker.instance_id != instance_id:
        if marker.instance_id is None:
            raise RootPairingRefused(
                "root_unclaimed",
                f"The storage root is not claimed, but this database (instance "
                f"{instance_id}) is already set up. A root is never adopted by a "
                "database that is not empty.",
            )
        raise RootPairingRefused(
            "root_claimed_by_other",
            f"This storage root belongs to PRKS instance {marker.instance_id}, not to "
            f"this database's instance {instance_id}. " + _RECOVERY_HINT,
        )

    database = StartState(int(row["start_generation"]), row["start_token"])
    on_root = marker.start_state
    reconciled = False
    if database.generation < on_root.generation:
        raise RootPairingRefused(
            "database_behind",
            f"Another database has run against this storage root since (root at start "
            f"generation {on_root.generation}, this database at {database.generation}). "
            "This database is a stale copy and must not be started against it.",
        )
    if database.generation > on_root.generation:
        if (
            database.generation != on_root.generation + 1
            or row["prev_start_token"] is None
            or row["prev_start_token"] != on_root.token
        ):
            raise RootPairingRefused(
                "database_ahead",
                f"This database is at start generation {database.generation}, ahead of "
                f"the storage root ({on_root.generation}) by more than one interrupted "
                "start of its own. It is not the database this root last ran with.",
            )
        # A bump whose marker write never happened: finish it before anything.
        _marker_write(root, lease=lease, instance_id=instance_id, expected=on_root, to=database)
        reconciled = True
    elif database.token != on_root.token:
        raise RootPairingRefused(
            "generation_forked",
            f"Another database reached start generation {on_root.generation} on this "
            "storage root separately. This database is a copy and must not be started "
            "against it.",
        )

    if not bump:
        if not reconciled and not marker_is_durable(root):
            raise _not_durable()
        return StartResult(
            StartStatus.READY, instance_id, database.generation, reconciled=reconciled
        )

    after = StartState(database.generation + 1, new_start_token())
    try:
        with conn.transaction():
            moved = conn.execute(
                "UPDATE prks.instance SET start_generation = %s, start_token = %s, "
                "prev_start_token = start_token "
                "WHERE id = %s AND start_generation = %s AND start_token = %s RETURNING id",
                (after.generation, after.token, instance_id, database.generation, database.token),
            ).fetchone()
    except psycopg.Error:
        raise RootPairingRefused(
            "database_unavailable", "Cannot record the start generation.", retryable=True
        ) from None
    if moved is None:
        raise RootPairingRefused(
            "start_state_changed",
            "The start generation changed while this process held the start lock.",
            retryable=True,
        )
    faults(FAULT_AFTER_DATABASE_BUMP)
    _marker_write(root, lease=lease, instance_id=instance_id, expected=database, to=after)
    return StartResult(
        StartStatus.READY, instance_id, after.generation, reconciled=reconciled, bumped=True
    )
