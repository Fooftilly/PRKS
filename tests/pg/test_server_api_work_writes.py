"""Research Notes, the role API, video sources and sync replays over HTTP on the PostgreSQL Library (PG-C2 #641).

A note names new Concepts and, once the research reindex has run, lists its
references; a failed reindex never loses the note, and a Concept or Argument
a note names refuses deletion even before its reindex ran. A missing
Argument is left out of the references. A Work created with roles keeps the
order given and starts at revision 0; the role API refuses roles outside the
domain and oversized credit names and advances the relationship revision. A
video Work needs a YouTube URL, is enriched whether or not ``video`` was
declared, and a file-backed Work never is. A replayed sync operation answers
its first result; a Research Note acknowledgement never carries the body,
and its references are pending until the reindex ran. These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from unittest import mock

from tests.pg.server_api_fixtures import ServerApiTestCase

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PDF = base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode()


def operation(name: str, work_id: str, payload: dict[str, Any], base_revision: int = 0) -> dict[str, Any]:
    return {"op_id": str(uuid.uuid4()), "device_id": str(uuid.uuid4()), "operation": name, "entity_type": "work",
            "entity_id": work_id, "payload": payload, "base_revision": base_revision,
            "occurred_at": "2026-09-11T00:00:00Z", "created_at": "2026-09-11T00:00:00Z", "depends_on": []}


class ResearchNoteTests(ServerApiTestCase):
    def note(self, work_id: str, text: str) -> None:
        self.ok(self.send("PATCH", f"/api/works/{work_id}", {"text_content": text}))

    def test_a_note_names_a_new_concept_and_lists_it_once_reindexed(self) -> None:
        work_id = self.create_work("Research note work")["id"]
        self.note(work_id, "See [[concept:Culture Industry]].")
        self.assertIn("Culture Industry", [c["name"] for c in self.ok(self.get("/api/concepts"))])
        self.run_jobs()
        self.assertTrue((self.ok(self.get(f"/api/works/{work_id}")).get("research_refs") or {}).get("concepts"))

    def test_a_failed_reindex_keeps_the_note(self) -> None:
        from backend.domain.research import reindex

        work_id = self.create_work("Index fail work")["id"]
        self.note(work_id, "[[concept:Instrumental Reason]]")
        with mock.patch.object(reindex, "reindex_work", side_effect=RuntimeError("boom")) as failed:
            self.run_jobs()
        failed.assert_called()
        self.assertIs(self.ok(self.get(f"/api/works/{work_id}"))["research_refs"]["pending"], True)
        self.assertIn("[[concept:Instrumental Reason]]", self.ok(self.get(f"/api/works/{work_id}"))["text_content"])

    def test_a_concept_or_argument_a_note_names_refuses_deletion_before_the_reindex(self) -> None:
        work_id = self.create_work("Stale delete")["id"]
        argument_id = self.ok(self.send("POST", "/api/arguments", {"name": "Linked argument", "kind": "argument"}),
                              201)["id"]
        self.note(work_id, f"[[concept:Culture Industry]] and [[argument:{argument_id}|label]]")
        concept_id = next(c["id"] for c in self.ok(self.get("/api/concepts")) if c["name"] == "Culture Industry")
        res = self.send("DELETE", f"/api/concepts/{concept_id}")
        self.assertEqual((res.status, res.body.get("code")), (409, "concept_in_use"), res.raw)
        res = self.send("DELETE", f"/api/arguments/{argument_id}")
        self.assertEqual((res.status, res.body.get("code")), (409, "argument_in_use"), res.raw)

    def test_a_missing_argument_is_left_out_of_the_references(self) -> None:
        work_id = self.create_work("Missing argument ref")["id"]
        argument_id = self.ok(self.send("POST", "/api/arguments", {"name": "Real argument", "kind": "argument"}),
                              201)["id"]
        self.note(work_id, f"[[argument:A-MISSING]] and [[argument:{argument_id}|ok]]")
        self.run_jobs()
        refs = self.ok(self.get(f"/api/works/{work_id}")).get("research_refs") or {}
        ids = [a["id"] for a in refs.get("arguments") or []]
        self.assertNotIn("A-MISSING", ids)
        self.assertIn(argument_id, ids)

    def test_a_note_acknowledgement_carries_the_references_not_the_body(self) -> None:
        work_id = self.create_work("Note ACK Refs")["id"]
        concept_id = self.ok(self.send("POST", "/api/concepts", {"name": "ACK Ref Concept"}), 201)["id"]
        body = "See [[concept:ACK Ref Concept]]."
        result = self.ok(self.send("POST", "/api/sync/operations",
                                   operation("SET_WORK_RESEARCH_NOTE", work_id, {"text": body})))
        self.assertEqual(result.get("code"), "ACKNOWLEDGED")
        self.assertTrue(result.get("value_omitted"))
        self.assertNotIn("text", result)
        self.assertNotIn(body, json.dumps(result))
        # On PostgreSQL the references come from the queued reindex, so the
        # acknowledgement says they are pending; the Work lists them once it ran.
        self.assertEqual(result.get("research_refs"), {"concepts": [], "arguments": [], "pending": True})
        self.run_jobs()
        refs = self.ok(self.get(f"/api/works/{work_id}"))["research_refs"]
        self.assertEqual(([c["id"] for c in refs["concepts"]], refs["pending"]), ([concept_id], False))


class RoleApiTests(ServerApiTestCase):
    def person(self, first: str, last: str) -> str:
        return self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]

    def scopes(self, work_id: str) -> list[dict[str, Any]]:
        return self.ok(self.get(f"/api/works/{work_id}/people-state"))["scopes"]

    def roles(self, work_id: str) -> list[dict[str, Any]]:
        return self.ok(self.get(f"/api/works/{work_id}"))["roles"]

    def test_a_work_created_with_roles_keeps_their_order_at_revision_0(self) -> None:
        pa, pb = self.person("Ann", "Lee"), self.person("Bo", "Ng")
        work_id = self.create_work("Co-authored", roles=[
            {"person_id": pa, "role_type": "Author", "credit_name": "A. Lee"},
            {"person_id": pb, "role_type": "Author"}])["id"]
        self.assertEqual(sorted(s["revision"] for s in self.scopes(work_id)), [0, 0],
                         "construction creates no revisions")
        roles = self.roles(work_id)
        self.assertEqual([(r["role_type"], r["id"]) for r in roles], [("Author", pa), ("Author", pb)])
        self.ok(self.send("DELETE", f"/api/works/{work_id}/roles?person_id={pa}&role_type=Author"))
        state = {s["person_id"]: s for s in self.scopes(work_id)}
        self.assertEqual(state[pa], {"person_id": pa, "role_type": "Author", "revision": 1, "present": False})
        self.assertEqual(state[pb]["revision"], 0, "the other is untouched")

    def test_a_role_outside_the_domain_is_refused(self) -> None:
        person_id, work_id = self.person("Pro", "Ducer"), self.create_work("Roleless")["id"]
        body = self.refused(self.send("POST", "/api/roles", {"person_id": person_id, "work_id": work_id,
                                                             "role_type": "Producer"}), 400)
        self.assertIn("not a Work role", body.get("error", ""))
        self.assertEqual(self.roles(work_id), [])

    def test_linking_and_crediting_advance_the_relationship_revision(self) -> None:
        person_id, work_id = self.person("Samuel", "Clemens"), self.create_work("Huck")["id"]
        self.ok(self.send("POST", "/api/roles", {"person_id": person_id, "work_id": work_id, "role_type": "Author",
                                                 "credit_name": "Mark Twain"}))
        self.assertEqual(self.scopes(work_id), [{"person_id": person_id, "role_type": "Author", "revision": 1,
                                                 "present": True}])
        self.assertEqual(self.roles(work_id)[0]["credit_name"], "Mark Twain")
        self.assertIn("Mark Twain", self.ok(self.get(f"/api/persons/{person_id}"))["aliases"])
        patch = {"person_id": person_id, "role_type": "Author"}
        self.ok(self.send("PATCH", f"/api/works/{work_id}/roles", {**patch, "order_index": 99,
                                                                   "credit_name": "M. Twain"}))
        self.assertEqual(self.scopes(work_id)[0]["revision"], 2, "ordinary credit PATCH must advance the revision")
        self.assertEqual(self.roles(work_id)[0]["credit_name"], "M. Twain")
        self.ok(self.send("PATCH", f"/api/works/{work_id}/roles", {**patch, "credit_name": ""}))
        self.assertEqual(self.scopes(work_id)[0]["revision"], 3)

    def test_an_oversized_credit_name_is_refused_on_add_and_on_edit(self) -> None:
        from backend.work_role_sync import MAX_CREDIT_NAME_BYTES

        person_id, work_id = self.person("Long", "Name"), self.create_work("Bound")["id"]
        too_long = "x" * (MAX_CREDIT_NAME_BYTES + 1)
        role = {"person_id": person_id, "work_id": work_id, "role_type": "Author"}
        self.assertIn("credit_name", self.refused(self.send("POST", "/api/roles", {**role, "credit_name": too_long}),
                                                  400).get("error", ""))
        self.assertEqual(self.roles(work_id), [])
        self.ok(self.send("POST", "/api/roles", role))
        body = self.refused(self.send("PATCH", f"/api/works/{work_id}/roles", {
            "person_id": person_id, "role_type": "Author", "credit_name": too_long}), 400)
        self.assertIn("credit_name", body.get("error", ""))
        self.assertIsNone(self.roles(work_id)[0].get("credit_name"))


class VideoSourceTests(ServerApiTestCase):
    def work(self, work_id: str) -> dict[str, Any]:
        return self.ok(self.get(f"/api/works/{work_id}"))

    def test_an_inferred_video_is_enriched_like_a_declared_one(self) -> None:
        seen: list[str] = []

        def oembed(requested: str) -> dict[str, str]:
            seen.append(requested)
            return {"title": "Enriched Title", "author_name": "Enriched Channel",
                    "thumbnail_url": "https://img.example/thumb.jpg"}

        with mock.patch.object(self.server_module, "_fetch_youtube_oembed", oembed):
            declared = self.create_work("", source_kind="video", source_url=VIDEO)
            inferred = self.ok(self.send("POST", "/api/works", {"source_url": VIDEO}))
        self.assertEqual(seen, [VIDEO, VIDEO], "the same enrichment boundary, twice")
        for created in (declared, inferred):
            row = self.work(created["id"])
            self.assertEqual((row["source_kind"], row["provider"], row["provider_id"], row["title"], row["author_text"],
                              row["thumb_url"]),
                             ("video", "youtube", "dQw4w9WgXcQ", "Enriched Title", "Enriched Channel",
                              "https://img.example/thumb.jpg"))

    def test_a_file_backed_work_is_never_enriched(self) -> None:
        seen: list[str] = []
        with mock.patch.object(self.server_module, "_fetch_youtube_oembed", seen.append):
            created = self.create_work("Paper", file_b64=PDF, file_name="paper.pdf", source_url=VIDEO)
        self.assertEqual(seen, [], "no video enrichment for a file-backed Work")
        row = self.work(created["id"])
        self.assertEqual((row["title"], row["provider_id"]), ("Paper", None))

    def test_the_source_kind_is_stored_canonically_and_an_unknown_one_is_refused(self) -> None:
        for sent, stored in (("PDF", "pdf"), ("Pdf", "pdf"), ("VIDEO", "video")):
            with self.subTest(sent=sent):
                extra = ({"source_url": "https://youtu.be/dQw4w9WgXcQ"} if stored == "video"
                         else {"file_b64": PDF, "file_name": "c.pdf"})
                created = self.create_work("Cased", source_kind=sent, **extra)
                self.assertEqual(self.work(created["id"])["source_kind"], stored)
        before = self.titles()
        body = self.refused(self.send("POST", "/api/works", {"title": "Web Work", "source_kind": "web",
                                                             "source_url": VIDEO}), 400)
        self.assertIn("not a source kind", body.get("error", ""))
        self.assertEqual(self.titles(), before)

    def test_a_video_needs_a_youtube_url_and_a_refusal_creates_nothing(self) -> None:
        before = self.titles()
        for url in ("not a url", "https://example.com/video", "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
                    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
                    "javascript://youtube.com/watch?v=dQw4w9WgXcQ", ""):
            with self.subTest(url=url):
                body = self.refused(self.send("POST", "/api/works", {"title": "Bad Video", "source_kind": "video",
                                                                     "source_url": url}), 400)
                self.assertEqual(body.get("error"), "Invalid YouTube URL")
        self.assertEqual(self.titles(), before)

    def test_a_watch_or_short_url_is_accepted(self) -> None:
        for title, url in (("Valid Watch URL Work", VIDEO), ("Valid Short URL Work", "https://youtu.be/dQw4w9WgXcQ")):
            with self.subTest(url=url):
                created = self.create_work(title, source_kind="video", source_url=url)
                self.assertIn(title, self.titles())
                row = self.work(created["id"])
                self.assertEqual((row.get("provider"), row.get("provider_id")), ("youtube", "dQw4w9WgXcQ"))

    def test_no_folder_files_the_work_in_uncategorized(self) -> None:
        work = self.work(self.create_work("Default Folder Work", folder_id=None)["id"])
        folder = next(f for f in self.ok(self.get("/api/folders")) if f.get("id") == work.get("folder_id"))
        self.assertEqual(folder.get("title"), "Uncategorized")


class SyncReplayTests(ServerApiTestCase):
    def test_a_tag_operation_replays_its_first_result(self) -> None:
        work_id = self.create_work("HTTP Sync")["id"]
        tag_id = self.ok(self.send("POST", "/api/tags", {"name": "HTTP Sync"}))["id"]
        path = f"/api/works/{work_id}/tag-options"
        first = self.get(path)
        self.assertEqual(first.status, 200)
        etag = first.header("ETag")[0]
        op = operation("ADD_WORK_TAG", work_id, {"tag_id": tag_id})
        added = self.send("POST", "/api/sync/operations", op)
        self.assertEqual(added.status, 200, added.raw)
        again = self.send("POST", "/api/sync/operations", op)
        self.assertEqual((again.status, again.body), (added.status, added.body))
        self.assertEqual(self.get(path, headers=[("If-None-Match", etag)]).status, 200)
        op.update(op_id=str(uuid.uuid4()), operation="REMOVE_WORK_TAG")
        removed = self.send("POST", "/api/sync/operations", op)
        self.assertEqual(removed.status, 409, removed.raw)
        again = self.send("POST", "/api/sync/operations", op)
        self.assertEqual((again.status, again.body), (removed.status, removed.body))

    def test_a_metadata_operation_whose_response_was_lost_replays_at_revision_1(self) -> None:
        work_id = self.create_work("HTTP Meta Lost")["id"]
        op = operation("SET_WORK_METADATA_FIELD", work_id, {"field": "doi", "value": "10.1/http-lost"})
        self.ok(self.send("POST", "/api/sync/operations", op))
        result = self.ok(self.send("POST", "/api/sync/operations", op))
        self.assertEqual((result.get("code"), result.get("server_revision"), result.get("value")),
                         ("ACKNOWLEDGED", 1, "10.1/http-lost"))
        doi = self.ok(self.get(f"/api/works/{work_id}/metadata-state"))["fields"]["doi"]
        self.assertEqual((doi["revision"], doi["value"]), (1, "10.1/http-lost"))
