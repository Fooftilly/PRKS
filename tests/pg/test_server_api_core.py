"""Works, Folders, People and settings over HTTP on the PostgreSQL Library (PG-C2 #641).

The first block of ``tests/test_server_api.py``, served from PostgreSQL:
request ids on success and on an internal error, the client error log, the
settings, the Work, Folder, Person and Person Group routes, and the create
route's refusals. A create that is refused after the Work exists is undone:
no Work, no text and no role remain, and an upload's bytes are left to the
orphan sweep (§11.2), never removed by the request.
"""

from __future__ import annotations

import os
from typing import Any
from unittest import mock
from urllib.parse import quote

from tests.pg.server_api_fixtures import ServerApiTestCase


class RequestIdTests(ServerApiTestCase):
    def test_a_read_carries_a_request_id(self) -> None:
        res = self.get("/api/works")
        self.assertEqual(self.ok(res), [])
        self.assertTrue(res.header("X-Request-ID")[0].strip())

    def test_an_internal_error_names_its_request_id(self) -> None:
        with mock.patch.object(self.facade, "get_all_works", side_effect=RuntimeError("boom")):
            res = self.get("/api/works")
        self.assertEqual(res.status, 500, res.raw)
        request_id = res.header("X-Request-ID")[0].strip()
        self.assertTrue(request_id)
        self.assertEqual(res.body, {"error": "internal_error", "request_id": request_id})

    def test_the_client_error_log_takes_an_object_only(self) -> None:
        payload = {"kind": "window_error", "error_name": "TypeError", "source": "app.js", "line": 10,
                   "column": 2, "request_id": "abc123"}
        res = self.send("POST", "/api/client-errors", payload)
        body = self.ok(res)
        self.assertEqual(body.get("status"), "logged")
        self.assertTrue(body.get("request_id", "").strip())
        self.assertTrue(res.header("X-Request-ID")[0].strip())
        self.assertIn("error", self.refused(self.send("POST", "/api/client-errors", ["window_error"]), 400))


class SettingsTests(ServerApiTestCase):
    def test_annotation_author_and_bibtex_fields_are_patched_and_read_back(self) -> None:
        settings = self.ok(self.get("/api/settings"))
        self.assertEqual(settings["annotation_author"], "")
        self.assertTrue(settings["bibtex_export_fields"].get("abstract"))
        out = self.ok(self.send("PATCH", "/api/settings", {"annotation_author": "Shared Author"}))
        self.assertEqual(out["annotation_author"], "Shared Author")
        self.assertTrue(out["bibtex_export_fields"]["abstract"])
        out = self.ok(self.send("PATCH", "/api/settings", {"bibtex_export_fields": {"abstract": False, "isbn": False}}))
        self.assertFalse(out["bibtex_export_fields"]["abstract"])
        self.assertFalse(out["bibtex_export_fields"]["isbn"])
        self.assertTrue(out["bibtex_export_fields"]["year"])
        again = self.ok(self.get("/api/settings"))
        self.assertEqual(again["annotation_author"], "Shared Author")
        self.assertFalse(again["bibtex_export_fields"]["abstract"])
        self.assertFalse(again["bibtex_export_fields"]["isbn"])


class WorkRouteTests(ServerApiTestCase):
    def test_create_get_patch_and_delete(self) -> None:
        work = self.create_work("API Test Work", status="Not Started")
        self.assertEqual(self.ok(self.get(f"/api/works/{work['id']}"))["title"], "API Test Work")
        self.ok(self.send("PATCH", f"/api/works/{work['id']}", {"title": "Patched Status", "status": "Completed"}))
        self.ok(self.send("PATCH", f"/api/works/{work['id']}", {"private_notes": "Thesis ch. 3 — check with Mark"}))
        single = self.ok(self.get(f"/api/works/{work['id']}"))
        self.assertEqual((single["title"], single["status"]), ("Patched Status", "Completed"))
        self.assertEqual(single["private_notes"], "Thesis ch. 3 — check with Mark")
        self.assertEqual(self.ok(self.send("DELETE", f"/api/works/{work['id']}")), {"status": "deleted"})
        self.assertEqual(self.get(f"/api/works/{work['id']}").status, 404)

    def test_deleting_a_missing_work_is_a_clean_no_op(self) -> None:
        self.assertEqual(self.ok(self.send("DELETE", "/api/works/W-DOESNOTEXIST")), {"status": "deleted"})


class FolderRouteTests(ServerApiTestCase):
    def folder(self, title: str, **fields: Any) -> str:
        return self.ok(self.send("POST", "/api/folders", {"title": title, "description": "", **fields}))["id"]

    def test_create_and_patch_private_notes(self) -> None:
        folder_id = self.folder("API Folder", description="Folder desc")
        self.ok(self.send("PATCH", f"/api/folders/{folder_id}", {"private_notes": "Literature review bucket"}))
        self.assertEqual(self.ok(self.get(f"/api/folders/{folder_id}"))["private_notes"], "Literature review bucket")

    def test_a_duplicate_name_is_a_conflict_whatever_its_case(self) -> None:
        self.folder("Dup Folder API")
        body = self.refused(self.send("POST", "/api/folders", {"title": "dup folder api", "description": "x"}), 409)
        self.assertIn("error", body)

    def test_a_subfolder_names_its_parent_and_a_cycle_is_refused(self) -> None:
        root = self.folder("Hierarchy Root")
        child = self.folder("Hierarchy Child", parent_id=root)
        grand = self.folder("Hierarchy Grand", parent_id=child)
        self.assertEqual(self.ok(self.get(f"/api/folders/{quote(child)}"))["parent_id"], root)
        self.refused(self.send("PATCH", f"/api/folders/{quote(root)}", {"parent_id": grand}), 400)
        self.ok(self.send("PATCH", f"/api/folders/{quote(child)}", {"parent_id": None}))
        self.assertIsNone(self.ok(self.get(f"/api/folders/{quote(child)}"))["parent_id"])


class CreateRefusalTests(ServerApiTestCase):
    def assert_no_work(self, title: str) -> None:
        self.assertNotIn(title, self.titles())
        self.assertEqual(self.sql("SELECT id FROM prks.works WHERE title = %s", (title,)), [])

    def test_a_missing_folder_is_refused_before_anything_is_stored(self) -> None:
        objects = self.sql("SELECT count(*) AS n FROM prks.assets")
        res = self.send("POST", "/api/works", {"title": "Stale Folder Work", "folder_id": "F-does-not-exist",
                                              **self.upload_fields("Stale folder body", "stale_folder.pdf")})
        body = self.refused(res, 404)
        self.assertEqual(body["code"], "FOLDER_NOT_FOUND")
        self.assertIn("folder", body["error"].lower())
        self.assert_no_work("Stale Folder Work")
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.assets"), objects)

    def test_a_filing_refused_after_the_create_undoes_the_work_and_its_text(self) -> None:
        term = "RollbackPartialWorkTextZed"
        with mock.patch.object(self.facade, "add_work_to_folder",
                               side_effect=ValueError("This file is already in another folder.")):
            res = self.send("POST", "/api/works", {"title": "Rollback Partial Work",
                                                  **self.upload_fields(f"Body contains {term}", "rollback.pdf")})
        self.refused(res, 409)
        self.assert_no_work("Rollback Partial Work")
        self.assertEqual(self.sql("SELECT asset_id FROM derived.asset_text"), [])
        self.assertEqual(self.sql("SELECT id FROM prks.assets"), [])

    def test_the_domain_refuses_filing_into_a_missing_folder_or_a_missing_work(self) -> None:
        folder = self.ok(self.send("POST", "/api/folders", {"title": "Filing Target", "description": ""}))["id"]
        with self.acting():
            work_id = self.facade.add_work("Race Filing Work")  # not filed anywhere yet
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                self.facade.add_work_to_folder("F-does-not-exist", work_id)
            with mock.patch.object(self.facade, "folder_exists", return_value=True):
                with self.assertRaisesRegex(ValueError, "no longer exists"):
                    self.facade.add_work_to_folder("F-deleted-meanwhile", work_id)
            with self.assertRaisesRegex(ValueError, "file no longer exists"):
                self.facade.add_work_to_folder(folder, "W-gone")

    def test_an_unknown_person_is_refused_before_anything_is_stored(self) -> None:
        res = self.send("POST", "/api/works", {"title": "Unknown Person Work",
                                              "roles": [{"person_id": "P-not-yet-synced", "role_type": "Author"}],
                                              **self.upload_fields("Unknown person body", "unknown.pdf")})
        self.assertEqual(self.refused(res, 409)["code"], "PERSON_NOT_FOUND")
        self.assert_no_work("Unknown Person Work")
        self.assertEqual(self.sql("SELECT id FROM prks.assets"), [])

    def test_a_person_deleted_after_the_check_leaves_no_work_and_no_alias(self) -> None:
        person = self.ok(self.send("POST", "/api/persons", {"first_name": "Alias", "last_name": "Keeper"}))["id"]
        before = self.ok(self.get(f"/api/persons/{person}"))
        with mock.patch.object(self.facade, "missing_initial_role_person_ids", return_value=[]):
            res = self.send("POST", "/api/works", {
                "title": "Alias Rollback Work",
                "roles": [{"person_id": person, "role_type": "Author", "credit_name": "A. Keeper"},
                          {"person_id": "P-deleted-meanwhile", "role_type": "Editor"}],
                **self.upload_fields("Alias rollback body", "alias.pdf")})
        self.assertEqual(self.refused(res, 409)["code"], "PERSON_NOT_FOUND")
        self.assert_no_work("Alias Rollback Work")
        after = self.ok(self.get(f"/api/persons/{person}"))
        self.assertEqual(after.get("aliases"), before.get("aliases"))
        self.assertEqual(after.get("revision"), before.get("revision"))

    def test_refused_roles_leave_the_playlist_untouched(self) -> None:
        playlist = self.ok(self.send("POST", "/api/playlists", {"title": "Untouched"}))["id"]
        state = ("SELECT p.updated_at, count(i.work_id) AS items FROM prks.playlists p "
                 "LEFT JOIN prks.playlist_items i ON i.playlist_id = p.id WHERE p.id = %s GROUP BY p.updated_at")
        before = self.sql(state, (playlist,))
        with (mock.patch.object(self.facade, "missing_initial_role_person_ids", return_value=[]),
              mock.patch.object(self.facade, "add_work_to_playlist",
                                wraps=self.facade.add_work_to_playlist) as attach):
            res = self.send("POST", "/api/works", {
                "title": "Playlist Race Work", "playlist_id": playlist,
                "roles": [{"person_id": "P-deleted-meanwhile", "role_type": "Author"}]})
        self.refused(res, 409)
        attach.assert_not_called()
        self.assertEqual(self.sql(state, (playlist,)), before)
        self.assert_no_work("Playlist Race Work")


    def test_undoing_a_create_never_claims_a_pdf_it_only_adopted(self) -> None:
        """A refused create that adopted an existing managed PDF records no cleanup claim for it."""
        def adopted(name: str) -> str:
            path = os.path.join(self.storage.pdfs_dir, name)
            with open(path, "wb") as handle:
                handle.write(b"%PDF-1.4\n%ADOPTED\n%%EOF\n")
            return path

        role_pdf = adopted("adopt-then-role-race.pdf")
        with mock.patch.object(self.facade, "missing_initial_role_person_ids", return_value=[]):
            res = self.send("POST", "/api/works", {
                "title": "Adopt Role Race", "file_path": "/api/pdfs/adopt-then-role-race.pdf",
                "roles": [{"person_id": "P-deleted-meanwhile", "role_type": "Author"}]})
        self.refused(res, 409)
        folder = self.ok(self.send("POST", "/api/folders", {"title": "Adopt Folder Race", "description": ""}))["id"]
        folder_pdf = adopted("adopt-then-folder-race.pdf")
        with mock.patch.object(self.facade, "add_work_to_folder",
                               side_effect=ValueError("The folder no longer exists.")):
            res = self.send("POST", "/api/works", {"title": "Adopt Folder Race", "folder_id": folder,
                                                  "file_path": "/api/pdfs/adopt-then-folder-race.pdf"})
        self.refused(res, 409)
        for title, path in (("Adopt Role Race", role_pdf), ("Adopt Folder Race", folder_pdf)):
            with self.subTest(title=title):
                self.assert_no_work(title)
                self.assertTrue(os.path.isfile(path))
        self.assertEqual(self.sql("SELECT name FROM prks.object_cleanup_claims"), [])


class InitialRoleTests(ServerApiTestCase):
    def person(self, first: str, last: str) -> str:
        return self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]

    def test_valid_roles_keep_their_order_and_promote_the_credit(self) -> None:
        a, b = self.person("Order", "First"), self.person("Order", "Second")
        work = self.create_work("Initial Roles Work", roles=[
            {"person_id": a, "role_type": "Author", "credit_name": "O. First"},
            {"person_id": b, "role_type": "Author"},
            {"person_id": b, "role_type": "Author"},
            {"person_id": a, "role_type": "not-a-role"},
        ])
        with self.acting():
            roles = self.facade.get_work_roles(work["id"])
        self.assertEqual([(r["id"], r["role_type"]) for r in roles], [(a, "Author"), (b, "Author")])
        self.assertEqual(roles[0]["credit_name"], "O. First")
        self.assertIn("O. First", self.ok(self.get(f"/api/persons/{a}")).get("aliases") or "")

    def test_roles_the_create_would_skip_do_not_block_it(self) -> None:
        work = self.create_work("Skipped Roles Work", roles=[
            {"person_id": "P-unknown-untyped"}, {"person_id": "P-unknown-bad-type", "role_type": "not-a-role"}])
        with self.acting():
            self.assertEqual(self.facade.get_work_roles(work["id"]), [])


class PersonRouteTests(ServerApiTestCase):
    def person(self, first: str, last: str) -> str:
        return self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]

    def test_links_are_patched_and_read_back(self) -> None:
        person = self.person("Test", "Philosopher")
        self.ok(self.send("PATCH", f"/api/persons/{person}", {
            "link_wikipedia": "https://en.wikipedia.org/wiki/Test", "link_iep": "https://iep.utm.edu/test/"}))
        read = self.ok(self.get(f"/api/persons/{person}"))
        self.assertEqual(read["link_wikipedia"], "https://en.wikipedia.org/wiki/Test")
        self.assertEqual(read["link_iep"], "https://iep.utm.edu/test/")

    def test_an_unlinked_person_is_deleted_and_a_linked_one_is_a_conflict(self) -> None:
        free = self.person("Delete", "Allowed")
        self.assertEqual(self.ok(self.send("DELETE", f"/api/persons/{quote(free)}"))["status"], "deleted")
        self.assertEqual(self.get(f"/api/persons/{quote(free)}").status, 404)
        linked = self.person("Delete", "Blocked")
        work = self.create_work("Linked Work")
        self.ok(self.send("POST", "/api/roles", {"person_id": linked, "work_id": work["id"], "role_type": "Author"}))
        self.assertIn("error", self.refused(self.send("DELETE", f"/api/persons/{quote(linked)}"), 409))
        self.assertEqual(self.get(f"/api/persons/{quote(linked)}").status, 200)

    def test_person_groups_and_their_members(self) -> None:
        root = self.ok(self.send("POST", "/api/person-groups", {"name": "Root Group"}))["id"]
        child = self.ok(self.send("POST", "/api/person-groups", {"name": "Child Group", "parent_id": root}))["id"]
        self.assertEqual(len(self.ok(self.get("/api/person-groups"))), 2)
        person = self.person("A", "Member")
        self.ok(self.send("POST", f"/api/person-groups/{child}/members", {"person_id": person}))
        self.assertEqual(len(self.ok(self.get(f"/api/person-groups/{child}"))["members"]), 1)
        self.ok(self.send("PATCH", f"/api/persons/{person}", {"group_ids": [root]}))
        self.assertEqual(len(self.ok(self.get(f"/api/person-groups/{child}"))["members"]), 0)
        self.assertEqual(len(self.ok(self.get(f"/api/person-groups/{root}"))["members"]), 1)
