"""The job worker (``docs/postgresql-architecture.md`` §12.1, §12.2, §11.1; L-J-1 #627).

:class:`Worker` is the loop one worker runs: wait until the process may
claim, then claim and run one job at a time until stopped. Between jobs it
waits for a ``NOTIFY prks_jobs`` (every enqueue sends one at COMMIT), with a
poll as the fallback, and on each idle tick it runs the reaper and, at most
once per interval, enqueues the deduplicated ``jobs.prune``.

**Start.** The worker claims nothing until its start check says so. The
``prks worker`` command (:func:`main`) takes the root's **shared**
``root.lock`` for the whole run and runs ``root_pairing.start_process(…,
bump=True)`` under it: while setup is ``unconfigured`` or ``bootstrapping``
it reports :attr:`~backend.domain.identity.root_pairing.StartStatus.SETUP_PENDING`,
and the worker checks again on its poll; once setup is ready the check
reconciles and bumps once, and the worker starts claiming. A refusal that is
not retryable (a foreign root, a stale copy, a testing mismatch) stops it
before it claims anything.

**Graceful stop** (SIGTERM or SIGINT, or :meth:`Worker.stop`): the worker
claims no further job; the running job may finish within ``stop_grace``.
Past that deadline its lease is released (the job is ``queued`` again at
once, without spending an attempt) and the worker returns; the handler's
later fenced transactions are refused because the lease is gone.

**Errors.** A database that cannot be reached for a claim, the reaper or the
prune enqueue is logged as a code and retried on the next poll; it never
stops the worker.

Until the PostgreSQL cutover the SQLite web process holds ``root.lock``
exclusively, so ``prks worker`` cannot run beside it; the web process will
run its own embedded worker instead (PR 3/3).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from types import FrameType
from typing import Any, Optional, TextIO

import psycopg

from backend.db.config import load_database_config
from backend.db.database import Database
from backend.db.errors import DatabaseConfigError, DatabaseError
from backend.domain.identity import root_pairing
from backend.domain.identity.errors import RootPairingRefused
from backend.jobs import queue
from backend.jobs.diagnostics import WorkerStats
from backend.jobs.handlers import REGISTRY, HandlerRegistry
from backend.jobs.prune import PRUNE_KIND  # importing it registers jobs.prune
from backend.jobs.queue import DEFAULT_BACKOFF, DEFAULT_LEASE, Backoff, ClaimedJob
from backend.jobs.runner import JobRun, Outcome, run_job
from backend.storage.errors import StorageRootError
from backend.storage.file_lock import SharedFileLock
from backend.storage.root_binding import acquire_shared_root_lease

LOGGER = logging.getLogger("prks.jobs")

LISTEN_APPLICATION_NAME = "prks-worker-listen"

# The start check: True once the process may claim, False while setup is
# pending; raises RootPairingRefused when the process must not run.
StartCheck = Callable[[], bool]

_COUNTER = {
    Outcome.SUCCEEDED: "succeeded",
    Outcome.RETRYING: "retried",
    Outcome.CANCELLED: "cancelled",
    Outcome.FAILED: "failed",
    Outcome.LOST: "lost",
}


@dataclass(frozen=True)
class WorkerSettings:
    lease: timedelta = DEFAULT_LEASE
    poll: timedelta = timedelta(seconds=5)  # the fallback when no NOTIFY arrives
    stop_grace: timedelta = timedelta(seconds=30)
    prune_every: timedelta = timedelta(hours=1)
    backoff: Backoff = DEFAULT_BACKOFF

    def __post_init__(self) -> None:
        for name in ("lease", "poll", "prune_every"):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.stop_grace < timedelta(0):
            raise ValueError("stop_grace must not be negative")


def default_worker_name() -> str:
    """``host:pid``; recorded as ``locked_by`` (no path, no user name)."""
    return f"{socket.gethostname()[:100]}:{os.getpid()}"


class _Listener:
    """``LISTEN prks_jobs`` on a dedicated connection; sets ``wake`` on each notification.

    A pooled connection can be recycled, so the listener owns its own. When
    the connection fails it reconnects on the next poll; meanwhile the
    worker's poll fallback keeps claiming.
    """

    def __init__(self, conninfo: str, wake: threading.Event, *, retry: float) -> None:
        self._conninfo = conninfo
        self._wake = wake
        self._retry = retry
        self._closing = threading.Event()
        self.listening = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="prks-job-listen", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._closing.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._closing.is_set():
            try:
                with psycopg.connect(
                    self._conninfo, autocommit=True, application_name=LISTEN_APPLICATION_NAME
                ) as conn:
                    conn.execute("LISTEN " + queue.NOTIFY_CHANNEL)
                    self.listening.set()
                    # A notification sent while the worker was not listening
                    # is covered by the claim that follows any wake.
                    self._wake.set()
                    while not self._closing.is_set():
                        for _ in conn.notifies(timeout=0.5, stop_after=1):
                            self._wake.set()
            except psycopg.Error as exc:
                LOGGER.warning("job_listen_failed error=%s", type(exc).__name__)
            self.listening.clear()
            self._closing.wait(self._retry)


class Worker:
    """Claims and runs jobs, one at a time, until stopped."""

    def __init__(
        self,
        db: Database,
        *,
        name: Optional[str] = None,
        registry: HandlerRegistry = REGISTRY,
        settings: WorkerSettings = WorkerSettings(),
        start_check: Optional[StartCheck] = None,
        stats: Optional[WorkerStats] = None,
    ) -> None:
        self.db = db
        self.name = name or default_worker_name()
        self.registry = registry
        self.settings = settings
        self.stats = stats or WorkerStats()
        self._start_check = start_check
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._job_done = threading.Event()
        self._last_prune: Optional[float] = None
        self.started = threading.Event()  # the start check passed; the worker claims
        self.idle = threading.Event()  # set while it waits for a NOTIFY or the poll

    def stop(self) -> None:
        """Claim nothing more; the running job may finish within ``stop_grace``."""
        self._stop.set()
        self._wake.set()
        self._job_done.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        """Run until :meth:`stop`; raises ``RootPairingRefused`` when the start check refuses."""
        poll = self.settings.poll.total_seconds()
        listener = _Listener(self.db.config.conninfo, self._wake, retry=poll)
        listener.start()
        try:
            if not self._wait_until_started():
                return
            self.started.set()
            listener.listening.wait(poll)  # a NOTIFY after this point is not missed
            while not self._stop.is_set():
                self._wake.clear()
                job = self._claim()
                if job is not None:
                    self._run(job)
                    continue
                self._idle_tick()
                self.idle.set()
                self._wake.wait(poll)
                self.idle.clear()
        finally:
            listener.close()

    # -- steps --------------------------------------------------------------------

    def _wait_until_started(self) -> bool:
        poll = self.settings.poll.total_seconds()
        waiting_for: Optional[str] = None
        while not self._stop.is_set():
            if self._start_check is None:
                return True
            try:
                if self._start_check():
                    return True
                reason = "setup_pending"
            except RootPairingRefused as exc:
                if not exc.retryable:
                    raise
                reason = exc.reason
            if reason != waiting_for:  # once per reason, not on every poll
                LOGGER.warning("worker_waiting reason=%s", reason)
                waiting_for = reason
            self._stop.wait(poll)
        return False

    def _claim(self) -> Optional[ClaimedJob]:
        try:
            job = queue.claim(self.db, self.name, lease=self.settings.lease)
        except DatabaseError as exc:
            self._error("claim", exc)
            return None
        if job is not None:
            self.stats.add("claimed")
        return job

    def _run(self, job: ClaimedJob) -> None:
        """Run ``job`` on its own thread so a stop can give it a deadline."""
        self._job_done.clear()
        if self._stop.is_set():  # stopped between the claim and here
            self._job_done.set()
        finished = threading.Event()
        started = threading.Event()
        runs: list[JobRun] = []

        def begin(run: JobRun) -> None:
            runs.append(run)
            started.set()

        def body() -> None:
            try:
                outcome = run_job(
                    self.db, job, registry=self.registry, lease=self.settings.lease,
                    backoff=self.settings.backoff, on_start=begin,
                )
                self.stats.add(_COUNTER[outcome])
            except DatabaseError as exc:  # the outcome could not be recorded; the lease expires
                self._error("record", exc)
            finally:
                started.set()
                finished.set()
                self._job_done.set()

        thread = threading.Thread(target=body, name="prks-job", daemon=True)
        thread.start()
        self._job_done.wait()
        if finished.is_set():
            thread.join()
            return
        # Stopping: give the job its grace period, then give it back.
        if finished.wait(self.settings.stop_grace.total_seconds()):
            thread.join()
            return
        started.wait()  # set before the handler runs, so promptly
        # Abandoned whether or not the release is recorded: its heartbeat
        # stops and its fenced transactions are refused from now on, so a
        # lease the release could not clear simply expires.
        for run in runs:
            run.mark_lost()
        self._release(job)

    def _release(self, job: ClaimedJob) -> None:
        try:
            state = queue.release(self.db, job)
        except DatabaseError as exc:
            # The handler may hold the row in its own transaction; the lease
            # then simply expires and the job is reclaimed.
            self._error("release", exc)
            return
        if state is not None:
            self.stats.add("released")
        LOGGER.warning(
            "job_released job_id=%s kind=%s state=%s",
            job.id, job.kind, state.value if state is not None else "lost",
        )

    def _idle_tick(self) -> None:
        try:
            reaped = queue.reap(self.db)
        except DatabaseError as exc:
            self._error("reap", exc)
        else:
            if reaped:
                self.stats.add("reaped", reaped)
                LOGGER.warning("jobs_reaped count=%s", reaped)
        now = time.monotonic()
        every = self.settings.prune_every.total_seconds()
        if self._last_prune is not None and now - self._last_prune < every:
            return
        if self.registry.get(PRUNE_KIND) is None:
            return
        try:
            self.db.command(
                lambda tx: queue.enqueue(
                    tx, PRUNE_KIND, dedupe_key=PRUNE_KIND, singleton_key=PRUNE_KIND, priority=-10
                )
            )
        except DatabaseError as exc:
            self._error("prune_enqueue", exc)
            return
        self._last_prune = now

    def _error(self, step: str, exc: DatabaseError) -> None:
        self.stats.add("errors")
        LOGGER.warning("worker_error step=%s error=%s", step, queue.error_code(exc))


# --- prks worker ----------------------------------------------------------------------

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

DatabaseFactory = Callable[[Mapping[str, str]], Database]


def _open_database(environ: Mapping[str, str]) -> Database:
    return Database(load_database_config(environ, role="worker"))


def root_start_check(
    db: Database, *, root: str, lease: SharedFileLock, testing: bool
) -> StartCheck:
    """§11.1 for a worker: check and bump once setup is ready; pending until then."""

    def check() -> bool:
        started = root_pairing.start_process(db, root=root, lease=lease, testing=testing, bump=True)
        return started.status is root_pairing.StartStatus.READY

    return check


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prks worker", description="Run the PRKS job worker.")
    parser.add_argument("--root", required=True, help="the bound PRKS data root")
    parser.add_argument("--testing", action="store_true", help="a disposable testing instance")
    return parser


def _install_signals(worker: Worker) -> None:
    def handle(_signum: int, _frame: Optional[FrameType]) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    stderr: Optional[TextIO] = None,
    open_database: DatabaseFactory = _open_database,
    settings: WorkerSettings = WorkerSettings(),
    registry: HandlerRegistry = REGISTRY,
    on_worker: Callable[[Worker], Any] = _install_signals,
) -> int:
    """``prks worker --root ROOT [--testing]``; returns the exit code.

    Output is fixed text: no connection string, path or driver message.
    """
    env = os.environ if environ is None else environ
    err = stderr or sys.stderr
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    root = os.path.realpath(os.path.abspath(args.root))
    try:
        db = open_database(env)
    except DatabaseConfigError as exc:
        print(f"prks worker: {exc.message}", file=err)
        return EXIT_USAGE
    except DatabaseError:
        print("prks worker: the database cannot be reached.", file=err)
        return EXIT_REFUSED
    try:
        lease = acquire_shared_root_lease(root)
    except StorageRootError as exc:
        db.close()
        print(f"prks worker: the data root cannot be used ({exc.reason}).", file=err)
        return EXIT_REFUSED
    try:
        with db:
            worker = Worker(
                db,
                registry=registry,
                settings=settings,
                start_check=root_start_check(db, root=root, lease=lease, testing=bool(args.testing)),
            )
            on_worker(worker)
            LOGGER.info("worker_starting name=%s", worker.name)
            worker.run()
    except RootPairingRefused as exc:
        print(f"prks worker: {exc.message}", file=err)
        return EXIT_REFUSED
    finally:
        lease.release()
    LOGGER.info("worker_stopped")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
