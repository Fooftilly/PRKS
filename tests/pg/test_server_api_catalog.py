"""Concepts, Playlists, Folders, Tags, Recent and BibTeX over HTTP on the PostgreSQL Library (PG-C2 #641).

A Concept is created on its own and never attaches a role, even when its
text mentions a Person. A Playlist takes a Work and lists it. A Folder that
still holds a Work refuses deletion. Tags are created, listed, merged and
deleted (with their aliases). A read of a Work never puts it in Recent; the
explicit open event does. BibTeX names the Work and its contributor roles.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

from typing import Any

from tests.pg.server_api_fixtures import ServerApiTestCase


class CatalogTests(ServerApiTestCase):
    def person(self, first: str, last: str) -> str:
        return self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]

    def tag_ids(self) -> list[str]:
        return [row["id"] for row in self.ok(self.get("/api/tags"))]

    def work_tag_ids(self, work_id: str) -> list[str]:
        with self.acting():
            return [row["id"] for row in self.facade.get_work_tags(work_id)]

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

    def test_a_tag_merge_moves_its_works_and_a_self_merge_is_refused(self) -> None:
        work_id = self.create_work("API Merge Work")["id"]
        with self.acting():
            source = self.facade.add_tag("ApiMergeSrc", "#a00")["id"]
            target = self.facade.add_tag("ApiMergeTgt", "#b00")["id"]
            self.facade.add_tag_to_work(work_id, source)
            solo = self.facade.add_tag("ApiMergeSolo", "#c00")["id"]
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
            tag_id = self.facade.add_tag("ApiDeleteCanon", "#d00")["id"]
            self.facade.add_tag_alias(tag_id, "ApiDeleteAlias")
            self.facade.add_tag_to_work(work_id, tag_id)
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
