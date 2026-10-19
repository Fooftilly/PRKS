"""Run one claimed job (``docs/postgresql-architecture.md`` §12.1, §11.4; L-J-1 #627).

:func:`run_job` runs the handler of a claimed job outside the claim
transaction, extends the lease from a heartbeat thread while it runs, and
then records the outcome with the lease-fenced statements of
:mod:`backend.jobs.queue`: ``succeeded``; a handled failure (``queued`` again
with backoff, ``cancelled`` in favour of a queued twin, or ``failed`` once
attempts are exhausted); or nothing at all when the lease was lost, in which
case the result is discarded.

**Every transaction a handler opens is fenced** by the job's lease, and a
Library job's also by its epoch:

- a Library job writes through :meth:`JobRun.library_command`, which is
  :func:`~backend.domain.identity.library_scope.library_command` with
  :meth:`JobRun.context`, a ``RequestContext.for_job`` whose Library and
  epoch come from the job row. ``authorize()`` locks the job row with its
  lease ``FOR SHARE`` inside that transaction, and the command refuses an
  epoch other than the one recorded at enqueue (a restore advanced it);
- an instance job (no Library) writes through :meth:`JobRun.command`, which
  locks the job row with its lease ``FOR SHARE`` first in the same
  transaction.

Holding the job row ``FOR SHARE`` until COMMIT makes a reclaim, the reaper
or a restore's cancellation wait for that transaction, so a handler
transaction either commits while the lease is still this worker's or does
not commit at all. Work a lost worker already committed is covered by the
idempotency rule: handlers are idempotent (§12.1).

**Progress is reported between transactions.** :meth:`JobRun.heartbeat`
updates the job row on another connection, and that update waits for the
``FOR SHARE`` lock the handler's own open transaction holds, so a heartbeat
from inside :meth:`JobRun.command` or :meth:`JobRun.library_command` would
wait on itself; it is refused instead. A batched handler commits a batch,
then calls ``heartbeat(progress=…)``.
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, Optional, TypeVar

from backend.db.database import Database, Isolation, Transaction
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.identity.request_context import RequestContext
from backend.jobs import queue
from backend.jobs.handlers import REGISTRY, HandlerRegistry
from backend.jobs.queue import DEFAULT_BACKOFF, DEFAULT_LEASE, Backoff, ClaimedJob, JobState, LeaseLost

LOGGER = logging.getLogger("prks.jobs")

T = TypeVar("T")

# Two statements: lock the job row first, then read the clock. A single
# SELECT computes its output before LockRows waits for the row lock, and
# re-evaluates it after the wait only when the holder changed the row, so a
# lease that ran out while a holder that changed nothing kept the lock would
# still read as live.
_LEASE_LOCK_SQL = (
    "SELECT locked_until FROM prks.jobs WHERE id = %s AND lease_id = %s AND state = 'running' FOR SHARE"
)
_LEASE_LIVE_SQL = "SELECT %s::timestamptz > clock_timestamp() AS live"


class Outcome(enum.Enum):
    """What :func:`run_job` recorded for the job."""

    SUCCEEDED = "succeeded"
    RETRYING = "retrying"  # back to queued, with backoff
    CANCELLED = "cancelled"  # a queued twin does the same work
    FAILED = "failed"  # attempts exhausted
    LOST = "lost"  # the lease was lost; nothing recorded, the result discarded


_SETTLED = {
    JobState.QUEUED: Outcome.RETRYING,
    JobState.CANCELLED: Outcome.CANCELLED,
    JobState.FAILED: Outcome.FAILED,
}


class JobRun:
    """What a handler gets: the job, its fenced transactions and its heartbeat."""

    def __init__(self, db: Database, job: ClaimedJob, *, lease: timedelta) -> None:
        self.db = db
        self.job = job
        self._lease = lease
        self._lost = threading.Event()
        # Set while this thread is inside one of the run's fenced transactions.
        self._fenced = threading.local()

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.job.payload

    @property
    def lost(self) -> bool:
        """The lease is known to be lost; the handler should stop."""
        return self._lost.is_set()

    def mark_lost(self) -> None:
        self._lost.set()

    def check(self) -> None:
        """Raise :class:`LeaseLost` once the lease is known to be lost (a cheap, local check)."""
        if self._lost.is_set():
            raise LeaseLost()

    def context(self) -> RequestContext:
        """The context of a Library job, for ``library_query`` (writes use :meth:`library_command`).

        Raises :class:`LeaseLost` when the lease is no longer this worker's.
        """
        if self.job.library_id is None:
            raise ValueError("an instance job has no Library context; use JobRun.command")
        self.check()
        ctx = RequestContext.for_job(self.db, self.job.id, self.job.lease_id)
        if ctx is None:
            self.mark_lost()
            raise LeaseLost()
        return ctx

    def command(
        self, fn: Callable[[Transaction], T], *, isolation: Isolation = Isolation.READ_COMMITTED
    ) -> T:
        """Run ``fn`` in one transaction of an instance job, fenced by the lease.

        The job row is locked with its lease ``FOR SHARE`` before ``fn``
        runs; raises :class:`LeaseLost` with nothing written when the lease is
        gone or expired. A Library job uses :meth:`library_command` instead,
        which also checks the epoch.
        """
        if self.job.library_id is not None:
            raise ValueError("a Library job writes through JobRun.library_command")
        self.check()

        def run(tx: Transaction) -> T:
            row = tx.execute(_LEASE_LOCK_SQL, (self.job.id, self.job.lease_id)).fetchone()
            if row is None:
                raise LeaseLost()
            live = tx.execute(_LEASE_LIVE_SQL, (row["locked_until"],)).fetchone()
            if live is None or not live["live"]:
                raise LeaseLost()
            return fn(tx)

        try:
            return self._in_fenced(lambda: self.db.command(run, isolation=isolation))
        except LeaseLost:
            self.mark_lost()
            raise

    def library_command(
        self,
        capability: Capability,
        fn: Callable[[LibraryScope], T],
        *,
        isolation: Isolation = Isolation.READ_COMMITTED,
    ) -> T:
        """Run ``fn`` as ``library_command`` for this Library job, fenced by its lease and epoch.

        Raises :class:`LeaseLost` when the lease is gone, and ``AccessRefused``
        (``LIBRARY_EPOCH_CHANGED``) with nothing written after a restore.
        """
        ctx = self.context()
        return self._in_fenced(
            lambda: library_command(self.db, ctx, capability, fn, isolation=isolation)
        )

    def _in_fenced(self, fn: Callable[[], T]) -> T:
        self._fenced.depth = getattr(self._fenced, "depth", 0) + 1
        try:
            return fn()
        finally:
            self._fenced.depth -= 1

    def heartbeat(self, progress: Optional[Mapping[str, Any]] = None) -> None:
        """Extend the lease now (and record ``progress``); raises :class:`LeaseLost` when it is gone.

        Call it between transactions, never inside :meth:`command` or
        :meth:`library_command`: the update would wait on the job row lock
        that transaction holds.
        """
        if getattr(self._fenced, "depth", 0):
            raise RuntimeError("report progress between transactions, not inside a fenced one")
        self.check()
        if not queue.heartbeat(self.db, self.job, lease=self._lease, progress=progress):
            self.mark_lost()
            raise LeaseLost()


class _Heartbeat:
    """Extends the lease every ``interval`` until stopped; marks the run lost when a beat matches nothing."""

    def __init__(self, run: JobRun, interval: timedelta) -> None:
        self._run = run
        self._interval = interval.total_seconds()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="prks-job-heartbeat", daemon=True)

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def _beat(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._run.heartbeat()
            except LeaseLost:
                return
            except Exception as exc:  # the database may be briefly unavailable; try again next beat
                LOGGER.warning(
                    "job_heartbeat_failed job_id=%s error=%s", self._run.job.id, queue.error_code(exc)
                )


def run_job(
    db: Database,
    job: ClaimedJob,
    *,
    registry: HandlerRegistry = REGISTRY,
    lease: timedelta = DEFAULT_LEASE,
    heartbeat_every: Optional[timedelta] = None,
    backoff: Backoff = DEFAULT_BACKOFF,
) -> Outcome:
    """Run ``job``'s handler and record its outcome; never raises for a handler error.

    ``lease`` must be the lease the job was claimed with. The heartbeat runs
    every third of it unless ``heartbeat_every`` says otherwise.
    """
    interval = heartbeat_every if heartbeat_every is not None else lease / 3
    if lease <= timedelta(0) or interval <= timedelta(0):
        # A zero wait would make the heartbeat thread spin on the database.
        raise ValueError("the lease and the heartbeat interval must be positive")
    run = JobRun(db, job, lease=lease)
    fn = registry.get(job.kind)
    if fn is None:
        return _failed(db, job, "unknown_kind", backoff)
    try:
        with _Heartbeat(run, interval):
            fn(run)
    except LeaseLost:
        return _log(job, Outcome.LOST)
    except Exception as exc:
        return _failed(db, job, queue.error_code(exc), backoff)
    if not queue.complete(db, job):
        return _log(job, Outcome.LOST)
    return _log(job, Outcome.SUCCEEDED)


def _failed(db: Database, job: ClaimedJob, error: str, backoff: Backoff) -> Outcome:
    state = queue.fail(db, job, error, backoff=backoff)
    outcome = Outcome.LOST if state is None else _SETTLED[state]
    return _log(job, outcome, error)


def _log(job: ClaimedJob, outcome: Outcome, error: Optional[str] = None) -> Outcome:
    # Metadata only: the job id, its kind, the outcome and an error code.
    if outcome is Outcome.SUCCEEDED:
        LOGGER.info("job_succeeded job_id=%s kind=%s attempts=%s", job.id, job.kind, job.attempts)
    else:
        LOGGER.warning(
            "job_%s job_id=%s kind=%s attempts=%s error=%s",
            outcome.value, job.id, job.kind, job.attempts, error or "-",
        )
    return outcome
