"""The ``processing.scan`` job: a Library's inbox scan on a worker (§11.5, §12.1; L-PR-1 #636).

A Library job (:func:`enqueue_scan`), one at a time per Library
(``singleton_key``) and coalesced while queued (``dedupe_key``). The handler
runs :func:`backend.domain.processing.inbox.scan` with the job's fenced
transactions (:class:`JobRunner`): a run whose lease was lost, or whose
Library a restore advanced, commits nothing. The scan is idempotent, so a
retried or repeated run finds what the last one left.

**The inbox directory.** The inbox is configuration of the process
(``StorageConfig.processing_dir``, which ``PRKS_FOR_PROCESSING_DIR`` may
move outside the data root), so the handler is not in the global registry:
:func:`attach` binds it to one inbox on a worker's own registry copy, and
refuses a data root that holds a SQLite catalogue, as the objects lane does
(``objects.jobs.root_refusal``): until the cutover a SQLite web process
scans that inbox itself. Binding it in ``prks worker`` and the embedded
worker needs the process's ``StorageConfig`` there, which is the cutover's
(PG-C1); nothing enqueues the job until then except a caller that attached
it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_query
from backend.domain.objects.jobs import root_refusal
from backend.domain.processing import inbox
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun

T = TypeVar("T")

KIND = "processing.scan"

LOGGER = logging.getLogger("prks.processing")


def enqueue_scan(scope: LibraryScope) -> Optional[uuid.UUID]:
    """Queue a scan of the scope's Library in its command; ``None`` when one is queued already."""
    key = f"{KIND}:{scope.library_id}"
    return queue.enqueue_library_job(scope, KIND, dedupe_key=key, singleton_key=key)


class JobRunner:
    """A Library job's :class:`~backend.domain.processing.inbox.Runner`: every transaction fenced."""

    def __init__(self, run: JobRun) -> None:
        if run.job.library_id is None:
            raise ValueError("processing.scan is a Library job")
        self._run = run
        self._library_id = run.job.library_id

    @property
    def database(self) -> Database:
        return self._run.db

    @property
    def library_id(self) -> str:
        return self._library_id

    def read(self, fn: Callable[[LibraryScope], T]) -> T:
        return library_query(self._run.db, self._run.context(), fn)

    def command(self, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
        isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
        return self._run.library_command(Capability.EDIT_LIBRARY, fn, isolation=isolation)


class InboxScan:
    """The ``processing.scan`` handler, bound to one inbox directory."""

    def __init__(self, inbox_dir: str, root: str) -> None:
        self.inbox_dir = inbox_dir
        self.root = root

    def __call__(self, run: JobRun) -> None:
        reason = root_refusal(self.root)
        if reason is not None:
            raise RuntimeError(f"processing.scan refused: {reason}")
        from backend.entity_ids import generate

        inbox.scan(JobRunner(run), self.inbox_dir, lambda: generate("PF"))


def attach(registry: HandlerRegistry, inbox_dir: str, root: str) -> Optional[InboxScan]:
    """Register ``processing.scan`` on ``registry`` for ``inbox_dir``; None when ``root`` is refused."""
    reason = root_refusal(root)
    if reason is not None:
        LOGGER.warning("processing_scan_disabled reason=%s", reason)
        return None
    scan = InboxScan(inbox_dir, root)
    registry.register(KIND, scan)
    return scan
