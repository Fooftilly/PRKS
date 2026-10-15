"""Shared fixtures for the authorization and Library-scope tests (L-ID-1 #626, PR 4/5)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Optional

import psycopg

from backend.domain.identity import sessions
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LIBRARY_LOCK_KEY_SQL, LibraryScope
from backend.domain.identity.request_context import RequestContext
from tests.pg.identity_fixtures import IdentityTestCase

# A representative Library-scoped table that exists today: one revision row per scope.
BUMP_SQL = (
    "UPDATE prks.entity_revisions SET revision = revision + 1 "
    "WHERE library_id = %(library_id)s AND scope_type = 'work' AND scope_id = %(scope_id)s "
    "RETURNING revision"
)
READ_SQL = (
    "SELECT scope_id, revision FROM prks.entity_revisions "
    "WHERE library_id = %(library_id)s ORDER BY scope_id"
)


def bump(scope_id: str) -> Callable[[LibraryScope], Optional[int]]:
    """A representative edit: advance one scope's revision; ``None`` if not in this Library."""

    def run(scope: LibraryScope) -> Optional[int]:
        row = scope.execute(BUMP_SQL, {"scope_id": scope_id}).fetchone()
        return None if row is None else int(row["revision"])

    return run


def read_all(scope: LibraryScope) -> list[tuple[str, int]]:
    return [(row["scope_id"], int(row["revision"])) for row in scope.execute(READ_SQL).fetchall()]


class ScopeTestCase(IdentityTestCase):
    """Two Libraries with real revision rows, and helpers for contexts and restores."""

    def setUp(self) -> None:
        super().setUp()
        self.lib_a = self.library("A")
        self.lib_b = self.library("B")
        for library_id, scope_ids in ((self.lib_a, ("a1", "a2")), (self.lib_b, ("b1",))):
            for scope_id in scope_ids:
                self.run_sql(
                    "INSERT INTO prks.entity_revisions (library_id, scope_type, scope_id, revision) "
                    "VALUES (%s, 'work', %s, 1)",
                    (library_id, scope_id),
                )

    # -- people and contexts ---------------------------------------------------

    def member(self, name: str, role: str, library_id: Optional[str] = None) -> str:
        return self.user(name, library=library_id or self.lib_a, role=role)

    def session_for(self, user_id: str, library_id: Optional[str] = None) -> sessions.ValidSession:
        new = self.db.command(
            lambda tx: sessions.create_session(tx, user_id=user_id, library_id=library_id or self.lib_a)
        )
        valid = sessions.resolve_session(self.db, new.token.reveal())
        assert valid is not None
        return valid

    def ctx(self, user_id: str, library_id: Optional[str] = None, *, epoch: object = 1,
            device_id: object = None) -> RequestContext:
        return RequestContext.for_session(
            self.session_for(user_id, library_id), epoch=epoch, device_id=device_id
        )

    def device(self, user_id: str) -> uuid.UUID:
        device_id = uuid.uuid4()
        self.run_sql("INSERT INTO prks.devices (id, user_id) VALUES (%s, %s)", (device_id, user_id))
        return device_id

    def job(self, library_id: str, epoch: int, *, state: str = "running") -> tuple[uuid.UUID, uuid.UUID]:
        lease_id = uuid.uuid4()
        rows = self.run_sql(
            "INSERT INTO prks.jobs (library_id, library_epoch, kind, state, lease_id) "
            "VALUES (%s, %s, 'test.edit', %s, %s) RETURNING id",
            (library_id, epoch, state, lease_id),
        )
        return rows[0]["id"], lease_id

    # -- state ------------------------------------------------------------------

    def revisions(self) -> dict[tuple[str, str], int]:
        return {
            (row["library_id"], row["scope_id"]): row["revision"]
            for row in self.run_sql("SELECT library_id, scope_id, revision FROM prks.entity_revisions")
        }

    def epoch(self, library_id: Optional[str] = None) -> int:
        rows = self.run_sql("SELECT epoch FROM prks.libraries WHERE id = %s", (library_id or self.lib_a,))
        return int(rows[0]["epoch"])

    def restore(self, library_id: Optional[str] = None) -> None:
        """A committed restore (R3 + R4 reduced to what fencing sees): the epoch moves on."""
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_lock(" + LIBRARY_LOCK_KEY_SQL + ")", (library_id or self.lib_a,))
            conn.execute("UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s", (library_id or self.lib_a,))
            conn.execute("SELECT pg_advisory_unlock_all()")

    def connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.harness.conninfo(self.name), autocommit=True)

    # -- assertions -------------------------------------------------------------

    def assert_refused(self, refusal: Refusal, fn: Callable[[], object]) -> AccessRefused:
        with self.assertRaises(AccessRefused) as caught:
            fn()
        self.assertIs(caught.exception.refusal, refusal)
        self.assert_private(str(caught.exception))
        return caught.exception

    def assert_private(self, text: str) -> None:
        for secret in (self.lib_a, self.lib_b, "LIB-", "U-", "postgres"):
            self.assertNotIn(secret, text)

    def wait_until(self, condition: Callable[[], bool], what: str) -> None:
        """Wait for a state of the database (not for an amount of time)."""
        deadline = time.monotonic() + 30
        while not condition():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.01)

    def waiting_on_lock(self, conn: psycopg.Connection[Any]) -> Callable[[], bool]:
        pid = conn.info.backend_pid

        def check() -> bool:
            rows = self.run_sql("SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted", (pid,))
            return bool(rows)

        return check


class Paused:
    """Runs ``target`` in a thread; the protected function pauses until released."""

    def __init__(self) -> None:
        self.inside = threading.Event()
        self.release = threading.Event()
        self.result: list[object] = []
        self.error: list[BaseException] = []

    def hold(self, fn: Callable[[LibraryScope], object]) -> Callable[[LibraryScope], object]:
        def run(scope: LibraryScope) -> object:
            value = fn(scope)
            self.inside.set()
            if not self.release.wait(30):
                raise TimeoutError("never released")
            return value

        return run

    def start(self, target: Callable[[], object]) -> threading.Thread:
        def body() -> None:
            try:
                self.result.append(target())
            except BaseException as exc:  # noqa: BLE001 - asserted by the test
                self.error.append(exc)

        thread = threading.Thread(target=body)
        thread.start()
        return thread
