"""Catalog ETags, browse projections, the open event and tag change reports over HTTP on PostgreSQL (PG-C2 #641).

An ETag names the representation: it moves with every rendered field and
stays put (``304``) when nothing a catalog renders changed. The browse
projections are compact and deterministically ordered. Reads never record an
open; only ``POST /api/works/{id}/opened`` does. A tag delete or merge
reports the Works and Folders it changed, and a refused merge changes
nothing. Every response forbids MIME sniffing and the shell serves its Vue
bundle. On PostgreSQL a managed PDF never changes in place, so a file size
changes only through a replace.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any, Optional

from tests.pg.server_api_fixtures import ServerApiTestCase
from tests.pg.test_search_text import pdf_bytes

BROWSE = {"works-browse": "/api/works?projection=browse", "recent": "/api/recent",
          "recently-added": "/api/recently-added"}


class FreshnessTestCase(ServerApiTestCase):
    def conditional(self, path: str, etag: Optional[str] = None) -> tuple[int, Optional[str], Any]:
        res = self.get(path, headers=[("If-None-Match", etag)] if etag else None)
        tags = res.header("ETag")
        return res.status, tags[0] if tags else None, res.body if res.status == 200 else None

    def row(self, body: list[dict[str, Any]], row_id: str) -> dict[str, Any]:
        found = next((item for item in body if item.get("id") == row_id), None)
        self.assertIsNotNone(found, row_id)
        assert found is not None
        return found

    def moved(self, path: str, previous: Optional[str], label: str) -> tuple[Optional[str], Any]:
        status, etag, body = self.conditional(path, previous)
        self.assertNotEqual(status, 304, f"{label}: served stale as Not Modified")
        self.assertEqual(status, 200, label)
        self.assertNotEqual(etag, previous, label)
        self.assertEqual(self.conditional(path, etag)[0], 304, f"{label}: changed again with no further edit")
        return etag, body

    def follows(self, label: str, mutate: Callable[[], Any], *, changes: bool = True) -> None:
        path = BROWSE[label]
        status, etag, body = self.conditional(path)
        self.assertEqual(status, 200, label)
        mutate()
        status, after, again = self.conditional(path, etag)
        if changes:
            self.assertEqual(status, 200, f"{label} served stale as Not Modified")
            self.assertNotEqual((after, again), (etag, body), label)
        else:
            self.assertEqual(status, 304, f"{label} changed when it should not have")

    def tag(self, name: str) -> str:
        return self.ok(self.send("POST", "/api/tags", {"name": name}))["id"]

    def replace_pdf(self, work_id: str, text: str) -> None:
        self.ok(self.send("POST", f"/api/works/{work_id}/pdf",
                          {"file_b64": base64.b64encode(pdf_bytes(text)).decode("ascii")}))


class CatalogEtagTests(FreshnessTestCase):
    def test_the_tag_catalog_follows_colors_aliases_and_the_set_but_not_links(self) -> None:
        tag, target = self.tag("ETag ABCD"), self.tag("ETag Target")
        work_id = self.create_work("ETag relationship")["id"]

        def check(mutate: Callable[[], Any], changes: bool = True) -> None:
            status, before, body = self.conditional("/api/tags")
            self.assertEqual(status, 200)
            mutate()
            status, after, result = self.conditional("/api/tags", before)
            self.assertEqual(status, 200 if changes else 304)
            if changes:
                self.assertNotEqual((before, body), (after, result))

        with self.acting():
            # No route renames a tag or an alias, and PostgreSQL keeps names in
            # step with their identity keys, so a name change is an alias
            # change here; the colour has no key and is changed directly.
            check(lambda: self.sql("UPDATE prks.tags SET color = %s WHERE id = %s RETURNING id", ("#abcdef", tag)))
            check(lambda: self.facade.add_tag_alias(tag, "ETag FOUR"))
            check(lambda: self.facade.add_tag_alias(tag, "ETag FIVE"))
            check(lambda: self.facade.delete_tag_alias(tag, "ETag FIVE"))
            check(lambda: self.facade.add_tag("ETag Created"))
            check(lambda: self.facade.add_tag_to_work(work_id, tag), False)
            check(lambda: self.facade.remove_tag_from_work(work_id, tag), False)
            check(lambda: self.facade.merge_tags_into(tag, target))
            check(lambda: self.facade.delete_tag(target))

    def folder(self, title: str, **fields: Any) -> str:
        return self.ok(self.send("POST", "/api/folders", {"title": title, **fields}))["id"]

    def test_the_folder_catalog_follows_a_move_and_a_bulk_move(self) -> None:
        source, dest = self.folder("ETag Move Source"), self.folder("ETag Move Dest")
        work_id = self.create_work("ETag Move Work", folder_id=source)["id"]
        status, etag, body = self.conditional("/api/folders")
        self.assertTrue(etag)
        self.assertEqual((self.row(body, source)["work_count"], self.row(body, dest)["work_count"]), (1, 0))
        self.ok(self.send("PATCH", f"/api/works/{work_id}", {"folder_id": dest}))
        etag, body = self.moved("/api/folders", etag, "move")
        self.assertEqual((self.row(body, source)["work_count"], self.row(body, dest)["work_count"]), (0, 1))
        self.ok(self.send("POST", "/api/works/bulk", {"action": "move_folder", "work_ids": [work_id],
                                                      "folder_id": source}))
        _, body = self.moved("/api/folders", etag, "bulk move")
        self.assertEqual(self.row(body, source)["work_count"], 1)

    def test_the_folder_catalog_follows_every_field_back_to_back(self) -> None:
        parent = self.folder("ETag Fields Parent")
        etag = self.conditional("/api/folders")[1]
        steps: list[tuple[str, Callable[[], Any]]] = []
        child: dict[str, str] = {}
        work: dict[str, str] = {}
        steps.append(("title", lambda: self.ok(self.send("PATCH", f"/api/folders/{parent}",
                                                         {"title": "ETag Fields Renamed"}))))
        steps.append(("description", lambda: self.ok(self.send("PATCH", f"/api/folders/{parent}",
                                                               {"description": "desc"}))))
        steps.append(("subfolder", lambda: child.setdefault("id", self.folder("ETag Fields Child", parent_id=parent))))
        steps.append(("reparent", lambda: self.ok(self.send("PATCH", f"/api/folders/{child['id']}",
                                                            {"parent_id": None}))))
        steps.append(("work filed", lambda: work.setdefault("id", self.create_work("ETag Fields Work",
                                                                                   folder_id=parent)["id"])))
        steps.append(("work deleted", lambda: self.ok(self.send("DELETE", f"/api/works/{work['id']}"))))
        steps.append(("folder deleted", lambda: self.ok(self.send("DELETE", f"/api/folders/{child['id']}"))))
        for label, mutate in steps:
            mutate()
            status, now, _ = self.conditional("/api/folders")
            self.assertEqual(status, 200, label)
            self.assertNotEqual(now, etag, f"ETag did not change after {label}")
            etag = now
        self.assertEqual(self.conditional("/api/folders", etag)[0], 304)

    def test_the_works_catalog_follows_a_replaced_pdfs_size(self) -> None:
        work_id = self.create_work("Works Size ETag", **self.upload_fields("small", "size.pdf"))["id"]
        status, etag, body = self.conditional("/api/works")
        size = self.row(body, work_id)["file_size_bytes"]
        self.assertEqual(self.conditional("/api/works", etag)[0], 304)
        self.replace_pdf(work_id, "a much longer document body " * 200)
        _, body = self.moved("/api/works", etag, "PDF replace")
        self.assertGreater(self.row(body, work_id)["file_size_bytes"], size)

    def test_the_person_catalog_follows_a_biography_and_a_membership(self) -> None:
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": "Etag", "last_name": "Person"}))["id"]
        group_id = self.ok(self.send("POST", "/api/person-groups", {"name": "Etag Person Membership"}))["id"]
        _, etag, body = self.conditional("/api/persons")
        count = len(body)
        self.ok(self.send("PATCH", f"/api/persons/{person_id}", {"about": "same-second biography"}))
        etag, body = self.moved("/api/persons", etag, "biography")
        self.assertEqual((self.row(body, person_id)["about"], len(body)), ("same-second biography", count))
        self.ok(self.send("POST", f"/api/person-groups/{group_id}/members", {"person_id": person_id}))
        _, body = self.moved("/api/persons", etag, "membership")
        self.assertIn(group_id, [g.get("id") for g in self.row(body, person_id).get("groups") or []])

    def test_the_person_group_catalog_follows_a_description_and_a_member(self) -> None:
        group_id = self.ok(self.send("POST", "/api/person-groups", {"name": "Etag Group Catalog"}))["id"]
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": "Group", "last_name": "Member"}))["id"]
        _, etag, body = self.conditional("/api/person-groups")
        self.assertEqual(self.row(body, group_id)["member_count"], 0)
        self.ok(self.send("PATCH", f"/api/person-groups/{group_id}", {"description": "same-second description"}))
        etag, body = self.moved("/api/person-groups", etag, "description")
        self.assertEqual(self.row(body, group_id)["description"], "same-second description")
        self.ok(self.send("POST", f"/api/person-groups/{group_id}/members", {"person_id": person_id}))
        _, body = self.moved("/api/person-groups", etag, "member")
        self.assertEqual(self.row(body, group_id)["member_count"], 1)

    def test_the_playlist_catalog_follows_title_membership_and_notes_but_not_order(self) -> None:
        playlist_id = self.ok(self.send("POST", "/api/playlists", {"title": "Etag Playlist", "description": ""}))["id"]
        first, second = (self.create_work(f"Etag Playlist {n}")["id"] for n in "AB")
        self.ok(self.send("POST", f"/api/playlists/{playlist_id}/items", {"work_id": first}))
        _, etag, body = self.conditional("/api/playlists")
        row = self.row(body, playlist_id)
        self.assertEqual(row["item_count"], 1)
        self.assertNotIn("item_ids", row)
        self.ok(self.send("PATCH", f"/api/playlists/{playlist_id}", {"title": "Etag Playlist Renamed"}))
        etag, body = self.moved("/api/playlists", etag, "title")
        self.assertEqual(self.row(body, playlist_id)["title"], "Etag Playlist Renamed")
        self.ok(self.send("POST", f"/api/playlists/{playlist_id}/items", {"work_id": second}))
        etag, body = self.moved("/api/playlists", etag, "membership")
        self.assertEqual(self.row(body, playlist_id)["item_count"], 2)
        self.ok(self.send("POST", f"/api/playlists/{playlist_id}/reorder", {"work_ids": [second, first]}))
        self.assertEqual(self.conditional("/api/playlists", etag)[0], 304)
        self.ok(self.send("PATCH", f"/api/playlists/{playlist_id}", {"description": "same-second notes"}))
        _, body = self.moved("/api/playlists", etag, "notes")
        self.assertEqual(self.row(body, playlist_id)["description"], "same-second notes")

    def test_an_unchanged_folder_catalog_revalidates(self) -> None:
        self.folder("ETag Stable Folder")
        etag = self.conditional("/api/folders")[1]
        self.assertEqual(self.conditional("/api/folders", etag)[0], 304)


class TagReportTests(FreshnessTestCase):
    def test_a_delete_reports_the_works_and_folders_it_changed(self) -> None:
        folder_id = self.ok(self.send("POST", "/api/folders", {"title": "Tag Del Folder"}))["id"]
        work_id, other = (self.create_work(title)["id"] for title in ("Tag Del Work", "Tag Del Untouched"))
        tag_id = self.tag("TagDelSubject")
        self.ok(self.send("POST", f"/api/works/{work_id}/tags", {"tag_id": tag_id}))
        self.ok(self.send("POST", f"/api/folders/{folder_id}/tags", {"tag_id": tag_id}))
        out = self.ok(self.send("DELETE", f"/api/tags/{tag_id}"))
        self.assertEqual((out.get("affected_work_ids"), out.get("affected_folder_ids")), ([work_id], [folder_id]))
        self.assertNotIn(other, out["affected_work_ids"])

    def test_a_merge_reports_the_sources_works_and_folders(self) -> None:
        folder_id = self.ok(self.send("POST", "/api/folders", {"title": "Tag Merge Folder"}))["id"]
        work_id, target_only = (self.create_work(t)["id"] for t in ("Tag Merge Work", "Tag Merge TargetOnly"))
        source, target = self.tag("MergeSource"), self.tag("MergeTarget")
        for path in (f"/api/works/{work_id}/tags", f"/api/folders/{folder_id}/tags"):
            self.ok(self.send("POST", path, {"tag_id": source}))
        self.ok(self.send("POST", f"/api/works/{target_only}/tags", {"tag_id": target}))
        out = self.ok(self.send("POST", "/api/tags/merge", {"source_tag_id": source, "target_tag_id": target}))
        self.assertEqual((out.get("affected_work_ids"), out.get("affected_folder_ids")), ([work_id], [folder_id]))

    def test_a_merge_reports_a_work_that_already_carried_both(self) -> None:
        work_id = self.create_work("Tag Merge Both")["id"]
        source, target = self.tag("BothSource"), self.tag("BothTarget")
        for tag_id in (source, target):
            self.ok(self.send("POST", f"/api/works/{work_id}/tags", {"tag_id": tag_id}))
        out = self.ok(self.send("POST", "/api/tags/merge", {"source_tag_id": source, "target_tag_id": target}))
        self.assertIn(work_id, out.get("affected_work_ids") or [])
        self.assertEqual([t.get("name") for t in self.ok(self.get(f"/api/works/{work_id}"))["tags"]], ["BothTarget"])

    def test_a_refused_merge_changes_nothing(self) -> None:
        work_id = self.create_work("Tag Merge Fail")["id"]
        source = self.tag("FailSource")
        self.ok(self.send("POST", f"/api/works/{work_id}/tags", {"tag_id": source}))
        out = self.refused(self.send("POST", "/api/tags/merge", {"source_tag_id": source,
                                                                 "target_tag_id": "T-does-not-exist"}), 400)
        self.assertIsNone(out.get("affected_work_ids"))
        self.assertEqual([t.get("name") for t in self.ok(self.get(f"/api/works/{work_id}"))["tags"]], ["FailSource"])


class BrowseTests(FreshnessTestCase):
    def test_the_works_browse_follows_every_rendered_field_but_not_a_move(self) -> None:
        work_id = self.create_work("Browse ETag Work", status="Not Started")["id"]
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": "Bro", "last_name": "Wser"}))["id"]
        folder_id = self.ok(self.send("POST", "/api/folders", {"title": "Browse ETag Folder"}))["id"]
        path = f"/api/works/{work_id}"
        for fields in ({"title": "Browse ETag Retitled"}, {"status": "Completed"}, {"doc_type": "book"}):
            self.follows("works-browse", lambda f=fields: self.ok(self.send("PATCH", path, f)))
        self.follows("works-browse", lambda: self.ok(self.send("POST", "/api/roles", {
            "person_id": person_id, "work_id": work_id, "role_type": "Author"})))
        self.follows("works-browse", lambda: self.ok(self.send("PATCH", f"/api/persons/{person_id}", {
            "first_name": "Bro", "last_name": "Renamed"})))
        self.follows("works-browse", lambda: self.ok(self.send("PATCH", path, {"folder_id": folder_id})),
                     changes=False)

    def test_the_works_browse_follows_a_replaced_pdfs_size(self) -> None:
        work_id = self.create_work("Browse Size Work", **self.upload_fields("small", "browse.pdf"))["id"]
        self.follows("works-browse", lambda: self.replace_pdf(work_id, "a much longer document body " * 200))

    def test_recent_follows_an_open_and_a_listed_works_title(self) -> None:
        first, second = (self.create_work(f"Recent ETag {n}")["id"] for n in ("One", "Two"))
        self.ok(self.send("POST", f"/api/works/{first}/opened", {}))
        self.follows("recent", lambda: self.ok(self.send("POST", f"/api/works/{second}/opened", {})))
        self.assertIn(second, [r["id"] for r in self.conditional("/api/recent")[2]])
        self.follows("recent", lambda: self.ok(self.send("PATCH", f"/api/works/{first}",
                                                         {"title": "Recent Display Renamed"})))

    def test_recently_added_follows_a_create_an_edit_and_a_move(self) -> None:
        self.follows("recently-added", lambda: self.create_work("Recently Added ETag New"))
        work_id = self.create_work("Recently Added Member")["id"]
        self.follows("recently-added", lambda: self.ok(self.send("PATCH", f"/api/works/{work_id}",
                                                                 {"status": "Paused"})))
        folder_id = self.ok(self.send("POST", "/api/folders", {"title": "RA ETag Folder"}))["id"]
        self.follows("recently-added", lambda: self.ok(self.send("PATCH", f"/api/works/{work_id}",
                                                                 {"folder_id": folder_id})))

    def test_the_projections_are_deterministically_ordered(self) -> None:
        for work_id in [self.create_work(f"Order Probe {i}")["id"] for i in range(5)]:
            self.ok(self.send("POST", f"/api/works/{work_id}/opened", {}))
        for path in BROWSE.values():
            first, second = self.conditional(path)[2], self.conditional(path)[2]
            self.assertEqual([r["id"] for r in first], [r["id"] for r in second], path)

    def test_the_browse_projection_is_compact(self) -> None:
        work_id = self.create_work("Compact Probe", abstract="x" * 5000)["id"]
        row = self.row(self.conditional(BROWSE["works-browse"])[2], work_id)
        self.assertNotIn("abstract", row)
        self.assertLessEqual(len(row["abstract_excerpt"]), 100)
        self.assertEqual(len(self.row(self.conditional("/api/works")[2], work_id)["abstract"]), 5000)


class OpenEventTests(FreshnessTestCase):
    def recent_ids(self) -> list[str]:
        return [r["id"] for r in self.ok(self.get("/api/recent"))]

    def test_reading_a_work_never_records_an_open(self) -> None:
        work_id = self.create_work("Pure Read Work")["id"]
        before = self.recent_ids()
        for _ in range(3):
            self.ok(self.get(f"/api/works/{work_id}"))
        self.assertEqual(self.recent_ids(), before)
        self.assertNotIn(work_id, before)

    def test_a_save_and_its_refresh_read_never_record_an_open(self) -> None:
        work_id = self.create_work("Refresh Shape Work")["id"]
        folder_id = self.ok(self.send("POST", "/api/folders", {"title": "Refresh Shape Folder"}))["id"]
        tag_id = self.tag("RefreshShapeTag")
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": "Ref", "last_name": "Shape"}))["id"]
        for label, method, path, body in (
            ("tag add", "POST", f"/api/works/{work_id}/tags", {"tag_id": tag_id}),
            ("folder move", "PATCH", f"/api/works/{work_id}", {"folder_id": folder_id}),
            ("role add", "POST", "/api/roles", {"person_id": person_id, "work_id": work_id, "role_type": "Author"}),
            ("metadata save", "PATCH", f"/api/works/{work_id}", {"title": "Refresh Shape Renamed"}),
            ("tag remove", "DELETE", f"/api/works/{work_id}/tags/{tag_id}", None),
        ):
            with self.subTest(step=label):
                self.ok(self.send(method, path, body))
                self.ok(self.get(f"/api/works/{work_id}"))
                self.assertNotIn(work_id, self.recent_ids(), f"{label} made the Work look recently opened")

    def test_the_open_event_lists_a_work_once_and_an_unknown_one_is_404(self) -> None:
        work_id = self.create_work("Open Event Work")["id"]
        for _ in range(2):
            self.ok(self.send("POST", f"/api/works/{work_id}/opened", {}))
            self.assertEqual(self.recent_ids().count(work_id), 1)
        self.assertEqual(self.send("POST", "/api/works/W-nope/opened", {}).status, 404)


class ShellTests(FreshnessTestCase):
    def test_every_response_forbids_mime_sniffing(self) -> None:
        self.create_work("<html><body>sniffable</body></html>")
        for path in ("/api/works", "/", "/index.html", "/api/no-such-endpoint"):
            res = self.get(path)
            self.assertIn(res.status, (200, 304, 404), path)
            self.assertEqual(res.header("X-Content-Type-Options"), ["nosniff"], path)

    def test_the_vue_bundle_is_served_with_the_shell(self) -> None:
        bundle = self.get("/vue/prks-vue.js")
        self.assertEqual(bundle.status, 200)
        self.assertIn(b"data-prks-vue-bootstrap", bundle.raw)
        self.assertNotIn(b"unpkg.com", bundle.raw)
        self.assertNotIn(b"cdn.jsdelivr.net", bundle.raw)
        self.assertEqual(bundle.header("X-Content-Type-Options"), ["nosniff"])
        self.assertIn("must-revalidate", bundle.header("Cache-Control")[0])
        page = self.get("/")
        self.assertEqual(page.status, 200)
        for marker in (b'id="prks-vue-root"', b'type="module"', b'src="/vue/prks-vue.js"'):
            self.assertIn(marker, page.raw)
