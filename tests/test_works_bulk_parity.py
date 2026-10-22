"""Bulk Work actions: PostgreSQL answers as SQLite does (L-W-2 #631).

Each ``bulk_update_works`` call runs on this leg's library and on a
reference SQLite library holding the same Works, Folders and Tags; the
result or the refusal (``BulkWorkError`` message and HTTP status) must be
equal, and so must what the action leaves behind: Status, Folder and Tags
with their revisions, and how a stale semantic operation is decided after
it. Ids are per side, labelled before comparing.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKS_BULK_WORK_MAX, BulkWorkError
from tests.backend_parity import parametrize_backends
from tests.test_folders_parity import FolderParityTestCase
from tests.test_tags_parity import SyncParityTestCase, both

_MISSING_WORK = "W-" + "0" * 32
_MISSING_FOLDER = "F-" + "0" * 32
_MISSING_TAG = "T-" + "0" * 32

Pair = tuple[Any, Any]


class BulkParityTestCase(SyncParityTestCase, FolderParityTestCase):
    def work_both(self, label: str, status: str = "Not Started") -> tuple[str, str]:  # type: ignore[override]
        return self.leg.work(label, title=label, status=status), self.ref.work(label, title=label, status=status)

    def body(self, index: int, action: str, works: list[Pair], folder: Optional[Pair] = None,
             tags: Optional[list[Pair]] = None, **extra: Any) -> dict[str, Any]:
        data: dict[str, Any] = {"action": action, "work_ids": [pair[index] for pair in works], **extra}
        if folder is not None:
            data["folder_id"] = folder[index]
        if tags is not None:
            data["tag_ids"] = [pair[index] for pair in tags]
        return data

    def bulk_both(self, action: str, works: list[Pair], **targets: Any) -> dict[str, Any]:
        mine = self.leg.db.bulk_update_works(self.body(0, action, works, **targets))
        theirs = self.ref.db.bulk_update_works(self.body(1, action, works, **targets))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), action)
        return mine

    def bulk_refused_both(self, action: str, works: list[Pair], **targets: Any) -> tuple[int, str]:
        answers = []
        for index, side in enumerate(self.sides()):
            with self.assertRaises(BulkWorkError) as caught:
                side.db.bulk_update_works(self.body(index, action, works, **targets))
            answers.append((caught.exception.http_status, str(caught.exception)))
        self.assertEqual(answers[0], answers[1], action)
        return answers[0]

    def body_refused_both(self, data: Any) -> tuple[int, str]:
        answers = []
        for side in self.sides():
            with self.assertRaises(BulkWorkError) as caught:
                side.db.bulk_update_works(data)
            answers.append((caught.exception.http_status, str(caught.exception)))
        self.assertEqual(answers[0], answers[1])
        return answers[0]

    def same_states(self, *works: Pair) -> None:
        """Status, Folder and Tags of each Work, with their revisions, are equal on both sides."""
        for work in works:
            self.same_pair("get_work_metadata_state", work)
            self.same_pair("get_work_folder_state", work)
            self.same_pair("get_work_tag_options", work)


@parametrize_backends
class BulkWorkParityTests(BulkParityTestCase):
    def test_set_status_answers_and_advances_alike(self) -> None:
        a, b = self.work_both("A", "Paused"), self.work_both("B", "Paused")
        done, other = self.work_both("Done", "Completed"), self.work_both("Other", "Paused")
        self.bulk_both("set_status", [b, a, done, a], status="Completed")
        self.same_states(a, b, done, other)
        self.bulk_both("set_status", [a, b, done], status="Completed")
        self.bulk_both("set_status", [a], status="In Progress")
        self.same_states(a, b, done)

    def test_move_folder_files_moves_and_clears_alike(self) -> None:
        x, y = self.folder_both("X"), self.folder_both("Y")
        a, b, c = self.work_both("A"), self.work_both("B"), self.work_both("C")
        for work in (a, b, c):
            self.same_pair("move_work_to_folder", work, x)
        self.same_pair("move_work_to_folder", b, y)
        self.bulk_both("move_folder", [a, b], folder=(f" {y[0]} ", f" {y[1]} "))
        self.same_states(a, b, c)
        self.same("get_all_folders")
        self.bulk_both("move_folder", [a, b, c], folder=both(None))
        self.bulk_both("move_folder", [a, b], folder=both(None))
        self.same_states(a, b, c)
        self.same("get_all_folders")

    def test_tag_actions_are_idempotent_alike(self) -> None:
        a, b, c = self.work_both("A"), self.work_both("B"), self.work_both("C")
        t1, t2 = self.tag_both("T1"), self.tag_both("T2")
        self.same_pair("add_tag_to_work", a, t1)
        self.same_pair("add_tag_to_work", c, t1)
        self.bulk_both("add_tags", [a, b], tags=[t2, t1, t2])
        self.bulk_both("add_tags", [a, b], tags=[t1, t2])
        self.same_states(a, b, c)
        self.bulk_both("remove_tags", [a, c], tags=[t2])
        self.bulk_both("remove_tags", [a, c], tags=[t2])
        self.same_states(a, b, c)
        self.same("get_tags_in_use")

    def test_invalid_bodies_are_refused_alike(self) -> None:
        work = self.work_both("W", "Planned")
        bodies: list[Any] = [
            [], "x", {"work_ids": [work[0]]},
            {"action": "delete_everything", "work_ids": ["W"]},
            {"action": "set_status", "work_ids": "W", "status": "Completed"},
            {"action": "set_status", "work_ids": [], "status": "Completed"},
            {"action": "set_status", "work_ids": [" ", "W"], "status": "Completed"},
            {"action": "set_status", "work_ids": [None], "status": "Completed"},
            {"action": "set_status", "work_ids": [f"W-{i}" for i in range(PRKS_BULK_WORK_MAX + 1)],
             "status": "Completed"},
            {"action": "set_status", "work_ids": ["W"], "status": "Finished"},
            {"action": "set_status", "work_ids": ["W"], "status": None},
            {"action": "move_folder", "work_ids": ["W"]},
            {"action": "move_folder", "work_ids": ["W"], "folder_id": "   "},
            {"action": "move_folder", "work_ids": ["W"], "folder_id": ["F"]},
            {"action": "add_tags", "work_ids": ["W"]},
            {"action": "add_tags", "work_ids": ["W"], "tag_ids": []},
            {"action": "remove_tags", "work_ids": ["W"], "tag_ids": [f"T-{i}" for i in range(501)]},
        ]
        for data in bodies:
            with self.subTest(body=str(data)[:60]):
                self.assertEqual(self.body_refused_both(data)[0], 400)
        self.same_states(work)

    def test_missing_works_folders_and_tags_are_refused_alike_and_change_nothing(self) -> None:
        a, b = self.work_both("A", "Paused"), self.work_both("B", "Paused")
        folder, tag = self.folder_both("F"), self.tag_both("T")
        gone, merged = self.tag_both("Gone"), self.tag_both("Merged")
        self.same_pair("delete_tag", gone)
        self.same_pair("merge_tags_into", merged, tag)
        deleted = self.work_both("Deleted")
        for side, work_id in zip(self.sides(), deleted):
            self.assertIsNotNone(side.db.delete_work_record(work_id))
        for missing in (both(_MISSING_WORK), deleted):
            for action, targets in (("set_status", {"status": "Completed"}), ("move_folder", {"folder": folder}),
                                    ("add_tags", {"tags": [tag]}), ("remove_tags", {"tags": [tag]})):
                with self.subTest(action=action):
                    self.assertEqual(self.bulk_refused_both(action, [a, missing, b], **targets)[0], 404)
        self.bulk_refused_both("move_folder", [both(_MISSING_WORK)], folder=both(_MISSING_FOLDER))
        self.bulk_refused_both("move_folder", [a, b], folder=both(_MISSING_FOLDER))
        for missing in (both(_MISSING_TAG), gone, merged):
            for action in ("add_tags", "remove_tags"):
                with self.subTest(action=action):
                    self.bulk_refused_both(action, [a, b], tags=[tag, missing])
        self.same_states(a, b)

    def test_ids_holding_nul_are_missing_alike(self) -> None:
        a, folder, tag = self.work_both("A", "Paused"), self.folder_both("F"), self.tag_both("T")
        nul = both("W-\x00")
        for action, targets in (("set_status", {"status": "Completed"}), ("move_folder", {"folder": folder}),
                                ("add_tags", {"tags": [tag]}), ("remove_tags", {"tags": [tag]})):
            with self.subTest(action=action):
                self.assertEqual(self.bulk_refused_both(action, [a, nul], **targets),
                                 (404, "One or more selected files no longer exist."))
        self.assertEqual(self.bulk_refused_both("move_folder", [a], folder=(folder[0] + "\x00", folder[1] + "\x00")),
                         (404, "Folder not found."))
        for action in ("add_tags", "remove_tags"):
            with self.subTest(action=action):
                self.assertEqual(self.bulk_refused_both(action, [a], tags=[tag, both("T-\x00")]),
                                 (404, "One or more selected tags no longer exist."))
        self.same_states(a)

    def test_operations_from_before_a_bulk_action_are_decided_alike(self) -> None:
        work = self.work_both("W", "Paused")
        x, y = self.folder_both("X"), self.folder_both("Y")
        tag = self.tag_both("T")
        self.same_pair("move_work_to_folder", work, x)
        self.bulk_both("set_status", [work], status="Completed")
        self.bulk_both("move_folder", [work], folder=y)
        self.bulk_both("add_tags", [work], tags=[tag])
        self.send_both("SET_WORK_METADATA_FIELD", work, {"field": "status", "value": "Planned"}, 0)
        self.send_both("SET_WORK_FOLDER", work, ({"folder_id": x[0]}, {"folder_id": x[1]}), 1)
        self.send_both("REMOVE_WORK_TAG", work, ({"tag_id": tag[0]}, {"tag_id": tag[1]}), 0)
        self.send_both("SET_WORK_METADATA_FIELD", work, {"field": "status", "value": "Completed"}, 0)
        self.send_both("SET_WORK_FOLDER", work, ({"folder_id": y[0]}, {"folder_id": y[1]}), 1)
        self.send_both("ADD_WORK_TAG", work, ({"tag_id": tag[0]}, {"tag_id": tag[1]}), 0)
        self.send_both("SET_WORK_METADATA_FIELD", work, {"field": "status", "value": "Planned"}, 1)
        self.send_both("SET_WORK_FOLDER", work, ({"folder_id": x[0]}, {"folder_id": x[1]}), 2)
        self.send_both("REMOVE_WORK_TAG", work, ({"tag_id": tag[0]}, {"tag_id": tag[1]}), 1)
        self.same_states(work)
