"""PDF annotations on PostgreSQL: what parity cannot show (L-A-1 #635).

``tests/test_annotations_parity.py`` proves the families, the replace, the
adoption and the reads answer as on SQLite. These tests cover what only
PostgreSQL has: annotation identity per Asset, the schema's keys, the Asset
row lock under concurrent and interleaved writers, replayed operations,
Library isolation, reader and writer permissions, a Work delete with
annotations present, rollback, and what PostgreSQL cannot store.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional
from unittest import mock

import psycopg

from backend.domain.annotations import operations, store
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.works import commands
from backend.pdf_annotations import WorkAnnotationError
from backend.pg_facade import context as facade_context
from tests.pg.test_works_sync import SyncTestCase

HIGHLIGHT: dict[str, Any] = {
    "id": "ann-1",
    "type": 9,
    "pageIndex": 3,
    "contents": "A comment",
    "color": "#FFCD45",
    "strokeColor": "#FFCD45",
    "opacity": 0.5,
    "blendMode": "Multiply",
    "segmentRects": [{"origin": {"x": 72, "y": 700.25}, "size": {"width": 160, "height": 14}}],
    "rect": {"origin": {"x": 72, "y": 700.25}, "size": {"width": 160, "height": 14}},
    "custom": {"prksComment": "A comment", "nested": {"list": [1, 2.5, None, True, "x"]}},
}


def annotation(**changes: Any) -> dict[str, Any]:
    item = copy.deepcopy(HIGHLIGHT)
    item.update(changes)
    return item


class AnnotationTestCase(SyncTestCase):
    maxDiff = None

    def add_pdf_work(self, name: str = "p.pdf") -> str:
        return self.library.add_work("Paper", file_path=self.store_pdf(name, b"%PDF-1.4\n" + name.encode()))

    def asset_of(self, work_id: str) -> str:
        return str(self.library.get_work(work_id)["primary_asset_id"])

    def op(self, operation: str, work_id: str, item: dict[str, Any], base: Optional[int] = None,
           **changes: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"annotation_id": item["id"]}
        if operation != "DELETE_PDF_ANNOTATION":
            payload["annotation"] = item
        return self.envelope(operation, work_id, payload, base, **changes)

    def create(self, work_id: str, item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self.send(self.op("CREATE_PDF_ANNOTATION", work_id, item))

    def listed(self, work_id: str) -> list[dict[str, Any]]:
        return json.loads(self.library.get_work_annotations(work_id))

    def scopes(self, asset_id: str) -> dict[str, int]:
        rows = self.sql("SELECT scope_id, revision FROM prks.entity_revisions"
                        " WHERE scope_type = 'asset-annotation' AND scope_id LIKE %s", ('["' + asset_id + '"%',))
        return {row["scope_id"]: row["revision"] for row in rows}


class PersistenceTests(AnnotationTestCase):
    def test_geometry_appearance_and_comments_round_trip(self) -> None:
        work_id = self.add_pdf_work()
        status, result = self.create(work_id, HIGHLIGHT)
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, True, 0))
        (listed,) = self.listed(work_id)
        updated_at = listed.pop("updated_at")
        self.assertRegex(updated_at, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(listed, HIGHLIGHT)
        self.assertEqual(result["annotation"], HIGHLIGHT)
        # The Work detail reads the same rows in its snapshot.
        self.assertEqual(json.loads(self.library.get_work(work_id)["annotations"]), self.listed(work_id))
        (row,) = self.sql("SELECT library_id, asset_id, work_id, type, content, page_index, color,"
                          " jsonb_typeof(geometry) AS kind, geometry FROM prks.annotations")
        self.assertEqual({k: row[k] for k in ("library_id", "asset_id", "work_id", "type", "content",
                                               "page_index", "color", "kind")},
                         {"library_id": self.actor.library_id, "asset_id": self.asset_of(work_id),
                          "work_id": work_id, "type": "9", "content": "A comment", "page_index": 3,
                          "color": "#FFCD45", "kind": "object"})
        # The first-class fields are columns, never repeated in the geometry.
        self.assertNotIn("contents", row["geometry"])
        self.assertEqual(row["geometry"]["custom"], HIGHLIGHT["custom"])

    def test_create_update_delete_and_set_revisions(self) -> None:
        work_id = self.add_pdf_work()
        asset_id = self.asset_of(work_id)
        self.create(work_id, HIGHLIGHT)
        edited = annotation(color="#000000", contents="Edited", rect={"origin": {"x": 1, "y": 2}})
        status, result = self.send(self.op("SET_PDF_ANNOTATION", work_id, edited, 0))
        self.assertEqual((status, result["server_revision"], result["canonical_annotation_set_revision"]),
                         (200, 1, 2))
        self.assertEqual(result["annotation"]["contents"], "Edited")
        status, result = self.send(self.op("DELETE_PDF_ANNOTATION", work_id, edited, 1))
        self.assertEqual((status, result["server_revision"], result["present"]), (200, 2, False))
        self.assertEqual(self.listed(work_id), [])
        self.assertEqual(self.scopes(asset_id), {store.scope_id(asset_id, "ann-1"): 2})
        snapshot = self.library.get_work_annotations_snapshot(work_id)
        self.assertEqual((snapshot["known_absent"], snapshot["canonical_annotation_set_revision"]),
                         ({"ann-1": 2}, 3))
        (asset,) = self.sql("SELECT canonical_annotation_set_revision AS c,"
                            " materialized_pdf_annotation_revision AS m FROM prks.assets")
        self.assertEqual(asset, {"c": 3, "m": 0})
        self.assertEqual(self.library.get_work_pdf_materialization(work_id)["stale"], True)

    def test_one_viewer_id_on_two_assets_is_two_annotations(self) -> None:
        first, second = self.add_pdf_work("a.pdf"), self.add_pdf_work("b.pdf")
        self.assertEqual(self.create(first, HIGHLIGHT)[1]["changed"], True)
        # SQLite answers ANNOTATION_ID_CONFLICT here; the id belongs to each file.
        self.assertEqual(self.create(second, annotation(color="#111111"))[1]["changed"], True)
        self.send(self.op("SET_PDF_ANNOTATION", second, annotation(color="#222222"), 0))
        self.send(self.op("DELETE_PDF_ANNOTATION", first, HIGHLIGHT, 0))
        self.assertEqual(self.listed(first), [])
        self.assertEqual([item["color"] for item in self.listed(second)], ["#222222"])
        self.assertEqual(self.scopes(self.asset_of(first)), {store.scope_id(self.asset_of(first), "ann-1"): 1})
        self.assertEqual(self.scopes(self.asset_of(second)), {store.scope_id(self.asset_of(second), "ann-1"): 1})
        # A tombstone on one Asset does not refuse the id on the other.
        self.assertEqual(self.send(self.op("SET_PDF_ANNOTATION", second, annotation(color="#333333"), 1))[1]["code"],
                         "ACKNOWLEDGED")

    def test_the_schema_keys(self) -> None:
        work_id = self.add_pdf_work()
        self.create(work_id, HIGHLIGHT)
        asset_id = self.asset_of(work_id)
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            other_work = self.library.add_work("Theirs", file_path=self.store_pdf("o.pdf", b"%PDF-1.4 o"))
        insert = "INSERT INTO prks.annotations (library_id, asset_id, id, work_id) VALUES (%s, %s, %s, %s)"
        refusals = (
            # The same identity on one Asset.
            ((self.actor.library_id, asset_id, "ann-1", work_id), psycopg.errors.UniqueViolation),
            # An Asset of another Library, or of another Work.
            ((other.library_id, asset_id, "x", work_id), psycopg.errors.ForeignKeyViolation),
            ((self.actor.library_id, asset_id, "x", other_work), psycopg.errors.ForeignKeyViolation),
            ((self.actor.library_id, asset_id, "x" * 201, work_id), psycopg.errors.CheckViolation),
        )
        with psycopg.connect(self.db_conninfo, autocommit=True) as conn:
            for params, error in refusals:
                with self.subTest(params=params), self.assertRaises(error):
                    conn.execute(insert, params)
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE prks.annotations SET geometry = '[]'")
            # An annotation owns a revision scope: its Asset cannot cascade it away.
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute("DELETE FROM prks.assets WHERE id = %s", (asset_id,))
        self.assertEqual(self.count("annotations"), 1)

    def test_a_work_without_a_managed_pdf(self) -> None:
        work_id = self.library.add_work("Plain")
        self.assertEqual(self.create(work_id, HIGHLIGHT), (404, {"code": "ENTITY_NOT_FOUND", "work_id": work_id,
                                                                 "annotation_id": "ann-1"}))
        self.assertEqual(self.library.save_work_annotations(work_id, "[]", base_set_revision=0), 0)
        with self.assertRaises(WorkAnnotationError) as caught:
            self.library.save_work_annotations(work_id, json.dumps([HIGHLIGHT]), base_set_revision=0)
        self.assertEqual((caught.exception.code, caught.exception.http_status), ("work_pdf_not_found", 404))
        with self.assertRaises(WorkAnnotationError):
            self.library.adopt_byte_only_user_markup(work_id, [HIGHLIGHT])
        self.assertEqual(self.library.get_work_annotations_snapshot(work_id)["items"], [])
        self.assertEqual(self.library.get_work_pdf_materialization(work_id)["stale"], False)
        self.assertEqual(self.count("annotations"), 0)

    def test_what_postgresql_cannot_store_is_refused(self) -> None:
        work_id = self.add_pdf_work()
        for item in (annotation(pageIndex=2**31), annotation(custom={"k": "a\x00b"}), annotation(id="x" * 201)):
            with self.subTest(item=item["id"][:8]), self.assertRaises(WorkAnnotationError) as caught:
                self.library.save_work_annotations(work_id, json.dumps([item]), base_set_revision=0)
            self.assertEqual(caught.exception.code, "malformed_annotation_payload")
        self.assertEqual(self.create(work_id, annotation(pageIndex=2**31)), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual((self.count("annotations"), self.ledger()), (0, []))


class SyncTests(AnnotationTestCase):
    def test_one_op_id_sent_concurrently_applies_once(self) -> None:
        work_id = self.add_pdf_work()
        data = self.op("CREATE_PDF_ANNOTATION", work_id, HIGHLIGHT)
        results = self.concurrently([lambda: self.send(data) for _ in range(6)])
        self.assertTrue(all(result == results[0] for result in results), results)
        self.assertEqual((results[0][1]["changed"], len(self.ledger()), self.count("annotations")), (True, 1, 1))

    def test_duplicate_creates_converge(self) -> None:
        work_id = self.add_pdf_work()
        results = self.concurrently([lambda: self.create(work_id, HIGHLIGHT) for _ in range(5)])
        self.assertEqual(sorted(result[1]["changed"] for result in results), [False] * 4 + [True])
        self.assertTrue(all(result[1]["code"] == "ACKNOWLEDGED" for result in results))
        snapshot = self.library.get_work_annotations_snapshot(work_id)
        self.assertEqual((snapshot["canonical_annotation_set_revision"], len(snapshot["items"])), (1, 1))

    def test_writers_on_one_base(self) -> None:
        work_id = self.add_pdf_work()
        self.create(work_id, HIGHLIGHT)
        results = self.concurrently([
            (lambda color=color: self.send(self.op("SET_PDF_ANNOTATION", work_id, annotation(color=color), 0)))
            for color in ("#000001", "#000002", "#000003", "#000004")
        ])
        self.assertEqual(sorted(result[1]["code"] for result in results),
                         ["ACKNOWLEDGED"] + ["REVISION_CONFLICT"] * 3)
        self.assertEqual(self.library.get_work_annotations_state(work_id)["annotations"],
                         [{"annotation_id": "ann-1", "revision": 1}])

    def test_an_edit_held_then_a_stale_delete(self) -> None:
        # The delete waits on the Asset row, then decides on the committed edit.
        work_id = self.add_pdf_work()
        self.create(work_id, HIGHLIGHT)
        edited, deleted = self.hold_work_then(
            lambda scope: operations.apply_set(scope, work_id, {"annotation_id": "ann-1",
                                                                "annotation": annotation(color="#0F0F0F")}, 0),
            lambda scope: operations.apply_delete(scope, work_id, "ann-1", 0))
        self.assertEqual((edited[1]["server_revision"], deleted[0], deleted[1]["code"]),
                         (1, 409, "REVISION_CONFLICT"))
        self.assertEqual([item["color"] for item in self.listed(work_id)], ["#0F0F0F"])

    def test_a_delete_held_then_an_edit(self) -> None:
        work_id = self.add_pdf_work()
        self.create(work_id, HIGHLIGHT)
        deleted, edited = self.hold_work_then(
            lambda scope: operations.apply_delete(scope, work_id, "ann-1", 0),
            lambda scope: operations.apply_set(scope, work_id, {"annotation_id": "ann-1",
                                                                "annotation": annotation(color="#0F0F0F")}, 0))
        self.assertEqual((deleted[1]["server_revision"], edited[0], edited[1]["code"]), (1, 404, "ENTITY_NOT_FOUND"))
        self.assertEqual(self.create(work_id, HIGHLIGHT)[1]["code"], "ANNOTATION_ID_REUSED")

    def test_a_replace_held_then_a_create(self) -> None:
        work_id = self.add_pdf_work()
        revision, created = self.hold_work_then(
            lambda scope: operations.replace_all(scope, work_id, [HIGHLIGHT], 0),
            lambda scope: operations.apply_create(scope, work_id, {"annotation_id": "ann-1",
                                                                   "annotation": annotation(color="#123123")}))
        self.assertEqual((revision, created[0], created[1]["code"]), (1, 409, "ANNOTATION_EXISTS"))

    def test_a_work_delete_and_an_annotation_edit(self) -> None:
        for _ in range(4):
            work_id = self.add_pdf_work("w%d.pdf" % self.count("works"))
            asset_id = self.asset_of(work_id)
            self.create(work_id, HIGHLIGHT)
            results = self.concurrently([
                lambda work_id=work_id: self.send(self.envelope("DELETE_WORK", work_id, {})),
                lambda work_id=work_id: self.send(self.op("SET_PDF_ANNOTATION", work_id,
                                                          annotation(color="#ABCDEF"), 0)),
            ])
            self.assertEqual(results[0][1]["changed"], True)
            edit = results[1][1]["code"]
            self.assertIn(edit, ("ACKNOWLEDGED", "ENTITY_NOT_FOUND"))
            # Whichever came first, no annotation is left and its scope is a tombstone.
            self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.annotations WHERE work_id = %s",
                                      (work_id,))[0]["n"], 0)
            self.assertEqual(self.scopes(asset_id),
                             {store.scope_id(asset_id, "ann-1"): 2 if edit == "ACKNOWLEDGED" else 1})

    def test_a_failure_after_the_write_rolls_everything_back(self) -> None:
        work_id = self.add_pdf_work()
        data = self.op("CREATE_PDF_ANNOTATION", work_id, HIGHLIGHT)
        with mock.patch.object(store, "bump_set", side_effect=RuntimeError("boom")), \
                self.assertRaises(RuntimeError):
            self.send(data)
        self.assertEqual((self.count("annotations"), self.ledger()), (0, []))
        self.assertEqual(self.library.get_work_pdf_materialization(work_id)["canonical_annotation_set_revision"], 0)
        # The op_id was not burnt.
        self.assertEqual(self.send(data)[1]["changed"], True)

    def test_a_refused_replace_writes_nothing(self) -> None:
        work_id = self.add_pdf_work()
        self.library.save_work_annotations(work_id, json.dumps([HIGHLIGHT, annotation(id="ann-2")]),
                                           base_set_revision=0)
        self.library.save_work_annotations(work_id, json.dumps([HIGHLIGHT]), base_set_revision=1)
        with self.assertRaises(WorkAnnotationError) as caught:
            # An update first, then a tombstoned id: the update is rolled back too.
            self.library.save_work_annotations(
                work_id, json.dumps([annotation(color="#999999"), annotation(id="ann-2")]), base_set_revision=2)
        self.assertEqual(caught.exception.code, "ANNOTATION_ID_REUSED")
        self.assertEqual([item["color"] for item in self.listed(work_id)], ["#FFCD45"])
        self.assertEqual(self.library.get_work_annotations_snapshot(work_id)["canonical_annotation_set_revision"], 2)


class WorkDeleteTests(AnnotationTestCase):
    def test_a_work_delete_removes_annotations_and_tombstones_their_scopes(self) -> None:
        work_id = self.add_pdf_work()
        asset_id = self.asset_of(work_id)
        kept = self.add_pdf_work("kept.pdf")
        for item in (HIGHLIGHT, annotation(id="ann-edited"), annotation(id="ann-gone")):
            self.create(work_id, item)
        self.create(kept, HIGHLIGHT)
        self.send(self.op("SET_PDF_ANNOTATION", work_id, annotation(id="ann-edited", color="#1"), 0))
        self.send(self.op("DELETE_PDF_ANNOTATION", work_id, annotation(id="ann-gone"), 0))
        self.assertEqual(self.send(self.envelope("DELETE_WORK", work_id, {}))[1]["changed"], True)
        self.assertEqual(self.sql("SELECT work_id FROM prks.annotations"), [{"work_id": kept}])
        # Every scope one revision on, the never-edited annotation's included.
        self.assertEqual(self.scopes(asset_id), {store.scope_id(asset_id, "ann-1"): 1,
                                                 store.scope_id(asset_id, "ann-edited"): 2,
                                                 store.scope_id(asset_id, "ann-gone"): 2})
        self.assertEqual(self.create(work_id, HIGHLIGHT)[1]["code"], "ENTITY_NOT_FOUND")
        self.assertIsNone(self.library.get_work_annotations_snapshot(work_id))
        self.assertEqual(self.listed(kept)[0]["id"], "ann-1")

    def test_the_command_deletes_annotations_before_assets(self) -> None:
        work_id = self.add_pdf_work()
        self.create(work_id, HIGHLIGHT)
        deleted = self.run_command(lambda scope: commands.delete_work(scope, work_id))
        self.assertIsNotNone(deleted)
        self.assertEqual((self.count("annotations"), self.count("assets")), (0, 0))


class AccessTests(AnnotationTestCase):
    def test_another_library_neither_sees_nor_reaches_annotations(self) -> None:
        work_id = self.add_pdf_work()
        self.create(work_id, HIGHLIGHT)
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertIsNone(self.library.get_work_annotations_snapshot(work_id))
            self.assertIsNone(self.library.get_work_annotations_state(work_id))
            self.assertIsNone(self.library.get_work_pdf_materialization(work_id))
            self.assertEqual(self.library.get_work_annotations(work_id), "[]")
            with self.assertRaises(WorkAnnotationError) as caught:
                self.library.save_work_annotations(work_id, "[]", base_set_revision=1)
            self.assertEqual(caught.exception.code, "work_not_found")
        status, result = self.send(dict(self.op("DELETE_PDF_ANNOTATION", work_id, HIGHLIGHT, 0),
                                        device_id=str(other.context.device_id)), other.context)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(len(self.listed(work_id)), 1)

    def test_a_reader_reads_but_never_writes(self) -> None:
        work_id = self.add_pdf_work()
        self.create(work_id, HIGHLIGHT)
        reader = self.reader_context()
        with facade_context.acting_as(reader):
            self.assertEqual(len(self.listed(work_id)), 1)
            self.assertEqual(self.library.get_work_annotations_state(work_id)["annotations"],
                             [{"annotation_id": "ann-1", "revision": 0}])
            for write in (lambda: self.library.save_work_annotations(work_id, "[]", base_set_revision=1),
                          lambda: self.library.adopt_byte_only_user_markup(work_id, [])):
                with self.assertRaises(AccessRefused) as caught:
                    write()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        with self.assertRaises(AccessRefused):
            self.send(dict(self.op("DELETE_PDF_ANNOTATION", work_id, HIGHLIGHT, 0),
                           device_id=str(reader.device_id)), reader)
        self.assertEqual((len(self.listed(work_id)), len(self.ledger())), (1, 1))
