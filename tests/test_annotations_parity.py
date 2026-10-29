"""PDF annotations: PostgreSQL answers as SQLite does (L-A-1 #635).

Each step runs on this leg's library and on a reference SQLite library
holding the same Works, and the results must be equal after ids and
timestamps become labels: the ``CREATE``/``SET``/``DELETE_PDF_ANNOTATION``
decision tables, the legacy full-list replace, byte-only markup adoption,
and the reads (list, revision state, snapshot, materialization status).
What only PostgreSQL has (Asset identity, locks, isolation, tombstones on a
Work delete) is in ``tests/pg/test_annotations.py``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Callable, Optional

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.pdf_annotations import WorkAnnotationError
from tests.backend_parity import parametrize_backends
from tests.works_parity_helpers import WorksParityTestCase

_MISSING = "W-" + "E" * 32

HIGHLIGHT: dict[str, Any] = {
    "id": "ann-hi-1",
    "type": 9,
    "pageIndex": 0,
    "contents": "Highlight comment",
    "color": "#FFCD45",
    "strokeColor": "#FFCD45",
    "opacity": 0.5,
    "blendMode": "Multiply",
    "segmentRects": [{"origin": {"x": 72, "y": 700.25}, "size": {"width": 160, "height": 14}}],
    "rect": {"origin": {"x": 72, "y": 700.25}, "size": {"width": 160, "height": 14}},
    "custom": {"prksComment": "Highlight comment", "tags": ["a", "b"]},
    "author": "Reader",
}
INK: dict[str, Any] = {
    "id": "ann-ink-1",
    "type": 15,
    "pageIndex": 2,
    "contents": "",
    "color": "#0000FF",
    "inkList": [{"points": [{"x": 1, "y": 2}, {"x": 3.5, "y": 4}]}],
    "rect": {"origin": {"x": 1, "y": 2}, "size": {"width": 2.5, "height": 2}},
}
NOTE: dict[str, Any] = {"id": "ann-note-1", "type": "note", "page": "1", "comment": "A margin note"}


def values_parsed(outcome: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    """A conflict's ``current_value``/``requested_value`` as JSON values.

    Each is the annotation's JSON text, and ``jsonb`` keeps the geometry's
    values but not its key order (``docs/postgresql-architecture.md`` §6.5).
    """
    status, result = outcome
    parsed = dict(result)
    for key in ("current_value", "requested_value"):
        if key in parsed:
            parsed[key] = json.loads(parsed[key])
    return status, parsed


def annotation(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    item = copy.deepcopy(base)
    item.update(changes)
    return item


@parametrize_backends
class AnnotationParityTests(WorksParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ids = self.add_pdf("pdf", "a.pdf", title="Annotated")

    def send_both(self, operation: str, ids: tuple[str, str], payload: dict[str, Any],
                  base_revision: Optional[int] = None, **options: Any) -> tuple[int, dict[str, Any]]:
        mine = self.send(self.leg, self.leg.envelope(operation, ids[0], payload, base_revision, **options))
        theirs = self.send(self.ref, self.ref.envelope(operation, ids[1], payload, base_revision, **options))
        self.assertEqual(self.leg.normal(values_parsed(mine)), self.ref.normal(values_parsed(theirs)), operation)
        return mine

    def create(self, item: dict[str, Any], ids: Optional[tuple[str, str]] = None) -> dict[str, Any]:
        return self.send_both("CREATE_PDF_ANNOTATION", ids or self.ids,
                              {"annotation_id": item["id"], "annotation": item})[1]

    def set(self, item: dict[str, Any], base: Optional[int]) -> dict[str, Any]:
        return self.send_both("SET_PDF_ANNOTATION", self.ids, {"annotation_id": item["id"], "annotation": item},
                              base)[1]

    def delete(self, annotation_id: str, base: Optional[int]) -> dict[str, Any]:
        return self.send_both("DELETE_PDF_ANNOTATION", self.ids, {"annotation_id": annotation_id}, base)[1]

    def assert_same_reads(self, ids: Optional[tuple[str, str]] = None) -> None:
        ids = ids or self.ids
        for call in ("get_work_annotations_state", "get_work_annotations_snapshot",
                     "get_work_pdf_materialization"):
            self.same_for(call, ids)
        # A JSON string: compared as values (jsonb keeps the value, not the key order).
        mine, theirs = (json.loads(side.db.get_work_annotations(work_id))
                        for side, work_id in ((self.leg, ids[0]), (self.ref, ids[1])))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs))
        # The Work detail carries the same list, as a JSON string too.
        details = []
        for side, work_id in ((self.leg, ids[0]), (self.ref, ids[1])):
            work = side.db.get_work(work_id)
            if work is not None:
                work = dict(work, annotations=json.loads(work["annotations"]))
            details.append(work)
        self.assertEqual(self.leg.normal(details[0]), self.ref.normal(details[1]))

    def same_outcome(self, call: Callable[[Any, str], Any]) -> Any:
        """``call(db, work_id)`` on both sides: equal results, or equal ``WorkAnnotationError``s."""
        outcomes = []
        for side, work_id in ((self.leg, self.ids[0]), (self.ref, self.ids[1])):
            try:
                outcomes.append(side.normal(call(side.db, work_id)))
            except WorkAnnotationError as exc:
                outcomes.append((exc.code, exc.http_status, str(exc)))
        self.assertEqual(outcomes[0], outcomes[1])
        return outcomes[0]

    def test_create_set_delete_decision_tables(self) -> None:
        self.assertEqual(self.create(HIGHLIGHT)["changed"], True)
        self.assertEqual(self.create(HIGHLIGHT)["changed"], False)              # replayed meaning
        self.assertEqual(self.create(annotation(HIGHLIGHT, contents="x" * 900))["code"],
                         "ANNOTATION_EXISTS")                                    # a bounded conflict
        self.create(INK)
        self.create(NOTE)
        changed = annotation(HIGHLIGHT, color="#00FF00", contents="Recoloured")
        self.assertEqual(self.set(changed, 0)["server_revision"], 1)
        self.assertEqual(self.set(changed, 1)["changed"], False)                # the same value
        self.assertEqual(self.set(changed, 0)["changed"], False)                # stale, convergent
        self.assertEqual(self.set(annotation(HIGHLIGHT, color="#123456"), 0)["code"], "REVISION_CONFLICT")
        self.assertEqual(self.set(changed, 7)["code"], "FUTURE_REVISION")
        self.assertEqual(self.set(annotation(HIGHLIGHT, id="ann-absent"), 0)["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.set(annotation(INK, rect={"origin": {"x": 9, "y": 9}}), 0)["changed"], True)
        self.assertEqual(self.delete(HIGHLIGHT["id"], 0)["code"], "REVISION_CONFLICT")
        self.assertEqual(self.delete(HIGHLIGHT["id"], 5)["code"], "FUTURE_REVISION")
        self.assertEqual(self.delete(HIGHLIGHT["id"], 1)["server_revision"], 2)
        self.assertEqual(self.delete(HIGHLIGHT["id"], 1)["changed"], False)     # already absent
        self.assertEqual(self.delete("ann-never", 0)["changed"], False)
        self.assertEqual(self.create(HIGHLIGHT)["code"], "ANNOTATION_ID_REUSED")
        self.assert_same_reads()

    def test_invalid_envelopes_and_missing_works(self) -> None:
        self.send_both("CREATE_PDF_ANNOTATION", self.ids, {"annotation_id": "a", "annotation": HIGHLIGHT})
        self.send_both("CREATE_PDF_ANNOTATION", self.ids,
                       {"annotation_id": HIGHLIGHT["id"], "annotation": HIGHLIGHT}, 0)
        self.send_both("CREATE_PDF_ANNOTATION", self.ids, {"annotation_id": "x" * 201,
                                                           "annotation": annotation(HIGHLIGHT, id="x" * 201)})
        self.send_both("CREATE_PDF_ANNOTATION", self.ids, {"annotation_id": " a",
                                                           "annotation": annotation(HIGHLIGHT, id=" a")})
        self.send_both("SET_PDF_ANNOTATION", self.ids, {"annotation_id": "a", "annotation": HIGHLIGHT}, None)
        self.send_both("DELETE_PDF_ANNOTATION", self.ids, {"annotation_id": "a", "extra": 1}, 0)
        self.send_both("DELETE_PDF_ANNOTATION", self.ids, {"annotation_id": "a"}, None)
        missing = (_MISSING, _MISSING)
        self.create(HIGHLIGHT, missing)
        self.send_both("DELETE_PDF_ANNOTATION", missing, {"annotation_id": "a"}, 0)
        self.assert_same_reads()
        self.assertEqual(self.both("get_work_annotations_snapshot", _MISSING), (None, None))
        self.assertEqual(self.both("get_work_pdf_materialization", _MISSING), (None, None))
        self.assertEqual(self.both("get_work_annotations", _MISSING), ("[]", "[]"))

    def test_replayed_operation_answers_from_the_ledger(self) -> None:
        payload = {"annotation_id": INK["id"], "annotation": INK}
        ops = [side.envelope("CREATE_PDF_ANNOTATION", work_id, payload)
               for side, work_id in ((self.leg, self.ids[0]), (self.ref, self.ids[1]))]
        first = [self.send(self.leg, ops[0]), self.send(self.ref, ops[1])]
        self.send_both("SET_PDF_ANNOTATION", self.ids, {"annotation_id": INK["id"],
                                                        "annotation": annotation(INK, color="#FF0000")}, 0)
        # The replay answers what the first delivery answered, whatever happened since.
        again = [self.send(self.leg, ops[0]), self.send(self.ref, ops[1])]
        self.assertEqual(again, first)
        self.assertEqual(self.leg.normal(again[0]), self.ref.normal(again[1]))
        self.assert_same_reads()

    def test_full_list_replace(self) -> None:
        def save(items: list[dict[str, Any]], base: Any) -> Callable[[Any, str], Any]:
            return lambda db, work_id: db.save_work_annotations(work_id, json.dumps(items), base_set_revision=base)

        self.assertEqual(self.same_outcome(save([HIGHLIGHT, INK], 0)), 1)
        self.assertEqual(self.same_outcome(save([HIGHLIGHT, INK], 1)), 1)          # identical: no change
        self.assertEqual(self.same_outcome(save([HIGHLIGHT], 0))[0], "ANNOTATION_SET_STALE")
        self.assertEqual(self.same_outcome(save([HIGHLIGHT], "one"))[0], "malformed_annotation_payload")
        self.assertEqual(self.same_outcome(save([HIGHLIGHT, HIGHLIGHT], 1))[0], "malformed_annotation_payload")
        self.assertEqual(self.same_outcome(save([annotation(HIGHLIGHT, color="#111111"), NOTE], 1)), 2)
        self.assert_same_reads()
        self.assertEqual(self.same_outcome(save([NOTE, INK], 2))[0], "ANNOTATION_ID_REUSED")
        self.assertEqual(self.same_outcome(save([], None)), 3)
        self.assertEqual(self.same_outcome(lambda db, work_id: db.save_work_annotations(work_id, "{}"))[0],
                         "malformed_annotation_payload")
        self.assert_same_reads()
        missing = self.same_outcome(lambda db, work_id: db.sync_work_annotations(_MISSING, [HIGHLIGHT]))
        self.assertEqual(missing[0], "work_not_found")

    def test_byte_only_markup_adoption(self) -> None:
        link = {"id": "link-1", "type": 2, "pageIndex": 0, "uri": "https://example.org",
                "rect": {"origin": {"x": 0, "y": 0}}}
        widget = {"id": "w-1", "type": "widget", "pageIndex": 0, "rect": {"origin": {"x": 0, "y": 0}}}
        viewer = [HIGHLIGHT, link, widget, "junk", INK, HIGHLIGHT]
        adopted = self.same_outcome(lambda db, work_id: db.adopt_byte_only_user_markup(work_id, viewer))
        self.assertEqual((adopted["adopted"], adopted["skipped_non_user"]), (["ann-hi-1", "ann-ink-1"], 3))
        self.assert_same_reads()
        # The bytes now lag the metadata: adopting from them again is refused.
        stale = self.same_outcome(lambda db, work_id: db.adopt_byte_only_user_markup(work_id, viewer))
        self.assertEqual(stale[0], "ANNOTATION_MATERIALIZATION_STALE")
        missing = self.same_outcome(lambda db, work_id: db.adopt_byte_only_user_markup(_MISSING, viewer))
        self.assertEqual(missing[0], "work_not_found")

    def test_adoption_skips_present_and_known_absent_ids(self) -> None:
        for side, work_id in ((self.leg, self.ids[0]), (self.ref, self.ids[1])):
            side.db.save_work_annotations(work_id, json.dumps([HIGHLIGHT, INK]), base_set_revision=0)
            side.db.save_work_annotations(work_id, json.dumps([HIGHLIGHT]), base_set_revision=1)
        # Mark the bytes current through each backend's own table so adoption may run.
        self.leg_mark_materialized()
        self.ref.db.mark_work_pdf_materialized(self.ids[1])
        adopted = self.same_outcome(
            lambda db, work_id: db.adopt_byte_only_user_markup(work_id, [HIGHLIGHT, INK, NOTE]))
        self.assertEqual((adopted["adopted"], adopted["skipped_existing"], adopted["skipped_known_absent"]),
                         (["ann-note-1"], 1, 1))
        self.assert_same_reads()

    def leg_mark_materialized(self) -> None:
        if self.parity_backend == "sqlite":
            self.leg.db.mark_work_pdf_materialized(self.ids[0])
            return
        # Materialization is not ported yet (it comes with the PDF rewrite).
        self.leg.db.parity_library.database.command(lambda tx: tx.execute(
            "UPDATE prks.assets SET materialized_pdf_annotation_revision = canonical_annotation_set_revision"
            " WHERE work_id = %s", (self.ids[0],)))
