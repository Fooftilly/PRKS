"""The worker embedded in the web process and the job diagnostics endpoint (L-J-1 #627, PR 3/3)."""

from __future__ import annotations

import io
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from contextlib import redirect_stderr
from datetime import timedelta
from typing import Any, Optional
from unittest.mock import patch

import prks_app
from backend.api_contract.jobs import JobDiagnostics, JobWorkerCounters
from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.db.errors import DatabaseUnavailableError
from backend.db.migrate import SCHEMA_DIR, migrate
from backend.jobs import diagnostics, embedded
from backend.jobs.embedded import EmbeddedWorker, EmbeddedWorkerConfigError, WebJobs, web_jobs
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.worker import WorkerSettings
from backend.storage import root_binding
from backend.storage.errors import StorageRootRefused
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, LocalFilesystemStorage, StorageKey
from tests.pg.http_fixtures import HttpAuthTestCase
from tests.pg.test_jobs_worker import WAIT, WorkerTestCase, wait_until

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Nothing(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        pass


class EmbeddedWorkerTestCase(WorkerTestCase):
    def worker_db(self) -> Database:
        db = Database(DatabaseConfig(conninfo=self.harness.conninfo(self.name), role="worker", pool_min=0))
        self.addCleanup(db.close)  # idempotent; the worker's stop closes it first
        return db

    def embedded(self, *, schema_dir: str = SCHEMA_DIR, **settings: Any) -> EmbeddedWorker:
        settings.setdefault("poll", timedelta(milliseconds=50))
        return EmbeddedWorker(
            self.worker_db(), registry=self.registry, settings=WorkerSettings(**settings), schema_dir=schema_dir
        )

    def serve(self, jobs: WebJobs) -> tuple[socketserver.TCPServer, threading.Thread, list[BaseException]]:
        """``jobs.serve`` on a thread (no signal handlers there), as run_server does on the main thread."""
        server = socketserver.TCPServer(("127.0.0.1", 0), _Nothing)
        self.addCleanup(server.server_close)
        thread, _result, error = self.in_thread(lambda: jobs.serve(server))

        def stop() -> None:
            if thread.is_alive():
                server.shutdown()
                thread.join(WAIT)

        self.addCleanup(stop)
        return server, thread, error

    def schema_dir_with_pending_file(self) -> str:
        directory = tempfile.mkdtemp(prefix="prks-schema-")
        self.addCleanup(shutil.rmtree, directory, True)
        for entry in os.listdir(SCHEMA_DIR):
            if entry.endswith(".sql"):
                shutil.copy(os.path.join(SCHEMA_DIR, entry), directory)
        with open(os.path.join(directory, "990_later.sql"), "w", encoding="utf-8") as handle:
            handle.write("CREATE TABLE prks.later (id int);\n")
        return directory


class LifecycleTests(EmbeddedWorkerTestCase):
    def test_the_worker_starts_with_the_server_and_stops_with_it_gracefully(self) -> None:
        inside, go = self.blocking_handler()
        job_id = self.enqueue_instance()
        worker = self.embedded()
        jobs = WebJobs(self.db, worker)
        self.assertEqual(worker.status()["state"], "starting")
        server, thread, error = self.serve(jobs)
        self.assertTrue(inside.wait(WAIT))  # claimed and running while the server serves
        self.assertEqual(worker.status()["state"], "running")
        address = server.server_address
        server.shutdown()  # serve_forever returns; the running job gets its grace period
        wait_until(lambda: worker.worker.stopping, "the worker to be told to stop")
        self.assertTrue(thread.is_alive())
        self.assertEqual(worker.status()["state"], "stopping")
        # The listening socket is already closed: a new connection is refused at
        # once instead of waiting in the backlog for the grace period.
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(address, timeout=WAIT).close()
        go.set()
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertEqual(error, [])
        self.assertEqual(self.state(job_id), "succeeded")  # type: ignore[arg-type]
        self.assertEqual(self.effects(), ["done"])
        status = worker.status()
        self.assertEqual(status["state"], "stopped")
        self.assertEqual(status["counters"]["succeeded"], 1)
        self.assertEqual(status["counters"]["released"], 0)

    def test_a_job_past_the_grace_period_is_released_when_the_server_stops(self) -> None:
        inside, go = self.blocking_handler()
        self.addCleanup(go.set)
        job_id = self.enqueue_instance()
        worker = self.embedded(stop_grace=timedelta(0))
        server, thread, error = self.serve(WebJobs(self.db, worker))
        self.assertTrue(inside.wait(WAIT))
        server.shutdown()
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertEqual(error, [])
        self.assertEqual(self.state(job_id), "queued")  # type: ignore[arg-type]
        self.assertEqual(worker.status()["counters"]["released"], 1)

    def test_without_an_embedded_worker_the_server_just_serves(self) -> None:
        job_id = self.enqueue_instance()
        server, thread, error = self.serve(WebJobs(self.db, None))
        server.shutdown()
        thread.join(WAIT)
        self.assertEqual(error, [])
        self.assertEqual(self.state(job_id), "queued")  # type: ignore[arg-type]

    def test_the_web_start_check_is_not_repeated(self) -> None:
        # The web process bumped once before opening its root; the worker only checks the schema.
        ran = threading.Event()
        self.registry.register("test.instance", lambda _run: ran.set())
        self.enqueue_instance()
        with patch("backend.domain.identity.root_pairing.start_process") as start_process:
            server, thread, _error = self.serve(WebJobs(self.db, self.embedded()))
            self.assertTrue(ran.wait(WAIT))
            server.shutdown()
            thread.join(WAIT)
        start_process.assert_not_called()

    def test_a_pending_schema_file_makes_it_wait_while_the_server_serves(self) -> None:
        ran = threading.Event()
        self.registry.register("test.instance", lambda _run: ran.set())
        job_id = self.enqueue_instance()
        directory = self.schema_dir_with_pending_file()
        worker = self.embedded(schema_dir=directory)
        server, thread, _error = self.serve(WebJobs(self.db, worker))
        wait_until(lambda: worker.status()["state"] == "waiting", "the worker to wait")
        self.assertEqual(worker.status()["reason"], "pending")
        self.assertTrue(thread.is_alive())  # still serving
        self.assertEqual(self.state(job_id), "queued")  # type: ignore[arg-type]
        migrate(self.harness.conninfo(self.name), directory=directory)
        self.assertTrue(ran.wait(WAIT))
        self.assertEqual(worker.status()["reason"], None)
        server.shutdown()
        thread.join(WAIT)

    def test_a_schema_newer_than_the_code_refuses_the_worker_not_the_server(self) -> None:
        self.run_sql(
            "INSERT INTO prks.schema_migrations (version, name, sha256) VALUES (999, 'future', %s)",
            ("0" * 64,),
        )
        job_id = self.enqueue_instance()
        worker = self.embedded()
        jobs = WebJobs(self.db, worker)
        server, thread, error = self.serve(jobs)
        wait_until(lambda: worker.status()["state"] == "refused", "the worker to be refused")
        self.assertTrue(thread.is_alive())  # the web process keeps serving
        body = jobs.diagnostics()
        assert body is not None
        self.assertEqual(body["embedded_worker"]["state"], "refused")
        self.assertIsNotNone(body["embedded_worker"]["reason"])
        self.assertIn("embedded_worker_refused reason=", self.logs.getvalue())
        server.shutdown()
        thread.join(WAIT)
        self.assertEqual(error, [])
        self.assertEqual(self.state(job_id), "queued")  # type: ignore[arg-type]

    def test_sigterm_stops_the_server_and_lets_the_running_job_finish(self) -> None:
        directory = tempfile.mkdtemp(prefix="prks-web-jobs-")
        self.addCleanup(shutil.rmtree, directory, True)
        go_file = os.path.join(directory, "go")
        job_id = self.enqueue_instance(kind="test.child", payload={"go": go_file})
        child = subprocess.Popen(
            [sys.executable, "-m", "tests.pg.web_jobs_child", self.harness.conninfo(self.name)],
            cwd=_PROJECT_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: child.poll() is None and child.kill())
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "running")
        child.send_signal(signal.SIGTERM)
        with open(go_file, "w", encoding="utf-8"):
            pass
        out, err = child.communicate(timeout=WAIT)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "stopped")
        self.assertEqual(self.state(job_id), "succeeded")  # type: ignore[arg-type]
        self.assertEqual(self.effects(), ["child"])


class ObjectMaintenanceTests(EmbeddedWorkerTestCase):
    def root_with_orphan(self, *files: str) -> tuple[str, StorageKey]:
        root = tempfile.mkdtemp(prefix="prks-web-root-")
        self.addCleanup(shutil.rmtree, root, True)
        for name in files:
            open(os.path.join(root, name), "wb").close()
        stored = StorageKey(NAMESPACE_ASSET_OBJECTS, "orphan.pdf")
        LocalFilesystemStorage(root).put_new(stored, lambda stream: stream.write(b"bytes"))
        self.run_sql("INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES (%s, %s)",
                     (stored.namespace, stored.name))
        return root, stored

    def test_the_bound_root_gets_object_maintenance_before_the_worker_starts(self) -> None:
        root, stored = self.root_with_orphan()
        worker = self.embedded()
        jobs = WebJobs(self.db, worker)
        jobs.bind_root(root)
        self.assertNotIn("objects.cleanup", self.registry.kinds(), "the process registry is left as it was")
        server, thread, error = self.serve(jobs)
        wait_until(lambda: LocalFilesystemStorage(root).stat(stored) is None, "the claim to be settled")
        with self.assertRaises(RuntimeError):
            worker.attach_objects(root)  # too late: the worker is running
        server.shutdown()
        thread.join(WAIT)
        self.assertEqual(error, [])

    def test_a_root_holding_a_sqlite_catalogue_gets_none(self) -> None:
        root, stored = self.root_with_orphan("prks_data.db")
        worker = self.embedded()
        with self.assertLogs("prks.objects", "WARNING"):
            WebJobs(self.db, worker).bind_root(root)
        self.assertNotIn("objects.cleanup", worker.registry.kinds())
        self.assertEqual(worker.registry.idle_tasks(), ())
        self.assertIsNotNone(LocalFilesystemStorage(root).stat(stored))

    def test_a_root_this_process_did_not_lease_is_refused(self) -> None:
        root, _stored = self.root_with_orphan()
        worker = self.embedded()
        leased = type("Leased", (), {"root_real": os.path.realpath(tempfile.gettempdir())})()
        with patch.object(root_binding, "_ACTIVE", leased), self.assertRaises(StorageRootRefused):
            WebJobs(self.db, worker).bind_root(root)
        self.assertNotIn("objects.cleanup", worker.registry.kinds())


class SettingTests(EmbeddedWorkerTestCase):
    def factory(self) -> tuple[Any, list[int]]:
        opened: list[int] = []

        def open_database(_env: Any) -> Database:
            opened.append(1)
            return self.worker_db()

        return open_database, opened

    def test_on_unless_set_to_0(self) -> None:
        for value in (None, "", "1", " 1 "):
            with self.subTest(value=value):
                open_database, opened = self.factory()
                env = {} if value is None else {"PRKS_EMBEDDED_WORKER": value}
                jobs = web_jobs(self.db, env, open_database=open_database, registry=self.registry)
                self.assertIsNotNone(jobs.worker)
                self.assertEqual(opened, [1])
                assert jobs.worker is not None
                jobs.worker.db.close()

    def test_0_runs_no_worker_and_opens_no_pool(self) -> None:
        open_database, opened = self.factory()
        jobs = web_jobs(self.db, {"PRKS_EMBEDDED_WORKER": "0"}, open_database=open_database)
        self.assertIsNone(jobs.worker)
        self.assertEqual(opened, [])
        self.assertEqual(jobs.diagnostics(), {"embedded_worker": None, "queue": []})

    def test_any_other_value_is_refused_before_a_pool_opens(self) -> None:
        for value in ("yes", "true", "2", "off"):
            with self.subTest(value=value):
                open_database, opened = self.factory()
                with self.assertRaises(EmbeddedWorkerConfigError):
                    web_jobs(self.db, {"PRKS_EMBEDDED_WORKER": value}, open_database=open_database)
                self.assertEqual(opened, [])

    def test_prks_app_exits_2_on_a_refused_setting(self) -> None:
        auth = _Auth(self.open_db())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            prks_app.start_jobs(auth, {"PRKS_EMBEDDED_WORKER": "yes"})
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("PRKS_EMBEDDED_WORKER must be 0 or 1", err.getvalue())

    def test_prks_app_exits_2_when_the_worker_pool_cannot_be_configured(self) -> None:
        auth = _Auth(self.open_db())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            prks_app.start_jobs(auth, {})  # no PRKS_DATABASE_URL for the worker pool
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("PRKS cannot start its job worker:", err.getvalue())
        self.assertNotIn("postgres", err.getvalue().lower())

    def test_prks_app_prepares_the_worker_without_starting_it(self) -> None:
        auth = _Auth(self.open_db())
        env = {"PRKS_DATABASE_URL": self.harness.conninfo(self.name)}
        jobs = prks_app.start_jobs(auth, env)
        assert jobs is not None and jobs.worker is not None
        self.addCleanup(jobs.worker.db.close)
        self.assertIs(jobs.db, auth.db)
        self.assertEqual(jobs.worker.db.config.role, "worker")
        self.assertEqual(jobs.worker.status()["state"], "starting")
        self.assertFalse(any(t.name == "prks-embedded-worker" for t in threading.enumerate()))

    def test_prks_app_has_no_jobs_without_auth(self) -> None:
        self.assertIsNone(prks_app.start_jobs(None, {"PRKS_EMBEDDED_WORKER": "yes"}))


class _Auth:
    """What ``prks_app.start_jobs`` reads of ``WebAuth``."""

    def __init__(self, db: Database) -> None:
        self.db = db


class DiagnosticsBodyTests(EmbeddedWorkerTestCase):
    def test_counters_match_the_worker_and_the_contract(self) -> None:
        worker = self.embedded()
        body = WebJobs(self.db, worker).diagnostics()
        assert body is not None
        self.assertEqual(tuple(body["embedded_worker"]["counters"]), diagnostics.COUNTERS)
        self.assertEqual(tuple(JobWorkerCounters.model_fields), diagnostics.COUNTERS)
        JobDiagnostics.model_validate(body)

    def test_an_unreachable_database_is_none_not_an_error(self) -> None:
        jobs = WebJobs(self.db, None)
        with patch.object(embedded, "queue_depth", side_effect=DatabaseUnavailableError()):
            self.assertIsNone(jobs.diagnostics())


class EndpointTests(HttpAuthTestCase):
    """``GET /api/diagnostics/jobs`` behind the real admission (owner only)."""

    def jobs(self, worker: Optional[EmbeddedWorker] = None) -> WebJobs:
        return WebJobs(self.db, worker)

    def test_the_owner_sees_the_queue_by_kind_and_nothing_else(self) -> None:
        self.db.command(
            lambda tx: tx.execute(
                "INSERT INTO prks.jobs (kind, payload) VALUES ('test.a', '{\"title\": \"Secret Title\"}')"
            )
        )
        self.serve(jobs=self.jobs())
        res = self.call("GET", "/api/diagnostics/jobs", cookies=self.session_cookie())
        self.assertEqual(res.status, 200, res.raw)
        body = JobDiagnostics.model_validate(res.body)
        self.assertIsNone(body.embedded_worker)
        self.assertEqual([(q.kind, q.state, q.count) for q in body.queue], [("test.a", "queued", 1)])
        self.assertNotIn("Secret", res.text)
        self.assertNotIn(self.lib, res.text)

    def test_it_reports_the_embedded_worker(self) -> None:
        worker_db = Database(DatabaseConfig(conninfo=self.harness.conninfo(self.name), role="worker", pool_min=0))
        self.addCleanup(worker_db.close)
        self.serve(jobs=self.jobs(EmbeddedWorker(worker_db, registry=HandlerRegistry())))
        res = self.call("GET", "/api/diagnostics/jobs", cookies=self.session_cookie())
        self.assertEqual(res.status, 200, res.raw)
        self.assertEqual(res.body["embedded_worker"]["state"], "starting")
        self.assertEqual(tuple(res.body["embedded_worker"]["counters"]), diagnostics.COUNTERS)

    def test_editors_and_readers_are_refused(self) -> None:
        self.serve(jobs=self.jobs())
        for role in ("editor", "reader"):
            with self.subTest(role=role):
                login = role.capitalize()
                self.user(login, library=self.lib, role=role)
                res = self.call("GET", "/api/diagnostics/jobs", cookies=self.session_cookie(login))
                self.assertEqual(res.status, 403, res.raw)
                self.assertEqual(res.body["code"], "forbidden")

    def test_nobody_signed_in_is_refused(self) -> None:
        self.serve(jobs=self.jobs())
        self.assertEqual(self.call("GET", "/api/diagnostics/jobs").status, 401)

    def test_an_unreadable_queue_is_503(self) -> None:
        self.serve(jobs=self.jobs())
        cookies = self.session_cookie()
        with patch.object(embedded, "queue_depth", side_effect=DatabaseUnavailableError()):
            res = self.call("GET", "/api/diagnostics/jobs", cookies=cookies)
        self.assertEqual(res.status, 503)
        self.assertEqual(res.body, {"error": "unavailable", "code": "unavailable"})

    def test_the_sqlite_runtime_has_no_such_route(self) -> None:
        self.serve(auth=None)
        self.assertEqual(self.call("GET", "/api/diagnostics/jobs").status, 404)
