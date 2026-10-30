"""A disposable PostgreSQL Library for each E2E server (#629 L-F-1, PR 1/3).

Every E2E ``AppServer`` runs the PostgreSQL-backed authenticated runtime:
``PRKS_AUTH=loopback-owner`` against its own database, created from the
migrated template of :mod:`tests.pg.harness` (``PRKS_TEST_DATABASE_URL``,
which ``scripts/test-postgres`` documents). There is no SQLite fallback: a
missing server fails the run instead of skipping it.

Order matters. Bootstrap claims the storage root only while the root holds no
objects (``docs/postgresql-architecture.md`` §9.2), so the empty root is
marked and paired first and the seed is copied in afterwards.

Library data still comes from the SQLite seed until each PostgreSQL slice
ports its reads and writes (PG-C1 #640); this module only gives the browser
a real session, identity and Library epoch.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional


AUTH_MODE = "loopback-owner"
OWNER_LOGIN = "owner"
OWNER_DISPLAY_NAME = "E2E Owner"
LIBRARY_NAME = "E2E Library"
# Never used to sign in (loopback-owner has no login form); bootstrap only
# needs a valid Argon2id hash.
_OWNER_PASSWORD = "e2e owner passphrase, never used to sign in"

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

    def server_env(self) -> dict[str, str]:
        """Environment for ``prks_app.py --testing`` on this database.

        The embedded worker stays off: its object sweep treats every object
        PostgreSQL does not reference as an orphan, and the seeded PDFs are
        still referenced only by SQLite (PG-C1 #640).
        """
        return {
            "PRKS_AUTH": AUTH_MODE,
            "PRKS_DATABASE_URL": self.conninfo,
            "PRKS_EMBEDDED_WORKER": "0",
        }

    def drop(self) -> None:
        from tests.pg.harness import get_harness

        get_harness().drop_database(self.database)


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

            _owner_hash = passwords.hash_password(_OWNER_PASSWORD)
        return _owner_hash


def provision_library(storage_root: str) -> E2ELibrary:
    """A new database, bootstrapped with one owner and paired with ``storage_root``.

    ``storage_root`` must be the empty directory the server will use.
    """
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
    )
