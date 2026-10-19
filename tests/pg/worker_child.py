"""Child process for the worker SIGTERM test (tests/pg/test_jobs_worker.py).

Runs a worker with the signal handlers ``prks worker`` installs and one
``test.child`` handler that prints ``running``, waits for the file its
payload names, then writes one fenced effect. The test sends SIGTERM while
the job runs: the job must finish and the process exit 0.

Usage: python -m tests.pg.worker_child CONNINFO
"""

from __future__ import annotations

import os
import sys
import time
from datetime import timedelta

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from backend.jobs.worker import Worker, WorkerSettings, _install_signals


def child_job(run: JobRun) -> None:
    print("running", flush=True)
    deadline = time.monotonic() + 30
    while not os.path.exists(str(run.payload["go"])):
        if time.monotonic() > deadline:
            raise TimeoutError()
        time.sleep(0.01)
    run.command(lambda tx: tx.execute("INSERT INTO prks.instance_effects (note) VALUES ('child')"))


def main(argv: list[str]) -> int:
    (conninfo,) = argv
    registry = HandlerRegistry()
    registry.register("test.child", child_job)
    with Database(DatabaseConfig(conninfo=conninfo, role="worker", pool_min=0)) as db:
        worker = Worker(
            db, registry=registry,
            settings=WorkerSettings(poll=timedelta(milliseconds=50), stop_grace=timedelta(seconds=30)),
        )
        _install_signals(worker)
        worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
