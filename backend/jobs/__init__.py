"""PostgreSQL jobs runtime (``docs/postgresql-architecture.md`` §12.1; L-J-1 #627).

The ``prks.jobs`` table is both the job queue and the internal outbox: a job
is enqueued inside the transaction of the command that needs it, so it exists
if and only if that command committed. This package owns the runtime on that
table:

- :mod:`backend.jobs.queue`: enqueue, claim and reclaim, heartbeat,
  completion, failure with retry, release and the reaper, each one statement
  fenced by the job's ``lease_id``.
- :mod:`backend.jobs.handlers`: the registry that maps a job ``kind`` to the
  handler of the lane that owns it.
- :mod:`backend.jobs.runner`: runs one claimed job with a heartbeat and
  records its outcome.
- :mod:`backend.jobs.worker`: the worker loop and the ``prks worker`` process.
- :mod:`backend.jobs.embedded`: the worker thread of a ``PRKS_AUTH`` web
  process and its job diagnostics.

Like the rest of the PostgreSQL code, nothing here is imported by the running
SQLite application.
"""
