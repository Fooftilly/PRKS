"""The ``jobs.prune`` maintenance job (``docs/postgresql-architecture.md`` §12.1; L-J-1 #627).

Deletes ``succeeded`` and ``cancelled`` jobs that finished more than
:data:`RETENTION` ago. ``failed`` jobs stay until dismissed, and ``queued``
or ``running`` ones are never touched. Cancelled rows are history like
succeeded ones (a queued twin did the work, or a restore superseded it), so
they are pruned too rather than kept forever.

Each worker enqueues it at most once per interval on its idle tick
(:mod:`backend.jobs.worker`), deduplicated and singleton, so any number of
workers run one prune at a time. It deletes in batches, each its own fenced
transaction, and reports progress between them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import LiteralString

from backend.db.database import Transaction
from backend.jobs.handlers import handler
from backend.jobs.runner import JobRun

PRUNE_KIND = "jobs.prune"
RETENTION = timedelta(days=7)
BATCH = 1000

_PRUNE_SQL: LiteralString = """
DELETE FROM prks.jobs
 WHERE id IN (SELECT id FROM prks.jobs
               WHERE state IN ('succeeded', 'cancelled')
                 AND finished_at < now() - make_interval(secs => %(retention)s)
               ORDER BY finished_at
               LIMIT %(batch)s)
"""


@handler(PRUNE_KIND)
def prune_jobs(run: JobRun) -> None:
    params = {"retention": RETENTION.total_seconds(), "batch": BATCH}

    def batch(tx: Transaction) -> int:
        return tx.execute(_PRUNE_SQL, params).rowcount

    deleted = 0
    while True:
        n = run.command(batch)
        deleted += n
        if n < BATCH:
            return
        run.heartbeat({"deleted": deleted})
