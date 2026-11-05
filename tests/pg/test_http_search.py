"""``/api/search`` served from PostgreSQL (L-S-1 #637; the route seam PG-C1 #640 binds).

``server.py`` searches through its bound ``db`` and its bound
``text_index``. These tests bind :class:`~backend.pg_facade.PostgresLibrary`
and :class:`~backend.pg_facade.text_index.PostgresTextIndex` there (the way
the cutover will), sign in over HTTP, and check the route's modes: keyword
search with author and publisher filters, the Tag filter, any mode with the
PDF text hits appended (and filtered), and the PDF text rebuild route, which
queues a ``text`` rebuild instead of extracting in the request. A Saved
View is a stored search the client runs through this same route.
"""

from __future__ import annotations

import os
from typing import Any

from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext
from backend.domain.search import rebuild
from backend.domain.search import text as search_text
from backend.jobs import queue
from backend.jobs.handlers import REGISTRY
from backend.jobs.runner import Outcome, run_job
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.pg_facade.text_index import PostgresTextIndex
from backend.storage.objects import LocalFilesystemStorage
from tests.pg.http_fixtures import HttpAuthTestCase
from tests.pg.jobs_fixtures import LEASE
from tests.pg.test_search_text import pdf_bytes


class SearchHttpTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        module = self.server_module
        previous = (module.db, module.text_index)
        self.addCleanup(self._restore, previous)
        self.storage = module._bound_storage
        self.facade = PostgresLibrary(self.db, storage=self.storage)
        module.db = self.facade
        module.text_index = PostgresTextIndex(self.db)
        self.serve()
        self.cookies = self.session_cookie()

    def _restore(self, previous: tuple[Any, Any]) -> None:
        self.server_module.db, self.server_module.text_index = previous

    def acting(self) -> Any:
        valid = sessions.resolve_session(self.db, self.cookies["prks_session"])
        assert valid is not None
        return facade_context.acting_as(RequestContext.for_session(valid, epoch=self.library_epoch()))

    def pdf_work(self, title: str, text: str, name: str, **fields: Any) -> str:
        with open(os.path.join(self.storage.pdfs_dir, name), "wb") as handle:
            handle.write(pdf_bytes(text))
        with self.acting():
            return self.facade.add_work(title, file_path=f"/api/pdfs/{name}", **fields)

    def run_jobs(self) -> list[Outcome]:
        registry = REGISTRY.copy()
        search_text.attach(registry, LocalFilesystemStorage.for_config(self.storage))
        outcomes = []
        while (job := queue.claim(self.db, "test-worker", lease=LEASE)) is not None:
            outcomes.append(run_job(self.db, job, registry=registry, lease=LEASE))
        return outcomes

    def search(self, **params: str) -> list[str]:
        query = "&".join(f"{key}={value}" for key, value in params.items())
        res = self.call("GET", f"/api/search?{query}", cookies=self.cookies)
        self.assertEqual(res.status, 200, (res.raw, self.logs.getvalue()[-3000:]))
        return [row["title"] for row in res.body]

    def test_keyword_search_and_its_filters(self) -> None:
        with self.acting():
            self.facade.add_work("Quantum notes", author_text="Ann", publisher="Press")
            self.facade.add_work("Quantum drafts", author_text="Bob", publisher="Press")
        self.assertEqual(sorted(self.search(q="quant")), ["Quantum drafts", "Quantum notes"])
        self.assertEqual(self.search(q="quant", author="ann"), ["Quantum notes"])
        self.assertEqual(sorted(self.search(publisher="press")), ["Quantum drafts", "Quantum notes"])
        self.assertEqual(self.search(q="nothing"), [])

    def test_the_tag_filter_with_an_author_filter(self) -> None:
        with self.acting():
            tag = self.facade.add_tag("Physics")
            self.facade.add_tag_alias(tag["id"], "phys")
            kept = self.facade.add_work("Tagged by Ann", author_text="Ann")
            other = self.facade.add_work("Tagged by Bob", author_text="Bob")
            self.facade.add_work("Untagged", author_text="Ann")
            for work in (kept, other):
                self.facade.add_tag_to_work(work, tag["id"])
        self.assertEqual(self.search(tag="phys"), ["Tagged by Bob", "Tagged by Ann"])  # newest first
        self.assertEqual(self.search(tag="Physics", author="ann"), ["Tagged by Ann"])
        self.assertEqual(self.search(tag="unknown"), [])

    def test_any_mode_appends_pdf_text_hits_that_pass_the_filters(self) -> None:
        with self.acting():
            self.facade.add_work("Zebra in the title")
        self.pdf_work("Body only", "a zebra hides in the body", "a.pdf", author_text="Ann")
        self.pdf_work("Other author", "another zebra in the body", "b.pdf", author_text="Bob")
        self.assertEqual(self.search(q="zebra", any="1"), ["Zebra in the title"])  # not extracted yet
        self.run_jobs()
        found = self.search(q="zebra", any="1")
        self.assertEqual(found[0], "Zebra in the title")
        self.assertEqual(sorted(found[1:]), ["Body only", "Other author"])
        # As on SQLite, the author filter applies to the PDF text hits only in any mode.
        self.assertEqual(self.search(q="zebra", any="1", author="ann"), ["Zebra in the title", "Body only"])
        self.assertEqual(self.search(q="zebra"), ["Zebra in the title"])  # PDF text is any mode only

    def test_the_pdf_text_rebuild_route_queues_a_text_rebuild(self) -> None:
        res = self.call("POST", "/api/works/reindex-pdf-text", body={"force": True}, cookies=self.cookies,
                        epoch=self.library_epoch())
        self.assertEqual(res.status, 200, res.raw)
        self.assertEqual((res.body["status"], res.body["queued"], res.body["processed"]), ("ok", True, 0))
        again = self.call("POST", "/api/works/reindex-pdf-text", body={}, cookies=self.cookies,
                          epoch=self.library_epoch())
        self.assertEqual(again.body["queued"], False)
        rows = self.db.query(lambda tx: tx.execute("SELECT payload FROM prks.jobs WHERE kind = %s",
                                                   (rebuild.KIND,)).fetchall())
        self.assertEqual([row["payload"] for row in rows], [{"scopes": ["text"]}])
