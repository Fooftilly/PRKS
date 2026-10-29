"""Object lifecycle: adoption, cleanup claims, settle and the orphan sweep (§11.2, §11.3, §7.4).

**References.** One data root serves every Library (§6.2), so a key is
referenced when any catalogue row names it, whatever its Library: an Asset's
``storage_locator`` or ``source_locator`` (``asset-objects``), a Person's
``portrait_locator`` (``portraits``), or a staged key of a restore run that
is neither committed nor failed (``restore_run_objects``). The check is
three-valued, as on SQLite (``backend/AGENTS.md``): ``None`` means "not
known" (a namespace with no catalogue), and nothing is deleted on it.

**The per-object lock.** ``pg_advisory_xact_lock`` on the key
(:data:`OBJECT_LOCK_SQL`) serializes the three things that decide a key's
fate: a writer adopting it (:func:`adopt`), a settle deciding to delete it
(:func:`settle`), and a restore staging it (L-B-1's R1 takes the same lock
before it inserts its ``restore_run_objects`` row). Each holds the lock to
the end of its transaction, so either order is safe:

- an adopter that commits first leaves a reference the settle then sees, so
  the object is kept;
- a settle that commits first has unlinked the bytes, so the adopter finds
  them gone (or of another size) and fails with :class:`ObjectMissing`,
  committing no reference.

**Claims** (``object_cleanup_claims``) are recorded by :func:`release`, in
the transaction that stops naming a key, and by :func:`sweep` for an orphan
nothing ever adopted (an interrupted ingest). Recording never deletes: only
:func:`settle` does, after re-reading the references under the per-object
lock, and the claim is deleted in that same transaction once nothing is
owed. A claim with ``hold`` (restore's replaced objects, §11.4) is never
settled automatically. A claim of a Library is settled only while that
Library's objects lock (:data:`OBJECTS_LOCK_KEY_SQL`) is free, because a
running backup holds it shared while it copies the objects its snapshot
names (§11.3).

**Transactions.** :func:`adopt`, :func:`release` and :func:`replace_object`
run inside the caller's Library command; settle, the retry pass and the
sweep are maintenance and run one transaction per key on the
:class:`~backend.db.database.Database`. Settle deletes the bytes before its
transaction commits; if the commit then fails, the claim is still there and
the next settle finds the bytes already gone, which is success.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any, LiteralString, Optional

from backend.db.database import Database, Transaction
from backend.domain.identity.library_scope import LibraryScope
from backend.storage.objects import (
    NAMESPACE_ASSET_OBJECTS,
    NAMESPACE_PORTRAITS,
    InvalidStorageKey,
    ObjectIntegrityError,
    StorageBackend,
    StorageKey,
    StorageObjectError,
)

LOGGER = logging.getLogger("prks.objects")

# The per-object lock key of §7.4 over the key's text form ``namespace/name``.
# Restore's R1 (L-B-1) takes it with this same expression.
OBJECT_LOCK_SQL: LiteralString = "SELECT pg_advisory_xact_lock(hashtextextended('obj:' || %s, 0))"
# A Library's objects lock (§11.3): a backup holds it shared for its whole
# copy; settle takes it exclusively, without waiting.
OBJECTS_LOCK_KEY_SQL: LiteralString = "hashtextextended('prks.objects:' || %s, 0)"
_TRY_OBJECTS_LOCK_SQL: LiteralString = (
    "SELECT pg_try_advisory_xact_lock(hashtextextended('prks.objects:' || %s, 0)) AS locked"
)

DEFAULT_SWEEP_GRACE = timedelta(hours=24)
DEFAULT_RETRY_LIMIT = 100

_ASSET_REFERENCES: LiteralString = """
SELECT EXISTS (SELECT 1 FROM prks.assets WHERE storage_locator = %(name)s)
    OR EXISTS (SELECT 1 FROM prks.assets WHERE source_locator = %(name)s) AS referenced
"""
_PORTRAIT_REFERENCES: LiteralString = """
SELECT EXISTS (SELECT 1 FROM prks.persons WHERE portrait_locator = %(name)s) AS referenced
"""
_STAGED_REFERENCES: LiteralString = """
SELECT EXISTS (
  SELECT 1 FROM prks.restore_run_objects o JOIN prks.restore_runs r ON r.id = o.run_id
  WHERE o.namespace = %(namespace)s AND o.name = %(name)s AND r.state = 'running'
) AS referenced
"""
# Adoption: a key another Library names is not this Library's to adopt. The
# reference check is global, so sharing would only keep it alive; refusing
# keeps one Library's catalogue from pointing at another's bytes.
_NAMED_BY_ANOTHER_LIBRARY: LiteralString = """
SELECT 1 FROM prks.assets
WHERE library_id <> %(library_id)s AND (storage_locator = %(name)s OR source_locator = %(name)s)
LIMIT 1
"""
_PORTRAIT_NAMED_BY_ANOTHER_LIBRARY: LiteralString = """
SELECT 1 FROM prks.persons WHERE library_id <> %(library_id)s AND portrait_locator = %(name)s LIMIT 1
"""
# A key another Library released is not this Library's to adopt either.
_CLAIMED_BY_ANOTHER_LIBRARY: LiteralString = """
SELECT 1 FROM prks.object_cleanup_claims
WHERE namespace = %(namespace)s AND name = %(name)s AND library_id <> %(library_id)s
"""
_NAMED_IN_LIBRARY: LiteralString = """
SELECT 1 FROM prks.assets
WHERE library_id = %(library_id)s AND (storage_locator = %(name)s OR source_locator = %(name)s)
LIMIT 1
"""
_PORTRAIT_NAMED_IN_LIBRARY: LiteralString = """
SELECT 1 FROM prks.persons WHERE library_id = %(library_id)s AND portrait_locator = %(name)s LIMIT 1
"""
# A claim names the Library that released the key; an orphan's claim (no
# Library) is taken over, another Library's claim is left as it is.
_RECORD_CLAIM: LiteralString = """
INSERT INTO prks.object_cleanup_claims AS c (namespace, name, library_id, hold)
VALUES (%(namespace)s, %(name)s, %(library_id)s, %(hold)s)
ON CONFLICT (namespace, name) DO UPDATE
  SET library_id = coalesce(c.library_id, EXCLUDED.library_id), hold = c.hold OR EXCLUDED.hold
"""
_NAMED_ELSEWHERE: dict[str, LiteralString] = {
    NAMESPACE_ASSET_OBJECTS: _NAMED_BY_ANOTHER_LIBRARY,
    NAMESPACE_PORTRAITS: _PORTRAIT_NAMED_BY_ANOTHER_LIBRARY,
}
_NAMED_HERE: dict[str, LiteralString] = {
    NAMESPACE_ASSET_OBJECTS: _NAMED_IN_LIBRARY,
    NAMESPACE_PORTRAITS: _PORTRAIT_NAMED_IN_LIBRARY,
}
_RECORD_ORPHAN: LiteralString = """
INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES (%(namespace)s, %(name)s)
ON CONFLICT (namespace, name) DO NOTHING
"""
_CLAIM_FOR_SETTLE: LiteralString = """
SELECT library_id, hold FROM prks.object_cleanup_claims
WHERE namespace = %(namespace)s AND name = %(name)s
FOR UPDATE
"""
_FORGET_CLAIM: LiteralString = """
DELETE FROM prks.object_cleanup_claims WHERE namespace = %(namespace)s AND name = %(name)s
"""
_NOTE_ATTEMPT: LiteralString = """
UPDATE prks.object_cleanup_claims SET last_attempt_at = clock_timestamp()
WHERE namespace = %(namespace)s AND name = %(name)s
"""
_DUE_CLAIMS: LiteralString = """
SELECT namespace, name FROM prks.object_cleanup_claims
WHERE NOT hold AND (%(library_id)s::text IS NULL OR library_id = %(library_id)s)
ORDER BY last_attempt_at NULLS FIRST, recorded_at, namespace, name
LIMIT %(limit)s
"""
# A rewrite locks the Asset's Work first, the order every Works command uses,
# so a Work delete either sees the new key or finds the Work already gone.
_LOCK_ASSET_WORK: LiteralString = """
SELECT w.id FROM prks.works w
JOIN prks.assets a ON a.library_id = w.library_id AND a.work_id = w.id
WHERE w.library_id = %(library_id)s AND a.id = %(asset_id)s
FOR UPDATE OF w
"""
_LOCK_ASSET_OBJECT: LiteralString = """
SELECT storage_locator FROM prks.assets
WHERE library_id = %(library_id)s AND id = %(asset_id)s AND kind = 'managed_file'
FOR UPDATE
"""
_SWITCH_ASSET_OBJECT: LiteralString = """
UPDATE prks.assets
SET storage_locator = %(name)s, content_sha256 = %(sha256)s, byte_size = %(size)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(asset_id)s
"""


class ObjectMissing(ValueError):
    """The bytes a writer meant to adopt are gone or not the ones it wrote.

    The write committed nothing; the caller writes a new object and retries.
    """

    def __init__(self) -> None:
        super().__init__("the stored file does not exist")


@dataclass(frozen=True)
class StoredObject:
    """An object a writer has just stored: its key and what it wrote.

    ``version`` is the backend's version token from the write, when the
    writer has it: adoption then also refuses bytes replaced since.
    """

    key: StorageKey
    size: int
    sha256: str
    version: Optional[str] = None


class Settled(Enum):
    REMOVED = "removed"  # the bytes are gone and the claim with them
    REFERENCED = "referenced"  # a catalogue row names the key: kept, claim retired
    UNKNOWN = "unknown"  # references unknown: kept, claim kept
    FAILED = "failed"  # the bytes could not be removed: claim kept
    HELD = "held"  # a hold claim: never settled automatically
    BUSY = "busy"  # the Library's objects lock is held (a backup): try later, after the others
    ABSENT = "absent"  # no such claim


def _key_params(key: StorageKey) -> dict[str, Any]:
    return {"namespace": key.namespace, "name": key.name}


def references(tx: Transaction, key: StorageKey) -> Optional[bool]:
    """Whether any catalogue row names ``key``: True, False, or None when not known."""
    params = _key_params(key)
    staged = tx.execute(_STAGED_REFERENCES, params).fetchone()
    if staged is not None and staged["referenced"]:
        return True
    if key.namespace == NAMESPACE_ASSET_OBJECTS:
        row = tx.execute(_ASSET_REFERENCES, params).fetchone()
        return bool(row is not None and row["referenced"])
    if key.namespace == NAMESPACE_PORTRAITS:
        row = tx.execute(_PORTRAIT_REFERENCES, params).fetchone()
        return bool(row is not None and row["referenced"])
    return None


# -- in a Library command ------------------------------------------------------------


def adopt(scope: LibraryScope, storage: StorageBackend, obj: StoredObject) -> None:
    """Take the per-object lock and confirm ``obj`` is still stored as written.

    Every transaction that first references a key it wrote calls this
    before it names the key (§11.2). The lock is held until the caller's
    transaction ends. Raises :class:`ObjectMissing` when the bytes are gone,
    have another size (a settle unlinked them first) or another version than
    the write reported (they were replaced since), or when another
    Library names the key or released it. So no key is ever named by two
    Libraries, and the claim's Library is the only one whose backups can
    hold it in a snapshot.
    """
    scope.execute_unscoped(OBJECT_LOCK_SQL, (str(obj.key),))
    named_elsewhere = _NAMED_ELSEWHERE.get(obj.key.namespace)
    if named_elsewhere is not None and scope.execute(
            named_elsewhere, {"name": obj.key.name}).fetchone() is not None:
        raise ObjectMissing()
    if scope.execute(_CLAIMED_BY_ANOTHER_LIBRARY, _key_params(obj.key)).fetchone() is not None:
        raise ObjectMissing()
    try:
        info = storage.stat(obj.key)
    except ObjectIntegrityError as exc:
        raise ObjectMissing() from exc
    if info is None or info.size != obj.size or (obj.version is not None and info.version != obj.version):
        raise ObjectMissing()


def release(scope: LibraryScope, key: StorageKey, *, hold: bool = False) -> bool:
    """Record a cleanup claim for ``key`` if this Library no longer names it.

    Called in the transaction that stops naming the key, after its last
    change. Returns whether a claim is recorded (or already was). ``hold``
    keeps the bytes until someone releases the hold (restore, §11.4).

    Takes the per-object lock first: two transactions that each drop one of
    the Library's last two names of a key then check in turn, so the second
    sees the first's delete committed and records the claim. A caller
    releasing several keys releases them in a fixed order.
    """
    scope.execute_unscoped(OBJECT_LOCK_SQL, (str(key),))
    named_here = _NAMED_HERE.get(key.namespace)
    if named_here is not None and scope.execute(named_here, {"name": key.name}).fetchone() is not None:
        return False
    scope.execute(_RECORD_CLAIM, {**_key_params(key), "hold": hold})
    return True


def replace_object(scope: LibraryScope, storage: StorageBackend, asset_id: str,
                   obj: StoredObject) -> Optional[str]:
    """Point a managed-file Asset at the new object ``obj`` (a rewrite, §11.2).

    Locks the Asset's Work, then the Asset row (the Works order), adopts the new key, switches ``storage_locator``,
    ``content_sha256`` and ``byte_size``, and releases the old key, all in
    the caller's transaction. Returns the old key's name, or None when this
    Library has no such managed-file Asset. Checking the annotation revision
    the bytes were made from is the caller's (materialization).
    """
    if obj.key.namespace != NAMESPACE_ASSET_OBJECTS:
        raise ValueError("an Asset's bytes live in the asset-objects namespace")
    if scope.execute(_LOCK_ASSET_WORK, {"asset_id": asset_id}).fetchone() is None:
        return None
    row = scope.execute(_LOCK_ASSET_OBJECT, {"asset_id": asset_id}).fetchone()
    if row is None:
        return None
    old = str(row["storage_locator"])
    adopt(scope, storage, obj)
    scope.execute(_SWITCH_ASSET_OBJECT, {"asset_id": asset_id, "name": obj.key.name,
                                         "sha256": obj.sha256, "size": obj.size})
    if old != obj.key.name:
        release(scope, StorageKey(NAMESPACE_ASSET_OBJECTS, old))
    return old


# -- maintenance ------------------------------------------------------------------------


def settle(db: Database, storage: StorageBackend, key: StorageKey) -> Settled:
    """Settle the claim on ``key`` in one transaction: delete the bytes only if nothing names them."""

    def run(tx: Transaction) -> Settled:
        params = _key_params(key)
        tx.execute(OBJECT_LOCK_SQL, (str(key),))
        claim = tx.execute(_CLAIM_FOR_SETTLE, params).fetchone()
        if claim is None:
            return Settled.ABSENT
        if claim["hold"]:
            return Settled.HELD
        if claim["library_id"] is not None:
            locked = tx.execute(_TRY_OBJECTS_LOCK_SQL, (claim["library_id"],)).fetchone()
            if locked is None or not locked["locked"]:
                # Stamped like an attempt, so the bounded pass rotates past
                # a Library under backup instead of retrying it first.
                tx.execute(_NOTE_ATTEMPT, params)
                return Settled.BUSY
        referenced = references(tx, key)
        if referenced:
            tx.execute(_FORGET_CLAIM, params)
            return Settled.REFERENCED
        if referenced is None:
            tx.execute(_NOTE_ATTEMPT, params)
            return Settled.UNKNOWN
        try:
            storage.delete(key)
        except (OSError, StorageObjectError) as exc:
            LOGGER.warning("object_cleanup_failed namespace=%s error=%s", key.namespace, type(exc).__name__)
            tx.execute(_NOTE_ATTEMPT, params)
            return Settled.FAILED
        tx.execute(_FORGET_CLAIM, params)
        return Settled.REMOVED

    return db.command(run)


def retry_claims(db: Database, storage: StorageBackend, *, library_id: Optional[str] = None,
                 limit: int = DEFAULT_RETRY_LIMIT) -> Counter[Settled]:
    """One bounded pass over due claims (never-attempted first), each settled in its own transaction."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    rows = db.query(lambda tx: [(row["namespace"], row["name"]) for row in tx.execute(
        _DUE_CLAIMS, {"library_id": library_id, "limit": limit}).fetchall()])
    outcomes: Counter[Settled] = Counter()
    for namespace, name in rows:
        try:
            key = StorageKey(namespace, name)
        except InvalidStorageKey:
            # Never a key PRKS wrote. Stamped like an attempt, so it rotates
            # behind the valid claims instead of filling every pass.
            LOGGER.warning("object_cleanup_invalid_claim namespace=%s", namespace)
            db.command(lambda tx, params={"namespace": namespace, "name": name}: tx.execute(
                _NOTE_ATTEMPT, params))
            outcomes[Settled.FAILED] += 1
            continue
        outcomes[settle(db, storage, key)] += 1
    return outcomes


def sweep(db: Database, storage: StorageBackend, *, grace: timedelta = DEFAULT_SWEEP_GRACE,
          now: Optional[float] = None,
          namespaces: Iterable[str] = (NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS)) -> int:
    """Claim every object older than ``grace`` that nothing names; returns how many were claimed.

    Never deletes: the claims are settled by :func:`settle` like any other.
    A write still in progress is younger than the grace period; one that
    stalls past it is caught by its own :func:`adopt`.
    """
    moment = time.time() if now is None else now
    cutoff = moment - grace.total_seconds()
    claimed = 0
    for namespace in namespaces:
        for key in storage.iter_keys(namespace):
            try:
                info = storage.stat(key)
            except StorageObjectError:
                continue
            if info is None or info.modified_at is None or info.modified_at > cutoff:
                continue
            if db.command(lambda tx, key=key: _claim_orphan(tx, key)):
                claimed += 1
    return claimed


def _claim_orphan(tx: Transaction, key: StorageKey) -> bool:
    tx.execute(OBJECT_LOCK_SQL, (str(key),))
    if references(tx, key) is not False:
        return False
    return tx.execute(_RECORD_ORPHAN, _key_params(key)).rowcount == 1
