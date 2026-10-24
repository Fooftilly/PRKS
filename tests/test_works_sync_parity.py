"""Works semantic operations: PostgreSQL answers as SQLite does (L-W-1 #628).

Each operation is sent to this leg's protocol and to the SQLite protocol of
a reference library holding the same Works, and the two ``(status,
result)`` pairs must be equal after ids and timestamps become labels; then
the Works and their revision state are compared. The ledger rules shared by
every family (replay, ``OP_ID_REUSE``, dependencies, validation before the
ledger) are covered here too, through the Works operations.

``CREATE_WORK`` files the Work in a folder: its parity tests are in
``tests/test_folders_parity.py`` and its PostgreSQL tests in
``tests/pg/test_works_sync.py`` and ``tests/pg/test_folders_sync.py``.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import parametrize_backends
from tests.works_parity_helpers import VIDEO, WorksParityTestCase

_OTHER_VIDEO = "https://www.youtube.com/watch?v=9bZkp7q19f0"
_MISSING = "W-" + "E" * 32


@parametrize_backends
class WorksSyncParityTests(WorksParityTestCase):
    def assert_same_state(self, ids: tuple[str, str], *, work: bool = True) -> None:
        calls = ("get_work_metadata_state", "get_work_notes_state") + (("get_work",) if work else ())
        for call in calls:
            self.same_for(call, ids)

    def test_open_events(self) -> None:
        ids = self.add_both("work", title="Work", source_url=VIDEO)
        result = self.send_both("MARK_WORK_OPENED", ids, {}, occurred_at="2026-09-11T10:00:00.123Z")
        self.assertEqual((result[0], result[1]["changed"]), (200, True))
        self.assertEqual(result[1]["recent_item"]["title"], "Work")
        later = self.send_both("MARK_WORK_OPENED", ids, {}, occurred_at="2026-09-11T12:00:00+02:00")
        self.assertFalse(later[1]["changed"])  # the same instant, in another zone
        self.send_both("MARK_WORK_OPENED", ids, {}, occurred_at="2030-01-01T00:00:00Z")  # clamped
        self.send_both("MARK_WORK_OPENED", (_MISSING, _MISSING), {})
        self.send_both("MARK_WORK_OPENED", ids, {"unexpected": True})
        self.send_both("MARK_WORK_OPENED", ids, {}, base_revision=0)
        self.same("get_recent_browse")
        self.same_for("get_work", ids)

    def test_metadata_fields(self) -> None:
        ids = self.add_both("work", title="Work")
        video = self.add_both("video", title="Video", source_url=VIDEO)
        steps = (
            (ids, {"field": "title", "value": "Renamed"}, 0),
            (ids, {"field": "title", "value": "Renamed"}, 1),       # no change
            (ids, {"field": "title", "value": "Renamed"}, 0),       # stale, convergent
            (ids, {"field": "title", "value": "Other"}, 0),         # stale, conflicting
            (ids, {"field": "title", "value": "Other"}, 5),         # future
            (ids, {"field": "status", "value": "Paused"}, 0),
            (ids, {"field": "status", "value": "Someday"}, 1),      # not a status
            (ids, {"field": "doc_type", "value": "book"}, 0),
            (ids, {"field": "year", "value": "1999"}, 0),
            (ids, {"field": "abstract", "value": "x" * 900}, 0),
            (ids, {"field": "abstract", "value": "y" * 900}, 0),    # a bounded conflict
            (ids, {"field": "nonsense", "value": "x"}, 0),
            (ids, {"field": "title", "value": "x"}, None),          # no base
            (video, {"field": "source_url", "value": "https://e.org"}, 0),
            (video, {"field": "thumb_url", "value": "https://img/t.jpg"}, 0),
            ((_MISSING, _MISSING), {"field": "title", "value": "Ghost"}, 0),
        )
        for target, payload, base in steps:
            with self.subTest(payload=payload, base=base):
                self.send_both("SET_WORK_METADATA_FIELD", target, payload, base)
        self.assert_same_state(ids)
        self.assert_same_state(video)
        self.same("get_works_browse_catalog")

    def test_notes(self) -> None:
        ids = self.add_both("work", title="Work", private_notes="first")
        for operation in ("SET_WORK_RESEARCH_NOTE", "SET_WORK_PRIVATE_NOTE"):
            for text, base in (("one", 0), ("one", 0), ("two", 0), ("two", 1), ("three", 9), ("", 2)):
                with self.subTest(operation=operation, text=text, base=base):
                    self.send_both(operation, ids, {"text": text}, base)
        self.send_both("SET_WORK_PRIVATE_NOTE", (_MISSING, _MISSING), {"text": "x"}, 0)
        self.send_both("SET_WORK_RESEARCH_NOTE", ids, {"text": "bell\x07"}, 3)
        self.send_both("SET_WORK_RESEARCH_NOTE", ids, {"text": "x"}, None)
        # The Research Note is empty again, so the Work JSON needs no wiki links.
        self.assert_same_state(ids)

    def test_sources(self) -> None:
        video = self.add_both("video", title="Video", source_url=VIDEO, thumb_url="https://img/old.jpg")
        plain = self.add_both("plain", title="Plain")
        pdf = self.add_pdf("pdf", "a.pdf", title="Pdf")
        steps = (
            (video, "https://youtu.be/dQw4w9WgXcQ", 0),   # the same video, spelled differently
            (video, _OTHER_VIDEO, 0),
            (video, VIDEO, 0),                             # stale, another video
            (video, _OTHER_VIDEO + "&t=4", 0),             # stale, the same video
            (video, VIDEO, 7),
            (plain, VIDEO, 0),
            (pdf, VIDEO, 0),
            ((_MISSING, _MISSING), VIDEO, 0),
        )
        for target, url, base in steps:
            with self.subTest(url=url, base=base):
                self.send_both("SET_WORK_SOURCE", target, {"source": {"kind": "video", "url": url}}, base)
        self.send_both("SET_WORK_SOURCE", video, {"source": {"kind": "video", "url": "https://e.org"}}, 1)
        self.send_both("SET_WORK_SOURCE", video, {"source": {"kind": "video", "url": VIDEO}}, None)
        for ids in (video, plain, pdf):
            self.same_for("get_work", ids)

    def test_deletes(self) -> None:
        plain = self.add_both("plain", title="Plain")
        pdf = self.add_pdf("pdf", "d.pdf", title="Pdf")
        op_id = str(uuid.uuid4())
        first = self.send_both("DELETE_WORK", pdf, {}, op_id=op_id)
        self.assertEqual(first[1]["file_path"], "/api/pdfs/d.pdf")
        # The replay is what the ledger kept: no path.
        replay = self.send_both("DELETE_WORK", pdf, {}, op_id=op_id)
        self.assertNotIn("file_path", replay[1])
        self.send_both("DELETE_WORK", plain, {})
        self.send_both("DELETE_WORK", plain, {})           # absence converges
        self.send_both("DELETE_WORK", plain, {}, base_revision=0)
        self.send_both("SET_WORK_METADATA_FIELD", plain, {"field": "title", "value": "Late"}, 0)
        self.assertEqual(self.both("get_all_works"), ([], []))

    def test_the_ledger(self) -> None:
        ids = self.add_both("work", title="Work")
        op_id = str(uuid.uuid4())
        payload = {"field": "title", "value": "Once"}
        first = self.send_both("SET_WORK_METADATA_FIELD", ids, payload, 0, op_id=op_id)
        self.assertEqual(first[1]["server_revision"], 1)
        # A retry replays the stored answer, even though the base is now stale.
        self.assertEqual(self.send_both("SET_WORK_METADATA_FIELD", ids, payload, 0, op_id=op_id), first)
        # Another spelling of the same envelope is the same request.
        self.send_both("SET_WORK_METADATA_FIELD", ids, payload, 0, op_id=op_id.upper(),
                       occurred_at="2026-09-11T12:00:00+02:00")
        self.assertEqual(
            self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "title", "value": "Twice"}, 0,
                           op_id=op_id)[1], {"code": "OP_ID_REUSE"})
        # A refused envelope burns nothing: the op_id stays usable.
        fresh = str(uuid.uuid4())
        self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "title"}, 1, op_id=fresh)
        self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "title", "value": "Fixed"}, 1, op_id=fresh)
        self.send_both("NOT_AN_OPERATION", ids, {}, 0)
        self.assert_same_state(ids)

    def test_dependencies(self) -> None:
        ids = self.add_both("work", title="Work")
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        early = self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "year", "value": "2001"}, 0,
                               op_id=second, depends_on=[first])
        self.assertEqual(early, (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "title", "value": "First"}, 0, op_id=first)
        # The early attempt was not ledgered, so its retry now applies.
        self.assertEqual(self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "year", "value": "2001"}, 0,
                                        op_id=second, depends_on=[first])[0], 200)
        # A dependency that was refused is not satisfied.
        refused = str(uuid.uuid4())
        self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "title", "value": "Clash"}, 0, op_id=refused)
        self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "issue", "value": "3"}, 0,
                       depends_on=[refused])
        self.send_both("SET_WORK_METADATA_FIELD", ids, {"field": "issue", "value": "3"}, 0,
                       depends_on=[str(uuid.uuid4()).upper(), str(uuid.uuid4())])
        self.assert_same_state(ids)


if __name__ == "__main__":
    unittest.main()
