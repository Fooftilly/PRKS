"""Folders, their hierarchy and fields, and Work filing: PostgreSQL answers as SQLite does (L-O-1 #633).

Each call, and each Folder semantic operation, runs on this leg's library
and on a reference SQLite library holding the same Works and Folders, and
the results must be equal after generated ids and timestamps become labels.
Inputs avoid the documented differences (``backend/pg_facade/organization.py``):
titles differ beyond ASCII case, a Work is filed by ``move_work_to_folder``
(``add_work_to_folder`` advances a revision on PostgreSQL only) before a
Work-Folder state is compared, no Folder is created again under a deleted
id, and no two Works of one Folder are compared by creation order.
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Optional

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import parametrize_backends
from tests.test_tags_parity import SyncParityTestCase, TagParityTestCase, both

_MISSING_FOLDER = "F-" + "0" * 32
_MISSING_WORK = "W-" + "0" * 32


class FolderParityTestCase(TagParityTestCase):
    def folder_both(self, label: str, parent: Optional[tuple[str, str]] = None,
                    description: str = "") -> tuple[str, str]:
        ids = []
        for index, side in enumerate(self.sides()):
            folder_id = side.db.add_folder(label, description, parent[index] if parent else None)
            ids.append(side.label(folder_id, f"<F {label}>"))
        return ids[0], ids[1]


@parametrize_backends
class FolderFacadeParityTests(FolderParityTestCase):
    def test_create_read_and_list(self) -> None:
        root = self.folder_both("Root", description="About")
        child = self.folder_both("Child", root)
        other = self.folder_both("Other")
        for args in ((both("root"), both(""), both(None)), (both("CHILD"), both(""), root),
                     (both("Lost"), both(""), both(_MISSING_FOLDER))):
            with self.subTest(title=args[0][0]):
                self.refused_pair("add_folder", *args)
        self.same("get_all_folders")
        # The same title under another parent (created after the catalog
        # compare: equal titles of one second order by id on PostgreSQL).
        self.folder_both("Child", other)
        self.same_pair("get_folder", other)
        self.same_pair("get_folder", root)
        self.same_pair("get_folder", child)
        self.same_pair("get_folder", both(_MISSING_FOLDER))
        self.same_pair("get_folder_sync_state", child)
        self.same_pair("get_folder_sync_state", both(_MISSING_FOLDER))
        self.same_pair("folder_exists", root)
        self.same_pair("folder_exists", both(_MISSING_FOLDER))
        self.same_pair("folder_exists", both("  "))
        # The catalog ETag hashes the rows, ids and times included: its shape matches.
        self.assertEqual([tag.split("-")[2] for tag in self.both("etag_folders_catalog")], ["4"] * 2)
        rows = self.both("get_all_folders")
        self.assertEqual(self.leg.db.etag_folders_catalog(rows[0]).split("-")[2], "4")

    def test_fields_hierarchy_and_their_revisions(self) -> None:
        a = self.folder_both("A")
        b = self.folder_both("B", a)
        twin = self.folder_both("b")
        self.same_pair("update_folder_metadata", b, both({"title": "Renamed", "description": " About ",
                                                         "private_notes": "Mine"}))
        self.same_pair("update_folder_metadata", b, both({"title": "Renamed", "ignored": "x"}))
        for fields in ({"parent_id": a[0]}, {"title": "a"}, {"title": "renamed", "parent_id": a[0]}):
            with self.subTest(fields=fields):
                pairs = (fields, {key: (a[1] if value == a[0] else value) for key, value in fields.items()})
                self.refused_pair("update_folder_metadata", a if "parent_id" in fields and len(fields) == 1
                                  else twin, pairs)
        self.refused_pair("update_folder_metadata", a, (({"parent_id": b[0]}), ({"parent_id": b[1]})))
        self.refused_pair("update_folder_metadata", a, (({"parent_id": a[0]}), ({"parent_id": a[1]})))
        self.refused_pair("update_folder_metadata", both(_MISSING_FOLDER), both({"title": "X"}))
        # Rename first, then move: the pair is judged against the new title.
        self.same_pair("update_folder_metadata", twin, (({"title": "C", "parent_id": a[0]}),
                                                        ({"title": "C", "parent_id": a[1]})))
        for folder in (a, b, twin):
            self.same_pair("get_folder_sync_state", folder)
        self.same_pair("get_folder", a)
        self.same("get_all_folders")

    def test_work_filing_and_the_work_projection(self) -> None:
        a, b = self.folder_both("A"), self.folder_both("B")
        work = self.work_both("Paper")
        self.same_pair("get_work_folder_state", work)
        self.refused_pair("add_work_to_folder", both(_MISSING_FOLDER), work)
        self.same_pair("move_work_to_folder", work, a)
        self.same_pair("move_work_to_folder", work, a)
        self.same_pair("get_work_folder_state", work)
        self.refused_pair("move_work_to_folder", work, both(_MISSING_FOLDER))
        self.refused_pair("add_work_to_folder", b, work)
        self.same_pair("add_work_to_folder", a, work)
        self.refused_pair("add_work_to_folder", both(" "), work)
        self.refused_pair("move_work_to_folder", both(" "), a)
        detail = self.same_pair("get_work", work)
        self.assertEqual(detail["folder_title"], "A")
        self.same("get_all_works")
        self.same("get_recently_added_browse")
        self.same_pair("get_folder", a)
        self.same_pair("get_folder_work_ids", a)
        self.same_pair("move_work_to_folder", work, b)
        self.same_pair("get_work_folder_state", work)
        self.same("get_all_folders")
        self.same_pair("move_work_to_folder", work, both(None))
        self.same_pair("get_work_folder_state", work)
        self.same_pair("get_work_folder_state", both(_MISSING_WORK))
        self.same_pair("get_work", work)

    def test_uncategorized_and_related_folders(self) -> None:
        ids = [side.label(side.db.ensure_default_uncategorized_folder_id(), "<F Uncategorized>")
               for side in self.sides()]
        uncategorized = (ids[0], ids[1])
        self.assertEqual(self.both("ensure_default_uncategorized_folder_id"), uncategorized)
        self.same("get_all_folders")  # hidden while empty
        talks = self.folder_both("Talks")
        first, second = self.work_both("First"), self.work_both("Second")
        person = self.person_both("Lee")
        for index, side in enumerate(self.sides()):
            side.db.add_role(person[index], first[index], "Author")
            side.db.add_role(person[index], second[index], "Author")
            side.db.move_work_to_folder(first[index], uncategorized[index])
            side.db.move_work_to_folder(second[index], talks[index])
        self.same("get_all_folders")
        self.same_pair("get_related_folders_for_work", first)
        self.same_pair("get_related_folders_for_work", second)

    def test_delete(self) -> None:
        parent = self.folder_both("Parent")
        self.folder_both("Child", parent)
        busy = self.folder_both("Busy")
        work = self.work_both("W")
        self.same_pair("move_work_to_folder", work, busy)
        for folder in (parent, busy, both(_MISSING_FOLDER), both(" ")):
            with self.subTest(folder=folder[0]):
                self.refused_pair("delete_empty_folder", folder)
        self.same_pair("move_work_to_folder", work, both(""))
        tag = self.tag_both("Ethics")
        self.same_pair("add_tag_to_folder", busy, tag)
        self.same_pair("delete_empty_folder", busy)
        self.same_pair("get_folder", busy)
        self.same("get_all_folders")
        self.same("get_all_tags")
        self.assertTrue(all(self.both_pair("delete_work_record", work)))
        self.same_pair("get_work", work)

    def both_pair(self, call: str, *pairs: tuple[Any, Any]) -> list[Any]:
        return [getattr(side.db, call)(*(pair[index] for pair in pairs))
                for index, side in enumerate(self.sides())]


@parametrize_backends
class FolderSyncParityTests(SyncParityTestCase):
    """The Folder operations and ``CREATE_WORK``'s filing."""

    def new_id(self, prefix: str, label: str) -> str:
        entity_id = prefix + "-" + uuid.uuid4().hex.upper()
        for side in self.sides():
            side.label(entity_id, f"<{prefix} {label}>")
        return entity_id

    def create_folder(self, label: str, title: Optional[str] = None, **fields: str) -> str:
        folder_id = self.new_id("F", label)
        payload = {"title": title or label, "description": "", "parent_id": "", "private_notes": "", **fields}
        self.assertEqual(self.send_both("CREATE_FOLDER", folder_id, payload)[0], 200)
        return folder_id

    def test_create_folder(self) -> None:
        folder_id = self.new_id("F", "Talks")
        op_id = str(uuid.uuid4())
        payload = {"title": "  Talks ", "description": "About", "parent_id": "", "private_notes": "Mine"}
        self.send_both("CREATE_FOLDER", folder_id, payload, op_id=op_id)
        self.send_both("CREATE_FOLDER", folder_id, payload, op_id=op_id)  # a replay
        self.send_both("CREATE_FOLDER", folder_id, dict(payload, title="Other"))
        self.send_both("CREATE_FOLDER", self.new_id("F", "Clash"), dict(payload, title="TALKS"))
        self.send_both("CREATE_FOLDER", self.new_id("F", "Child"), dict(payload, parent_id=folder_id))
        self.send_both("CREATE_FOLDER", self.new_id("F", "Lost"), dict(payload, parent_id=_MISSING_FOLDER))
        self.send_both("CREATE_FOLDER", self.new_id("F", "Empty"), dict(payload, title="  "))
        self.send_both("CREATE_FOLDER", self.new_id("F", "Base"), payload, base=0)
        self.send_both("CREATE_FOLDER", "F-1", payload)
        self.send_both("CREATE_FOLDER", self.new_id("F", "Short"), {"title": "x"})
        self.same("get_all_folders")

    def test_folder_fields(self) -> None:
        a = self.create_folder("A")
        b = self.create_folder("B", parent_id=a)
        twin = self.create_folder("b")
        work = self.work_both("W")
        for side in self.sides():
            side.db.move_work_to_folder(work[0] if side is self.leg else work[1], b)
        self.send_both("SET_FOLDER_FIELD", b, {"field": "title", "value": "Renamed"}, 0)
        self.send_both("SET_FOLDER_FIELD", b, {"field": "title", "value": " Renamed "}, 0)
        self.send_both("SET_FOLDER_FIELD", b, {"field": "title", "value": "Stale"}, 0)
        self.send_both("SET_FOLDER_FIELD", b, {"field": "title", "value": "Future"}, 7)
        self.send_both("SET_FOLDER_FIELD", b, {"field": "private_notes", "value": "Mine"}, 0)
        self.send_both("SET_FOLDER_FIELD", a, {"field": "parent_id", "value": b}, 0)
        self.send_both("SET_FOLDER_FIELD", a, {"field": "parent_id", "value": a}, 0)
        self.send_both("SET_FOLDER_FIELD", a, {"field": "parent_id", "value": _MISSING_FOLDER}, 0)
        self.send_both("SET_FOLDER_FIELD", twin, {"field": "parent_id", "value": a}, 0)
        self.send_both("SET_FOLDER_FIELD", twin, {"field": "title", "value": "a"}, 0)
        self.send_both("SET_FOLDER_FIELD", twin, {"field": "parent_id", "value": b}, 0)
        self.send_both("SET_FOLDER_FIELD", _MISSING_FOLDER, {"field": "title", "value": "X"}, 0)
        self.send_both("SET_FOLDER_FIELD", a, {"field": "colour", "value": "X"}, 0)
        self.send_both("SET_FOLDER_FIELD", a, {"field": "title", "value": "X"}, None)
        self.send_both("SET_FOLDER_FIELD", a, {"field": "title", "value": 5}, 0)
        for folder in (a, b, twin):
            self.same_pair("get_folder_sync_state", both(folder))
        self.same("get_all_folders")

    def test_work_folder_and_delete(self) -> None:
        a, b = self.create_folder("A"), self.create_folder("B")
        work = self.work_both("W")
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": a}, 0)
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": a}, 0)
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": b}, 0)
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": b}, 4)
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": _MISSING_FOLDER}, 1)
        self.send_both("SET_WORK_FOLDER", both(_MISSING_WORK), {"folder_id": a}, 0)
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": " a"}, 1)
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": a}, None)
        self.same_pair("get_work_folder_state", work)
        self.send_both("DELETE_FOLDER", a, {})
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": b}, 1)
        self.send_both("DELETE_FOLDER", a, {})
        self.send_both("DELETE_FOLDER", _MISSING_FOLDER, {})
        self.send_both("SET_WORK_FOLDER", work, {"folder_id": ""}, 2)
        self.send_both("DELETE_FOLDER", b, {})
        self.send_both("DELETE_FOLDER", b, {"x": 1})
        self.send_both("DELETE_FOLDER", b, {}, base=0)
        self.same_pair("get_work_folder_state", work)
        self.same("get_all_folders")

    def test_create_work_files_the_work(self) -> None:
        folder_id = self.create_folder("Talks")
        video = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        payload = {"title": "A talk", "status": "", "doc_type": "", "abstract": "", "author_text": "",
                   "year": "", "published_date": "", "urldate": "", "private_notes": "", "thumb_url": "",
                   "source": {"kind": "video", "url": video}, "folder_id": "", "playlist_id": "",
                   "roles": []}
        first = self.new_id("W", "First")
        self.send_both("CREATE_WORK", first, payload)
        self.send_both("CREATE_WORK", first, payload)  # converged
        explicit = self.new_id("W", "Explicit")
        self.send_both("CREATE_WORK", explicit, dict(payload, folder_id=folder_id,
                                                     source={"kind": "video", "url": video + "1"}))
        self.send_both("CREATE_WORK", self.new_id("W", "Lost"), dict(payload, folder_id=_MISSING_FOLDER))
        self.same_pair("get_work_folder_state", both(first))
        self.same_pair("get_work_folder_state", both(explicit))
        self.same("get_all_folders")
