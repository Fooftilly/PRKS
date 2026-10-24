"""Shared fixtures for the jobs runtime tests (L-J-1 #627)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Optional

import psycopg

from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.queue import ClaimedJob
from backend.jobs.runner import JobRun
from tests.pg.scope_fixtures import ScopeTestCase

# An idempotent Library edit: set one scope's revision to the payload's value.
SET_KIND = "test.set_revision"
SET_SQL = (
    "UPDATE prks.entity_revisions SET revision = %(value)s "
    "WHERE library_id = %(library_id)s AND scope_type = 'work' AND scope_id = %(scope_id)s"
)

# Short enough that tests never wait for one; expiry is forced with SQL instead.
LEASE = timedelta(seconds=30)
NO_BACKOFF = queue.Backoff(base=timedelta(0), cap=timedelta(0))


def set_revision(run: JobRun) -> None:
    payload = run.payload

    def edit(scope: LibraryScope) -> None:
        scope.execute(SET_SQL, {"value": payload["value"], "scope_id": payload["scope_id"]})

    run.library_command(Capability.EDIT_LIBRARY, edit)


class JobsTestCase(ScopeTestCase):
    """Two Libraries with revision rows (``ScopeTestCase``), plus job helpers."""

    def setUp(self) -> None:
        super().setUp()
        self.registry = HandlerRegistry()
        self.registry.register(SET_KIND, set_revision)

    # -- enqueue -----------------------------------------------------------------

    def enqueue_library(
        self, library_id: Optional[str] = None, *, kind: str = SET_KIND,
        payload: Optional[dict[str, Any]] = None, epoch: Optional[int] = None, **kwargs: Any,
    ) -> Optional[uuid.UUID]:
        """Enqueue through a real ``library_command`` of an owner (what a domain command does)."""
        library = library_id or self.lib_a
        owner = self.owner_of(library)
        ctx = self.ctx(owner, library, epoch=epoch if epoch is not None else self.epoch(library))
        return library_command(
            self.db, ctx, Capability.EDIT_LIBRARY,
            lambda scope: queue.enqueue_library_job(
                scope, kind, payload if payload is not None else {"scope_id": "a1", "value": 7}, **kwargs
            ),
        )

    def enqueue_instance(self, kind: str = "test.instance", **kwargs: Any) -> Optional[uuid.UUID]:
        return self.db.command(lambda tx: queue.enqueue(tx, kind, **kwargs))

    def owner_of(self, library_id: str) -> str:
        owners = getattr(self, "_owners", None)
        if owners is None:
            owners = self._owners = {}
        if library_id not in owners:
            owners[library_id] = self.member(f"Owner{len(owners)}", "owner", library_id)
        return owners[library_id]

    # -- claims and state --------------------------------------------------------

    def claim(self, worker: str = "w1") -> Optional[ClaimedJob]:
        return queue.claim(self.db, worker, lease=LEASE)

    def expire(self, job_id: uuid.UUID) -> None:
        """The worker died: its lease runs out (forced, so no test waits for time)."""
        self.run_sql(
            "UPDATE prks.jobs SET locked_until = now() - interval '1 second' WHERE id = %s", (job_id,)
        )

    def row(self, job_id: uuid.UUID) -> dict[str, Any]:
        rows = self.run_sql("SELECT * FROM prks.jobs WHERE id = %s", (job_id,))
        self.assertEqual(len(rows), 1)
        return rows[0]

    def state(self, job_id: uuid.UUID) -> str:
        return str(self.row(job_id)["state"])

    def running(self) -> list[dict[str, Any]]:
        return self.run_sql("SELECT id, singleton_key FROM prks.jobs WHERE state = 'running'")

    def make_ready(self, job_id: uuid.UUID) -> None:
        self.run_sql("UPDATE prks.jobs SET run_after = now() WHERE id = %s", (job_id,))

    # -- concurrency helpers -----------------------------------------------------

    def lock_waiters(self) -> int:
        """Sessions of the test pool blocked on a lock right now."""
        rows = self.run_sql(
            "SELECT count(*) AS n FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        return int(rows[0]["n"])

    def in_thread(self, fn: Callable[[], object]) -> tuple[threading.Thread, list[object], list[BaseException]]:
        result: list[object] = []
        error: list[BaseException] = []

        def body() -> None:
            try:
                result.append(fn())
            except BaseException as exc:  # noqa: BLE001 - asserted by the test
                error.append(exc)

        thread = threading.Thread(target=body)
        thread.start()
        return thread, result, error

    def open_tx(self) -> psycopg.Connection[Any]:
        """A raw connection with an open transaction, for holding uncommitted work."""
        conn = psycopg.connect(self.harness.conninfo(self.name))
        self.addCleanup(conn.close)
        return conn
