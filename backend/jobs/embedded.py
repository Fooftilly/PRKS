"""The job worker embedded in the web process (``docs/postgresql-architecture.md`` §12.2, §12.4; L-J-1 #627).

With ``PRKS_AUTH`` set (the PostgreSQL path), ``prks_app`` builds a
:class:`WebJobs` after the web start check and ``backend.server.run_server``
serves through :meth:`WebJobs.serve`. Unless ``PRKS_EMBEDDED_WORKER`` is
``0``, that runs one :class:`~backend.jobs.worker.Worker` on a thread of the
web process, on its own ``worker`` connection pool:

- **Start.** The web process already ran the §11.1 pairing check and the
  start-generation bump before it opened the root, and it holds that root
  for its lifetime, so the embedded worker never bumps again: its start
  check only waits until the database applied exactly this code's schema
  files (§4.4).
- **Stop.** When the server stops (``shutdown()``, or SIGTERM/SIGINT when it
  serves on the main thread) the worker stops gracefully: no new claim, the
  running job may finish within ``stop_grace``, then its lease is released.
  The signal handlers only record the request (see
  :class:`~backend.jobs.worker.SignalStop`); a watcher thread turns it into
  ``shutdown()``.
- **Isolation.** A worker that cannot start, or fails, is logged as a code
  and shown in the diagnostics; it never stops the web process from serving.

``PRKS_EMBEDDED_WORKER=0`` runs no worker here (a separate ``prks worker``
runs the jobs once the cutover allows one beside ``web``). The SQLite
runtime without ``PRKS_AUTH`` never imports this module, so it never loads
psycopg.

:meth:`WebJobs.diagnostics` is the ``GET /api/diagnostics/jobs`` body
(:class:`backend.api_contract.jobs.JobDiagnostics`): the embedded worker's
state and process-local counters, and the queue's depth and age by kind and
state. Counts, ages, kinds and reason codes only.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any, Optional, Protocol

from backend.db.database import Database
from backend.db.errors import DatabaseError
from backend.db.migrate import SCHEMA_DIR
from backend.jobs.diagnostics import queue_depth
from backend.jobs.handlers import REGISTRY, HandlerRegistry
from backend.jobs.worker import (
    SignalStop,
    StartRefused,
    Worker,
    WorkerSettings,
    open_worker_database,
    schema_start_check,
)

LOGGER = logging.getLogger("prks.jobs")

EMBEDDED_WORKER_ENV = "PRKS_EMBEDDED_WORKER"

# How much longer than stop_grace a stop waits for the worker thread: the
# release after the deadline and the listener's close (at most 2 s).
_STOP_MARGIN = 10.0


class EmbeddedWorkerConfigError(ValueError):
    """``PRKS_EMBEDDED_WORKER`` is unusable; the web process does not start."""


def embedded_worker_enabled(environ: Mapping[str, str]) -> bool:
    """``PRKS_EMBEDDED_WORKER``: unset or ``1`` runs the worker, ``0`` does not; nothing else."""
    raw = (environ.get(EMBEDDED_WORKER_ENV) or "").strip()
    if raw in ("", "1"):
        return True
    if raw == "0":
        return False
    raise EmbeddedWorkerConfigError(f"{EMBEDDED_WORKER_ENV} must be 0 or 1.")


class EmbeddedWorker:
    """One worker on a daemon thread of the web process; owns its database pool."""

    def __init__(
        self,
        db: Database,
        *,
        registry: HandlerRegistry = REGISTRY,
        settings: WorkerSettings = WorkerSettings(),
        schema_dir: str = SCHEMA_DIR,
    ) -> None:
        self.db = db
        self.worker = Worker(
            db, registry=registry, settings=settings,
            start_check=schema_start_check(db, schema_dir=schema_dir),
        )
        self._thread = threading.Thread(target=self._run, name="prks-embedded-worker", daemon=True)
        self._refused: Optional[str] = None
        self._finished = threading.Event()

    def start(self) -> None:
        LOGGER.info("embedded_worker_starting name=%s", self.worker.name)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.worker.run()
        except StartRefused as exc:
            self._refused = exc.reason
            LOGGER.error("embedded_worker_refused reason=%s", exc.reason)
        except Exception as exc:  # the web process keeps serving
            self._refused = "worker_error"
            LOGGER.error("embedded_worker_failed error=%s", type(exc).__name__)
        finally:
            self._finished.set()

    def stop(self) -> bool:
        """Stop gracefully and close the pool; False if the thread outlived the wait."""
        self.worker.stop()
        if self._thread.ident is not None:
            self._thread.join(self.worker.settings.stop_grace.total_seconds() + _STOP_MARGIN)
        if self._thread.is_alive():
            # A daemon thread: it ends with the process. Its job's lease expires.
            LOGGER.warning("embedded_worker_stop_timeout")
            return False
        self.db.close()
        LOGGER.info("embedded_worker_stopped")
        return True

    def status(self) -> dict[str, Any]:
        """The ``embedded_worker`` part of the diagnostics body."""
        worker = self.worker
        reason: Optional[str] = None
        if self._refused is not None:
            state, reason = "refused", self._refused
        elif self._finished.is_set():
            state = "stopped"
        elif worker.stopping:
            state = "stopping"
        elif worker.started.is_set():
            state = "running"
        elif worker.waiting_for is not None:
            state, reason = "waiting", worker.waiting_for
        else:
            state = "starting"
        return {"state": state, "reason": reason, "counters": dict(worker.stats.snapshot())}


class Serving(Protocol):
    """What :meth:`WebJobs.serve` needs of the HTTP server (``socketserver.BaseServer``)."""

    def serve_forever(self, poll_interval: float = ...) -> None: ...

    def shutdown(self) -> None: ...


class WebJobs:
    """The jobs side of one web process: its embedded worker (if any) and the queue diagnostics."""

    def __init__(self, db: Database, worker: Optional[EmbeddedWorker]) -> None:
        self.db = db  # the web pool; diagnostics read the queue through it
        self.worker = worker

    def serve(self, server: Serving, *, check_every: float = 0.1) -> None:
        """Run ``server.serve_forever()`` with the worker running; stop the worker after it returns.

        On the main thread SIGTERM and SIGINT shut the server down (and so
        stop the worker gracefully); elsewhere only ``server.shutdown()`` does.
        """
        signals: Optional[SignalStop] = None
        if threading.current_thread() is threading.main_thread():
            signals = SignalStop()
            signals.install()
        done = threading.Event()

        def watch() -> None:
            while not done.wait(check_every):
                if signals is not None and signals.requested:
                    LOGGER.info("server_stopping reason=signal")
                    server.shutdown()
                    return

        watcher = threading.Thread(target=watch, name="prks-web-signals", daemon=True)
        try:
            if self.worker is not None:
                self.worker.start()
            watcher.start()
            server.serve_forever()
        finally:
            done.set()
            if watcher.ident is not None:
                watcher.join()
            if signals is not None:
                signals.restore()
            if self.worker is not None:
                self.worker.stop()

    def diagnostics(self) -> Optional[dict[str, Any]]:
        """The ``GET /api/diagnostics/jobs`` body; ``None`` when the database cannot be read."""
        try:
            depth = queue_depth(self.db)
        except DatabaseError:
            return None
        return {
            "embedded_worker": self.worker.status() if self.worker is not None else None,
            "queue": [asdict(row) for row in depth],
        }


WorkerDatabaseFactory = Callable[[Mapping[str, str]], Database]


def web_jobs(
    db: Database,
    environ: Mapping[str, str],
    *,
    open_database: WorkerDatabaseFactory = open_worker_database,
    registry: HandlerRegistry = REGISTRY,
    settings: WorkerSettings = WorkerSettings(),
    schema_dir: str = SCHEMA_DIR,
) -> WebJobs:
    """The jobs runtime for a web process whose start check passed; ``db`` is its web pool.

    Raises :class:`EmbeddedWorkerConfigError`, or the worker pool's
    ``DatabaseConfigError``/``DatabaseError``; nothing has started then.
    """
    if not embedded_worker_enabled(environ):
        LOGGER.info("embedded_worker_disabled")
        return WebJobs(db, None)
    worker_db = open_database(environ)
    try:
        worker = EmbeddedWorker(worker_db, registry=registry, settings=settings, schema_dir=schema_dir)
    except BaseException:
        worker_db.close()
        raise
    return WebJobs(db, worker)
