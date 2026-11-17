"""Roles and the legacy annotation handshake over HTTP on the PostgreSQL Library (PG-C2 #641).

A role is added, credited, re-credited and removed. The legacy full-list
annotation route replaces a PDF Work's annotations against the acknowledged
set revision and never marks the PDF materialized; only ``POST /pdf`` with a
current ``materialized_annotation_set_revision`` does, and a stale claim is a
409 that changes nothing. A save token is recorded only after a save
succeeded. Byte-only markup is adopted and links are skipped. On PostgreSQL a
Work without a managed PDF has no annotations to store, a viewer id belongs to
each file (no cross-Work conflict), and a plain replace reports what its new
bytes embed.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlencode

from tests.pg.server_api_fixtures import ServerApiTestCase
from tests.pg.test_search_text import pdf_bytes


def row(ann_id: str, contents: str) -> dict[str, Any]:
    return {"id": ann_id, "type": 9, "contents": contents, "pageIndex": 0,
            "segmentRects": [{"origin": {"x": 1, "y": 1}, "size": {"width": 2, "height": 2}}],
            "custom": {"prksComment": contents}}


class AnnotationHttpTestCase(ServerApiTestCase):
    def pdf_work(self, title: str) -> str:
        return self.create_work(title, status="Planned", **self.upload_fields(title, f"{title[:12]}.pdf"))["id"]

    def materialization(self, work_id: str) -> dict[str, Any]:
        with self.acting():
            return self.facade.get_work_pdf_materialization(work_id)

    def post_annotations(self, work_id: str, body: dict[str, Any]):
        payload = dict(body)
        if "annotations_json" in payload and "canonical_annotation_set_revision" not in payload:
            payload["canonical_annotation_set_revision"] = int(
                self.materialization(work_id).get("canonical_annotation_set_revision") or 0)
        return self.send("POST", f"/api/works/{work_id}/annotations", payload)

    def save(self, work_id: str, items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return self.ok(self.post_annotations(work_id, {"annotations_json": json.dumps(items), **extra}))

    def annotations(self, work_id: str) -> list[dict[str, Any]]:
        payload = self.ok(self.get(f"/api/works/{work_id}/annotations"))
        self.assertEqual(payload.get("work_id"), work_id)
        return json.loads(payload["annotations_json"])

    def post_pdf(self, work_id: str, text: str, **extra: Any):
        body = {"file_b64": base64.b64encode(pdf_bytes(text)).decode("ascii"), **extra}
        return self.send("POST", f"/api/works/{work_id}/pdf", body)

    def save_confirm(self, work_id: str, token: str) -> dict[str, Any]:
        return self.ok(self.get(f"/api/works/{work_id}/save-confirm?{urlencode({'token': token})}"))


class RoleTests(AnnotationHttpTestCase):
    def person(self, first: str, last: str) -> str:
        return self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]

    def bibtex(self, work_id: str) -> str:
        return self.get(f"/api/bibtex/{work_id}").raw.decode("utf-8", errors="replace")

    def test_a_role_is_added_and_removed_once_beside_annotations(self) -> None:
        person_id = self.person("Role", "Owner")
        work_id = self.pdf_work("Role Work")
        self.ok(self.send("POST", "/api/roles", {"person_id": person_id, "work_id": work_id, "role_type": "Author"}))
        self.save(work_id, [{"id": "role-work-ann-1", "type": "note", "contents": "hello", "pageIndex": 0,
                             "color": "#fff"}])
        self.assertEqual([item["id"] for item in self.annotations(work_id)], ["role-work-ann-1"])
        query = urlencode({"person_id": person_id, "role_type": "Author", "order_index": "0"})
        self.assertEqual(self.ok(self.send("DELETE", f"/api/works/{work_id}/roles?{query}")).get("status"),
                         "removed")
        self.assertEqual(self.send("DELETE", f"/api/works/{work_id}/roles?{query}").status, 404)

    def test_a_credit_name_is_set_changed_and_cleared(self) -> None:
        person_id = self.person("Mark", "Johnson")
        work_id = self.create_work("Credit API Work", status="Planned")["id"]
        self.ok(self.send("POST", "/api/roles", {"person_id": person_id, "work_id": work_id, "role_type": "Author",
                                                 "credit_name": "Mark S. Johnson"}))
        self.assertIn("author = {Mark S. Johnson}", self.bibtex(work_id))
        patch = {"person_id": person_id, "role_type": "Author", "order_index": 0}
        body = self.ok(self.send("PATCH", f"/api/works/{work_id}/roles", {**patch, "credit_name": "Mark Johnson"}))
        self.assertEqual(body.get("status"), "updated")
        self.assertIn("author = {Mark Johnson}", self.bibtex(work_id))
        self.ok(self.send("PATCH", f"/api/works/{work_id}/roles", {**patch, "credit_name": ""}))
        self.assertIn("author = {Johnson, Mark}", self.bibtex(work_id))
        self.assertIn("Mark S. Johnson", self.ok(self.get(f"/api/persons/{person_id}")).get("aliases") or "")


class LegacyReplaceTests(AnnotationHttpTestCase):
    def test_a_malformed_or_conflicting_list_changes_nothing(self) -> None:
        work_id = self.pdf_work("Ann HTTP Valid")
        other = self.pdf_work("Ann HTTP Other")
        self.save(work_id, [{"id": "keep", "type": "note", "contents": "hello", "pageIndex": 0}])
        self.assertEqual(self.annotations(work_id)[0]["contents"], "hello")
        cases = [
            {"save_token": "no-list"},
            {"annotations_json": "{"},
            {"annotations_json": json.dumps({"id": "a"})},
            {"annotations_json": json.dumps("x")},
            {"annotations_json": json.dumps([{"type": "note", "pageIndex": 0}])},
            {"annotations_json": json.dumps([{"id": "dup", "pageIndex": 0}, {"id": "dup", "pageIndex": 1}])},
        ]
        for body in cases:
            with self.subTest(body=body):
                refused = self.refused(self.post_annotations(work_id, body), 400)
                self.assertEqual(refused.get("code"), "malformed_annotation_payload")
                self.assertEqual(self.annotations(work_id)[0]["id"], "keep")
        # SQLite refused a viewer id another Work held (annotation_id_conflict);
        # on PostgreSQL the id belongs to each file, so each Work keeps its own.
        self.save(other, [{"id": "owned-x", "pageIndex": 0, "contents": "other"}])
        self.save(work_id, [{"id": "owned-x", "pageIndex": 0, "contents": "mine"}])
        self.assertEqual([(i["id"], i["contents"]) for i in self.annotations(work_id)], [("owned-x", "mine")])
        self.assertEqual([(i["id"], i["contents"]) for i in self.annotations(other)], [("owned-x", "other")])
        refused = self.refused(self.send("POST", "/api/works/W-MISSING/annotations",
                                         {"annotations_json": "[]", "canonical_annotation_set_revision": 0}), 404)
        self.assertEqual(refused.get("code"), "work_not_found")

    def test_a_work_without_a_managed_pdf_stores_no_annotation(self) -> None:
        work_id = self.create_work("Ann No PDF", status="Planned")["id"]
        refused = self.refused(self.post_annotations(work_id, {"annotations_json": json.dumps([row("x", "x")])}),
                               404)
        self.assertEqual(refused.get("code"), "work_pdf_not_found")
        self.assertEqual(self.annotations(work_id), [])

    def test_a_stale_set_revision_is_refused_and_the_base_is_required(self) -> None:
        work_id = self.pdf_work("Ann Stale Set")
        tip = self.save(work_id, [row("stale-a", "first")])["canonical_annotation_set_revision"]
        current = [row("stale-a", "second"), row("stale-b", "new-B")]
        tip2 = self.save(work_id, current)["canonical_annotation_set_revision"]
        self.assertGreater(tip2, tip)
        refused = self.refused(self.post_annotations(work_id, {
            "annotations_json": json.dumps([row("stale-a", "stale-A")]), "canonical_annotation_set_revision": tip}),
            409)
        self.assertEqual(refused.get("code"), "ANNOTATION_SET_STALE")
        self.assertEqual({item["id"]: item["contents"] for item in self.annotations(work_id)},
                         {"stale-a": "second", "stale-b": "new-B"})
        again = self.save(work_id, current, canonical_annotation_set_revision=tip2)
        self.assertEqual(again["canonical_annotation_set_revision"], tip2)
        self.refused(self.send("POST", f"/api/works/{work_id}/annotations",
                               {"annotations_json": json.dumps([row("stale-a", "x")])}), 400)

    def test_a_save_token_is_recorded_only_after_a_save(self) -> None:
        work_id = self.pdf_work("Ann Token")
        self.save(work_id, [{"id": "tok", "contents": "ok", "pageIndex": 0}], save_token="token-ok")
        self.assertTrue(self.save_confirm(work_id, "token-ok")["annotations_saved"])
        self.refused(self.post_annotations(work_id, {"annotations_json": "{", "save_token": "token-bad"}), 400)
        self.assertFalse(self.save_confirm(work_id, "token-bad")["annotations_saved"])
        self.assertTrue(self.save_confirm(work_id, "token-ok")["annotations_saved"])
        self.assertEqual(self.annotations(work_id)[0]["id"], "tok")

    def test_byte_only_markup_is_adopted_and_links_are_skipped(self) -> None:
        work_id = self.pdf_work("Ann Adopt")
        meta = {"id": "meta-only", "type": 9, "contents": "m", "pageIndex": 0,
                "segmentRects": [{"origin": {"x": 1, "y": 1}, "size": {"width": 2, "height": 2}}],
                "custom": {"prksComment": "m"}}
        body = self.save(work_id, [meta])
        self.assertNotIn("materialized_pdf_annotation_revision", body)
        self.assertNotIn("stale", body)
        tip = body["canonical_annotation_set_revision"]
        self.ok(self.post_pdf(work_id, "adopt bytes", materialized_annotation_set_revision=tip))
        viewer = [
            {"id": "byte-only", "type": 9, "contents": "b", "pageIndex": 0,
             "segmentRects": [{"origin": {"x": 3, "y": 3}, "size": {"width": 2, "height": 2}}],
             "custom": {"prksComment": "b"}},
            {"id": "link-skip", "type": 2, "uri": "https://example.test/x", "pageIndex": 0},
            {"id": "link-type1", "type": 1, "pageIndex": 0,
             "rect": {"origin": {"x": 8, "y": 8}, "size": {"width": 20, "height": 8}}},
            meta,
        ]
        adopted = self.ok(self.send("POST", f"/api/works/{work_id}/annotations/adopt", {"viewer_annotations": viewer}))
        self.assertEqual(adopted.get("adopted"), ["byte-only"])
        self.assertEqual({item["id"] for item in self.annotations(work_id)}, {"meta-only", "byte-only"})


class MaterializationClaimTests(AnnotationHttpTestCase):
    def test_only_a_pdf_claim_marks_the_bytes_materialized(self) -> None:
        work_id = self.pdf_work("Legacy Mat Order")
        body = self.save(work_id, [row("legacy-ann-1", "hi")], save_token="legacy-handshake")
        tip = body["canonical_annotation_set_revision"]
        self.assertIsInstance(tip, int)
        self.assertGreaterEqual(tip, 1)
        self.assertNotIn("materialized_pdf_annotation_revision", body)
        middle = self.materialization(work_id)
        self.assertTrue(middle["stale"])
        self.assertEqual(middle["canonical_annotation_set_revision"], tip)
        pdf = self.ok(self.post_pdf(work_id, "legacy mark order v2", save_token="legacy-handshake",
                                    materialized_annotation_set_revision=tip))
        self.assertEqual((pdf.get("materialized_pdf_annotation_revision"), pdf.get("canonical_annotation_set_revision"),
                          bool(pdf.get("stale"))), (tip, tip, False))
        done = self.materialization(work_id)
        self.assertEqual(done["canonical_annotation_set_revision"], done["materialized_pdf_annotation_revision"])
        self.assertFalse(done["stale"])
        after = self.save(work_id, [row("legacy-ann-1", "changed")], save_token="another-token")
        self.assertNotIn("materialized_pdf_annotation_revision", after)
        lagging = self.materialization(work_id)
        self.assertTrue(lagging["stale"])
        self.assertGreater(lagging["canonical_annotation_set_revision"], lagging["materialized_pdf_annotation_revision"])

    def test_an_annotation_save_never_marks_even_with_a_leftover_pdf_token(self) -> None:
        work_id = self.pdf_work("Ann Only No Mark")
        pdf = self.ok(self.post_pdf(work_id, "token plant", save_token="leftover-pdf-token"))
        # A plain replace reports what the new bytes hold: with no annotations
        # yet, the empty set is embedded, so the Work is not stale.
        self.assertEqual((pdf.get("canonical_annotation_set_revision"),
                          pdf.get("materialized_pdf_annotation_revision"), pdf.get("stale")), (1, 1, False))
        before = self.materialization(work_id)
        body = self.save(work_id, [row("only-ann", "x")], save_token="leftover-pdf-token")
        self.assertIn("canonical_annotation_set_revision", body)
        self.assertNotIn("materialized_pdf_annotation_revision", body)
        self.assertNotIn("stale", body)
        after = self.materialization(work_id)
        self.assertEqual(after["materialized_pdf_annotation_revision"], before["materialized_pdf_annotation_revision"])
        self.assertGreater(after["canonical_annotation_set_revision"], after["materialized_pdf_annotation_revision"])
        self.assertTrue(after["stale"])

    def test_a_replayed_token_never_marks_a_later_set_and_a_stale_claim_is_refused(self) -> None:
        work_id = self.pdf_work("Legacy Token Replay")
        token = "replay-token-A"
        gen_a = self.save(work_id, [row("a1", "first")], save_token=token)["canonical_annotation_set_revision"]
        self.ok(self.post_pdf(work_id, "replay token A bytes", save_token=token,
                              materialized_annotation_set_revision=gen_a))
        body_b = self.save(work_id, [row("a1", "second-B")], save_token=token)
        gen_b = body_b["canonical_annotation_set_revision"]
        self.assertGreater(gen_b, gen_a)
        self.assertNotIn("materialized_pdf_annotation_revision", body_b)
        state = self.materialization(work_id)
        self.assertEqual((state["materialized_pdf_annotation_revision"], state["canonical_annotation_set_revision"],
                          state["stale"]), (gen_a, gen_b, True))
        refused = self.refused(self.post_pdf(work_id, "stale claim A", save_token=token,
                                             materialized_annotation_set_revision=gen_a), 409)
        self.assertEqual(refused.get("code"), "ANNOTATION_MATERIALIZATION_STALE")
        state = self.materialization(work_id)
        self.assertEqual((state["materialized_pdf_annotation_revision"], state["canonical_annotation_set_revision"]),
                         (gen_a, gen_b))

    def test_a_claim_overtaken_by_another_save_is_refused_and_the_current_one_wins(self) -> None:
        work_id = self.pdf_work("Legacy Race Claim")
        gen_a = self.save(work_id, [row("r1", "A")])["canonical_annotation_set_revision"]
        gen_b = self.save(work_id, [row("r1", "B")])["canonical_annotation_set_revision"]
        self.assertGreater(gen_b, gen_a)
        self.refused(self.post_pdf(work_id, "bytes for A", save_token="tab-a",
                                   materialized_annotation_set_revision=gen_a), 409)
        state = self.materialization(work_id)
        self.assertTrue(state["stale"])
        self.assertEqual(state["canonical_annotation_set_revision"], gen_b)
        self.assertLess(state["materialized_pdf_annotation_revision"], gen_b)
        body = self.ok(self.post_pdf(work_id, "bytes for B", save_token="tab-b",
                                     materialized_annotation_set_revision=gen_b))
        self.assertEqual(body.get("materialized_pdf_annotation_revision"), gen_b)
        state = self.materialization(work_id)
        self.assertEqual((state["stale"], state["materialized_pdf_annotation_revision"]), (False, gen_b))

    def test_a_claim_on_a_work_without_a_managed_pdf_is_404_and_records_nothing(self) -> None:
        work_id = self.create_work("No Managed PDF")["id"]
        self.assertEqual(self.ok(self.get(f"/api/works/{work_id}"))["file_path"], "")
        before = self.materialization(work_id)
        res = self.post_pdf(work_id, "orphan bytes", save_token="no-pdf-token",
                            materialized_annotation_set_revision=max(1, int(before["canonical_annotation_set_revision"])))
        self.assertEqual(res.status, 404, res.raw)
        self.assertFalse(self.save_confirm(work_id, "no-pdf-token").get("pdf_saved"))
        after = self.materialization(work_id)
        for key in ("materialized_pdf_annotation_revision", "canonical_annotation_set_revision"):
            self.assertEqual(after.get(key), before.get(key))
        self.assertEqual(self.post_pdf("W-missing-pdf-lock-probe", "lock probe", save_token="lock-probe").status, 404)
