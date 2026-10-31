"""The job queue statements (``docs/postgresql-architecture.md`` §12.1; L-J-1 #627).

Every function here is one statement in its own transaction, except
:func:`enqueue` and :func:`enqueue_library_job`, which run inside the
caller's command transaction so the job commits with the change that needs
it (the transactional outbox).

**Lease fencing.** Every claim, including a reclaim of a job whose worker
died, mints a new ``lease_id``. :func:`heartbeat`, :func:`complete`,
:func:`fail` and :func:`release` match the job's ``id``, that ``lease_id``
**and** ``state = 'running'``; a worker that lost its lease (reclaimed, reaped,
or cancelled by a restore) updates nothing and learns it from the result.

**Singleton arbitration.** Two workers can each see no running row for a
``singleton_key`` and claim different queued rows of it; the second claim
then fails on ``jobs_singleton`` with ``23505``. Nothing changed, so
:func:`claim` claims again, and now skips that key. Any other error stays an
error, so no caller ever sees that ``23505``.

**Retry versus a queued twin.** ``jobs_dedupe`` covers queued rows only, so
a failed job cannot always return to ``queued``: a twin with the same
``(kind, dedupe_key)`` may be queued already. :func:`fail` therefore ends the
job ``failed`` once its attempts are exhausted (checked first, so a last
attempt never returns to ``queued`` while holding its dedupe key), else
``cancelled`` when a twin is queued (the twin does the same work), else
``queued`` with exponential backoff. A twin committed between that check and
the update makes the update fail with ``23505`` on ``jobs_dedupe``, and the
statement is repeated, now seeing the twin.

**Privacy.** ``last_error`` holds a short code (an exception type name or a
PRKS error code), never a message, a path, a payload value or Library
metadata (backend/AGENTS.md, logging rules). NOTIFY payloads are empty.
"""

from __future__ import annotations

import enum
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, LiteralString, Optional

from backend.db.database import Database, Transaction
from backend.db.errors import UNIQUE_VIOLATION, DatabaseError
from backend.domain.identity.authorization import AccessRefused
from backend.domain.identity.library_scope import LibraryScope

# The channel every enqueue notifies at COMMIT; workers LISTEN on it.
NOTIFY_CHANNEL = "prks_jobs"

# The partial unique indexes of 030_jobs.sql whose 23505 is part of the protocol.
SINGLETON_INDEX = "jobs_singleton"
DEDUPE_INDEX = "jobs_dedupe"

DEFAULT_LEASE = timedelta(seconds=60)
DEFAULT_MAX_ATTEMPTS = 10

# A race lost on a partial unique index is repeated at once. Each repeat
# follows a commit by another worker that the next statement sees, so a
# handful is plenty; running out means something else is wrong.
_RACE_RETRIES = 8

# Dotted lower-case names such as ``text.extract`` or ``jobs.prune`` (§12.1).
_KIND_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", re.ASCII)
_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}", re.ASCII)
_KEY_MAX = 512


class JobState(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LeaseLost(Exception):
    """The worker no longer holds the job's lease; it must discard its result."""


@dataclass(frozen=True)
class Backoff:
    """Delay before retry n (attempts already made): ``base * 2**(n-1)``, at most ``cap``."""

    base: timedelta = timedelta(seconds=10)
    cap: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if self.base < timedelta(0) or self.cap < self.base:
            raise ValueError("backoff needs 0 <= base <= cap")


DEFAULT_BACKOFF = Backoff()


@dataclass(frozen=True)
class ClaimedJob:
    """A job this worker holds under ``lease_id``. ``attempts`` counts claims, this one included."""

    id: uuid.UUID
    kind: str
    payload: Mapping[str, Any]
    library_id: Optional[str]
    library_epoch: Optional[int]
    lease_id: uuid.UUID
    attempts: int
    max_attempts: int

    def __repr__(self) -> str:  # payloads can name Library content; keep them out of logs
        return f"ClaimedJob(id={self.id!r}, kind={self.kind!r}, attempts={self.attempts})"


def check_kind(kind: str) -> str:
    if not isinstance(kind, str) or _KIND_RE.fullmatch(kind) is None:
        raise ValueError("a job kind is a dotted lower-case name such as 'jobs.prune'")
    return kind


def error_code(exc: BaseException) -> str:
    """The metadata-only ``last_error`` for ``exc``: a PRKS error code or the exception type name."""
    if isinstance(exc, DatabaseError):
        code = exc.code
    elif isinstance(exc, AccessRefused):
        code = exc.refusal.code
    else:
        code = type(exc).__name__
    return code if _ERROR_CODE_RE.fullmatch(code) else "error"


def _check_error(code: str) -> str:
    if _ERROR_CODE_RE.fullmatch(code) is None:
        raise ValueError("last_error is a short code, never a message")
    return code


def _check_key(value: Optional[str], name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _KEY_MAX:
        raise ValueError(f"{name} must be a non-empty string of at most {_KEY_MAX} characters")
    return value


def _seconds(delta: timedelta) -> float:
    return delta.total_seconds()


def _lost_race(exc: DatabaseError, index: str) -> bool:
    return exc.sqlstate == UNIQUE_VIOLATION and exc.constraint == index


# --- enqueue (inside the caller's command transaction) ------------------------------


_INSERT_SQL: LiteralString = """
INSERT INTO prks.jobs (library_id, library_epoch, kind, payload, dedupe_key, singleton_key,
                       priority, run_after, max_attempts, created_by)
VALUES (%(library_id)s, %(library_epoch)s, %(kind)s, %(payload)s::jsonb, %(dedupe_key)s,
        %(singleton_key)s, %(priority)s, now() + make_interval(secs => %(delay)s),
        %(max_attempts)s, %(created_by)s)
ON CONFLICT (kind, dedupe_key) WHERE state = 'queued' DO NOTHING
RETURNING id
"""

_NOTIFY_SQL: LiteralString = "SELECT pg_notify('prks_jobs', '')"


def _job_params(
    *,
    kind: str,
    payload: Optional[Mapping[str, Any]],
    dedupe_key: Optional[str],
    singleton_key: Optional[str],
    priority: int,
    delay: timedelta,
    max_attempts: int,
    created_by: Optional[str],
) -> dict[str, Any]:
    if not -32768 <= priority <= 32767:
        raise ValueError("priority is a smallint")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if delay < timedelta(0):
        raise ValueError("delay must not be negative")
    return {
        "kind": check_kind(kind),
        # Plain JSON text: a payload must not depend on psycopg adapters.
        "payload": json.dumps(dict(payload or {}), separators=(",", ":"), allow_nan=False),
        "dedupe_key": _check_key(dedupe_key, "dedupe_key"),
        "singleton_key": _check_key(singleton_key, "singleton_key"),
        "priority": priority,
        "delay": _seconds(delay),
        "max_attempts": max_attempts,
        "created_by": created_by,
    }


def enqueue(
    tx: Transaction,
    kind: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    dedupe_key: Optional[str] = None,
    singleton_key: Optional[str] = None,
    priority: int = 0,
    delay: timedelta = timedelta(0),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    created_by: Optional[str] = None,
) -> Optional[uuid.UUID]:
    """Enqueue an instance job (no Library) in the caller's transaction.

    Returns the new job's id, or ``None`` when a queued job of the same
    ``(kind, dedupe_key)`` already exists (the enqueue coalesced into it).
    Workers are notified at COMMIT only; a rolled-back transaction leaves
    neither a job nor a notification.
    """
    params = _job_params(
        kind=kind, payload=payload, dedupe_key=dedupe_key, singleton_key=singleton_key,
        priority=priority, delay=delay, max_attempts=max_attempts, created_by=created_by,
    )
    row = tx.execute(_INSERT_SQL, {**params, "library_id": None, "library_epoch": None}).fetchone()
    if row is None:
        return None
    tx.execute(_NOTIFY_SQL)
    return row["id"]


def enqueue_library_job(
    scope: LibraryScope,
    kind: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    dedupe_key: Optional[str] = None,
    singleton_key: Optional[str] = None,
    priority: int = 0,
    delay: timedelta = timedelta(0),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    created_by: Optional[str] = None,
) -> Optional[uuid.UUID]:
    """Enqueue a job of the scope's Library inside a ``library_command``.

    The job records the epoch the command checked (``scope.epoch``), so a
    restore that advances the epoch fences it: it is never claimed, and its
    handler's commands are refused (§11.4). Returns ``None`` on a dedupe hit.
    """
    params = _job_params(
        kind=kind, payload=payload, dedupe_key=dedupe_key, singleton_key=singleton_key,
        priority=priority, delay=delay, max_attempts=max_attempts, created_by=created_by,
    )
    row = scope.execute(_INSERT_SQL, {**params, "library_epoch": scope.epoch}).fetchone()
    if row is None:
        return None
    scope.execute_unscoped(_NOTIFY_SQL)
    return row["id"]


def enqueue_instance_job_for_library(
    scope: LibraryScope,
    kind: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    dedupe_key: Optional[str] = None,
    singleton_key: Optional[str] = None,
    priority: int = 0,
    delay: timedelta = timedelta(0),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Optional[uuid.UUID]:
    """Enqueue an instance job (no Library) inside a ``library_command``.

    For instance maintenance that a Library change makes due, such as an
    object cleanup pass (cleanup claims span Libraries, §11.2): the job
    commits with the command, like :func:`enqueue_library_job`, but no
    restore fences it, because it acts on no Library's rows. The row has no
    ``library_id``, so the insert is reviewed as unscoped. Returns ``None``
    on a dedupe hit.
    """
    params = _job_params(
        kind=kind, payload=payload, dedupe_key=dedupe_key, singleton_key=singleton_key,
        priority=priority, delay=delay, max_attempts=max_attempts, created_by=None,
    )
    row = scope.execute_unscoped(_INSERT_SQL, {**params, "library_id": None, "library_epoch": None}).fetchone()
    if row is None:
        return None
    scope.execute_unscoped(_NOTIFY_SQL)
    return row["id"]


# --- claim and reclaim --------------------------------------------------------------


_CLAIM_SQL: LiteralString = """
UPDATE prks.jobs
   SET state = 'running', lease_id = gen_random_uuid(), locked_by = %(worker)s,
       locked_until = now() + make_interval(secs => %(lease)s), attempts = attempts + 1
 WHERE id = (SELECT j.id FROM prks.jobs j
              WHERE ((j.state = 'queued'  AND j.run_after <= now())
                  OR (j.state = 'running' AND j.locked_until < now()))
                AND j.attempts < j.max_attempts
                AND (j.library_id IS NULL OR EXISTS
                     (SELECT 1 FROM prks.libraries l
                       WHERE l.id = j.library_id AND l.maintenance IS NULL
                         AND l.epoch = j.library_epoch))
                AND (j.singleton_key IS NULL OR j.state = 'running' OR NOT EXISTS
                     (SELECT 1 FROM prks.jobs r
                       WHERE r.singleton_key = j.singleton_key AND r.state = 'running'))
              ORDER BY j.priority DESC, j.run_after
              FOR UPDATE SKIP LOCKED
              LIMIT 1)
RETURNING id, kind, payload, library_id, library_epoch, lease_id, attempts, max_attempts
"""


def claim(db: Database, worker: str, *, lease: timedelta = DEFAULT_LEASE) -> Optional[ClaimedJob]:
    """Claim the next runnable job, or reclaim one whose lease expired; ``None`` when there is none."""
    if not worker or len(worker) > 200:
        raise ValueError("worker is a non-empty id of at most 200 characters")
    if lease <= timedelta(0):
        raise ValueError("lease must be positive")
    params = {"worker": worker, "lease": _seconds(lease)}
    for _ in range(_RACE_RETRIES):
        try:
            row = db.command(lambda tx: tx.execute(_CLAIM_SQL, params).fetchone())
        except DatabaseError as exc:
            if _lost_race(exc, SINGLETON_INDEX):
                continue  # another worker took this singleton key first; nothing changed
            raise
        if row is None:
            return None
        return ClaimedJob(
            id=row["id"],
            kind=row["kind"],
            payload=row["payload"],
            library_id=row["library_id"],
            library_epoch=row["library_epoch"],
            lease_id=row["lease_id"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
        )
    raise RuntimeError("the job claim kept losing singleton races")


# --- the worker's updates, each fenced by the lease --------------------------------


_HEARTBEAT_SQL: LiteralString = """
UPDATE prks.jobs
   SET locked_until = now() + make_interval(secs => %(lease)s),
       progress = COALESCE(%(progress)s::jsonb, progress)
 WHERE id = %(id)s AND lease_id = %(lease_id)s AND state = 'running'
RETURNING id
"""


def heartbeat(
    db: Database,
    job: ClaimedJob,
    *,
    lease: timedelta = DEFAULT_LEASE,
    progress: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Extend the lease (and record ``progress``); ``False`` when the lease is lost."""
    params = {
        "id": job.id,
        "lease_id": job.lease_id,
        "lease": _seconds(lease),
        "progress": None if progress is None else json.dumps(dict(progress), allow_nan=False),
    }
    return db.command(lambda tx: tx.execute(_HEARTBEAT_SQL, params).fetchone()) is not None


_COMPLETE_SQL: LiteralString = """
UPDATE prks.jobs
   SET state = 'succeeded', finished_at = now(), lease_id = NULL, locked_by = NULL,
       locked_until = NULL, last_error = NULL
 WHERE id = %(id)s AND lease_id = %(lease_id)s AND state = 'running'
RETURNING id
"""


def complete(db: Database, job: ClaimedJob) -> bool:
    """Mark the job ``succeeded``; ``False`` (nothing changed) when the lease is lost."""
    params = {"id": job.id, "lease_id": job.lease_id}
    return db.command(lambda tx: tx.execute(_COMPLETE_SQL, params).fetchone()) is not None


# A Library job whose epoch moved on (a restore) can never be claimed again,
# so it is never put back: it ends ``cancelled`` and frees its dedupe and
# singleton keys for the new epoch's work. A Library in maintenance is not
# stale; its epoch is checked again once maintenance ends.
_STALE_EPOCH: LiteralString = """
(j.library_id IS NOT NULL AND NOT EXISTS
   (SELECT 1 FROM prks.libraries l WHERE l.id = j.library_id AND l.epoch = j.library_epoch))
"""

# The twin test is the same in fail() and release(): another queued row with
# this job's (kind, dedupe_key). A NULL dedupe_key has no twin.
_FAIL_SQL: LiteralString = """
WITH cur AS (
  SELECT j.id,
         """ + _STALE_EPOCH + """ AS stale,
         j.attempts >= j.max_attempts AS exhausted,
         EXISTS (SELECT 1 FROM prks.jobs t
                  WHERE t.kind = j.kind AND t.dedupe_key = j.dedupe_key
                    AND t.state = 'queued' AND t.id <> j.id) AS twin,
         LEAST(%(cap)s, %(base)s * power(2, GREATEST(j.attempts - 1, 0))) AS backoff
    FROM prks.jobs j
   WHERE j.id = %(id)s AND j.lease_id = %(lease_id)s AND j.state = 'running'
)
UPDATE prks.jobs j
   SET state = CASE WHEN cur.stale THEN 'cancelled'
                    WHEN cur.exhausted THEN 'failed'
                    WHEN cur.twin THEN 'cancelled'
                    ELSE 'queued' END,
       run_after = CASE WHEN cur.stale OR cur.exhausted OR cur.twin THEN j.run_after
                        ELSE now() + make_interval(secs => cur.backoff) END,
       finished_at = CASE WHEN cur.stale OR cur.exhausted OR cur.twin THEN now() ELSE NULL END,
       last_error = %(error)s,
       lease_id = NULL, locked_by = NULL, locked_until = NULL
  FROM cur
 WHERE j.id = cur.id AND j.lease_id = %(lease_id)s AND j.state = 'running'
RETURNING j.state
"""


def fail(
    db: Database,
    job: ClaimedJob,
    error: str,
    *,
    backoff: Backoff = DEFAULT_BACKOFF,
) -> Optional[JobState]:
    """Record a handled failure; the new state, or ``None`` when the lease is lost.

    ``cancelled`` when the Library's epoch moved on, else ``failed`` once
    attempts are exhausted, else ``cancelled`` when a twin is queued, else
    ``queued`` again after the backoff. ``error`` is a code
    (see :func:`error_code`), never a message.
    """
    params = {
        "id": job.id,
        "lease_id": job.lease_id,
        "error": _check_error(error),
        "base": _seconds(backoff.base),
        "cap": _seconds(backoff.cap),
    }
    return _settle(db, _FAIL_SQL, params)


_RELEASE_SQL: LiteralString = """
WITH cur AS (
  SELECT j.id,
         """ + _STALE_EPOCH + """ AS stale,
         EXISTS (SELECT 1 FROM prks.jobs t
                  WHERE t.kind = j.kind AND t.dedupe_key = j.dedupe_key
                    AND t.state = 'queued' AND t.id <> j.id) AS twin
    FROM prks.jobs j
   WHERE j.id = %(id)s AND j.lease_id = %(lease_id)s AND j.state = 'running'
)
UPDATE prks.jobs j
   SET state = CASE WHEN cur.stale OR cur.twin THEN 'cancelled' ELSE 'queued' END,
       attempts = CASE WHEN cur.stale OR cur.twin THEN j.attempts ELSE GREATEST(j.attempts - 1, 0) END,
       run_after = CASE WHEN cur.stale OR cur.twin THEN j.run_after ELSE now() END,
       finished_at = CASE WHEN cur.stale OR cur.twin THEN now() ELSE NULL END,
       last_error = CASE WHEN cur.stale THEN 'library_epoch_changed'
                         WHEN cur.twin THEN 'worker_stopped'
                         ELSE j.last_error END,
       lease_id = NULL, locked_by = NULL, locked_until = NULL
  FROM cur
 WHERE j.id = cur.id AND j.lease_id = %(lease_id)s AND j.state = 'running'
RETURNING j.state
"""


def release(db: Database, job: ClaimedJob) -> Optional[JobState]:
    """Give the job back on a graceful stop; the new state, or ``None`` when the lease is lost.

    The job returns to ``queued`` at once without spending the attempt this
    claim counted (a stop is not a failure), or is ``cancelled`` when the
    Library's epoch moved on or a twin is queued.
    """
    return _settle(db, _RELEASE_SQL, {"id": job.id, "lease_id": job.lease_id})


def _settle(db: Database, statement: LiteralString, params: Mapping[str, Any]) -> Optional[JobState]:
    for _ in range(_RACE_RETRIES):
        try:
            row = db.command(lambda tx: tx.execute(statement, params).fetchone())
        except DatabaseError as exc:
            if _lost_race(exc, DEDUPE_INDEX):
                continue  # a twin committed after the check; it is visible now
            raise
        return None if row is None else JobState(row["state"])
    raise RuntimeError("the job update kept losing dedupe races")


# --- the reaper ---------------------------------------------------------------------


_REAP_SQL: LiteralString = """
UPDATE prks.jobs j
   SET state = CASE WHEN """ + _STALE_EPOCH + """ THEN 'cancelled' ELSE 'failed' END,
       last_error = CASE WHEN """ + _STALE_EPOCH + """ THEN 'library_epoch_changed'
                         ELSE 'lease_expired' END,
       finished_at = now(), lease_id = NULL, locked_by = NULL, locked_until = NULL
 WHERE j.state = 'running' AND j.locked_until < now()
   AND (j.attempts >= j.max_attempts OR """ + _STALE_EPOCH + """)
RETURNING j.id
"""


def reap(db: Database) -> int:
    """Settle every running job whose lease expired and that no claim can take; the number settled.

    A job with no attempt left ends ``failed``; one whose Library's epoch
    moved on ends ``cancelled``. This frees their singleton keys, and a
    worker that resumes afterwards matches no row (its lease is gone).
    """
    return len(db.command(lambda tx: tx.execute(_REAP_SQL).fetchall()))


# --- reads --------------------------------------------------------------------------


@dataclass(frozen=True)
class JobStatus:
    id: uuid.UUID
    kind: str
    state: JobState
    attempts: int
    max_attempts: int
    run_after: datetime
    finished_at: Optional[datetime]
    last_error: Optional[str]


def job_status(db: Database, job_id: uuid.UUID) -> Optional[JobStatus]:
    row = db.query(
        lambda tx: tx.execute(
            "SELECT id, kind, state, attempts, max_attempts, run_after, finished_at, last_error "
            "FROM prks.jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
    )
    if row is None:
        return None
    return JobStatus(
        id=row["id"],
        kind=row["kind"],
        state=JobState(row["state"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        run_after=row["run_after"],
        finished_at=row["finished_at"],
        last_error=row["last_error"],
    )
