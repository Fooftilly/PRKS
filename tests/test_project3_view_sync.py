"""Contract tests for the Project #3 Agent Queue ownership filter."""
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
             "layout": "TABLE_LAYOUT"},
            {"id": "V7", "number": 7, "name": "Active Work",
             "filter": "status:Review", "layout": "TABLE_LAYOUT"},
        ]
        self.mutations: list[dict] = []
        self.fail_after_write = False
        self.page_size = 100

    def __call__(self, document, variables):
        if document == sync.VIEWS_QUERY:
            start = int(variables["after"]) if variables.get("after") else 0
            end = start + self.page_size
            return {"user": {"projectV2": {
                "id": sync.PROJECT_ID, "number": sync.PROJECT_NUMBER,
                "owner": {"__typename": "User", "login": sync.OWNER},
                "views": {"totalCount": len(self.items),
                          "nodes": copy.deepcopy(self.items[start:end]),
                          "pageInfo": {"hasNextPage": end < len(self.items),
                                       "endCursor": str(end) if end < len(self.items) else None}}
            }}}
        if document == sync.UPDATE_VIEW:
            values = variables["input"]
            item = next(item for item in self.items if item["id"] == values["viewId"])
            self.mutations.append(copy.deepcopy(values))
            if not self.fail_after_write:
                item["filter"] = values["filter"]
            return {"updateProjectV2View": {"projectV2View": copy.deepcopy(item)}}
        raise AssertionError("unexpected GraphQL document")


class AgentQueueViewTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGraphQL()

    def test_check_is_read_only_with_legacy_filter(self):
        self.assertEqual(len(sync.reconcile(self.api, apply=False)), 1)
        self.assertEqual(self.api.mutations, [])

    def test_apply_reconciles_only_filter_and_preserves_other_views(self):
        previous = copy.deepcopy(self.api.items[1])
        self.assertEqual(len(sync.reconcile(self.api, apply=True)), 1)
        self.assertEqual(self.api.items[0]["filter"], sync.OWNED_FILTER)
        self.assertEqual(self.api.items[1], previous)
        self.assertEqual(len(self.api.mutations), 1)
        self.assertEqual(set(self.api.mutations[0]), {"viewId", "filter"})

    def test_second_apply_is_idempotent(self):
        sync.reconcile(self.api, apply=True)
        self.api.mutations.clear()
        self.assertEqual(sync.reconcile(self.api, apply=True), [])
        self.assertEqual(self.api.mutations, [])

    def test_queue_number_and_name_are_both_guarded(self):
        self.api.items[0]["name"] = "Maintenance"
        with self.assertRaisesRegex(sync.ViewError, "no longer the expected"):
            sync.reconcile(self.api, apply=True)
        self.assertEqual(self.api.mutations, [])

    def test_view9_requires_actual_table_layout_enum(self):
        self.api.items[0]["layout"] = "BOARD_LAYOUT"
        with self.assertRaisesRegex(sync.ViewError, "no longer"):
            sync.reconcile(self.api, apply=True)

    def test_partial_pagination_reads_all_views(self):
        self.api.page_size = 1
        self.assertEqual(len(sync.views(self.api)), 2)
        sync.reconcile(self.api, apply=True)
        self.assertEqual(len(self.api.mutations), 1)

    def test_incomplete_page_is_not_accepted(self):
        normal = self.api
        def incomplete(query, args):
            x = normal(query, args)
            if query == sync.VIEWS_QUERY:
                x["user"]["projectV2"]["views"]["totalCount"] = 10
            return x
        with self.assertRaisesRegex(sync.ViewError, "Incomplete"):
            sync.views(incomplete)

    def test_duplicate_view_number_is_rejected(self):
        self.api.items.append({**self.api.items[1], "id": "V100"})
        with self.assertRaisesRegex(sync.ViewError, "Duplicate"):
            sync.reconcile(self.api, apply=True)

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

    def test_strict_read_only_check_fails_on_drift(self):
        self.assertEqual(sync.main(["--fail-on-drift"], transport=self.api), 1)
        self.assertFalse(self.api.mutations)
        sync.reconcile(self.api, apply=True)
        self.api.mutations.clear()
        self.assertEqual(sync.main(["--fail-on-drift"], transport=self.api), 0)
        self.assertFalse(self.api.mutations)

    def test_strict_check_cannot_be_combined_with_apply(self):
        self.assertEqual(sync.main(["--apply", "--fail-on-drift"], transport=self.api), 1)
        self.assertFalse(self.api.mutations)

    def test_agent_ownership_visible_without_inventing_readiness(self):
        self.assertIn('agent:"Cursor Cloud","Claude Code","ChatGPT/Codex"',
                      sync.OWNED_FILTER)
        self.assertNotIn("status:", sync.OWNED_FILTER)
        self.assertNotIn("execution:", sync.OWNED_FILTER)
        self.assertIn("is:issue", sync.OWNED_FILTER)
        self.assertNotIn("-label:dependencies", sync.OWNED_FILTER)


if __name__ == "__main__":
    unittest.main()
