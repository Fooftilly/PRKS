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


if __name__ == "__main__":
    unittest.main()
