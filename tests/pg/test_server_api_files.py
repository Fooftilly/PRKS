"""Managed PDFs, their text and thumbnails over HTTP on the PostgreSQL Library (PG-C2 #641).

An upload is stored as an object and served from ``/api/pdfs/<name>``; its
text is extracted by a queued job, so ``any=1`` search finds it once the job
ran and keyword search never does. A replace keeps the served path, drops
the old text and queues the new, and a replace whose extraction fails leaves
neither text. Adopting another managed PDF by ``file_path`` moves the text
with it. The PDF route validates the whole suffix after ``/api/pdfs/``.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import base64
import os
from typing import Any
from unittest import mock
from urllib.parse import quote

from tests.pg.server_api_fixtures import ServerApiTestCase
from tests.pg.test_search_text import pdf_bytes


class ManagedPdfTestCase(ServerApiTestCase):
    def upload(self, title: str, text: str, name: str) -> dict[str, Any]:
        work = self.create_work(title, status="Planned", **self.upload_fields(text, name))
        return self.ok(self.get(f"/api/works/{work['id']}"))

    def found(self, term: str, mode: str = "any=1&") -> list[str]:
        return self.search_ids(f"/api/search?{mode}q={quote(term)}")


class ManagedPdfTests(ManagedPdfTestCase):
    def test_an_upload_is_served_from_its_managed_path(self) -> None:
        work = self.upload("Upload API Work", "PRKS upload fetch smoke", "upload_test.pdf")
        self.assertTrue(work["file_path"].startswith("/api/pdfs/"))
        res = self.get(work["file_path"])
        self.assertEqual(res.status, 200)
        self.assertIn(b"%PDF", res.raw[:32])

    def test_pdf_text_is_found_by_any_mode_only_once_extracted(self) -> None:
        term = "UniqPdfSearchTermAlpha"
        work = self.upload("PDF Text Search Work", f"Body contains {term} and extra words", "search.pdf")
        self.assertNotIn(work["id"], self.found(term))
        self.run_jobs()
        self.assertNotIn(work["id"], self.found(term, mode=""))
        self.assertIn(work["id"], self.found(term))

    @mock.patch.dict(os.environ, {"PRKS_PDF_LINEARIZE": "0"})
    def test_a_replace_stores_a_new_object_and_swaps_the_text(self) -> None:
        """The new PDF is a fresh immutable object: the response and the Work name its new path.

        Linearization is pinned off: with qpdf installed the working file is
        the linearized copy, not the uploaded bytes this test compares.
        """
        work = self.upload("Overwrite Work", "oldtermreplacepdf", "overwrite.pdf")
        self.run_jobs()
        self.assertIn(work["id"], self.found("oldtermreplacepdf"))
        original = self.ok(self.get(f"/api/works/{work['id']}"))["file_path"]
        new_pdf = pdf_bytes("newtermreplacepdf")
        res = self.send("POST", f"/api/works/{work['id']}/pdf",
                        {"file_b64": base64.b64encode(new_pdf).decode("ascii")})
        replaced = self.ok(res)
        self.assertIn("file_path", replaced)
        path = replaced["file_path"]
        self.assertEqual(self.ok(self.get(f"/api/works/{work['id']}"))["file_path"], path)
        self.assertNotEqual(path, original, "a replace never overwrites the old object")
        self.assertTrue(path.startswith("/api/pdfs/"))
        served = self.get(path)
        self.assertEqual(served.status, 200)
        self.assertEqual(served.raw, new_pdf)
        self.assertNotIn(work["id"], self.found("oldtermreplacepdf"))
        self.run_jobs()
        self.assertIn(work["id"], self.found("newtermreplacepdf"))
        self.assertNotIn(work["id"], self.found("oldtermreplacepdf"))

    def test_a_replace_whose_extraction_fails_leaves_neither_text(self) -> None:
        from backend import pdf_text

        work = self.upload("Overwrite Fail Work", "overwritefailoldterm", "overwrite_fail.pdf")
        self.run_jobs()
        self.assertIn(work["id"], self.found("overwritefailoldterm"))
        self.ok(self.send("POST", f"/api/works/{work['id']}/pdf",
                          {"file_b64": base64.b64encode(pdf_bytes("overwritefailnewterm")).decode("ascii")}))
        with mock.patch.object(pdf_text, "extract_pdf_bounded", side_effect=pdf_text.PDFTextExtractionError("bad")):
            self.run_jobs()
        self.assertNotIn(work["id"], self.found("overwritefailoldterm"))
        self.assertNotIn(work["id"], self.found("overwritefailnewterm"))
        self.assertEqual(self.get(f"/api/works/{work['id']}").status, 200)

    def test_a_file_path_change_is_refused_as_not_ported_and_changes_nothing(self) -> None:
        """A Work's file changes through the replace route; ``file_path`` PATCH is not ported (pre-1.0)."""
        other = self.upload("Other Work", "patchfilepathtermA", "patch_fp_a.pdf")
        work = self.upload("Patch File Path Work", "patchfilepathtermB", "patch_fp_upload.pdf")
        self.run_jobs()
        res = self.send("PATCH", f"/api/works/{work['id']}", {"file_path": other["file_path"]})
        self.assertEqual((res.status, res.body.get("code")), (501, "not_ported"), res.raw)
        self.assertEqual(self.ok(self.get(f"/api/works/{work['id']}"))["file_path"], work["file_path"])
        self.assertIn(work["id"], self.found("patchfilepathtermB"))
        self.assertNotIn(work["id"], self.found("patchfilepathtermA"))

    def test_a_thumbnail_is_rendered_for_a_pdf_work(self) -> None:
        work = self.upload("Thumb Work", "thumb token", "thumb_test.pdf")
        res = self.get(f"/api/works/{work['id']}/thumbnail?page=1")
        self.assertEqual(res.status, 200, res.raw[:300])
        self.assertTrue(res.header("Content-Type")[0].startswith("image/"))

    def test_a_thumbnail_rendered_from_replaced_bytes_is_never_served_for_the_new_ones(self) -> None:
        """A render that started before a replace and publishes after it (#1331:
        no gate orders the two any more) is cached under the old bytes' name:
        it is never served for the new PDF and never evicts the new copy."""
        import threading

        from backend.derived_caches import prks_thumb_cache_stem

        work = self.upload("Thumb Race", "thumbraceold", "thumb_race.pdf")
        original = self.server_module._prks_thumbnail_bytes_from_pixmap
        rendering, replaced = threading.Event(), threading.Event()
        self.addCleanup(replaced.set)
        first = [True]

        def slow(pix: Any) -> tuple[bytes, str]:
            if first[0]:
                first[0] = False
                rendering.set()
                self.assertTrue(replaced.wait(10))
            return original(pix)

        result: dict[str, Any] = {}
        with mock.patch.object(self.server_module, "_prks_thumbnail_bytes_from_pixmap", slow):
            viewer = threading.Thread(target=lambda: result.setdefault(
                "res", self.get(f"/api/works/{work['id']}/thumbnail?page=1")), daemon=True)
            viewer.start()
            self.assertTrue(rendering.wait(10))
            new = self.replace_pdf(work["id"], "thumbracenew")
            # The new PDF is viewed, and cached, before the old render ends.
            self.assertEqual(self.get(f"/api/works/{work['id']}/thumbnail?page=1").status, 200)
            replaced.set()
            viewer.join(20)
            self.assertEqual(result["res"].status, 200)
        assert self.storage is not None
        primary = self.ok(self.get(f"/api/works/{work['id']}")).get("primary_asset_id")
        old_stem = prks_thumb_cache_stem(work["id"], 1, primary, source=work["file_path"].rsplit("/", 1)[1])
        new_stem = prks_thumb_cache_stem(work["id"], 1, primary, source=new["file_path"].rsplit("/", 1)[1])
        self.assertNotEqual(old_stem, new_stem)
        names = set(os.listdir(self.storage.thumbs_dir))
        # The late render landed under the old bytes' name and evicted nothing.
        self.assertEqual({f"{old_stem}.webp", f"{new_stem}.webp"} - names, set())
        with open(os.path.join(self.storage.thumbs_dir, f"{new_stem}.webp"), "rb") as handle:
            current = handle.read()
        self.assertEqual(self.get(f"/api/works/{work['id']}/thumbnail?page=1").raw, current)
        # The first render of a newer PDF prunes both older copies.
        newest = self.replace_pdf(work["id"], "thumbracenewest")
        self.assertEqual(self.get(f"/api/works/{work['id']}/thumbnail?page=1").status, 200)
        newest_stem = prks_thumb_cache_stem(work["id"], 1, primary, source=newest["file_path"].rsplit("/", 1)[1])
        self.assertEqual(self.thumb_names(work["id"]), {f"{newest_stem}.webp"})

    def test_a_render_that_resumes_its_prune_after_a_replace_keeps_the_new_copy(self) -> None:
        """The interleaving a current-PDF check could not close: the old render
        is held between publishing its copy and pruning, the PDF is replaced
        and its thumbnail cached, then the old prune runs. It removes only
        copies older than the PDF it rendered, so the new copy survives."""
        import threading

        from backend.derived_caches import prks_thumb_cache_stem

        work = self.upload("Thumb Prune Race", "thumbpruneold", "thumb_prune_race.pdf")
        original = self.server_module.prks_prune_thumbnail_page
        pruning, replaced = threading.Event(), threading.Event()
        self.addCleanup(replaced.set)
        first = [True]
        # The handler swallows errors from its prune, so the held call records
        # what happened and the test asserts it outside the handler.
        resumed: list[bool] = []

        def held(*args: Any, **kwargs: Any) -> None:
            if first[0]:
                first[0] = False
                pruning.set()
                resumed.append(replaced.wait(10))
            original(*args, **kwargs)

        result: dict[str, Any] = {}
        with mock.patch.object(self.server_module, "prks_prune_thumbnail_page", held):
            viewer = threading.Thread(target=lambda: result.setdefault(
                "res", self.get(f"/api/works/{work['id']}/thumbnail?page=1")), daemon=True)
            viewer.start()
            self.assertTrue(pruning.wait(10))
            new = self.replace_pdf(work["id"], "thumbprunenew")
            self.assertEqual(self.get(f"/api/works/{work['id']}/thumbnail?page=1").status, 200)
            replaced.set()
            viewer.join(20)
            self.assertFalse(viewer.is_alive())
            self.assertEqual(result["res"].status, 200)
        self.assertEqual(resumed, [True])  # the old prune ran, after the replace
        primary = self.ok(self.get(f"/api/works/{work['id']}")).get("primary_asset_id")
        new_stem = prks_thumb_cache_stem(work["id"], 1, primary, source=new["file_path"].rsplit("/", 1)[1])
        self.assertIn(f"{new_stem}.webp", self.thumb_names(work["id"]))

    def test_a_render_published_after_its_work_is_deleted_leaves_no_copy(self) -> None:
        """The render is held just before publishing its copy while the Work is
        deleted and its thumbnails cleared; the copy it then publishes must
        not outlive the delete."""
        import threading

        work = self.upload("Thumb Delete Race", "thumbdeleterace", "thumb_delete_race.pdf")
        original = self.server_module.publish_derived_cache_bytes
        publishing, deleted = threading.Event(), threading.Event()
        self.addCleanup(deleted.set)
        # The handler swallows errors from its cache write, so the held call
        # records what happened and the test asserts it outside the handler.
        resumed: list[bool] = []

        def held(*args: Any, **kwargs: Any) -> Any:
            publishing.set()
            resumed.append(deleted.wait(10))
            return original(*args, **kwargs)

        result: dict[str, Any] = {}
        with mock.patch.object(self.server_module, "publish_derived_cache_bytes", held):
            viewer = threading.Thread(target=lambda: result.setdefault(
                "res", self.get(f"/api/works/{work['id']}/thumbnail?page=1")), daemon=True)
            viewer.start()
            self.assertTrue(publishing.wait(10))
            self.assertIn(self.send("DELETE", f"/api/works/{work['id']}").status, (200, 204))
            deleted.set()
            viewer.join(20)
            self.assertFalse(viewer.is_alive())
        self.assertEqual(resumed, [True])  # the copy was published after the delete
        self.assertEqual(result["res"].status, 404)
        self.assertEqual(self.thumb_names(work["id"]), set())

    def replace_pdf(self, work_id: str, text: str) -> dict[str, Any]:
        return self.ok(self.send("POST", f"/api/works/{work_id}/pdf",
                                 {"file_b64": base64.b64encode(pdf_bytes(text)).decode("ascii")}))

    def thumb_names(self, work_id: str) -> set[str]:
        assert self.storage is not None
        return {name for name in os.listdir(self.storage.thumbs_dir) if name.startswith(work_id + "_")}

    def test_the_pdf_route_validates_the_whole_suffix(self) -> None:
        work = self.upload("Route Suffix Guard", "route", "route_suffix_guard.pdf")
        name = work["file_path"].rsplit("/", 1)[1]
        assert self.storage is not None
        target = os.path.join(self.storage.pdfs_dir, name)
        self.assertTrue(os.path.isfile(target))
        for method in ("GET", "HEAD"):
            self.assertEqual(self.call(method, f"/api/pdfs/{name}", cookies=self.cookies).status, 200)
        for suffix in ("..", f"nested/{name}", f"nested%2F{name}", f"..%2F{name}", f"nested%5C{name}",
                       f"%20{name}"):
            for method in ("GET", "HEAD"):
                with self.subTest(suffix=suffix, method=method):
                    self.assertEqual(self.call(method, f"/api/pdfs/{suffix}", cookies=self.cookies).status, 404)
        self.assertTrue(os.path.isfile(target))

    def test_an_unknown_work_is_404_and_a_search_is_a_list(self) -> None:
        self.assertEqual(self.get("/api/works/W-00000000-NOTFOUND").status, 404)
        self.assertEqual(self.ok(self.get("/api/search?q=nonexistenttokenxyz")), [])

    def test_a_dotdot_file_path_is_refused_on_create_and_on_patch(self) -> None:
        body = self.refused(self.send("POST", "/api/works", {"title": "Dotdot create", "file_path": "/api/pdfs/.."}),
                            400)
        self.assertEqual(body.get("error"), "file_path is not a managed PDF")
        self.assertNotIn("Dotdot create", self.titles())
        work = self.create_work("Dotdot patch target")
        res = self.send("PATCH", f"/api/works/{work['id']}", {"file_path": "/api/pdfs/.."})
        self.assertEqual((res.status, res.body.get("code")), (501, "not_ported"), res.raw)  # any file_path change
        self.assertEqual(self.ok(self.get(f"/api/works/{work['id']}"))["file_path"], "")

    def test_linearizing_existing_pdfs_is_refused_as_not_ported(self) -> None:
        """Managed PDFs are immutable stored objects on PostgreSQL; rewriting them in place is not ported."""
        work = self.upload("Linearize Existing Work", "linearize me", "linearize.pdf")
        assert self.storage is not None
        path = os.path.join(self.storage.pdfs_dir, work["file_path"].rsplit("/", 1)[1])
        with open(path, "rb") as handle:
            before = handle.read()
        res = self.send("POST", "/api/works/linearize-existing-pdfs", {"unlinearized_only": True})
        self.assertEqual((res.status, res.body.get("code")), (501, "not_ported"), res.raw)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)


class AdoptionSpellingTests(ManagedPdfTestCase):
    def test_only_the_exact_managed_path_is_adopted(self) -> None:
        """Encoded slashes, URL delimiters and padding never name a managed PDF.

        SQLite trimmed outer whitespace before adopting; PostgreSQL refuses
        every spelling but the exact path it serves.
        """
        work = self.upload("Spelling Owner", "spelling", "spelling.pdf")
        path = work["file_path"]
        name = path.rsplit("/", 1)[1]
        for spelling in (f"{path}%2F", f"/api/pdfs/x%2F{name}", f"{path}?x", f"{path};x", f"{path}#x",
                         f"  {path}  "):
            with self.subTest(spelling=spelling):
                body = self.refused(self.send("POST", "/api/works", {"title": "Adopt", "file_path": spelling}), 400)
                self.assertEqual(body.get("error"), "file_path is not a managed PDF")
        self.assertEqual(self.titles(), ["Spelling Owner"])
        for suffix in ("?x", ";x"):
            with self.subTest(suffix=suffix):
                res = self.get(f"{path}{suffix}")
                self.assertEqual(res.status, 200, "the route serves the path, never the delimiter")
                self.assertIn(b"%PDF", res.raw[:32])
