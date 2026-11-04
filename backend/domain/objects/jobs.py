"""Object maintenance jobs: cleanup retries and the orphan sweep (§11.2, §11.3, §12.1; L-A-1 #635).

Two instance jobs, each a singleton (one runs at a time, whichever worker
claims it) and each bounded:

- ``objects.cleanup`` settles at most :data:`CLEANUP_BATCH` due claims
  (:func:`cleanup_pass`). A claim it cannot settle (a backup holds its
  Library, the references are unknown, the delete failed) is stamped and is
  due again :data:`RETRY_AFTER` later. When the batch was full and claims
  are still due, the pass enqueues the next one.
- ``objects.sweep`` examines at most :data:`SWEEP_BATCH` stored keys
  (:func:`sweep_pass`) and records a claim for each one older than the grace
  period that nothing names. It never deletes: the claims are settled by the
  cleanup job. When keys are left, the pass enqueues the next one with a
  cursor (the namespace and the last key name examined) in its payload;
  :meth:`~backend.storage.objects.StorageBackend.list_keys` returns a
  bounded page in name order after a name, so the next pass resumes after it.

**Fencing.** Every transaction of a pass runs through
:meth:`~backend.jobs.runner.JobRun.command`, which locks the job row with its
lease first: a run whose lease was reclaimed, reaped, released or cancelled,
or has expired, commits nothing, and since a settle deletes the bytes inside
that transaction, it deletes nothing either. The per-object lock, the claim
row lock and the Library objects lock are the ones :func:`lifecycle.settle_tx`
and :func:`lifecycle.claim_orphan` take. A pass stopped half-way is repeated
safely: a settled claim is gone, a stamped one is not due yet, and a sweep
claim is recorded at most once.

**Scheduling.** :func:`lifecycle.release` enqueues ``objects.cleanup`` in the
command that records a claim. The rest comes from :meth:`ObjectMaintenance.tick`,
the worker's idle task, at most once per :data:`SCHEDULE_EVERY` per worker,
in one transaction that holds an advisory lock so concurrent workers decide
in turn:

- ``objects.cleanup`` when a claim is due and no cleanup job is queued or
  running (claims left by a restart, claims stamped by an earlier pass);
- ``objects.sweep`` when no sweep job is queued or running and no sweep
  cycle completed in the last :data:`SWEEP_EVERY`. A cycle is complete when
  its last pass succeeds having reached the end of the listing; a cycle cut
  short by a failed pass starts again from the beginning at the next tick.

So an idle instance with nothing to settle enqueues nothing, and a missed
NOTIFY is covered by the worker's poll.

**The data root.** :func:`attach` binds both jobs to a worker's paired and
leased root, on that worker's own copy of the registry. It refuses a root
that holds a SQLite catalogue (:data:`SQLITE_CATALOGUES`): until the
cutover, a ``PRKS_AUTH`` web process keeps its Library in SQLite on the same
root, and its PDFs and portraits are in the directories the sweep lists but
the PostgreSQL catalogue does not name. Each pass checks the root again
before it touches anything.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, LiteralString, Optional

from backend.db.database import Database, Transaction
from backend.domain.objects import lifecycle
from backend.domain.objects.lifecycle import CLEANUP_KIND, Settled
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from backend.storage.objects import (
    NAMESPACE_ASSET_OBJECTS,
    NAMESPACE_PORTRAITS,
    InvalidStorageKey,
    LocalFilesystemStorage,
    StorageBackend,
    StorageKey,
    StorageObjectError,
    validate_key_name,
)

SWEEP_KIND = "objects.sweep"
CLEANUP_BATCH = 100
SWEEP_BATCH = 1000
RETRY_AFTER = timedelta(minutes=10)
SWEEP_EVERY = timedelta(days=7)
SCHEDULE_EVERY = timedelta(minutes=1)
HEARTBEAT_EVERY = 25
# The sweep's order; a cursor names one of these.
SWEEP_NAMESPACES = (NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS)
# The SQLite catalogue of a data root (production and testing names).
SQLITE_CATALOGUES = ("prks_data.db", "prks_data_testing.db")

LOGGER = logging.getLogger("prks.objects")

_SCHEDULE_LOCK: LiteralString = "SELECT pg_advisory_xact_lock(hashtextextended('prks.objects:schedule', 0))"
_CLAIM_DUE: LiteralString = """
SELECT EXISTS (
  SELECT 1 FROM prks.object_cleanup_claims
  WHERE NOT hold AND (last_attempt_at IS NULL
                      OR last_attempt_at <= clock_timestamp() - make_interval(secs => %(retry_after)s))
) AS due
"""
_ACTIVE: LiteralString = """
SELECT EXISTS (SELECT 1 FROM prks.jobs WHERE kind = %(kind)s AND state IN ('queued', 'running')) AS active
"""
# A sweep cycle completed recently: its last pass succeeded and said so. A
# failed or partial cycle does not count. ``jobs.prune`` keeps a finished job
# for ``prune.RETENTION``, which is no shorter than :data:`SWEEP_EVERY`.
_RECENT: LiteralString = """
SELECT EXISTS (
  SELECT 1 FROM prks.jobs
  WHERE kind = %(kind)s AND state = 'succeeded' AND progress @> '{"complete": true}'
    AND finished_at > now() - make_interval(secs => %(every)s)
) AS recent
"""


class ObjectsRootRefused(RuntimeError):
    """The data root holds files the PostgreSQL catalogue does not own; no pass runs on it."""


def root_refusal(root: str) -> Optional[str]:
    """Why object maintenance must not run on ``root`` (a reason code), or None."""
    for name in SQLITE_CATALOGUES:
        if os.path.lexists(os.path.join(root, name)):
            return "sqlite_catalogue"
    return None


def _flag(tx: Transaction, sql: LiteralString, params: Mapping[str, Any], column: str) -> bool:
    row = tx.execute(sql, params).fetchone()
    return bool(row is not None and row[column])


def enqueue_cleanup(tx: Transaction) -> Optional[uuid.UUID]:
    """Queue a cleanup pass; ``None`` when one is queued already."""
    return queue.enqueue(tx, CLEANUP_KIND, dedupe_key=CLEANUP_KIND, singleton_key=CLEANUP_KIND)


def enqueue_sweep(tx: Transaction, cursor: Optional[tuple[str, str]] = None) -> Optional[uuid.UUID]:
    """Queue a sweep pass (from the start, or after ``cursor``); ``None`` when one is queued already."""
    payload = None if cursor is None else {"namespace": cursor[0], "after": cursor[1]}
    return queue.enqueue(
        tx, SWEEP_KIND, payload, dedupe_key=SWEEP_KIND, singleton_key=SWEEP_KIND, priority=-5
    )


def schedule(tx: Transaction, *, retry_after: timedelta = RETRY_AFTER,
             sweep_every: timedelta = SWEEP_EVERY) -> list[str]:
    """Enqueue the maintenance that is due (see the module docs); the kinds enqueued."""
    tx.execute(_SCHEDULE_LOCK)
    enqueued: list[str] = []
    if (_flag(tx, _CLAIM_DUE, {"retry_after": retry_after.total_seconds()}, "due")
            and not _flag(tx, _ACTIVE, {"kind": CLEANUP_KIND}, "active")
            and enqueue_cleanup(tx) is not None):
        enqueued.append(CLEANUP_KIND)
    if (not _flag(tx, _ACTIVE, {"kind": SWEEP_KIND}, "active")
            and not _flag(tx, _RECENT, {"kind": SWEEP_KIND, "every": sweep_every.total_seconds()}, "recent")
            and enqueue_sweep(tx) is not None):
        enqueued.append(SWEEP_KIND)
    return enqueued


def cleanup_pass(run: JobRun, storage: StorageBackend, *, limit: int = CLEANUP_BATCH,
                 retry_after: timedelta = RETRY_AFTER) -> Counter[Settled]:
    """Settle at most ``limit`` due claims, each in its own fenced transaction; the outcomes.

    Enqueues the next pass when the batch was full and a claim is still due.
    """
    keys = run.command(lambda tx: lifecycle.due_claim_keys(tx, limit=limit, retry_after=retry_after))
    outcomes: Counter[Settled] = Counter()
    for examined, (namespace, name) in enumerate(keys, 1):
        run.check()
        try:
            key = StorageKey(namespace, name)
        except InvalidStorageKey:
            # Never a key PRKS wrote: stamped, so it rotates behind the valid claims.
            LOGGER.warning("object_cleanup_invalid_claim namespace=%s", namespace)
            run.command(lambda tx, namespace=namespace, name=name: lifecycle.note_attempt(tx, namespace, name))
            outcomes[Settled.FAILED] += 1
        else:
            outcomes[run.command(lambda tx, key=key: lifecycle.settle_tx(tx, storage, key))] += 1
        if examined % HEARTBEAT_EVERY == 0:
            run.heartbeat({"examined": examined})
    if len(keys) == limit:
        params = {"retry_after": retry_after.total_seconds()}
        run.command(lambda tx: _flag(tx, _CLAIM_DUE, params, "due") and enqueue_cleanup(tx))
    run.heartbeat({"examined": len(keys)})
    return outcomes


def _cursor(payload: Mapping[str, Any]) -> Optional[tuple[str, str]]:
    if not payload:
        return None
    namespace, after = payload.get("namespace"), payload.get("after")
    if set(payload) != {"namespace", "after"} or namespace not in SWEEP_NAMESPACES or not isinstance(after, str):
        raise ValueError("a sweep cursor is a namespace and a key name")
    return namespace, validate_key_name(after)


def sweep_pass(run: JobRun, storage: StorageBackend, *, grace: timedelta = lifecycle.DEFAULT_SWEEP_GRACE,
               batch: int = SWEEP_BATCH) -> int:
    """Claim the orphans among at most ``batch`` stored keys after the payload's cursor; how many.

    Each namespace is read with one bounded
    :meth:`~backend.storage.objects.StorageBackend.list_keys` call, so a pass
    holds at most ``batch + 1`` names. Listing and ``stat`` change nothing;
    each claim is its own fenced transaction that checks the references under
    the per-object lock. A key younger than ``grace`` is left alone (a write
    may still adopt it). When keys are left, the next pass is enqueued with
    this pass's last key; the pass that reaches the end records
    ``{"complete": true}`` in its progress, which ends the sweep cycle.
    """
    if grace < timedelta(0):
        raise ValueError("grace must not be negative")
    if batch < 1:
        raise ValueError("batch must be at least 1")
    cursor = _cursor(run.payload)
    cutoff = time.time() - grace.total_seconds()
    start = 0 if cursor is None else SWEEP_NAMESPACES.index(cursor[0])
    examined = claimed = 0
    last: Optional[StorageKey] = None
    for namespace in SWEEP_NAMESPACES[start:]:
        after = cursor[1] if cursor is not None and namespace == cursor[0] else None
        remaining = batch - examined
        keys = storage.list_keys(namespace, after=after, limit=remaining + 1)
        for key in keys[:remaining]:
            run.check()
            examined += 1
            last = key
            try:
                info = storage.stat(key)
            except StorageObjectError:
                info = None
            if info is not None and info.modified_at is not None and info.modified_at <= cutoff:
                if run.command(lambda tx, key=key: lifecycle.claim_orphan(tx, key)):
                    claimed += 1
            if examined % HEARTBEAT_EVERY == 0:
                run.heartbeat({"examined": examined, "claimed": claimed})
        if len(keys) > remaining and last is not None:
            position = (last.namespace, last.name)
            run.command(lambda tx, position=position: enqueue_sweep(tx, position))
            run.heartbeat({"examined": examined, "claimed": claimed})
            return claimed
    run.heartbeat({"examined": examined, "claimed": claimed, "complete": True})
    return claimed


class ObjectMaintenance:
    """The objects lane of one worker: its handlers and its idle task, bound to one data root."""

    def __init__(self, storage: StorageBackend, root: str, *,
                 schedule_every: timedelta = SCHEDULE_EVERY) -> None:
        self.storage = storage
        self.root = root
        self._schedule_every = schedule_every.total_seconds()
        self._last_schedule: Optional[float] = None
        self._lock = threading.Lock()

    def _check_root(self) -> None:
        reason = root_refusal(self.root)
        if reason is not None:
            raise ObjectsRootRefused(reason)

    def cleanup(self, run: JobRun) -> None:
        """The ``objects.cleanup`` handler."""
        self._check_root()
        cleanup_pass(run, self.storage)

    def sweep(self, run: JobRun) -> None:
        """The ``objects.sweep`` handler."""
        self._check_root()
        sweep_pass(run, self.storage)

    def tick(self, db: Database) -> None:
        """The idle task: :func:`schedule`, at most once per interval, never on a refused root."""
        now = time.monotonic()
        with self._lock:
            if self._last_schedule is not None and now - self._last_schedule < self._schedule_every:
                return
            if root_refusal(self.root) is not None:
                return
            enqueued = db.command(schedule)
            self._last_schedule = now
        if enqueued:
            LOGGER.info("object_maintenance_scheduled kinds=%s", ",".join(enqueued))


def attach(registry: HandlerRegistry, root: str) -> Optional[ObjectMaintenance]:
    """Register the objects lane on ``registry`` for the paired, leased ``root``; None when refused.

    ``registry`` is the worker's own copy (:meth:`HandlerRegistry.copy`):
    the handlers act on this root only.
    """
    reason = root_refusal(root)
    if reason is not None:
        LOGGER.warning("object_maintenance_disabled reason=%s", reason)
        return None
    maintenance = ObjectMaintenance(LocalFilesystemStorage(root), root)
    registry.register(CLEANUP_KIND, maintenance.cleanup)
    registry.register(SWEEP_KIND, maintenance.sweep)
    registry.add_idle_task(maintenance.tick)
    LOGGER.info("object_maintenance_attached")
    return maintenance
