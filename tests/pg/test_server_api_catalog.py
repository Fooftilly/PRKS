"""Concepts, Playlists, Folders, Tags, Recent and BibTeX over HTTP on the PostgreSQL Library (PG-C2 #641).

A Concept is created on its own and never attaches a role, even when its
text mentions a Person. A Playlist takes a Work and lists it. A Folder that
still holds a Work refuses deletion. Tags are created, listed, merged and
deleted (with their aliases). A read of a Work never puts it in Recent; the
explicit open event does. BibTeX names the Work and its contributor roles.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from backend.domain.entity_ids import generate
from tests.pg.server_api_fixtures import ServerApiTestCase
from backend.domain.identity.library_scope import library_query
from backend.domain.organization import links
from backend.domain.organization import tag_commands
from tests.acting_context import current_context


class CatalogTests(ServerApiTestCase):
    def person(self, first: str, last: str) -> str:
        return self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]

    def tag_ids(self) -> list[str]:
        return [row["id"] for row in self.ok(self.get("/api/tags"))]

    def work_tag_ids(self, work_id: str) -> list[str]:
        with self.acting():
            return [row["id"] for row in library_query(self.facade.database, current_context(), lambda scope: links.tags_of(scope, links.WORK, work_id))]

    def test_a_concept_is_created_from_one_json_body(self) -> None:
        body = self.ok(self.send("POST", "/api/concepts", {"name": "API Concept", "description": "from test"}), 201)
        self.assertTrue(body.get("id"))
        self.assertEqual(body.get("name"), "API Concept")

    def test_a_concept_never_attaches_a_role_to_the_work_it_mentions(self) -> None:
        person_id = self.person("Concept", "Mention")
        work_id = self.create_work("Concept Work", status="Not Started")["id"]
        body = self.ok(self.send("POST", "/api/concepts", {
            "name": "Linked Concept", "description": "", "work_id": work_id,
            "annotations_text": "See [[Concept Mention]] for details.",
        }), 201)
        self.assertEqual(body.get("name"), "Linked Concept")
        self.assertTrue(body.get("id"))
        rows = self.sql("SELECT role_type FROM prks.roles WHERE person_id = %s AND work_id = %s",
                        (person_id, work_id))
        self.assertEqual(rows, [])

    def test_a_playlist_lists_the_work_added_to_it(self) -> None:
        playlist_id = self.ok(self.send("POST", "/api/playlists", {"title": "Test PL", "description": ""}))["id"]
        work_id = self.create_work("PL Item Work", status="Not Started")["id"]
        added = self.ok(self.send("POST", f"/api/playlists/{playlist_id}/items", {"work_id": work_id}))
        self.assertEqual(added.get("status"), "added")
        playlist = self.ok(self.get(f"/api/playlists/{playlist_id}"))
        self.assertIn(work_id, [item.get("id") for item in playlist.get("items") or []])

    def test_a_folder_that_holds_a_work_refuses_deletion(self) -> None:
        folder_id = self.ok(self.send("POST", "/api/folders", {"title": "Conflict Folder", "description": ""}))["id"]
        self.create_work("Foldered Work", status="Planned", folder_id=folder_id)
        body = self.refused(self.send("DELETE", f"/api/folders/{folder_id}"), 409)
        self.assertIn("error", body)
        self.assertEqual(self.ok(self.get(f"/api/folders/{folder_id}"))["id"], folder_id)

    def test_a_tag_is_created_and_listed(self) -> None:
        tag = self.ok(self.send("POST", "/api/tags", {"name": "api-tag", "color": "#123abc"}))
        self.assertIn(tag["id"], self.tag_ids())

    def test_attaching_a_missing_tag_or_owner_is_refused_not_a_server_error(self) -> None:
        # A Tag whose creation is still queued on the client is not on the
        # server yet: its attach must be a 404 the form can report, never 500.
        work_id = self.create_work("API Missing Tag Work")["id"]
        folder_id = self.ok(self.send("POST", "/api/folders", {"title": "Missing Tag Folder", "description": ""}))["id"]
        tag_id = self.ok(self.send("POST", "/api/tags", {"name": "api-attach"}))["id"]
        for path, tag, error in (
            (f"/api/works/{work_id}/tags", generate("T"), "tag not found"),
            (f"/api/folders/{folder_id}/tags", generate("T"), "tag not found"),
            (f"/api/works/{generate('W')}/tags", tag_id, "work not found"),
            (f"/api/folders/{generate('F')}/tags", tag_id, "folder not found"),
        ):
            with self.subTest(path=path, error=error):
                self.assertEqual(self.refused(self.send("POST", path, {"tag_id": tag}), 404), {"error": error})
        self.assertEqual(self.work_tag_ids(work_id), [])

    def test_a_tag_merge_moves_its_works_and_a_self_merge_is_refused(self) -> None:
        work_id = self.create_work("API Merge Work")["id"]
        with self.acting():
            source = tag_commands.create(self.facade.database, current_context(), "ApiMergeSrc", "#a00")["id"]
            target = tag_commands.create(self.facade.database, current_context(), "ApiMergeTgt", "#b00")["id"]
            tag_commands.tag_work(self.facade.database, current_context(), work_id, source)
            solo = tag_commands.create(self.facade.database, current_context(), "ApiMergeSolo", "#c00")["id"]
        body = self.ok(self.send("POST", "/api/tags/merge", {"source_tag_id": source, "target_tag_id": target}))
        self.assertEqual((body.get("status"), body.get("canonical_tag_id"), body.get("canonical_name")),
                         ("merged", target, "ApiMergeTgt"))
        self.assertNotIn(source, self.tag_ids())
        self.assertEqual(self.work_tag_ids(work_id), [target])
        self.refused(self.send("POST", "/api/tags/merge", {"source_tag_id": solo, "target_tag_id": solo}), 400)
        self.assertIn(solo, self.tag_ids())

    def test_a_tag_delete_removes_it_with_its_aliases_and_promotes_nothing(self) -> None:
        work_id = self.create_work("API Delete Tag Work")["id"]
        with self.acting():
            tag_id = tag_commands.create(self.facade.database, current_context(), "ApiDeleteCanon", "#d00")["id"]
            tag_commands.add_alias(self.facade.database, current_context(), tag_id, "ApiDeleteAlias")
            tag_commands.tag_work(self.facade.database, current_context(), work_id, tag_id)
        body = self.ok(self.send("DELETE", f"/api/tags/{tag_id}"))
        self.assertEqual(body.get("status"), "deleted")
        self.assertNotIn("promoted", body)
        self.assertNotIn(tag_id, self.tag_ids())
        self.assertEqual(self.work_tag_ids(work_id), [])
        self.assertEqual(self.sql("SELECT 1 FROM prks.tags WHERE id = %s", (tag_id,)), [])
        self.assertEqual(self.sql("SELECT 1 FROM prks.tag_aliases WHERE tag_id = %s", (tag_id,)), [])
        self.assertEqual(self.sql("SELECT 1 FROM prks.tag_keys WHERE tag_id = %s", (tag_id,)), [])


class RecentAndBibtexTests(ServerApiTestCase):
    def ids(self, path: str) -> list[Any]:
        rows = self.ok(self.get(path))
        self.assertIsInstance(rows, list)
        return [row.get("id") for row in rows]

    def bibtex(self, work_id: str) -> str:
        res = self.get(f"/api/bibtex/{work_id}")
        self.assertEqual(res.status, 200, res.raw[:300])
        return res.raw.decode("utf-8", errors="replace")

    def role(self, work_id: str, first: str, last: str, role_type: str) -> None:
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]
        self.ok(self.send("POST", "/api/roles", {"person_id": person_id, "work_id": work_id, "role_type": role_type}))

    def test_only_the_open_event_puts_a_work_in_recent(self) -> None:
        work_id = self.create_work("Graph Bib Work", status="Not Started", author_text="Ada")["id"]
        self.ok(self.get(f"/api/works/{work_id}"))
        self.assertNotIn(work_id, self.ids("/api/recent"), "GET /api/works/:id must not record an open")
        self.ok(self.send("POST", f"/api/works/{work_id}/opened", {}))
        self.assertIn(work_id, self.ids("/api/recent"))
        self.assertIn(work_id, self.ids("/api/recently-added"))
        text = self.bibtex(work_id)
        self.assertIn("@", text)
        self.assertIn("Graph Bib Work", text)

    def test_bibtex_names_the_translator(self) -> None:
        work_id = self.create_work("Translated API Work", status="Not Started", doc_type="book")["id"]
        self.role(work_id, "Anne", "Translator", "Translator")
        self.assertIn("translator = {Translator, Anne}", self.bibtex(work_id))

    def test_bibtex_names_a_books_introduction_foreword_and_afterword(self) -> None:
        work_id = self.create_work("Contributors API Work", status="Not Started", doc_type="book")["id"]
        contributors = [
            ("Ivy", "Intro", "Introduction", "introduction = {Intro, Ivy}"),
            ("Fiona", "Fore", "Foreword", "foreword = {Fore, Fiona}"),
            ("Aaron", "After", "Afterword", "afterword = {After, Aaron}"),
        ]
        for first, last, role_type, _ in contributors:
            self.role(work_id, first, last, role_type)
        text = self.bibtex(work_id)
        for *_, expected in contributors:
            self.assertIn(expected, text)


class CitationExportTests(ServerApiTestCase):
    """``POST /api/citations`` (Citation V1, #41) and the New File PDF check (#772)."""

    def cite(self, work_ids: Any, fmt: str = "csl-json", *, schema_valid: bool = True) -> Any:
        """Send one citation request and check it against the OpenAPI slice.

        ``schema_valid=False`` sends a body the schema refuses; only the
        response is checked then.
        """
        from openapi_core import OpenAPI
        from openapi_core.testing import MockRequest, MockResponse

        from backend.api_contract.openapi import openapi_document

        payload = {"work_ids": work_ids, "format": fmt}
        res = self.send("POST", "/api/citations", payload)
        api = OpenAPI.from_dict(openapi_document())
        request = MockRequest(host_url="http://127.0.0.1", method="post", path="/api/citations",
                              data=json.dumps(payload).encode())
        if schema_valid:
            api.validate_request(request)
        api.validate_response(request, MockResponse(data=res.raw, status_code=res.status,
                                                    content_type="application/json"))
        return res

    def test_selected_works_export_as_csl_json_in_request_order(self) -> None:
        book = self.create_work("A Book", doc_type="book", year="1999", publisher="Press",
                                location="Paris; Lyon", isbn="978-0", status="Not Started")["id"]
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": "Ada", "last_name": "Byron"}))["id"]
        self.ok(self.send("POST", "/api/roles", {"person_id": person_id, "work_id": book, "role_type": "Author"}))
        article = self.create_work("An Article", doc_type="article", journal="Journal", volume="3",
                                   pages="1--9", doi="10.1/x", published_date="2020-05-02",
                                   author_text="Doe, Jane; John Roe", status="Not Started")["id"]
        body = self.ok(self.cite([article, book, article, "W-NOSUCHWORK"]))
        self.assertEqual(body["missing"], ["W-NOSUCHWORK"])
        first, second = body["items"]
        self.assertEqual(first, {
            "id": article, "type": "article-journal", "title": "An Article",
            "author": [{"family": "Doe", "given": "Jane"}, {"family": "Roe", "given": "John"}],
            "issued": {"date-parts": [[2020, 5, 2]]}, "container-title": "Journal", "volume": "3",
            "page": "1-9", "DOI": "10.1/x"})
        self.assertEqual(second, {
            "id": book, "type": "book", "title": "A Book", "author": [{"family": "Byron", "given": "Ada"}],
            "issued": {"date-parts": [[1999]]}, "publisher": "Press", "publisher-place": "Paris; Lyon",
            "ISBN": "978-0"})

    def test_selected_works_export_as_one_bibtex_text(self) -> None:
        one = self.create_work("First Bib", doc_type="book", year="2001", status="Not Started")["id"]
        two = self.create_work("Second Bib", doc_type="book", year="2002", status="Not Started")["id"]
        body = self.ok(self.cite([one, two], "bibtex"))
        single = [self.get(f"/api/bibtex/{work_id}").raw.decode() for work_id in (one, two)]
        self.assertEqual(body, {"format": "bibtex", "text": "\n\n".join(single), "missing": []})

    def test_a_malformed_selection_is_refused(self) -> None:
        work_id = self.create_work("Refused Cite", status="Not Started")["id"]
        self.assertEqual(self.refused(self.cite([work_id], "ris", schema_valid=False), 400)["error"], "Unknown citation format.")
        self.assertEqual(self.refused(self.cite([], schema_valid=False), 400)["error"], "No files selected.")
        self.refused(self.cite(work_id, schema_valid=False), 400)
        self.refused(self.send("POST", "/api/citations", ["not", "an", "object"]), 400)

    def test_new_file_refuses_an_empty_or_non_pdf_upload(self) -> None:
        before = self.titles()
        empty = self.send("POST", "/api/works", {"title": "Empty Upload", "file_b64": "", "file_name": "e.pdf"})
        self.assertEqual(self.refused(empty, 400)["error"], "The uploaded file is empty.")
        text = self.send("POST", "/api/works", {"title": "Text Upload", "file_name": "t.pdf",
                                                "file_b64": base64.b64encode(b"hello").decode("ascii")})
        self.assertEqual(self.refused(text, 400)["error"], "The upload is not a PDF.")
        self.assertEqual(self.titles(), before)
