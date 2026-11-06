"""A web process that serves its Library from PostgreSQL (PG-C1 #640, ``backend.library_backend``).

``python prks_app.py --testing`` with ``PRKS_AUTH`` and
``PRKS_LIBRARY_BACKEND=postgres`` runs as a real process: it binds the
PostgreSQL facade, never opens or creates a SQLite catalogue, and its routes
read and write ``prks.*``. The selection is refused without ``PRKS_AUTH``,
with an unknown value, and on a root that still holds a SQLite catalogue.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
from typing import Any, Optional

from backend.storage import root_binding
from backend.storage.config import StorageConfig
from tests.pg.test_readiness import _free_port, _wait_for
from tests.pg.test_root_pairing import RootPairingTestCase

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CATALOGUES = ("prks_data.db", "prks_data_testing.db")


class PostgresLibraryStartTests(RootPairingTestCase):
    def migrated(self) -> str:
        name = self.harness.create_database("pglib", empty=True)
        self.addCleanup(self.harness.drop_database, name)
        from backend.db.migrate import migrate

        migrate(self.harness.conninfo(name))
        return name

    def environment(self, name: Optional[str], **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRKS_") or k == "PRKS_TEST_DATABASE_URL"}
        env["PRKS_EMBEDDED_WORKER"] = "0"
        if name is not None:
            env.update({"PRKS_AUTH": "loopback-owner", "PRKS_DATABASE_URL": self.harness.conninfo(name)})
        env.update(extra)
        return env

    def web(self, env: dict[str, str], root: str, port: int) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "prks_app.py", "--testing", "--storage-root", root, "--port", str(port)],
            cwd=_PROJECT_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )

    def stop(self, proc: subprocess.Popen[str]) -> None:
        proc.send_signal(signal.SIGTERM)
        _out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err)

    def refused(self, env: dict[str, str], root: str) -> str:
        proc = self.web(env, root, _free_port())
        _out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 2, err)
        return err

    def bootstrapped(self, name: str, root: str) -> None:
        lease = root_binding.acquire_shared_root_lease(root)
        try:
            self.bootstrap(name, root, lease=lease)
        finally:
            lease.release()
        self.close(name)

    def call(self, port: int, method: str, path: str, body: Any = None,
             headers: Optional[dict[str, str]] = None) -> tuple[int, Any, dict[str, str]]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            sent = dict(headers or {})
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                sent["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=sent)
            res = conn.getresponse()
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None), {k.lower(): v for k, v in res.getheaders()}
        finally:
            conn.close()

    def test_the_web_process_serves_works_from_postgresql(self) -> None:
        name = self.migrated()
        root = self.new_root()
        self.bootstrapped(name, root)
        port = _free_port()
        proc = self.web(self.environment(name, PRKS_LIBRARY_BACKEND="postgres"), root, port)
        try:
            self.assertEqual(_wait_for(port, "/readyz", proc), 200)
            status, session, _headers = self.call(port, "GET", "/api/session")
            self.assertEqual(status, 200, session)
            epoch = str(session["library"]["epoch"])
            status, created, _headers = self.call(
                port, "POST", "/api/works", {"title": "Served from PostgreSQL"},
                {"Origin": f"http://127.0.0.1:{port}", "PRKS-Library-Epoch": epoch},
            )
            self.assertIn(status, (200, 201), created)
            status, works, _headers = self.call(port, "GET", "/api/works")
            self.assertEqual(status, 200, works)
            self.assertEqual([w["title"] for w in works], ["Served from PostgreSQL"])
            db = self.db(name)
            rows = db.query(lambda tx: tx.execute("SELECT title FROM prks.works").fetchall())
            self.assertEqual([r["title"] for r in rows], ["Served from PostgreSQL"])
            origin = {"Origin": f"http://127.0.0.1:{port}", "PRKS-Library-Epoch": epoch}
            # The SQLite backup routes would replace the root and rebind to SQLite.
            for method, path in (("POST", "/api/backups/restore"), ("POST", "/api/backups/progress"),
                                 ("GET", "/api/backups/download")):
                status, refused, _headers = self.call(port, method, path, {} if method == "POST" else None,
                                                      origin if method == "POST" else None)
                self.assertEqual((status, refused["code"]), (501, "not_ported"), path)
            # A file on the shared root that no Asset of this Library names is not served.
            pdfs = StorageConfig.for_testing(root).pdfs_dir
            os.makedirs(pdfs, exist_ok=True)
            with open(os.path.join(pdfs, "loose.pdf"), "wb") as handle:
                handle.write(b"%PDF-1.4\nloose\n%%EOF\n")
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                conn.request("GET", "/api/pdfs/loose.pdf")
                res = conn.getresponse()
                res.read()
                self.assertEqual(res.status, 404)
            finally:
                conn.close()
        finally:
            self.stop(proc)
        for catalogue in _CATALOGUES:
            self.assertFalse(os.path.lexists(os.path.join(root, catalogue)), catalogue)

    def test_the_selection_needs_prks_auth(self) -> None:
        err = self.refused(self.environment(None, PRKS_LIBRARY_BACKEND="postgres"), self.new_root())
        self.assertIn("PRKS_LIBRARY_BACKEND=postgres needs PRKS_AUTH", err)

    def test_an_unknown_selection_is_refused(self) -> None:
        name = self.migrated()
        err = self.refused(self.environment(name, PRKS_LIBRARY_BACKEND="sqlite"), self.new_root())
        self.assertIn("PRKS_LIBRARY_BACKEND must be unset or 'postgres'", err)

    def test_a_root_holding_a_sqlite_catalogue_is_refused(self) -> None:
        name = self.migrated()
        root = self.new_root()
        self.bootstrapped(name, root)
        with open(os.path.join(root, "prks_data_testing.db"), "wb"):
            pass
        err = self.refused(self.environment(name, PRKS_LIBRARY_BACKEND="postgres"), root)
        self.assertIn("still holds a SQLite catalogue", err)
