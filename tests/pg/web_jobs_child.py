"""Child process for the embedded-worker SIGTERM test (tests/pg/test_embedded_worker.py).

Serves a loopback TCP server through ``WebJobs.serve`` on the main thread,
as ``run_server`` does with ``PRKS_AUTH``, with one embedded worker whose
``test.child`` handler prints ``running``, waits for the file its payload
names, then writes one fenced effect. The test sends SIGTERM while the job
runs: the server must stop, the job finish, and the process exit 0.

Usage: python -m tests.pg.web_jobs_child CONNINFO
"""

from __future__ import annotations

import socketserver
import sys
from datetime import timedelta

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.jobs.embedded import EmbeddedWorker, WebJobs
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.worker import WorkerSettings
from tests.pg.worker_child import child_job


class _Nothing(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        pass


def main(argv: list[str]) -> int:
    (conninfo,) = argv
    registry = HandlerRegistry()
    registry.register("test.child", child_job)
    web_db = Database(DatabaseConfig(conninfo=conninfo, role="web", pool_min=0))
    worker_db = Database(DatabaseConfig(conninfo=conninfo, role="worker", pool_min=0))
    worker = EmbeddedWorker(
        worker_db, registry=registry,
        settings=WorkerSettings(poll=timedelta(milliseconds=50), stop_grace=timedelta(seconds=30)),
    )
    with web_db, socketserver.TCPServer(("127.0.0.1", 0), _Nothing) as server:
        WebJobs(web_db, worker).serve(server)
    print(worker.status()["state"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
