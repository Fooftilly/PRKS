"""PDF annotations on PostgreSQL (L-A-1 #635).

:class:`AnswerTests` spells out what the families, the replace, the adoption
and the reads answer. The other tests cover annotation identity per Asset, the schema's keys, the Asset
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
from backend.pdf.pdf_annotations import WorkAnnotationError
from tests import acting_context as facade_context
from tests.pg.test_works_facade import WorksAnswerTestCase
from tests.pg.test_works_sync import SyncTestCase
from tests.narrowing import require
from backend.domain.annotations import annotation_commands
from backend.domain.works import work_commands
from tests import works_fixtures
from tests.acting_context import current_context

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
        return works_fixtures.add_work(self.db, self.ctx, self.storage, "Paper", file_path=self.store_pdf(name, b"%PDF-1.4\n" + name.encode()))

    def asset_of(self, work_id: str) -> str:
        return str(require(work_commands.detail(self.db, self.ctx, work_id))["primary_asset_id"])

    def op(self, operation: str, work_id: str, item: dict[str, Any], base: Optional[int] = None,
           **changes: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"annotation_id": item["id"]}
        if operation != "DELETE_PDF_ANNOTATION":
            payload["annotation"] = item
        return self.envelope(operation, work_id, payload, base, **changes)

    def create(self, work_id: str, item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self.send(self.op("CREATE_PDF_ANNOTATION", work_id, item))

    def listed(self, work_id: str) -> list[dict[str, Any]]:
        return json.loads(annotation_commands.annotations_json(self.db, self.ctx, work_id))

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
        self.assertEqual(json.loads(require(work_commands.detail(self.db, self.ctx, work_id))["annotations"]), self.listed(work_id))
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
        snapshot = require(annotation_commands.snapshot(self.db, self.ctx, work_id))
        self.assertEqual((snapshot["known_absent"], snapshot["canonical_annotation_set_revision"]),
                         ({"ann-1": 2}, 3))
        (asset,) = self.sql("SELECT canonical_annotation_set_revision AS c,"
                            " materialized_pdf_annotation_revision AS m FROM prks.assets")
        self.assertEqual(asset, {"c": 3, "m": 0})
        self.assertEqual(require(annotation_commands.materialization(self.db, self.ctx, work_id))["stale"], True)

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
            other_work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs", file_path=self.store_pdf("o.pdf", b"%PDF-1.4 o"))
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
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Plain")
        self.assertEqual(self.create(work_id, HIGHLIGHT), (404, {"code": "ENTITY_NOT_FOUND", "work_id": work_id,
                                                                 "annotation_id": "ann-1"}))
        self.assertEqual(annotation_commands.save(self.db, self.ctx, work_id, "[]", base_set_revision=0), 0)
        with self.assertRaises(WorkAnnotationError) as caught:
            annotation_commands.save(self.db, self.ctx, work_id, json.dumps([HIGHLIGHT]), base_set_revision=0)
        self.assertEqual((caught.exception.code, caught.exception.http_status), ("work_pdf_not_found", 404))
        with self.assertRaises(WorkAnnotationError):
            annotation_commands.adopt(self.db, self.ctx, work_id, [HIGHLIGHT])
        self.assertEqual(require(annotation_commands.snapshot(self.db, self.ctx, work_id))["items"], [])
        self.assertEqual(require(annotation_commands.materialization(self.db, self.ctx, work_id))["stale"], False)
        self.assertEqual(self.count("annotations"), 0)

    def test_what_postgresql_cannot_store_is_refused(self) -> None:
        work_id = self.add_pdf_work()
        for item in (annotation(pageIndex=2**31), annotation(custom={"k": "a\x00b"}), annotation(id="x" * 201)):
            with self.subTest(item=item["id"][:8]), self.assertRaises(WorkAnnotationError) as caught:
                annotation_commands.save(self.db, self.ctx, work_id, json.dumps([item]), base_set_revision=0)
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
        snapshot = require(annotation_commands.snapshot(self.db, self.ctx, work_id))
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
        self.assertEqual(require(annotation_commands.state(self.db, self.ctx, work_id))["annotations"],
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

    def test_a_number_jsonb_cannot_hold_is_refused_before_the_ledger(self) -> None:
        """Python's json reads NaN and Infinity; jsonb refuses both (tests/test_sync_envelope_properties.py)."""
        work_id = self.add_pdf_work()
        for name, value in (("NaN", float("nan")), ("Infinity", float("-inf"))):
            with self.subTest(number=name):
                self.assertEqual(self.create(work_id, {**HIGHLIGHT, "color": value}),
                                 (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.listed(work_id), [])

    def test_a_failure_after_the_write_rolls_everything_back(self) -> None:
        work_id = self.add_pdf_work()
        data = self.op("CREATE_PDF_ANNOTATION", work_id, HIGHLIGHT)
        with mock.patch.object(store, "bump_set", side_effect=RuntimeError("boom")), \
                self.assertRaises(RuntimeError):
            self.send(data)
        self.assertEqual((self.count("annotations"), self.ledger()), (0, []))
        self.assertEqual(require(annotation_commands.materialization(self.db, self.ctx, work_id))["canonical_annotation_set_revision"], 0)
        # The op_id was not burnt.
        self.assertEqual(self.send(data)[1]["changed"], True)

    def test_a_refused_replace_writes_nothing(self) -> None:
        work_id = self.add_pdf_work()
        annotation_commands.save(self.db, self.ctx, work_id, json.dumps([HIGHLIGHT, annotation(id="ann-2")]), base_set_revision=0)
        annotation_commands.save(self.db, self.ctx, work_id, json.dumps([HIGHLIGHT]), base_set_revision=1)
        with self.assertRaises(WorkAnnotationError) as caught:
            # An update first, then a tombstoned id: the update is rolled back too.
            annotation_commands.save(self.db, self.ctx, work_id, json.dumps([annotation(color="#999999"), annotation(id="ann-2")]), base_set_revision=2)
        self.assertEqual(caught.exception.code, "ANNOTATION_ID_REUSED")
        self.assertEqual([item["color"] for item in self.listed(work_id)], ["#FFCD45"])
        self.assertEqual(require(annotation_commands.snapshot(self.db, self.ctx, work_id))["canonical_annotation_set_revision"], 2)


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
        self.assertIsNone(annotation_commands.snapshot(self.db, self.ctx, work_id))
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
            self.assertIsNone(annotation_commands.snapshot(self.db, self.ctx, work_id))
            self.assertIsNone(annotation_commands.state(self.db, self.ctx, work_id))
            self.assertIsNone(annotation_commands.materialization(self.db, self.ctx, work_id))
            self.assertEqual(annotation_commands.annotations_json(self.db, self.ctx, work_id), "[]")
            with self.assertRaises(WorkAnnotationError) as caught:
                annotation_commands.save(self.db, self.ctx, work_id, "[]", base_set_revision=1)
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
            self.assertEqual(require(annotation_commands.state(self.db, self.ctx, work_id))["annotations"],
                             [{"annotation_id": "ann-1", "revision": 0}])
            for write in (lambda: annotation_commands.save(self.db, self.ctx, work_id, "[]", base_set_revision=1),
                          lambda: annotation_commands.adopt(self.db, self.ctx, work_id, [])):
                with self.assertRaises(AccessRefused) as caught:
                    write()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        with self.assertRaises(AccessRefused):
            self.send(dict(self.op("DELETE_PDF_ANNOTATION", work_id, HIGHLIGHT, 0),
                           device_id=str(reader.device_id)), reader)
        self.assertEqual((len(self.listed(work_id)), len(self.ledger())), (1, 1))


class DecisionTableTests(AnnotationTestCase):
    """Each answer of the three families and the full-list replace (the retired ``tests/test_pdf_annotation_sync.py``)."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.add_pdf_work()
        self.asset = self.asset_of(self.work)

    def put(self, operation: str, item: dict[str, Any], base: Optional[int] = None,
            work_id: Optional[str] = None) -> tuple[int, dict[str, Any]]:
        return self.send(self.op(operation, work_id or self.work, item, base))

    def revision(self, annotation_id: str = "ann-1") -> int:
        """A scope with no row is at revision 0, as on SQLite."""
        return self.scopes(self.asset).get(store.scope_id(self.asset, annotation_id), 0)

    def stored(self, annotation_id: str = "ann-1") -> list[dict[str, Any]]:
        return self.sql("SELECT content, geometry FROM prks.annotations WHERE asset_id = %s AND id = %s",
                        (self.asset, annotation_id))

    def save(self, items: list[dict[str, Any]], base: Optional[int] = None) -> int:
        return annotation_commands.save(self.db, self.ctx, self.work, json.dumps(items), base_set_revision=base)

    def assert_scalar_conflict(self, result: dict[str, Any]) -> None:
        self.assertNotIn("current_annotation", result)
        self.assertNotIn("requested_annotation", result)
        for key, value in result.items():
            if key not in ("work_id", "annotation_id", "code"):
                self.assertNotIsInstance(value, (dict, list), key)

    def test_create(self) -> None:
        status, result = self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT)
        self.assertEqual((status, result["code"], result["changed"], result["server_revision"]),
                         (200, "ACKNOWLEDGED", True, 0))
        self.assertEqual((self.revision(), len(self.stored())), (0, 1))
        self.assertEqual(result["annotation"]["custom"]["prksComment"], "A comment")
        # An identical create converges; a different one is named.
        status, result = self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT)
        self.assertEqual((status, result["changed"], self.revision()), (200, False, 0))
        status, result = self.put("CREATE_PDF_ANNOTATION", annotation(contents="Different"))
        self.assertEqual((status, result["code"]), (409, "ANNOTATION_EXISTS"))
        status, result = self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        status, result = self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT, work_id="W-" + "E" * 32)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    def test_set(self) -> None:
        status, result = self.put("SET_PDF_ANNOTATION", HIGHLIGHT, 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT)
        self.assertEqual(self.put("SET_PDF_ANNOTATION", HIGHLIGHT)[1]["code"], "INVALID_BASE_REVISION")
        self.assertEqual(self.put("SET_PDF_ANNOTATION", HIGHLIGHT, 5)[1]["code"], "FUTURE_REVISION")
        status, result = self.put("SET_PDF_ANNOTATION", HIGHLIGHT, 0)
        self.assertEqual((status, result["changed"], self.revision()), (200, False, 0))
        edited = annotation(contents="Edited")
        status, result = self.put("SET_PDF_ANNOTATION", edited, 0)
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, True, 1))
        # A stale base that agrees converges at the real revision.
        status, result = self.put("SET_PDF_ANNOTATION", edited, 0)
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, False, 1))
        self.put("SET_PDF_ANNOTATION", HIGHLIGHT, 1)
        self.put("SET_PDF_ANNOTATION", annotation(contents="Mid"), 2)
        self.put("SET_PDF_ANNOTATION", HIGHLIGHT, 3)
        status, result = self.put("SET_PDF_ANNOTATION", HIGHLIGHT, 2)
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, False, 4))
        # A stale base that disagrees conflicts with scalars only.
        status, result = self.put("SET_PDF_ANNOTATION", annotation(contents="Client"), 2)
        self.assertEqual((status, result["code"], result["current_revision"]), (409, "REVISION_CONFLICT", 4))
        self.assertIs(result.get("current_state"), True)
        self.assertIs(result.get("requested_state"), True)
        self.assertTrue(isinstance(result.get("current_value"), str) or isinstance(result.get("current_preview"), str))
        self.assert_scalar_conflict(result)

    def test_an_ink_list_change_is_detected_and_stored(self) -> None:
        ink = annotation(id="ann-ink", type=15, contents="", inkList=[[[0, 0], [10, 10], [20, 5]]],
                         segmentRects=[], custom={})
        self.put("CREATE_PDF_ANNOTATION", ink)
        edited = dict(ink, inkList=[[[0, 0], [12, 12], [24, 8]]])
        status, result = self.put("SET_PDF_ANNOTATION", edited, 0)
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, True, 1))
        (row,) = self.stored("ann-ink")
        self.assertEqual(row["geometry"]["inkList"], [[[0, 0], [12, 12], [24, 8]]])

    def test_delete(self) -> None:
        self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT)
        self.put("SET_PDF_ANNOTATION", annotation(contents="Moved"), 0)
        status, result = self.put("DELETE_PDF_ANNOTATION", HIGHLIGHT, 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertIs(result.get("current_state"), True)
        self.assertIs(result.get("requested_state"), False)
        self.assert_scalar_conflict(result)
        status, result = self.put("DELETE_PDF_ANNOTATION", HIGHLIGHT, 1)
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, True, 2))
        self.assertEqual((self.stored(), self.revision()), ([], 2))
        status, result = self.put("DELETE_PDF_ANNOTATION", HIGHLIGHT, 2)
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        # A deleted id is never created again.
        status, result = self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT)
        self.assertEqual((status, result["code"], result["current_revision"]), (409, "ANNOTATION_ID_REUSED", 2))

    def test_an_exact_replay_answers_the_first_result(self) -> None:
        data = self.op("CREATE_PDF_ANNOTATION", self.work, HIGHLIGHT)
        first = self.send(data)
        self.assertTrue(first[1]["changed"])
        self.assertEqual(self.send(data), first)

    def test_the_state_and_the_snapshot_read_one_transaction(self) -> None:
        self.put("CREATE_PDF_ANNOTATION", HIGHLIGHT)
        state = require(annotation_commands.state(self.db, self.ctx, self.work))
        self.assertEqual((state["work_id"], state["annotations"]),
                         (self.work, [{"annotation_id": "ann-1", "revision": 0}]))
        snapshot = require(annotation_commands.snapshot(self.db, self.ctx, self.work))
        self.assertEqual((snapshot["work_id"], [item["id"] for item in snapshot["items"]]), (self.work, ["ann-1"]))
        self.assertEqual(snapshot["annotations"], state["annotations"])
        self.assertEqual(snapshot["known_absent"], state["known_absent"])
        self.assertGreaterEqual(snapshot["canonical_annotation_set_revision"], 1)
        self.assertIn("materialized_pdf_annotation_revision", snapshot)

    def test_the_full_list_replace_advances_the_same_revisions(self) -> None:
        self.save([HIGHLIGHT])
        self.assertEqual(self.revision(), 0)
        self.save([HIGHLIGHT])
        self.assertEqual(self.revision(), 0, "an identical replace advances nothing")
        self.save([annotation(contents="Via HTTP")])
        self.assertEqual(self.revision(), 1)
        self.save([])
        self.assertEqual((self.revision(), self.stored()), (2, []))
        self.assertEqual(require(annotation_commands.state(self.db, self.ctx, self.work))["known_absent"], {"ann-1": 2})
        with self.assertRaises(WorkAnnotationError) as caught:
            self.save([HIGHLIGHT])
        self.assertEqual(caught.exception.code, "ANNOTATION_ID_REUSED")
        self.assertEqual((self.revision(), self.stored()), (2, []))

    def test_a_stale_full_list_replace_is_refused(self) -> None:
        tip0 = self.save([annotation(id="ann-a", contents="A")])
        both = [annotation(id="ann-a", contents="A2"), annotation(id="ann-b", contents="B")]
        tip1 = self.save(both, base=tip0)
        self.assertGreater(tip1, tip0)
        with self.assertRaises(WorkAnnotationError) as caught:
            self.save([annotation(id="ann-a", contents="stale")], base=tip0)
        self.assertEqual(caught.exception.code, "ANNOTATION_SET_STALE")
        rows = self.sql("SELECT id, content FROM prks.annotations WHERE asset_id = %s ORDER BY id", (self.asset,))
        self.assertEqual(rows, [{"id": "ann-a", "content": "A2"}, {"id": "ann-b", "content": "B"}])
        self.assertEqual(self.save(both, base=tip1), tip1)


class ReplaceTests(AnnotationTestCase):
    """The canonical rows behind the full-list replace (the retired ``tests/test_pdf_annotations.py``)."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.add_pdf_work()

    def rows(self, work_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self.sql("SELECT id, type, content, page_index, color, geometry FROM prks.annotations"
                        " WHERE work_id = %s ORDER BY id", (work_id or self.work,))

    def by_id(self, work_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in self.listed(work_id or self.work)}

    def replace(self, items: list[dict[str, Any]], work_id: Optional[str] = None) -> int:
        return annotation_commands.replace_all(self.db, self.ctx, work_id or self.work, items)

    def canonical(self) -> int:
        return require(annotation_commands.materialization(self.db, self.ctx, self.work))["canonical_annotation_set_revision"]

    def test_a_realistic_annotation_is_columns_plus_geometry(self) -> None:
        annotation_commands.save(self.db, self.ctx, self.work, json.dumps([HIGHLIGHT]))
        (row,) = self.rows()
        self.assertEqual({k: row[k] for k in ("id", "type", "content", "page_index", "color")},
                         {"id": "ann-1", "type": "9", "content": "A comment", "page_index": 3, "color": "#FFCD45"})
        for kept in ("segmentRects", "rect", "custom", "opacity", "strokeColor", "blendMode"):
            self.assertEqual(row["geometry"][kept], HIGHLIGHT[kept], kept)
        for banned in ("id", "type", "contents", "pageIndex", "color", "work_id"):
            self.assertNotIn(banned, row["geometry"])
        got = self.by_id()["ann-1"]
        got.pop("updated_at")
        self.assertEqual(got, HIGHLIGHT)
        self.assertNotIn("page", got)
        self.assertEqual(json.loads(require(work_commands.detail(self.db, self.ctx, self.work))["annotations"])[0]["custom"]["prksComment"],
                         "A comment")

    def test_a_replace_updates_deletes_inserts_and_orders_by_page_then_id(self) -> None:
        self.replace([{"id": "A", "contents": "a0", "pageIndex": 0}, {"id": "B", "contents": "b0", "pageIndex": 0},
                      {"id": "C", "contents": "c0", "pageIndex": 1}])
        self.replace([{"id": "b", "contents": "a1", "pageIndex": 1}, {"id": "C", "contents": "c0", "pageIndex": 1},
                      {"id": "D", "contents": "d0", "pageIndex": 0}])
        self.assertEqual([item["id"] for item in self.listed(self.work)], ["D", "C", "b"])
        self.assertEqual([row["id"] for row in self.rows()], ["C", "D", "b"])
        self.replace([])
        self.assertEqual((self.listed(self.work), self.rows()), ([], []))

    def test_an_identical_replace_does_not_advance_the_set(self) -> None:
        items = [annotation(id="a")]
        self.replace(items)
        after_create = self.canonical()
        self.replace(items)
        self.assertEqual(self.canonical(), after_create)

    def test_a_refused_replace_changes_nothing(self) -> None:
        self.replace([{"id": "A", "contents": "keep", "pageIndex": 0}, {"id": "B", "contents": "also", "pageIndex": 0}])
        before = self.rows()
        for items, code in (
                ([{"id": "A", "contents": "changed", "pageIndex": 0}, {"type": "note", "pageIndex": 0}],
                 "malformed_annotation_payload"),
                ([{"id": "A", "contents": "new", "pageIndex": 0}, {"id": "A", "contents": "other", "pageIndex": 1}],
                 "malformed_annotation_payload")):
            with self.subTest(code=code), self.assertRaises(WorkAnnotationError) as caught:
                self.replace(items)
            self.assertEqual((caught.exception.code, caught.exception.http_status), (code, 400))
        for raw in ("{", json.dumps({"id": "A"})):
            with self.subTest(raw=raw), self.assertRaises(WorkAnnotationError) as caught:
                annotation_commands.save(self.db, self.ctx, self.work, raw)
            self.assertEqual(caught.exception.http_status, 400)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.by_id()["A"]["contents"], "keep")

    def test_a_missing_work_is_404_even_for_an_empty_list(self) -> None:
        for call in (lambda: self.replace([], "W-" + "E" * 32),
                     lambda: annotation_commands.save(self.db, self.ctx, "W-" + "E" * 32, json.dumps([{"id": "A", "pageIndex": 0}]))):
            with self.assertRaises(WorkAnnotationError) as caught:
                call()
            self.assertEqual((caught.exception.code, caught.exception.http_status), ("work_not_found", 404))

    def test_stored_geometry_reconstructs_with_the_columns_winning(self) -> None:
        self.replace([annotation(id="old-1")])
        legacy = {"rects": [{"x": 1}], "quadPoints": None, "position": None, "id": "other", "type": "highlight",
                  "contents": "stale", "pageIndex": 99, "color": "#000",
                  "rect": {"origin": {"x": 1, "y": 2}, "size": {"width": 3, "height": 4}}}
        self.sql("UPDATE prks.annotations SET geometry = %s::jsonb, type = 'highlight', content = 'note',"
                 " page_index = 0, color = '#fff' WHERE id = 'old-1'", (json.dumps(legacy),))
        got = self.by_id()["old-1"]
        self.assertEqual((got["id"], got["type"], got["contents"], got["pageIndex"], got["color"]),
                         ("old-1", "highlight", "note", 0, "#fff"))
        self.assertEqual((got["rect"], got["rects"], got["quadPoints"], got["position"]),
                         (legacy["rect"], [{"x": 1}], None, None))


_HI: dict[str, Any] = {
    "id": "ann-hi-1", "type": 9, "pageIndex": 0, "contents": "Highlight comment", "color": "#FFCD45",
    "strokeColor": "#FFCD45", "opacity": 0.5, "blendMode": "Multiply",
    "segmentRects": [{"origin": {"x": 72, "y": 700.25}, "size": {"width": 160, "height": 14}}],
    "rect": {"origin": {"x": 72, "y": 700.25}, "size": {"width": 160, "height": 14}},
    "custom": {"prksComment": "Highlight comment", "tags": ["a", "b"]}, "author": "Reader",
}
_INK: dict[str, Any] = {
    "id": "ann-ink-1", "type": 15, "pageIndex": 2, "contents": "", "color": "#0000FF",
    "inkList": [{"points": [{"x": 1, "y": 2}, {"x": 3.5, "y": 4}]}],
    "rect": {"origin": {"x": 1, "y": 2}, "size": {"width": 2.5, "height": 2}},
}
_NOTE: dict[str, Any] = {"id": "ann-note-1", "type": "note", "page": "1", "comment": "A margin note"}
_NOTE_STORED = {"color": "", "contents": "A margin note", "id": "ann-note-1", "pageIndex": 1, "type": "note",
                "updated_at": "<s>"}
_MISSING_WORK = "W-" + "E" * 32


def _with(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    item = copy.deepcopy(base)
    item.update(changes)
    return item


class AnswerTests(AnnotationTestCase, WorksAnswerTestCase):
    """What the annotation families, the replace, the adoption and the reads answer, spelled out."""

    def setUp(self) -> None:
        super().setUp()
        self.work = self.add_pdf("pdf", "a.pdf", title="Annotated")

    def put(self, operation: str, item: dict[str, Any], base: Optional[int] = None,
            work_id: Optional[str] = None) -> Any:
        status, result = self.send(self.op(operation, work_id or self.work, item, base))
        result = dict(result)
        for key in ("current_value", "requested_value"):
            if key in result:
                result[key] = json.loads(result[key])
        return self.labels.show((status, result))

    def reads(self) -> Any:
        return self.labels.show((annotation_commands.state(self.db, self.ctx, self.work),
                                 annotation_commands.snapshot(self.db, self.ctx, self.work),
                                 annotation_commands.materialization(self.db, self.ctx, self.work)))

    def outcome(self, call: Any) -> Any:
        try:
            return self.labels.show(call())
        except WorkAnnotationError as exc:
            return exc.code, exc.http_status, str(exc)

    def save(self, items: Any, base: Any) -> Any:
        return self.outcome(lambda: annotation_commands.save(self.db, self.ctx, self.work, json.dumps(items), base_set_revision=base))

    def test_create_set_delete_decision_tables(self) -> None:
        ack = {"code": "ACKNOWLEDGED", "work_id": "<W pdf>"}
        self.answer(self.put("CREATE_PDF_ANNOTATION", _HI), (200, {
            **ack, "annotation_id": "ann-hi-1", "annotation": {**_HI}, "canonical_annotation_set_revision": 1,
            "changed": True, "server_revision": 0}))
        self.answer(self.put("CREATE_PDF_ANNOTATION", _HI), (200, {  # a replayed meaning
            **ack, "annotation": {"id": "ann-hi-1", "updated_at": "<s>"}, "canonical_annotation_set_revision": 1,
            "changed": False, "server_revision": 0}))
        current = {"blendMode": "Multiply", "color": "#FFCD45", "content": "Highlight comment",
                   "custom": {"prksComment": "Highlight comment", "tags": ["a", "b"]}, "id": "ann-hi-1",
                   "opacity": 0.5, "page_index": 0, "strokeColor": "#FFCD45", "type": "9"}
        self.answer(self.put("CREATE_PDF_ANNOTATION", _with(_HI, contents="x" * 900)), (409, {
            "annotation_id": "ann-hi-1", "code": "ANNOTATION_EXISTS", "current_revision": 0, "current_state": True,
            "current_value": current, "requested_state": True, "requested_value": {"content": "x" * 900}}))
        self.answer(self.put("CREATE_PDF_ANNOTATION", _INK), (200, {
            "annotation_id": "ann-ink-1", "canonical_annotation_set_revision": 2, "changed": True}))
        self.answer(self.put("CREATE_PDF_ANNOTATION", _NOTE), (200, {
            "annotation": {k: v for k, v in _NOTE_STORED.items() if k != "updated_at"},
            "canonical_annotation_set_revision": 3, "changed": True}))
        changed = _with(_HI, color="#00FF00", contents="Recoloured")
        recoloured = {**current, "color": "#00FF00", "content": "Recoloured"}
        set_answer = {**ack, "annotation_id": "ann-hi-1", "canonical_annotation_set_revision": 4,
                      "server_revision": 1, "annotation": {"color": "#00FF00", "contents": "Recoloured",
                                                           "updated_at": "<s>"}}
        self.answer(self.put("SET_PDF_ANNOTATION", changed, 0), (200, {**set_answer, "changed": True}))
        self.answer(self.put("SET_PDF_ANNOTATION", changed, 1), (200, {**set_answer, "changed": False}))
        self.answer(self.put("SET_PDF_ANNOTATION", changed, 0), (200, {**set_answer, "changed": False}))
        clash = {"annotation_id": "ann-hi-1", "current_revision": 1, "current_state": True,
                 "current_value": recoloured}
        self.answer(self.put("SET_PDF_ANNOTATION", _with(_HI, color="#123456"), 0), (409, {
            **clash, "code": "REVISION_CONFLICT", "requested_state": True,
            "requested_value": {"color": "#123456", "content": "Highlight comment"}}))
        self.answer(self.put("SET_PDF_ANNOTATION", changed, 7), (400, {**clash, "code": "FUTURE_REVISION"}))
        self.answer(self.put("SET_PDF_ANNOTATION", _with(_HI, id="ann-absent"), 0), (404, {
            "annotation_id": "ann-absent", "code": "ENTITY_NOT_FOUND", "work_id": "<W pdf>"}))
        self.answer(self.put("SET_PDF_ANNOTATION", _with(_INK, rect={"origin": {"x": 9, "y": 9}}), 0), (200, {
            "annotation": {"rect": {"origin": {"x": 9, "y": 9}}}, "canonical_annotation_set_revision": 5,
            "changed": True, "server_revision": 1}))
        deleted = {"annotation_id": "ann-hi-1", "requested_state": False, "work_id": "<W pdf>"}
        self.answer(self.put("DELETE_PDF_ANNOTATION", _HI, 0), (409, {**clash, **deleted, "code": "REVISION_CONFLICT"}))
        self.answer(self.put("DELETE_PDF_ANNOTATION", _HI, 5), (400, {**clash, **deleted, "code": "FUTURE_REVISION"}))
        gone = {**ack, "canonical_annotation_set_revision": 6, "present": False}
        self.assertEqual(self.put("DELETE_PDF_ANNOTATION", _HI, 1),
                         (200, {**gone, "annotation_id": "ann-hi-1", "changed": True, "server_revision": 2}))
        self.assertEqual(self.put("DELETE_PDF_ANNOTATION", _HI, 1),  # already absent
                         (200, {**gone, "annotation_id": "ann-hi-1", "changed": False, "server_revision": 2}))
        self.assertEqual(self.put("DELETE_PDF_ANNOTATION", {"id": "ann-never"}, 0),
                         (200, {**gone, "annotation_id": "ann-never", "changed": False, "server_revision": 0}))
        self.assertEqual(self.put("CREATE_PDF_ANNOTATION", _HI), (409, {
            "annotation_id": "ann-hi-1", "code": "ANNOTATION_ID_REUSED", "current_revision": 2, "work_id": "<W pdf>"}))
        ink = {"color": "#0000FF", "contents": "", "id": "ann-ink-1", "pageIndex": 2, "type": 15,
               "inkList": [{"points": [{"x": 1, "y": 2}, {"x": 3.5, "y": 4}]}], "rect": {"origin": {"x": 9, "y": 9}},
               "updated_at": "<s>"}
        listing = [{"annotation_id": "ann-note-1", "revision": 0}, {"annotation_id": "ann-ink-1", "revision": 1}]
        self.answer(self.reads(), (
            {"annotations": listing, "known_absent": {"ann-hi-1": 2}, "work_id": "<W pdf>"},
            {"annotations": listing, "canonical_annotation_set_revision": 6, "items": [_NOTE_STORED, ink],
             "known_absent": {"ann-hi-1": 2}, "materialized_pdf_annotation_revision": 0, "work_id": "<W pdf>"},
            {"canonical_annotation_set_revision": 6, "code": "ANNOTATION_MATERIALIZATION_STALE",
             "materialized_pdf_annotation_revision": 0, "stale": True, "work_id": "<W pdf>"}))
        self.answer(self.labels.show(self.listed(self.work)), [_NOTE_STORED, ink])
        work = require(work_commands.detail(self.db, self.ctx, self.work))
        self.answer(self.labels.show(json.loads(work["annotations"])), [_NOTE_STORED, ink])
        self.assertEqual(work["canonical_annotation_set_revision"], 6)

    def test_invalid_envelopes_and_missing_works(self) -> None:
        def sent(operation: str, payload: dict[str, Any], base: Optional[int] = None,
                 work_id: Optional[str] = None) -> Any:
            return self.labels.show(self.send(self.envelope(operation, work_id or self.work, payload, base)))

        invalid, no_base = (400, {"code": "INVALID_ENVELOPE"}), (400, {"code": "INVALID_BASE_REVISION"})
        for operation, payload, base, expected in (
            ("CREATE_PDF_ANNOTATION", {"annotation_id": "a", "annotation": _HI}, None, invalid),
            ("CREATE_PDF_ANNOTATION", {"annotation_id": "ann-hi-1", "annotation": _HI}, 0, no_base),
            ("CREATE_PDF_ANNOTATION", {"annotation_id": "x" * 201, "annotation": _with(_HI, id="x" * 201)},
             None, invalid),
            ("CREATE_PDF_ANNOTATION", {"annotation_id": " a", "annotation": _with(_HI, id=" a")}, None, invalid),
            ("SET_PDF_ANNOTATION", {"annotation_id": "a", "annotation": _HI}, None, no_base),
            ("DELETE_PDF_ANNOTATION", {"annotation_id": "a", "extra": 1}, 0, invalid),
            ("DELETE_PDF_ANNOTATION", {"annotation_id": "a"}, None, no_base),
        ):
            with self.subTest(operation=operation, payload=str(payload)[:40]):
                self.assertEqual(sent(operation, payload, base), expected)
        self.assertEqual(sent("CREATE_PDF_ANNOTATION", {"annotation_id": "ann-hi-1", "annotation": _HI},
                              work_id=_MISSING_WORK),
                         (404, {"annotation_id": "ann-hi-1", "code": "ENTITY_NOT_FOUND", "work_id": "<unknown id>"}))
        self.assertEqual(sent("DELETE_PDF_ANNOTATION", {"annotation_id": "a"}, 0, work_id=_MISSING_WORK),
                         (404, {"annotation_id": "a", "code": "ENTITY_NOT_FOUND", "work_id": "<unknown id>"}))
        self.assertEqual(self.reads(), (
            {"annotations": [], "known_absent": {}, "work_id": "<W pdf>"},
            {"annotations": [], "canonical_annotation_set_revision": 0, "items": [], "known_absent": {},
             "materialized_pdf_annotation_revision": 0, "work_id": "<W pdf>"},
            {"canonical_annotation_set_revision": 0, "code": None, "materialized_pdf_annotation_revision": 0,
             "stale": False, "work_id": "<W pdf>"}))
        self.assertIsNone(annotation_commands.snapshot(self.db, self.ctx, _MISSING_WORK))
        self.assertIsNone(annotation_commands.materialization(self.db, self.ctx, _MISSING_WORK))
        self.assertEqual(annotation_commands.annotations_json(self.db, self.ctx, _MISSING_WORK), "[]")

    def test_replayed_operation_answers_from_the_ledger(self) -> None:
        first_op = self.op("CREATE_PDF_ANNOTATION", self.work, _INK)
        first = self.send(first_op)
        self.assertEqual(self.put("SET_PDF_ANNOTATION", _with(_INK, color="#FF0000"), 0)[1]["server_revision"], 1)
        # The replay answers what the first delivery answered, whatever happened since.
        self.assertEqual(self.send(first_op), first)
        self.answer(self.labels.show(first), (200, {"annotation": {"color": "#0000FF"},
                                                    "canonical_annotation_set_revision": 1, "changed": True}))

    def test_full_list_replace(self) -> None:
        self.assertEqual(self.save([_HI, _INK], 0), 1)
        self.assertEqual(self.save([_HI, _INK], 1), 1)  # identical: no change
        self.assertEqual(self.save([_HI], 0), ("ANNOTATION_SET_STALE", 409, "Annotation set revision is stale."))
        self.assertEqual(self.save([_HI], "one"), (
            "malformed_annotation_payload", 400, "canonical_annotation_set_revision must be an integer."))
        self.assertEqual(self.save([_HI, _HI], 1), ("malformed_annotation_payload", 400, "Duplicate annotation ID."))
        self.assertEqual(self.save([_with(_HI, color="#111111"), _NOTE], 1), 2)
        self.answer(self.reads()[0], {"annotations": [{"annotation_id": "ann-hi-1", "revision": 1},
                                                      {"annotation_id": "ann-note-1", "revision": 0}],
                                      "known_absent": {"ann-ink-1": 1}})
        self.assertEqual(self.save([_NOTE, _INK], 2), (
            "ANNOTATION_ID_REUSED", 409, "Annotation ID was previously used and cannot be reinserted."))
        self.assertEqual(self.save([], None), 3)
        self.assertEqual(self.outcome(lambda: annotation_commands.save(self.db, self.ctx, self.work, "{}")),
                         ("malformed_annotation_payload", 400, "annotations_json must be a JSON list."))
        self.answer(self.reads(), (
            {"annotations": [], "known_absent": {"ann-hi-1": 2, "ann-ink-1": 1, "ann-note-1": 1}},
            {"canonical_annotation_set_revision": 3, "items": []},
            {"canonical_annotation_set_revision": 3, "stale": True, "code": "ANNOTATION_MATERIALIZATION_STALE"}))
        self.assertEqual(self.outcome(lambda: annotation_commands.replace_all(self.db, self.ctx, _MISSING_WORK, [_HI])),
                         ("work_not_found", 404, "Work not found."))

    def test_byte_only_markup_adoption(self) -> None:
        link = {"id": "link-1", "type": 2, "pageIndex": 0, "uri": "https://example.org",
                "rect": {"origin": {"x": 0, "y": 0}}}
        widget = {"id": "w-1", "type": "widget", "pageIndex": 0, "rect": {"origin": {"x": 0, "y": 0}}}
        viewer = [_HI, link, widget, "junk", _INK, _HI]
        self.assertEqual(self.outcome(lambda: annotation_commands.adopt(self.db, self.ctx, self.work, viewer)), {
            "adopted": ["ann-hi-1", "ann-ink-1"], "adopted_count": 2, "skipped_existing": 0,
            "skipped_known_absent": 0, "skipped_non_user": 3, "work_id": "<W pdf>"})
        self.answer(self.reads(), (
            {"annotations": [{"annotation_id": "ann-hi-1", "revision": 0},
                             {"annotation_id": "ann-ink-1", "revision": 0}], "known_absent": {}},
            {"canonical_annotation_set_revision": 1},
            {"canonical_annotation_set_revision": 1, "materialized_pdf_annotation_revision": 0, "stale": True}))
        # The bytes now lag the metadata: adopting from them again is refused.
        self.assertEqual(self.outcome(lambda: annotation_commands.adopt(self.db, self.ctx, self.work, viewer)), (
            "ANNOTATION_MATERIALIZATION_STALE", 409, "Cannot adopt annotations from known-stale PDF bytes."))
        self.assertEqual(self.outcome(lambda: annotation_commands.adopt(self.db, self.ctx, _MISSING_WORK, viewer)),
                         ("work_not_found", 404, "Work not found."))

    def test_adoption_skips_present_and_known_absent_ids(self) -> None:
        annotation_commands.save(self.db, self.ctx, self.work, json.dumps([_HI, _INK]), base_set_revision=0)
        annotation_commands.save(self.db, self.ctx, self.work, json.dumps([_HI]), base_set_revision=1)
        # PostgreSQL acknowledges only bytes it committed: mark the column directly.
        self.sql("UPDATE prks.assets SET materialized_pdf_annotation_revision = canonical_annotation_set_revision"
                 " WHERE work_id = %s", (self.work,))
        self.assertEqual(self.outcome(lambda: annotation_commands.adopt(self.db, self.ctx, self.work, [_HI, _INK, _NOTE])), {
            "adopted": ["ann-note-1"], "adopted_count": 1, "skipped_existing": 1, "skipped_known_absent": 1,
            "skipped_non_user": 0, "work_id": "<W pdf>"})
        self.answer(self.reads(), (
            {"annotations": [{"annotation_id": "ann-hi-1", "revision": 0},
                             {"annotation_id": "ann-note-1", "revision": 0}], "known_absent": {"ann-ink-1": 1}},
            {"canonical_annotation_set_revision": 3},
            {"canonical_annotation_set_revision": 3, "materialized_pdf_annotation_revision": 2, "stale": True}))

    def test_marks_that_need_no_new_bytes(self) -> None:
        def outcome(call: Any) -> Any:
            try:
                return call()
            except (LookupError, ValueError) as exc:
                return type(exc).__name__, str(exc)

        stale = ("ValueError", "ANNOTATION_MATERIALIZATION_STALE")
        library, work = self.library, self.work
        # The uploaded bytes embed the empty set (revision 0).
        for call, expected in (
                (lambda: works_fixtures.mark_materialized_if_claim_current(library.database, current_context(), library.storage, work, 0), 0),
                (lambda: works_fixtures.mark_materialized(library.database, current_context(), work), 0),
                (lambda: works_fixtures.mark_materialized(library.database, current_context(), work, at_revision=0), 0),
                (lambda: works_fixtures.mark_materialized(library.database, current_context(), work, at_revision=1), stale),
                (lambda: works_fixtures.mark_materialized_if_claim_current(library.database, current_context(), library.storage, work, 1), stale),
                (lambda: works_fixtures.mark_materialized_if_claim_current(library.database, current_context(), library.storage, work, False), stale),
                (lambda: works_fixtures.mark_materialized(library.database, current_context(), _MISSING_WORK), 0),
                (lambda: works_fixtures.mark_materialized_if_claim_current(library.database, current_context(), library.storage, _MISSING_WORK, 0),
                 ("LookupError", "ENTITY_NOT_FOUND"))):
            self.assertEqual(outcome(call), expected)
        self.answer(self.reads()[2], {"canonical_annotation_set_revision": 0, "stale": False})

    def test_materialization_claims(self) -> None:
        annotation_commands.save(self.db, self.ctx, self.work, json.dumps([_HI]), base_set_revision=0)

        def accept(work_id: str, claim: Any) -> Any:
            try:
                return works_fixtures.accept_materialization_claim(self.db, self.ctx, work_id, claim)
            except (LookupError, ValueError) as exc:
                return type(exc).__name__, str(exc)

        for claim in (1, 0, 2, True, 1.0, "1", None):
            with self.subTest(claim=claim):
                self.assertEqual(accept(self.work, claim), 1 if claim == 1 and type(claim) is int
                                 else ("ValueError", "ANNOTATION_MATERIALIZATION_STALE"))
        self.assertEqual(accept(_MISSING_WORK, 0)[0], "LookupError")
        # Accepting a claim marks nothing: only committed bytes do.
        self.answer(self.reads()[2], {"canonical_annotation_set_revision": 1, "materialized_pdf_annotation_revision": 0,
                                      "stale": True})

