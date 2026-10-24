"""Job diagnostics (``docs/postgresql-architecture.md`` §12.4; L-J-1 #627).

Two privacy-safe views: :class:`WorkerStats`, a process-local set of
counters one worker keeps, and :func:`queue_depth`, the queue's depth and
age by kind and state. Both carry only counts, ages and job kinds; never a
payload, an error text, a Library id or any other Library metadata.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass

from backend.db.database import Database

# The counters a worker keeps, in display order.
COUNTERS = (
    "claimed",  # claims, reclaims included
    "succeeded",
    "retried",  # a handled failure, queued again with backoff
    "cancelled",  # a queued twin does the work, or the Library's epoch moved on
    "failed",  # attempts exhausted
    "lost",  # the lease was lost; the result discarded
    "released",  # given back on a graceful stop that ran past its deadline
    "reaped",  # expired runs the reaper settled
    "errors",  # the worker could not reach the database for a step
)


class WorkerStats:
    """Process-local counters of one worker; safe to read from another thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = dict.fromkeys(COUNTERS, 0)

    def add(self, counter: str, n: int = 1) -> None:
        if counter not in self._counts:
            raise ValueError(f"unknown worker counter {counter!r}")
        with self._lock:
            self._counts[counter] += n

    def snapshot(self) -> Mapping[str, int]:
        with self._lock:
            return dict(self._counts)


@dataclass(frozen=True)
class QueueDepth:
    kind: str
    state: str
    count: int
    oldest_age_seconds: float  # since the oldest row of this kind and state was created


_DEPTH_SQL = """
SELECT kind, state, count(*) AS n,
       extract(epoch FROM now() - min(created_at))::float8 AS oldest
  FROM prks.jobs
 WHERE state IN ('queued', 'running', 'failed')
 GROUP BY kind, state
 ORDER BY kind, state
"""


def queue_depth(db: Database) -> list[QueueDepth]:
    """Queued, running and failed jobs by kind; succeeded and cancelled rows are history."""
    rows = db.query(lambda tx: tx.execute(_DEPTH_SQL).fetchall())
    return [
        QueueDepth(
            kind=str(row["kind"]),
            state=str(row["state"]),
            count=int(row["n"]),
            oldest_age_seconds=max(0.0, float(row["oldest"])),
        )
        for row in rows
    ]
