"""Without ``PRKS_AUTH`` the web process runs no job worker, no object maintenance, and never loads psycopg.

L-J-1 #627; L-A-1 #635.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PROBE = """
import sys, threading
import prks_app
from backend import server
assert prks_app.start_jobs(None, {"PRKS_EMBEDDED_WORKER": "1"}) is None
loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('psycopg', 'psycopg_pool')
                or m.startswith(('backend.jobs', 'backend.domain.objects')))
threads = sorted(t.name for t in threading.enumerate() if t is not threading.main_thread())
print(repr((loaded, threads)))
"""


class EmbeddedWorkerOffTests(unittest.TestCase):
    def test_the_sqlite_runtime_has_no_worker_thread_and_no_psycopg(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PRKS_AUTH", "PRKS_EMBEDDED_WORKER")}
        env["PRKS_TESTING"] = "1"
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE], cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(([], [])))

    def test_the_sqlite_runtime_answers_queue_paths_without_psycopg(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PRKS_AUTH", "PRKS_EMBEDDED_WORKER")}
        env["PRKS_TESTING"] = "1"
        proc = subprocess.run(
            [sys.executable, "-c", _QUEUE_PROBE], cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), repr(([], [404, 404])))


# Every queue handler, called as server.py calls it on the SQLite runtime (no
# admitted context, a PRKSDatabase-like db): none claims a path that is not
# its own to answer, and nothing imports psycopg (backend/api/processing.py).
_QUEUE_PROBE = """
import sys
from backend.api import processing
class Handler:
    _prks_request_context = None
    errors = []
    def send_error(self, status, message=None):
        self.errors.append(status)
handler, db, item = Handler(), object(), "/api/processing-items/PI-" + "0" * 32
claimed = [
    processing.handle_get(handler, db, "/api/processing-items", ""),
    processing.handle_get(handler, db, item, ""),
    processing.handle_patch(handler, db, item, {"revision": 0}),
    processing.handle_delete(handler, db, item),
    processing.handle_post(handler, db, item + "/import", {}),
    processing.handle_post(handler, db, "/api/processing-items/import", {"item_ids": []}),
    processing.handle_get(handler, db, "/api/positions", ""),
]
assert claimed == [False] * len(claimed), claimed
assert processing.handle_head(handler, db, item + "/pdf") is True
processing.handle_upload(handler, db)
loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('psycopg', 'psycopg_pool')
                or m.startswith('backend.domain.processing'))
print(repr((loaded, handler.errors)))
"""


if __name__ == "__main__":
    unittest.main()
