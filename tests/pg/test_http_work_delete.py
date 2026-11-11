"""``DELETE /api/works/{id}`` served from PostgreSQL (PG-C2 #641).

``server._delete_work`` deletes the Work through the bound
:class:`~backend.pg_facade.PostgresLibrary`: the delete records the cleanup
claims of its files in its own transaction (§11.2) and the text rows go with
the Work. Only the Work's thumbnails, derived files under the root, are
removed by the request, best effort: a thumbnail that cannot be removed is
logged and the delete still succeeds. These replace the SQLite
``tests/test_work_deletion.py`` cases for the request's own cleanup.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

from backend.derived_caches import prks_thumb_cache_stem
from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.pg_facade.text_index import PostgresTextIndex
from tests.pg.http_fixtures import HttpAuthTestCase
from tests.pg.test_search_text import pdf_bytes


class WorkDeleteHttpTests(HttpAuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        module = self.server_module
        previous = (module.db, module.text_index)
        self.addCleanup(self._restore, previous)
        self.storage = module._bound_storage
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
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

    def pdf_work(self, title: str, name: str) -> str:
        with open(os.path.join(self.storage.pdfs_dir, name), "wb") as handle:
            handle.write(pdf_bytes(title))
        with self.acting():
            return self.facade.add_work(title, file_path=f"/api/pdfs/{name}")

    def thumb(self, work_id: str, page: int, ext: str = "webp") -> str:
        path = os.path.join(self.storage.thumbs_dir, f"{prks_thumb_cache_stem(work_id, page)}.{ext}")
        with open(path, "wb") as handle:
            handle.write(b"t")
        return path

    def delete(self, work_id: str) -> None:
        res = self.call("DELETE", f"/api/works/{work_id}", cookies=self.cookies, epoch=self.library_epoch())
        self.assertEqual(res.status, 200, (res.raw, self.logs.getvalue()[-3000:]))
        self.assertEqual(res.body, {"status": "deleted"})

    def work_exists(self, work_id: str) -> bool:
        return bool(self.run_sql("SELECT 1 FROM prks.works WHERE id = %s", (work_id,)))

    def claimed(self) -> list[str]:
        return [row["name"] for row in self.run_sql("SELECT name FROM prks.object_cleanup_claims")]

    def test_a_delete_removes_the_work_its_thumbnails_and_claims_its_file(self) -> None:
        work_id = self.pdf_work("Deleted", "deleted.pdf")
        other = self.pdf_work("Kept", "kept.pdf")
        thumbs = [self.thumb(work_id, 1), self.thumb(work_id, 2, "png")]
        kept_thumb = self.thumb(other, 1)
        self.delete(work_id)
        self.assertFalse(self.work_exists(work_id))
        self.assertTrue(self.work_exists(other))
        self.assertEqual([os.path.isfile(path) for path in thumbs], [False, False])
        self.assertTrue(os.path.isfile(kept_thumb))
        self.assertEqual(len(self.claimed()), 1, "the delete records its file's claim; the job removes the bytes")

    def test_a_thumbnail_that_cannot_be_removed_is_logged_and_the_delete_still_succeeds(self) -> None:
        work_id = self.pdf_work("Thumb fails", "thumb-fails.pdf")
        stuck, removed = self.thumb(work_id, 1), self.thumb(work_id, 2, "png")
        real_remove = os.remove

        def remove(path: str, *args: Any, **kwargs: Any) -> None:
            if os.path.realpath(path) == os.path.realpath(stuck):
                raise OSError("forced thumbnail cleanup failure")
            real_remove(path, *args, **kwargs)

        with patch("backend.derived_caches.os.remove", side_effect=remove):
            self.delete(work_id)
        self.assertFalse(self.work_exists(work_id))
        self.assertTrue(os.path.isfile(stuck))
        self.assertFalse(os.path.isfile(removed))
        logged = self.logs.getvalue()
        self.assertIn("work_delete_thumbnails_cleanup_failed", logged)
        self.assertIn("failed_count=1", logged)
        for private in ("forced thumbnail cleanup failure", stuck, os.path.basename(stuck), "Thumb fails"):
            self.assertNotIn(private, logged)

    def test_a_failed_delete_keeps_the_work_its_thumbnails_its_file_and_its_text(self) -> None:
        work_id = self.pdf_work("Kept whole", "kept-whole.pdf")
        thumb = self.thumb(work_id, 1)
        text_rows = self.run_sql("SELECT count(*) AS n FROM derived.work_search WHERE work_id = %s", (work_id,))
        with patch("backend.domain.works.commands.delete_work", side_effect=RuntimeError("forced delete failure")):
            res = self.call("DELETE", f"/api/works/{work_id}", cookies=self.cookies, epoch=self.library_epoch())
        self.assertEqual(res.status, 500, res.raw)
        self.assertTrue(self.work_exists(work_id))
        self.assertTrue(os.path.isfile(thumb))
        self.assertTrue(os.path.isfile(os.path.join(self.storage.pdfs_dir, "kept-whole.pdf")))
        self.assertEqual(self.claimed(), [])
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM derived.work_search WHERE work_id = %s", (work_id,)),
                         text_rows)

    def test_a_work_without_artifacts_or_a_missing_work_deletes_cleanly(self) -> None:
        with self.acting():
            plain = self.facade.add_work("No file")
        self.delete(plain)
        self.assertFalse(self.work_exists(plain))
        self.delete("W-MISSING")
        self.assertEqual(self.claimed(), [])
