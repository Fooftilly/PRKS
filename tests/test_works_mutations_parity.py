"""Work open, metadata, notes and delete: PostgreSQL matches SQLite (L-W-1 #628).

Same fixture as ``tests/test_works_core_parity.py``: every call runs on this
leg's library object and on a reference SQLite ``PRKSDatabase``, and the
results, the Work JSON and the synchronization state are compared after ids
and timestamps are replaced by labels.
"""

from __future__ import annotations

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import parametrize_backends
from tests.works_parity_helpers import VIDEO, WorksParityTestCase

_MISSING = "W-" + "E" * 32


@parametrize_backends
class WorksMutationParityTests(WorksParityTestCase):
    def assert_same_state(self, ids: tuple[str, str]) -> None:
        for call in ("get_work", "get_work_metadata_state", "get_work_notes_state"):
            self.same_for(call, ids)

    # -- open -------------------------------------------------------------------

    def test_opening_orders_recent(self) -> None:
        first = self.add_both("first", title="First")
        second = self.add_both("second", title="Second", source_url=VIDEO)
        self.add_both("never", title="Never")
        for ids in (first, second, first):
            self.assertEqual((self.leg.db.mark_work_opened(ids[0]), self.ref.db.mark_work_opened(ids[1])),
                             (True, True))
        rows = self.same("get_recent_browse")
        self.assertEqual([row["title"] for row in rows], ["First", "Second"])
        self.same("get_recent_works", 1)
        self.same("etag_recent_works")
        self.assertRegex(self.same_for("get_work", first)["last_opened_at"],
                         r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}$")

    def test_opening_a_missing_work(self) -> None:
        self.assertEqual(self.both("mark_work_opened", _MISSING), (False, False))
        for db in (self.leg.db, self.ref.db):
            with self.assertRaises(ValueError):
                db.mark_work_opened("  ")

    # -- metadata ---------------------------------------------------------------

    def test_a_metadata_patch_and_its_revisions(self) -> None:
        ids = self.add_both("work", title="Work")
        patch = {"title": "Renamed", "status": "Completed", "abstract": "New abstract",
                 "author_text": "Ann", "year": "1999", "published_date": "1999-02",
                 "publisher": "Press", "location": "Rome", "edition": "3", "journal": "J",
                 "volume": "4", "issue": "5", "pages": "6-7", "isbn": "isbn", "doi": "10.9/Y",
                 "doc_type": "Book", "urldate": "2024-01-02", "source_url": "https://e.org/x",
                 "hide_pdf_link_annotations": "yes", "not_a_field": "ignored"}
        self.assertEqual(self.both("update_work_metadata", ids[0], patch)[0], ())
        self.ref.db.update_work_metadata(ids[1], patch)
        self.assert_same_state(ids)
        # The same values again change nothing and advance nothing.
        self.leg.db.update_work_metadata(ids[0], patch)
        self.ref.db.update_work_metadata(ids[1], patch)
        self.assert_same_state(ids)
        state = self.leg.db.get_work_metadata_state(ids[0])
        self.assertEqual(state["fields"]["title"], {"revision": 1})
        self.assertEqual(state["fields"]["doc_type"], {"revision": 1, "value": "book"})
        self.leg.db.update_work_metadata(ids[0], {"doc_type": "nonsense", "year": None})
        self.ref.db.update_work_metadata(ids[1], {"doc_type": "nonsense", "year": None})
        self.assert_same_state(ids)
        for call in ("get_all_works", "get_works_browse_catalog", "get_recently_added_browse"):
            self.same(call)

    def test_pdf_fields(self) -> None:
        ids = self.add_pdf("pdf", "a.pdf", title="Pdf", source_url="https://e.org/a")
        patch = {"thumb_page": 4, "source_mime": "application/pdf", "source_url": "https://e.org/b"}
        self.leg.db.update_work_metadata(ids[0], patch)
        self.ref.db.update_work_metadata(ids[1], patch)
        self.assert_same_state(ids)
        self.same_for("get_primary_thumbnail_fields", ids)
        self.leg.db.update_work_metadata(ids[0], {"thumb_page": "4"})
        self.ref.db.update_work_metadata(ids[1], {"thumb_page": "4"})
        self.assertEqual(self.leg.db.get_work_metadata_state(ids[0])["fields"]["thumb_page"],
                         {"revision": 1, "value": "4"})
        self.leg.db.update_work_metadata(ids[0], {"thumb_page": None})
        self.ref.db.update_work_metadata(ids[1], {"thumb_page": None})
        self.assert_same_state(ids)

    def test_refused_patches_change_nothing(self) -> None:
        ids = self.add_both("work", title="Work")
        video = self.add_both("video", title="Video", source_url=VIDEO)
        for target, patch in (
            (ids, {"status": "Someday"}),
            (ids, {"thumb_page": 0}),
            (ids, {"thumb_page": "abc"}),
            (ids, {"year": "y" * 51}),
            (ids, {"title": "x" * (64 * 1024 + 1)}),
            (ids, {"provider_id": "abc"}),
            (ids, {"source_kind": "video", "title": "Also refused"}),
            (ids, {"thumb_url": "https://t"}),
            (video, {"source_url": "https://e.org/other"}),
        ):
            with self.subTest(patch=sorted(patch)):
                for db, work_id in ((self.leg.db, target[0]), (self.ref.db, target[1])):
                    with self.assertRaises(ValueError):
                        db.update_work_metadata(work_id, patch)
        self.assert_same_state(ids)
        self.assert_same_state(video)

    def test_a_patch_of_nothing_or_of_a_missing_work(self) -> None:
        ids = self.add_both("work", title="Work")
        self.assertEqual(self.both("update_work_metadata", ids[0], {}), ((), ()))
        self.assertEqual(self.both("update_work_metadata", _MISSING, {"title": "Ghost"}), ((), ()))
        self.assertEqual(self.both("get_work_metadata_state", _MISSING), (None, None))
        self.assertEqual(self.both("get_work_notes_state", _MISSING), (None, None))

    def test_a_private_note_through_patch(self) -> None:
        ids = self.add_both("work", title="Work", private_notes="first")
        for value in ("second", "second", "  spaced  ", "", None):
            with self.subTest(value=value):
                self.leg.db.update_work_metadata(ids[0], {"private_notes": value})
                self.ref.db.update_work_metadata(ids[1], {"private_notes": value})
                self.assert_same_state(ids)
        self.assertEqual(self.leg.db.get_work_notes_state(ids[0])["private_note_revision"], 3)

    # -- delete -----------------------------------------------------------------

    def test_deleting_works(self) -> None:
        plain = self.add_both("plain", title="Plain")
        video = self.add_both("video", title="Video", source_url=VIDEO)
        for ids in (plain, video):
            mine = self.leg.db.delete_work_record(ids[0])
            theirs = self.ref.db.delete_work_record(ids[1])
            self.assertEqual(self.leg.normal(mine.__dict__), self.ref.normal(theirs.__dict__))
            self.assertEqual(self.both("get_work", ids[0])[0], None)
        self.assertEqual(self.both("delete_work_record", _MISSING), (None, None))
        self.assertEqual(self.both("get_all_works"), ([], []))

    def test_deleting_one_of_two_works_on_one_file(self) -> None:
        first = self.add_pdf("first", "shared.pdf", title="First")
        second = (self.leg.add("second", title="Second", file_path="/api/pdfs/shared.pdf"),
                  self.ref.add("second", title="Second", file_path="/api/pdfs/shared.pdf"))
        mine = self.leg.db.delete_work_record(first[0])
        theirs = self.ref.db.delete_work_record(first[1])
        self.assertEqual(self.leg.normal(mine.__dict__), self.ref.normal(theirs.__dict__))
        self.assertTrue(mine.managed_pdf_still_referenced)
        self.same_for("get_work", second)
        mine = self.leg.db.delete_work_record(second[0], claim_pdf=False)
        theirs = self.ref.db.delete_work_record(second[1], claim_pdf=False)
        self.assertEqual(self.leg.normal(mine.__dict__), self.ref.normal(theirs.__dict__))
        self.assertFalse(mine.managed_pdf_still_referenced)


if __name__ == "__main__":
    unittest.main()
