"""Work create and reads: the PostgreSQL facade returns SQLite's JSON (L-W-1 #628).

Each test runs the same calls on the library object of its leg and on a
reference ``PRKSDatabase`` built in the same test, then compares the two
results after replacing what differs by construction: generated ids (each
becomes a label naming its Work) and the current time (each timestamp
becomes its format). On the SQLite leg the two sides are both SQLite; on the
``postgres`` leg (``scripts/test-postgres --parity``) the left side is
:class:`backend.pg_facade.PostgresLibrary`, so equality is parity with
today's representation. A test that reaches a method the facade has not
ported is skipped there, never counted.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.backend_parity import BackendParityTestCase, parametrize_backends

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")
_ID = re.compile(r"\b(W|MF|AS)-[0-9A-F]{32}\b")
_MOMENT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{3})?")
_ETAG = re.compile(r'W/"prks-([a-z-]+)-(\d+)-([0-9a-f]{32})"')
_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_PDF_BYTES = b"%PDF-1.4\n% parity fixture\n%%EOF\n"


class _Side:
    """One library object plus the labels of the ids it generated."""

    def __init__(self, db: Any, pdfs_dir: str) -> None:
        self.db = db
        self.pdfs_dir = pdfs_dir
        self.labels: dict[str, str] = {}

    def add(self, label: str, **fields: Any) -> str:
        work_id = self.db.add_work(**fields)
        self.labels[work_id] = f"<W {label}>"
        work = self.db.get_work(work_id)
        self.labels[work["primary_manifestation_id"]] = f"<MF {label}>"
        if work["primary_asset_id"]:
            self.labels[work["primary_asset_id"]] = f"<AS {label}>"
        return work_id

    def store_pdf(self, name: str, data: bytes = _PDF_BYTES) -> str:
        os.makedirs(self.pdfs_dir, exist_ok=True)
        with open(os.path.join(self.pdfs_dir, name), "wb") as handle:
            handle.write(data)
        return f"/api/pdfs/{name}"

    def normal(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.normal(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [self.normal(item) for item in value]
        if isinstance(value, str):
            text = _ID.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
            text = _MOMENT.sub(lambda match: "<ms>" if match.group(1) else "<s>", text)
            return _ETAG.sub(lambda match: f"<etag {match.group(1)} {match.group(2)}>", text)
        return value


@parametrize_backends
class WorksCoreParityTests(BackendParityTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="prks-works-parity-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        leg_storage = StorageConfig.for_testing(os.path.join(self.tmp, "leg"))
        ref_storage = StorageConfig.for_testing(os.path.join(self.tmp, "ref"))
        self.leg = _Side(self.open_library(leg_storage), leg_storage.pdfs_dir)
        self.ref = _Side(PRKSDatabase(storage=ref_storage, schema_path=_SCHEMA_PATH), ref_storage.pdfs_dir)

    def both(self, call: str, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        return (getattr(self.leg.db, call)(*args, **kwargs),
                getattr(self.ref.db, call)(*args, **kwargs))

    def same(self, call: str, *args: Any, **kwargs: Any) -> Any:
        mine, theirs = self.both(call, *args, **kwargs)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def add_both(self, label: str, **fields: Any) -> tuple[str, str]:
        return self.leg.add(label, **fields), self.ref.add(label, **fields)

    def same_for(self, call: str, ids: tuple[str, str], *args: Any) -> Any:
        mine = getattr(self.leg.db, call)(ids[0], *args)
        theirs = getattr(self.ref.db, call)(ids[1], *args)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def add_pdf(self, label: str, name: str, **fields: Any) -> tuple[str, str]:
        mine = self.leg.add(label, file_path=self.leg.store_pdf(name), source_kind="pdf", **fields)
        theirs = self.ref.add(label, file_path=self.ref.store_pdf(name), source_kind="pdf", **fields)
        return mine, theirs

    # -- one Work ---------------------------------------------------------------

    def test_a_work_without_a_source(self) -> None:
        ids = self.add_both("plain", title="Plain", status="In Progress", abstract="An abstract",
                            author_text="Ann Lee", year="2020", publisher="Press",
                            journal="J", volume="3", issue="2", pages="1-9", isbn="978",
                            doi="10.1/X", doc_type="book", published_date="2020-01-02",
                            location="Paris", edition="2", urldate="2024-05-06",
                            private_notes="  remember this  ")
        work = self.same_for("get_work", ids)
        self.assertEqual(work["private_notes"], "remember this")
        self.assertIsNone(work["source_kind"])
        self.assertEqual(work["file_path"], "")

    def test_a_managed_pdf_records_its_file(self) -> None:
        ids = self.add_pdf("pdf", "paper.pdf", title="Paper", source_url="https://example.org/p",
                           thumb_page=3, source_mime="application/pdf", thumb_url="https://t")
        work = self.same_for("get_work", ids)
        self.assertEqual(work["file_path"], "/api/pdfs/paper.pdf")
        self.assertEqual(work["file_size_bytes"], len(_PDF_BYTES))
        self.assertEqual(work["asset_count"], 1)
        self.same_for("get_primary_thumbnail_fields", ids)

    def test_a_video_is_a_stream(self) -> None:
        ids = self.add_both("video", title="Video", source_kind="video", source_url=_VIDEO,
                            thumb_url="https://i.ytimg.com/x.jpg", urldate="2024-01-01")
        work = self.same_for("get_work", ids)
        self.assertEqual((work["source_kind"], work["provider"], work["provider_id"]),
                         ("video", "youtube", "dQw4w9WgXcQ"))
        self.assertIsNone(self.leg.db.get_work(ids[0])["file_size_bytes"])

    def test_a_url_without_a_kind_is_a_video(self) -> None:
        ids = self.add_both("inferred", title="Inferred", source_url=_VIDEO)
        self.assertEqual(self.same_for("get_work", ids)["source_kind"], "video")

    def test_a_contradictory_source_is_refused_on_both(self) -> None:
        for fields in (
            {"source_kind": "web"},
            {"source_kind": "video", "source_url": "https://example.org/not-a-video"},
            {"source_kind": "video", "source_url": _VIDEO, "provider_id": "other"},
            {"provider": "youtube"},
        ):
            with self.subTest(fields=fields):
                for db in (self.leg.db, self.ref.db):
                    with self.assertRaises(ValueError):
                        db.add_work("Refused", **fields)
        self.assertEqual(self.both("get_all_works"), ([], []))

    def test_an_unknown_work_reads_as_none(self) -> None:
        self.assertEqual(self.both("get_work", "W-" + "0" * 32), (None, None))
        self.assertEqual(self.both("get_primary_thumbnail_fields", "W-" + "0" * 32), (None, None))

    def test_generated_ids_have_the_distributed_form(self) -> None:
        for prefix in ("W", "MF", "AS", "F"):
            mine, theirs = self.both("generate_id", prefix)
            self.assertRegex(mine, rf"^{prefix}-[0-9A-F]{{32}}$")
            self.assertRegex(theirs, rf"^{prefix}-[0-9A-F]{{32}}$")
            self.assertNotEqual(mine, self.leg.db.generate_id(prefix))

    # -- lists ------------------------------------------------------------------

    def _catalog(self) -> None:
        # Titles chosen so the browse order exercises SQLite's NOCASE rule:
        # ASCII letters fold, everything else compares by code point.
        for title in ("beta", "Alpha", "_under", "Zed", "Ä umlaut", "ä umlaut"):
            self.add_both(title, title=title, abstract="x" * 150 if title == "Zed" else "")
        self.add_pdf("file", "f.pdf", title="file")
        self.add_both("stream", title="stream", source_url=_VIDEO)

    def test_the_catalog_lists(self) -> None:
        self._catalog()
        for call in ("get_all_works", "get_recently_added_works", "get_recently_added_browse"):
            with self.subTest(call=call):
                mine, theirs = self.both(call)
                # Works created in one second are ordered by id, which each
                # side generated at random: compare the rows by Work, and the
                # order by its rule.
                self.assertEqual(sorted(map(self.leg.normal, mine), key=str),
                                 sorted(map(self.ref.normal, theirs), key=str))
                if call == "get_recently_added_works":
                    continue  # SQLite gives this list no tie-break
                for rows in (mine, theirs):
                    expected = sorted(rows, key=lambda row: row["id"])
                    expected.sort(key=lambda row: row["created_at"], reverse=True)
                    self.assertEqual(rows, expected)

    def test_the_browse_catalog_is_ordered_by_title(self) -> None:
        self._catalog()
        rows = self.same("get_works_browse_catalog")
        self.assertEqual([row["title"] for row in rows],
                         ["_under", "Alpha", "beta", "file", "stream", "Zed", "Ä umlaut", "ä umlaut"])
        self.assertEqual(next(r for r in rows if r["title"] == "Zed")["abstract_excerpt"], "x" * 100)

    def test_limits(self) -> None:
        self._catalog()
        for call, limit in (("get_recently_added_browse", 3), ("get_recently_added_works", 2),
                            ("get_recently_added_works", -1)):
            with self.subTest(call=call, limit=limit):
                mine, theirs = self.both(call, limit)
                self.assertEqual(len(mine), len(theirs))

    def test_nothing_opened_yet(self) -> None:
        self._catalog()
        self.assertEqual(self.both("get_recent_browse"), ([], []))
        self.assertEqual(self.both("get_recent_works", 5), ([], []))
        self.same("etag_recent_works")

    def test_summaries_follow_the_requested_order(self) -> None:
        a = self.add_both("a", title="A")
        b = self.add_both("b", title="B")
        missing = "W-" + "F" * 32
        mine = self.leg.db.get_work_summaries_by_ids_ordered([b[0], " ", missing, a[0], b[0]])
        theirs = self.ref.db.get_work_summaries_by_ids_ordered([b[1], " ", missing, a[1], b[1]])
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs))
        self.assertEqual([row["title"] for row in mine], ["B", "A", "B"])
        self.assertEqual(self.both("get_work_summaries_by_ids_ordered", []), ([], []))

    def test_etags_follow_the_representation(self) -> None:
        self._catalog()
        mine, theirs = self.both("get_all_works")
        self.assertEqual(self.leg.db.etag_works_catalog(mine),
                         PRKSDatabase.etag_for_representation("works", mine))
        self.assertEqual(self.leg.db.etag_for_representation("works", mine),
                         self.leg.db.etag_works_catalog(mine))
        self.assertRegex(self.leg.db.etag_works_catalog(mine), r'^W/"prks-works-8-[0-9a-f]{32}"$')
        self.same("etag_recently_added_works")
        before = self.leg.db.etag_works_catalog(mine)
        self.add_both("later", title="Later")
        self.assertNotEqual(self.leg.db.etag_works_catalog(self.leg.db.get_all_works()), before)
        del theirs


if __name__ == "__main__":
    unittest.main()
