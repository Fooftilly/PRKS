"""A disposable PostgreSQL Library for each E2E server (#629 L-F-1, PR 1/3).

Every E2E ``AppServer`` runs the PostgreSQL-backed authenticated runtime:
``PRKS_AUTH=loopback-owner`` against its own database, created from the
migrated template of :mod:`tests.pg.harness` (``PRKS_TEST_DATABASE_URL``,
which ``scripts/test-postgres`` documents). There is no SQLite fallback: a
missing server fails the run instead of skipping it.

Order matters. Bootstrap claims the storage root only while the root holds no
objects (``docs/postgresql-architecture.md`` §9.2), so the empty root is
marked and paired first and the seed is copied in afterwards.

By default Library data still comes from the SQLite seed, and this module
only gives the browser a real session, identity and Library epoch. With
``PRKS_E2E_LIBRARY_BACKEND=postgres`` (PG-C1 #640) the server serves the
Library from this database too (``PRKS_LIBRARY_BACKEND=postgres``): the seed
writes through the PostgreSQL facade as the owner (:func:`seed_postgres_library`),
and the embedded worker runs the jobs the seed queued.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Optional


AUTH_MODE = "loopback-owner"
SESSION_MODE = "session"
OWNER_LOGIN = "owner"
OWNER_DISPLAY_NAME = "E2E Owner"
LIBRARY_NAME = "E2E Library"
# PRKS_E2E_LIBRARY_BACKEND=postgres: Library data on PostgreSQL too.
LIBRARY_BACKEND_ENV = "PRKS_E2E_LIBRARY_BACKEND"
# Every E2E account's password. Loopback-owner never signs in; session-mode
# tests sign in through POST /api/login with it.
PASSWORD = "e2e account passphrase"

_hash_lock = threading.Lock()
_owner_hash: Optional[str] = None


class PostgresUnavailable(RuntimeError):
    """The E2E run has no disposable PostgreSQL server."""


@dataclass(frozen=True)
class E2ELibrary:
    """One server's database and the identity bootstrap gave it."""

    database: str
    conninfo: str
    instance_id: str
    user_id: str
    library_id: str
    auth: str = AUTH_MODE
    # Login name -> user id: the owner and every member added with it.
    accounts: Mapping[str, str] = field(default_factory=dict)

    def server_env(self) -> dict[str, str]:
        """Environment for ``prks_app.py --testing`` on this database.

        With the SQLite Library the embedded worker stays off: its object
        sweep treats every object PostgreSQL does not reference as an
        orphan, and the seeded PDFs are referenced only by SQLite. With the
        PostgreSQL Library it runs, so the seed's text and research jobs
        complete.
        """
        env = {"PRKS_AUTH": self.auth, "PRKS_DATABASE_URL": self.conninfo}
        if postgres_library():
            env["PRKS_LIBRARY_BACKEND"] = "postgres"
        else:
            env["PRKS_EMBEDDED_WORKER"] = "0"
        return env

    def drop(self) -> None:
        from tests.pg.harness import get_harness

        get_harness().drop_database(self.database)


def postgres_library(environ=None) -> bool:
    """Whether this run serves Library data from PostgreSQL (``PRKS_E2E_LIBRARY_BACKEND``)."""
    value = ((os.environ if environ is None else environ).get(LIBRARY_BACKEND_ENV) or "").strip()
    if not value:
        return False
    if value == "postgres":
        return True
    raise ValueError(f"{LIBRARY_BACKEND_ENV} must be unset or 'postgres'")


def seed_postgres_library(seed_fn, storage_root: str, library: E2ELibrary) -> dict:
    """Run ``seed_fn(storage_root)`` against ``library``'s PostgreSQL Library, as its owner.

    The seed functions (``tests/e2e/fixtures.py``) call the public method
    names both stores share, so the same seed fills either one; here they
    reach :class:`~backend.pg_facade.PostgresLibrary` acting for a session
    of the bootstrap owner. Managed PDFs are copied into the root first, as
    the SQLite seed copies them, and adopted by the create.
    """
    from backend.db.config import DatabaseConfig
    from backend.db.database import Database
    from backend.domain.identity import sessions
    from backend.domain.identity.request_context import RequestContext
    from backend.pg_facade import PostgresLibrary
    from backend.pg_facade.context import acting_as
    from backend.storage.config import StorageConfig
    from tests.e2e.fixtures import seeding_into

    db = Database(DatabaseConfig(conninfo=library.conninfo, role="test", pool_min=0, pool_max=2))
    try:
        def owner_session(tx):
            epoch = tx.execute(
                "SELECT epoch FROM prks.libraries WHERE id = %s", (library.library_id,)
            ).fetchone()["epoch"]
            return sessions.create_session(tx, user_id=library.user_id, library_id=library.library_id), epoch

        new, epoch = db.command(owner_session)
        valid = sessions.resolve_session(db, new.token.reveal())
        if valid is None:  # pragma: no cover - the session was just created
            raise RuntimeError("the E2E seed session does not resolve")
        facade = PostgresLibrary(db, storage=StorageConfig.for_testing(os.path.realpath(storage_root)))
        with acting_as(RequestContext.for_session(valid, epoch=epoch)), seeding_into(facade):
            return seed_fn(storage_root) or {}
    finally:
        db.close()


def require_server() -> None:
    # Imported here: tests.pg.harness needs psycopg, which E2E planning and
    # the unit tests of this harness do not install.
    from tests.pg.harness import ENV_TEST_DATABASE_URL, admin_conninfo_from_env

    if admin_conninfo_from_env() is None:
        raise PostgresUnavailable(
            f"E2E needs a disposable PostgreSQL 18 server: set {ENV_TEST_DATABASE_URL} "
            "to its 'postgres' maintenance database (see scripts/test-postgres)"
        )


def _password_hash() -> str:
    """One Argon2id hash per process; hashing is deliberately slow."""
    global _owner_hash
    with _hash_lock:
        if _owner_hash is None:
            from backend.domain.identity import passwords

            _owner_hash = passwords.hash_password(PASSWORD)
        return _owner_hash


def provision_library(
    storage_root: str, *, auth: str = AUTH_MODE, members: Sequence[str] = ()
) -> E2ELibrary:
    """A new database, bootstrapped with one owner and paired with ``storage_root``.

    ``storage_root`` must be the empty directory the server will use. With
    ``auth="session"`` the server signs accounts in by password
    (:data:`PASSWORD`); ``members`` adds one editor of the Library per login
    name, so a test can sign two accounts into one browser.
    """
    if auth not in (AUTH_MODE, SESSION_MODE):
        raise ValueError(f"unknown E2E auth mode {auth!r}")
    if members and auth != SESSION_MODE:
        raise ValueError("members need auth='session': loopback-owner acts only as the owner")
    require_server()
    from backend.db.config import DatabaseConfig
    from backend.db.database import Database
    from backend.domain.identity import bootstrap_tokens, root_pairing
    from backend.storage.config import StorageConfig
    from backend.storage.root_binding import acquire_shared_root_lease, open_storage_root
    from tests.pg.harness import get_harness

    root = os.path.realpath(storage_root)
    # Mark the new root the way the first start would, then let go of it.
    open_storage_root(StorageConfig.for_testing(root), register=False).release()
    harness = get_harness()
    name = harness.create_database("e2e")
    try:
        conninfo = harness.conninfo(name)
        db = Database(DatabaseConfig(conninfo=conninfo, role="test", pool_min=0, pool_max=2))
        try:
            owner = root_pairing.OwnerSpec(
                login_name=OWNER_LOGIN,
                display_name=OWNER_DISPLAY_NAME,
                library_name=LIBRARY_NAME,
                password_hash=_password_hash(),
            )
            lease = acquire_shared_root_lease(root)
            try:
                result = root_pairing.bootstrap(
                    db,
                    owner,
                    token=bootstrap_tokens.issue_token(db).token,
                    root=root,
                    lease=lease,
                    testing=True,
                )
            finally:
                lease.release()
            accounts = {OWNER_LOGIN: result.user_id}
            for login in members:
                accounts[login] = _add_member(db, result.library_id, login)
        finally:
            db.close()
    except BaseException:
        harness.drop_database(name)
        raise
    return E2ELibrary(
        database=name,
        conninfo=conninfo,
        instance_id=result.instance_id,
        user_id=result.user_id,
        library_id=result.library_id,
        auth=auth,
        accounts=accounts,
    )


def _add_member(db, library_id: str, login: str) -> str:
    """One more editor of the Library, signing in with :data:`PASSWORD`."""
    from backend import entity_ids

    user_id = entity_ids.generate("U")

    def add(tx) -> None:
        tx.execute(
            "INSERT INTO prks.users (id, login_name, display_name, password_hash) VALUES (%s, %s, %s, %s)",
            (user_id, login, login, _password_hash()),
        )
        tx.execute(
            "INSERT INTO prks.library_memberships (library_id, user_id, role) VALUES (%s, %s, 'editor')",
            (library_id, user_id),
        )

    db.command(add)
    return user_id
