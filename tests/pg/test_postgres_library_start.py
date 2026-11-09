"""A web process that serves its Library from PostgreSQL (PG-C1 #640, ``backend.library_backend``).

``python prks_app.py --testing`` with ``PRKS_AUTH`` and
``PRKS_LIBRARY_BACKEND=postgres`` runs as a real process: it binds the
PostgreSQL facade, never opens or creates a SQLite catalogue, and its routes
read and write ``prks.*``. The selection is refused without ``PRKS_AUTH``,
with an unknown value, and on a root that still holds a SQLite catalogue.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import signal
import subprocess
import sys
import uuid
from typing import Any, Iterator, Optional

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

    @contextlib.contextmanager
    def serving(self) -> Iterator[tuple[str, int, dict[str, str]]]:
        """A ready PostgreSQL web process, with the headers a mutation sends."""
        name = self.migrated()
        root = self.new_root()
        self.bootstrapped(name, root)
        port = _free_port()
        proc = self.web(self.environment(name, PRKS_LIBRARY_BACKEND="postgres"), root, port)
        try:
            self.assertEqual(_wait_for(port, "/readyz", proc), 200)
            _status, session, _headers = self.call(port, "GET", "/api/session")
            yield name, port, {"Origin": f"http://127.0.0.1:{port}",
                               "PRKS-Library-Epoch": str(session["library"]["epoch"])}
        finally:
            self.stop(proc)

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

    def test_sync_operations_apply_through_postgresql(self) -> None:
        with self.serving() as (name, port, headers):
            status, created, _headers = self.call(port, "POST", "/api/works", {"title": "Synced"}, headers)
            self.assertIn(status, (200, 201), created)
            work_id = created["id"]
            device = str(uuid.uuid4())  # a browser the server has never seen

            def op(operation: str, payload: dict[str, Any], base: int) -> dict[str, Any]:
                return {"op_id": str(uuid.uuid4()), "device_id": device, "operation": operation,
                        "entity_type": "work", "entity_id": work_id, "payload": payload,
                        "base_revision": base, "occurred_at": "2026-11-06T10:00:00Z",
                        "created_at": "2026-11-06T10:00:00Z", "depends_on": []}

            title = op("SET_WORK_METADATA_FIELD", {"field": "title", "value": "Renamed offline"}, 0)
            status, acked, _headers = self.call(port, "POST", "/api/sync/operations", title, headers)
            self.assertEqual((status, acked["code"]), (200, "ACKNOWLEDGED"), acked)
            acked_title = acked
            # A replay answers from the ledger.
            self.assertEqual(self.call(port, "POST", "/api/sync/operations", title, headers)[:2],
                             (200, acked))
            status, work, _headers = self.call(port, "GET", f"/api/works/{work_id}")
            self.assertEqual((status, work["title"]), (200, "Renamed offline"))
            # The note ACK carries the refs, still pending: no worker runs here.
            note = op("SET_WORK_RESEARCH_NOTE", {"text": "[[concept:Ethics]]"}, 0)
            status, acked, _headers = self.call(port, "POST", "/api/sync/operations", note, headers)
            self.assertEqual((status, acked["code"]), (200, "ACKNOWLEDGED"), acked)
            self.assertEqual(acked["research_refs"], {"concepts": [], "arguments": [], "pending": True})
            # A stale epoch is refused before anything is applied (§11.4).
            stale = dict(headers, **{"PRKS-Library-Epoch": str(int(headers["PRKS-Library-Epoch"]) + 1)})
            retitle = op("SET_WORK_METADATA_FIELD", {"field": "title", "value": "Stale"}, 1)
            status, refused, _headers = self.call(port, "POST", "/api/sync/operations", retitle, stale)
            self.assertEqual(status, 409, refused)
            # A retry of a ledgered op (a lost ACK) replays at any epoch claim.
            self.assertEqual(self.call(port, "POST", "/api/sync/operations", title, stale)[:2],
                             (200, acked_title))
            # A new op without a claim is still refused by the protocol's fence.
            unclaimed = {k: v for k, v in headers.items() if k != "PRKS-Library-Epoch"}
            status, refused, _headers = self.call(port, "POST", "/api/sync/operations", retitle, unclaimed)
            self.assertEqual((status, refused["code"]), (400, "LIBRARY_EPOCH_REQUIRED"), refused)
            self.assertEqual(self.call(port, "GET", f"/api/works/{work_id}")[1]["title"], "Renamed offline")
            db = self.db(name)
            rows = db.query(lambda tx: tx.execute(
                "SELECT d.user_id, count(o.op_id) AS ops FROM prks.devices d"
                " JOIN prks.sync_operations o ON o.device_id = d.id WHERE d.id = %s GROUP BY d.user_id",
                (device,)).fetchall())
            self.assertEqual([r["ops"] for r in rows], [2])

    def test_work_routes_read_through_the_facade(self) -> None:
        with self.serving() as (name, port, headers):
            _status, created, _headers = self.call(port, "POST", "/api/works", {"title": "Work"}, headers)
            work_id = created["id"]
            status, people, _headers = self.call(port, "GET", f"/api/works/{work_id}/people-state")
            self.assertEqual(status, 200, people)
            status, source, _headers = self.call(port, "GET", f"/api/works/{work_id}/source-state")
            self.assertEqual((status, source), (200, {"work_id": work_id, "revision": 0}))
            # An echoed file_path is dropped; the rest of the patch applies.
            status, _body, _headers = self.call(port, "PATCH", f"/api/works/{work_id}",
                                                {"title": "Patched", "file_path": ""}, headers)
            self.assertEqual(status, 200)
            self.assertEqual(self.call(port, "GET", f"/api/works/{work_id}")[1]["title"], "Patched")
            # Changing it is not ported: a 501, never a SQLite fallback.
            status, refused, _headers = self.call(port, "PATCH", f"/api/works/{work_id}",
                                                  {"file_path": "/api/pdfs/elsewhere.pdf"}, headers)
            self.assertEqual((status, refused["code"]), (501, "not_ported"), refused)
            _status, folder, _headers = self.call(port, "POST", "/api/folders", {"title": "Shelf"}, headers)
            # The refusal comes before any part of the PATCH commits: a folder
            # move sent with a retarget is not applied behind the 501.
            before = self.call(port, "GET", f"/api/works/{work_id}/folder-state")[1]
            status, refused, _headers = self.call(
                port, "PATCH", f"/api/works/{work_id}",
                {"folder_id": folder["id"], "file_path": "/api/pdfs/elsewhere.pdf"}, headers)
            self.assertEqual((status, refused["code"]), (501, "not_ported"), refused)
            self.assertEqual(self.call(port, "GET", f"/api/works/{work_id}/folder-state")[1], before)
            self.assertNotEqual(before.get("folder_id"), folder["id"], before)
            # Both exist, so the facade decides: a filed Work stays in its one folder.
            status, body, _headers = self.call(port, "POST", f"/api/folders/{folder['id']}/works",
                                               {"work_id": work_id}, headers)
            self.assertEqual((status, body), (409, {"error": "This file is already in another folder."}))
            self.assertEqual(self.call(port, "POST", "/api/folders/F-missing/works",
                                       {"work_id": work_id}, headers)[0], 404)
            self.assertEqual(self.call(port, "POST", f"/api/folders/{folder['id']}/works",
                                       {"work_id": "W-" + "0" * 32}, headers)[0], 404)

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
