"""Lease-fenced PostgreSQL object cleanup job handlers (L-A-1 #635).

The handler's destructive step is inside JobRun.command, not Database.command:
a reclaimed, cancelled or expired lease cannot delete an object after it has
lost its right to run. Register handlers with a storage backend only after
the worker has joined the validated data root. Scheduling is a separate step.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import timedelta
from typing import LiteralString

from backend.domain.objects import lifecycle
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from backend.storage.objects import (
    InvalidStorageKey,
    NAMESPACE_ASSET_OBJECTS,
    NAMESPACE_PORTRAITS,
    StorageBackend,
    StorageKey,
    StorageObjectError,
)

CLEANUP_KIND = "objects.cleanup"
SWEEP_KIND = "objects.sweep"
CLEANUP_LIMIT = 100
HEARTBEAT_EVERY = 25
LOGGER = logging.getLogger("prks.objects")

_NOTE_INVALID: LiteralString = """
UPDATE prks.object_cleanup_claims SET last_attempt_at = clock_timestamp()
WHERE namespace = %(namespace)s AND name = %(name)s
"""


def enqueue_cleanup(tx: object) -> object:
    """Queue one instance-level retry pass; never store object names in payloads."""
    return queue.enqueue(tx, CLEANUP_KIND, dedupe_key=CLEANUP_KIND, singleton_key=CLEANUP_KIND)


def enqueue_sweep(tx: object) -> object:
    """Queue one instance-level orphan scan; actual byte deletion is a later cleanup pass."""
    return queue.enqueue(tx, SWEEP_KIND, dedupe_key=SWEEP_KIND, singleton_key=SWEEP_KIND, priority=-5)


def cleanup_pass(run: JobRun, storage: StorageBackend, *, limit: int = CLEANUP_LIMIT) -> Counter[lifecycle.Settled]:
    """Retry at most `limit` claims with a lease-fenced transaction for EACH settle.

    A failed or busy claim is rotated by lifecycle's attempt timestamp. A
    running restore's staged key, a backup-held Library and a hold claim are
    protected by the existing reference and object-lock protocol.
    """
    names = run.command(lambda tx: lifecycle.due_claim_keys(tx, limit=limit))
    results: Counter[lifecycle.Settled] = Counter()
    for index, (namespace, name) in enumerate(names, 1):
        run.check()
        try:
            key = StorageKey(namespace, name)
            # StorageKey validates the namespace and the key name.
            from backend.storage.objects import validate_key_name
            validate_key_name(key.name)
            if key.namespace not in (NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS):
                raise InvalidStorageKey("unsupported namespace")
        except InvalidStorageKey:
            LOGGER.warning("object_cleanup_invalid_claim namespace=%s", namespace)
            run.command(lambda tx, namespace=namespace, name=name: tx.execute(
                _NOTE_INVALID, {"namespace": namespace, "name": name}
            ))
            results[lifecycle.Settled.FAILED] += 1
        else:
            outcome = run.command(lambda tx, key=key: lifecycle.settle_tx(tx, storage, key))
            results[outcome] += 1
        if index % HEARTBEAT_EVERY == 0:
            run.heartbeat({"examined": index})
    run.heartbeat({"examined": len(names)})
    return results


def sweep_pass(run: JobRun, storage: StorageBackend, *, grace: timedelta = lifecycle.DEFAULT_SWEEP_GRACE
               ) -> int:
    """Find old orphan keys without deleting; claim each under the job lease.

    Storage enumeration and stat do not mutate anything. Every claim is made
    inside a separately fenced transaction that rechecks catalogue references
    and the per-object lock. Lease loss stops further enumeration.
    """
    if grace < timedelta(0):
        raise ValueError("grace must not be negative")
    cutoff = time.time() - grace.total_seconds()
    examined = claimed = 0
    for namespace in (NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS):
        for key in storage.iter_keys(namespace):
            run.check()
            examined += 1
            try:
                info = storage.stat(key)
            except StorageObjectError:
                continue
            if info is not None and info.modified_at is not None and info.modified_at <= cutoff:
                if run.command(lambda tx, key=key: lifecycle.claim_orphan(tx, key)):
                    claimed += 1
            if examined % HEARTBEAT_EVERY == 0:
                run.heartbeat({"examined": examined, "claimed": claimed})
    run.heartbeat({"examined": examined, "claimed": claimed})
    return claimed


def register_handlers(registry: HandlerRegistry, storage: StorageBackend) -> None:
    """Register the objects lane only after the worker's storage root is validated."""
    registry.register(CLEANUP_KIND, lambda run: cleanup_pass(run, storage))
    registry.register(SWEEP_KIND, lambda run: sweep_pass(run, storage))
