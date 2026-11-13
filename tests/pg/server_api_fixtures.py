"""A signed-in owner talking to a PRKS server bound to the PostgreSQL Library (PG-C2 #641).

``ServerApiTestCase`` binds :class:`~tests.library_adapter.TestLibrary`, its
text index and its research index to the server module the way the PostgreSQL runtime does,
serves it on a free loopback port in session mode, and signs the owner in.
``get``/``send`` carry the session and, for a mutation, the Library epoch, so
a ported ``tests/test_server_api.py`` case reads like the route it checks.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
from typing import Any, Optional

from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext
from backend.domain.search import text as search_text
from backend.jobs import queue
from backend.jobs.handlers import REGISTRY
from backend.jobs.runner import Outcome, run_job
from tests.library_adapter import TestLibrary
from backend.pg_facade import context as facade_context
from backend.pg_facade.research_index import PostgresResearchIndex
from backend.pg_facade.text_index import PostgresTextIndex
from backend.storage.objects import LocalFilesystemStorage
from tests.pg.http_fixtures import HttpAuthTestCase, Response
from tests.pg.jobs_fixtures import LEASE
from tests.pg.test_search_text import pdf_bytes


class ServerApiTestCase(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        module = self.server_module
        from backend.domain.research import reindex  # noqa: F401  registers research.reindex_work

        previous = (module.db, module.text_index, module.research_index)
        self.addCleanup(self._restore, previous)
        self.storage = module._bound_storage
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
        self.facade = TestLibrary(self.db, storage=self.storage)
        module.db = self.facade
        module.text_index = PostgresTextIndex(self.db)
        module.research_index = PostgresResearchIndex(self.db)
        self.serve()
        self.cookies = self.session_cookie()

    def _restore(self, previous: tuple[Any, Any, Any]) -> None:
        self.server_module.db, self.server_module.text_index, self.server_module.research_index = previous

    # -- requests ----------------------------------------------------------------------

    def get(self, path: str, headers: Optional[list[tuple[str, str]]] = None) -> Response:
        return self.call("GET", path, cookies=self.cookies, headers=headers)

    def send(self, method: str, path: str, body: Any = None,
             headers: Optional[list[tuple[str, str]]] = None) -> Response:
        return self.call(method, path, body=body, cookies=self.cookies, epoch=self.library_epoch(),
                         headers=headers)

    def send_as(self, method: str, path: str, body: Any = None, *, origin: Optional[str] = None,
                content_type: str = "application/json") -> Response:
        """A mutation with this session and epoch but the given ``Origin`` and media type."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            conn.putheader("Origin", origin or self.origin)
            conn.putheader("Cookie", f"prks_session={self.cookies['prks_session']}")
            conn.putheader("PRKS-Library-Epoch", str(self.library_epoch()))
            payload = b"" if body is None else json.dumps(body).encode("utf-8")
            if body is not None:
                conn.putheader("Content-Type", content_type)
            conn.putheader("Content-Length", str(len(payload)))
            conn.endheaders(payload or None)
            res = conn.getresponse()
            return Response(res.status, list(res.getheaders()), res.read())
        finally:
            conn.close()

    def ok(self, res: Response, status: int = 200) -> Any:
        self.assertEqual(res.status, status, (res.raw[:2000], self.logs.getvalue()[-3000:]))
        return res.body

    def refused(self, res: Response, status: int) -> dict[str, Any]:
        self.assertEqual(res.status, status, res.raw[:2000])
        body = res.body
        self.assertIsInstance(body, dict, res.raw[:2000])
        return body

    # -- the Library -------------------------------------------------------------------

    def acting(self) -> Any:
        """Facade calls made directly by a test act as the signed-in owner."""
        valid = sessions.resolve_session(self.db, self.cookies["prks_session"])
        assert valid is not None
        return facade_context.acting_as(RequestContext.for_session(valid, epoch=self.library_epoch()))

    def create_work(self, title: str, **fields: Any) -> dict[str, Any]:
        return self.ok(self.send("POST", "/api/works", {"title": title, **fields}))

    def upload_fields(self, text: str, name: str) -> dict[str, str]:
        return {"file_b64": base64.b64encode(pdf_bytes(text)).decode("ascii"), "file_name": name}

    def titles(self) -> list[str]:
        return sorted(row["title"] for row in self.ok(self.get("/api/works")))

    def sql(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        return self.run_sql(query, params)

    def run_jobs(self) -> list[Outcome]:
        """Run every queued job the way the worker would (text extraction, rebuilds, research reindex)."""
        registry = REGISTRY.copy()
        search_text.attach(registry, LocalFilesystemStorage.for_config(self.storage))
        outcomes = []
        while (job := queue.claim(self.db, "test-worker", lease=LEASE)) is not None:
            outcomes.append(run_job(self.db, job, registry=registry, lease=LEASE))
        return outcomes

    def search_ids(self, query: str) -> list[str]:
        return [row["id"] for row in self.ok(self.get(query))]
