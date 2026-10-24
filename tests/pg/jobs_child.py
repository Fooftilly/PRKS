"""Child process for the jobs kill test (tests/pg/test_jobs_queue.py).

Claims one job, runs its handler's write (which commits), prints the job id
and lease, and is then killed with SIGKILL before it can record completion,
as for a worker container killed mid-job: no ``finally`` runs, and the
server ends the session.

Usage: python -m tests.pg.jobs_child CONNINFO WORKER
"""

from __future__ import annotations

import os
import signal
import sys

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.jobs import queue
from backend.jobs.runner import JobRun
from tests.pg.jobs_fixtures import LEASE, set_revision


def main(argv: list[str]) -> int:
    conninfo, worker = argv
    db = Database(DatabaseConfig(conninfo=conninfo, role="test", pool_min=0))
    job = queue.claim(db, worker, lease=LEASE)
    if job is None:
        print("none", flush=True)
        return 3
    set_revision(JobRun(db, job, lease=LEASE))  # the side effect commits
    print(f"{job.id} {job.lease_id}", flush=True)
    os.kill(os.getpid(), signal.SIGKILL)
    return 4  # not reached


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
