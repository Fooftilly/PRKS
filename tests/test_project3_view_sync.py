"""Contract tests for permanent, versioned Project #3 agent view policy."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import project3_view_sync as sync


class FakeGraphQL:
    def __init__(self):
        self.items = [
            {"id": "V9", "number": 9, "name": "Agent Queue",
             "filter": 'is:open is:issue execution:Agent,Mixed status:Ready,"In Progress"',
             "layout": "TABLE"},
            {"id": "V7", "number": 7, "name": "Active Work",
             "filter": "status:Review", "layout": "TABLE"},
        ]
        self.mutations: list[tuple[str, dict]] = []
        self.fail_after_write = False
        self.page_size = 100
        self.next_num = 10

    def __call__(self, document, variables):
        if document == sync.VIEWS_QUERY:
            after = variables.get("after")
            start = int(after) if after else 0
            end = start + self.page_size
            nodes = copy.deepcopy(self.items[start:end])
            return {"user": {"projectV2": {
                "id": sync.PROJECT_ID, "number": sync.PROJECT_NUMBER,
                "owner": {"__typename": "User", "login": sync.OWNER},
                "views": {"totalCount": len(self.items), "nodes": nodes,
                          "pageInfo": {"hasNextPage": end < len(self.items),
                                       "endCursor": str(end) if end < len(self.items) else None}}
            }}}
        if document == sync.UPDATE_VIEW:
            values = variables["input"]
            item = next(item for item in self.items if item["id"] == values["viewId"])
            self.mutations.append(("update", copy.deepcopy(values)))
            if not self.fail_after_write:
                item["filter"] = values["filter"]
            return {"updateProjectV2View": {"projectV2View": copy.deepcopy(item)}}
        if document == sync.CREATE_VIEW:
            values = variables["input"]
            self.mutations.append(("create", copy.deepcopy(values)))
            item = {"id": f"V{self.next_num}", "number": self.next_num,
                    "name": values["name"], "layout": values["layout"], "filter": None}
            self.next_num += 1
            self.items.append(item)
            return {"createProjectV2View": {"projectV2View": copy.deepcopy(item)}}
        raise AssertionError(f"unexpected document: {document[:70]}")


class AgentViewTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGraphQL()

    def test_check_is_read_only_even_with_legacy_filter(self):
        changes = sync.reconcile(self.api, apply=False)
        self.assertEqual(len(changes), 2)
        self.assertEqual(self.api.mutations, [])
        self.assertIn("status:Ready", self.api.items[0]["filter"])

    def test_apply_fixes_queue_and_creates_ready_view(self):
        changes = sync.reconcile(self.api, apply=True)
        self.assertEqual(len(changes), 2)
        self.assertEqual(self.api.items[0]["filter"], sync.OWNED_FILTER)
        ready = self.api.items[2]
        self.assertEqual(ready["name"], sync.READY_NAME)
        self.assertEqual(ready["filter"], sync.READY_FILTER)
        self.assertEqual([x[0] for x in self.api.mutations], ["update", "create", "update"])
        self.assertEqual(self.api.items[1]["filter"], "status:Review")

    def test_second_apply_is_idempotent(self):
        sync.reconcile(self.api, apply=True)
        self.api.mutations.clear()
        self.assertEqual(sync.reconcile(self.api, apply=True), [])
        self.assertEqual(self.api.mutations, [])

    def test_existing_ready_view_is_reconciled_not_duplicated(self):
        self.api.items.append({"id": "V11", "number": 11, "name": sync.READY_NAME,
                               "filter": "bad", "layout": "TABLE"})
        sync.reconcile(self.api, apply=True)
        self.assertEqual([x[0] for x in self.api.mutations], ["update", "update"])
        self.assertEqual(self.api.items[2]["filter"], sync.READY_FILTER)

    def test_partial_create_is_recovered(self):
        self.api.items[0]["filter"] = sync.OWNED_FILTER
        self.api.items.append({"id": "V11", "number": 11, "name": sync.READY_NAME,
                               "filter": None, "layout": "TABLE"})
        sync.reconcile(self.api, apply=True)
        self.assertEqual(len(self.api.items), 3)
        self.assertEqual(self.api.items[2]["filter"], sync.READY_FILTER)

    def test_queue_cannot_be_replaced_by_another_view(self):
        self.api.items[0]["name"] = "Maintenance"
        with self.assertRaisesRegex(sync.ViewError, "no longer the expected"):
            sync.reconcile(self.api, apply=True)
        self.assertFalse(self.api.mutations)

    def test_view9_must_be_table(self):
        self.api.items[0]["layout"] = "BOARD"
        with self.assertRaisesRegex(sync.ViewError, "no longer"):
            sync.reconcile(self.api, apply=True)

    def test_duplicate_ready_views_refused(self):
        for i in range(2):
            self.api.items.append({"id": f"VR{i}", "number": 20 + i,
                                   "name": sync.READY_NAME, "filter": "", "layout": "TABLE"})
        with self.assertRaisesRegex(sync.ViewError, "Multiple"):
            sync.reconcile(self.api, apply=True)
        self.assertFalse(self.api.mutations)

    def test_unexpected_ready_view_layout_refused(self):
        self.api.items.append({"id": "VR", "number": 12,
                               "name": sync.READY_NAME, "filter": "", "layout": "BOARD"})
        with self.assertRaisesRegex(sync.ViewError, "not a table"):
            sync.reconcile(self.api, apply=True)

    def test_partial_pagination_reads_all_views(self):
        self.api.page_size = 1
        self.assertEqual(len(sync.views(self.api)), 2)
        sync.reconcile(self.api, apply=True)
        self.assertEqual(len(self.api.items), 3)

    def test_incomplete_page_is_not_accepted(self):
        normal = self.api
        def broken(query, args):
            x = normal(query, args)
            if query == sync.VIEWS_QUERY:
                x["user"]["projectV2"]["views"]["totalCount"] = 10
            return x
        with self.assertRaisesRegex(sync.ViewError, "Incomplete"):
            sync.views(broken)

    def test_project_identity_is_guarded(self):
        normal = self.api
        def wrong_project(query, args):
            x = normal(query, args)
            if query == sync.VIEWS_QUERY:
                x["user"]["projectV2"]["id"] = "other"
            return x
        with self.assertRaisesRegex(sync.ViewError, "identity"):
            sync.reconcile(wrong_project, apply=True)
        self.assertFalse(self.api.mutations)

    def test_post_write_verification_refuses_stale_output(self):
        self.api.fail_after_write = True
        with self.assertRaisesRegex(sync.ViewError, "post-write"):
            sync.reconcile(self.api, apply=True)
        self.assertEqual(len(self.api.mutations), 1)

    def test_cli_apply_refuses_outside_master(self):
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "fork/PRKS",
                                        "GITHUB_REF": "refs/heads/master"}):
            self.assertEqual(sync.main(["--apply"], transport=self.api), 1)
        self.assertFalse(self.api.mutations)

    def test_cli_check_does_not_require_master(self):
        self.assertEqual(sync.main([], transport=self.api), 0)
        self.assertFalse(self.api.mutations)

    def test_desired_filters_keep_ownership_separate_from_actionability(self):
        self.assertIn('agent:"Cursor Cloud","Claude Code","ChatGPT/Codex"',
                      sync.OWNED_FILTER)
        self.assertNotIn("status:", sync.OWNED_FILTER)
        self.assertNotIn("execution:", sync.OWNED_FILTER)
        self.assertIn("status:Ready", sync.READY_FILTER)
        self.assertIn("execution:Agent,Mixed", sync.READY_FILTER)
        self.assertNotIn("In Progress", sync.READY_FILTER)


if __name__ == "__main__":
    unittest.main()
