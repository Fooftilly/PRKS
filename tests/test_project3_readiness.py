"""Tests for scripts/project3_readiness.py and its workflow (#441).

``FakeGitHub`` answers the three read-only queries from synthetic fixtures; no
live API is called and no real Project #3 data is used.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "project3_readiness.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "project3-readiness.yml"

_spec = importlib.util.spec_from_file_location("project3_readiness", _SCRIPT)
assert _spec and _spec.loader
pr = importlib.util.module_from_spec(_spec)
sys.modules["project3_readiness"] = pr
_spec.loader.exec_module(pr)

NOW = datetime(2026, 10, 12, 2, 30, 0, tzinfo=timezone.utc)


def single(name: Optional[str]) -> Optional[dict]:
    return None if name is None else {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": name}


def issue_item(item_id: str, number: int, *, status: Optional[str] = None, execution: Optional[str] = None,
               agent: Optional[str] = None, state: str = "OPEN", repo: str = "Fooftilly/PRKS",
               assignees: tuple[str, ...] = (), archived: bool = False) -> dict:
    return {
        "id": item_id, "isArchived": archived,
        "status": single(status), "execution": single(execution), "agent": single(agent),
        "content": {"__typename": "Issue", "number": number, "state": state,
                    "repository": {"nameWithOwner": repo, "isPrivate": False},
                    "assignees": {"totalCount": len(assignees), "nodes": [{"login": a} for a in assignees]}},
    }


def pr_item(item_id: str, number: int) -> dict:
    return {"id": item_id, "isArchived": False, "status": single("Review"), "execution": None, "agent": None,
            "content": {"__typename": "PullRequest", "number": number,
                        "repository": {"nameWithOwner": "Fooftilly/PRKS"}}}


def open_issue(number: int, assignees: tuple[str, ...] = ()) -> dict:
    return {"number": number, "state": "OPEN",
            "assignees": {"totalCount": len(assignees), "nodes": [{"login": a} for a in assignees]}}


class FakeGitHub:
    """Serves the project, its item pages and the open-issue pages."""

    def __init__(self, items: list[list[dict]], issues: list[list[dict]], *, public: bool = True,
                 fields: Optional[dict[str, str]] = None) -> None:
        self.public = public
        self.items = items
        self.issues = issues
        self.fields = fields if fields is not None else {
            "Title": "TITLE", "Status": "SINGLE_SELECT", "Execution": "SINGLE_SELECT", "Agent": "SINGLE_SELECT"}
        self.calls: list[tuple[str, dict]] = []
        self.items_total: Optional[int] = None
        self.issues_total: Optional[int] = None
        self.override: dict[str, Any] = {}

    def project(self) -> dict:
        nodes = [{"__typename": "ProjectV2SingleSelectField" if t == "SINGLE_SELECT" else "ProjectV2Field",
                  "id": f"F_{n}", "name": n, "dataType": t} for n, t in self.fields.items()]
        return {"user": {"projectV2": {
            "id": pr.PROJECT_ID, "number": 3, "public": self.public, "closed": False,
            "owner": {"__typename": "User", "login": "Fooftilly"},
            "fields": {"totalCount": len(nodes), "nodes": nodes}}}}

    @staticmethod
    def _page(pages: list[list[dict]], after: Optional[str], total: int) -> dict:
        index = 0 if after is None else int(after.split("-")[1])
        more = index + 1 < len(pages)
        return {"totalCount": total, "nodes": pages[index],
                "pageInfo": {"hasNextPage": more, "endCursor": f"c-{index + 1}" if more else None}}

    def __call__(self, query: str, variables: dict) -> dict:
        self.calls.append((query, variables))
        if query is pr.PROJECT_QUERY:
            return self.override.get("project") or self.project()
        if query is pr.ITEMS_QUERY:
            total = self.items_total if self.items_total is not None else sum(map(len, self.items))
            page = self._page(self.items, variables["after"], total)
            return {"user": {"projectV2": {"id": pr.PROJECT_ID, "items": page}}}
        if query is pr.ISSUES_QUERY:
            total = self.issues_total if self.issues_total is not None else sum(map(len, self.issues))
            page = self._page(self.issues, variables["after"], total)
            return {"repository": {"nameWithOwner": "Fooftilly/PRKS", "isPrivate": False, "issues": page}}
        raise AssertionError(f"unexpected query {query[:40]!r}")


def snapshot(fake: FakeGitHub) -> dict:
    return pr.build_snapshot(fake, generated_at=NOW)


def rows(snap: dict) -> dict[int, dict]:
    return {r["number"]: r for r in snap["issues"]}


class SnapshotTests(unittest.TestCase):
    def test_reads_every_page_of_items_and_issues(self) -> None:
        fake = FakeGitHub(
            items=[[issue_item("I1", 1, status="Ready")], [issue_item("I2", 2, status="Backlog")],
                   [issue_item("I3", 3, status="In Progress", execution="Agent", agent="Claude Code",
                               assignees=("zed", "amy"))]],
            issues=[[open_issue(1), open_issue(2)], [open_issue(3, ("amy", "zed")), open_issue(4)]],
        )
        snap = snapshot(fake)
        got = rows(snap)
        self.assertEqual(sorted(got), [1, 2, 3, 4])
        self.assertEqual(got[3], {"number": 3, "state": "OPEN", "in_project": True, "project_item_id": "I3",
                                  "archived": False, "status": "In Progress", "execution": "Agent",
                                  "agent": "Claude Code", "assignees": ["amy", "zed"]})
        afters = [v["after"] for q, v in fake.calls if q is pr.ITEMS_QUERY]
        self.assertEqual(afters, [None, "c-1", "c-2"])
        self.assertEqual(snap["counts"], {"issues": 4, "in_project": 3, "open_not_in_project": 1,
                                          "skipped_items": {}})
        self.assertEqual(snap["generated_at"], "2026-10-12T02:30:00Z")
        self.assertEqual(snap["stale_after"], "2026-10-13T08:30:00Z")
        self.assertEqual(snap["project"]["id"], pr.PROJECT_ID)

    def test_absent_issue_differs_from_present_issue_with_blank_fields(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 10)]], issues=[[open_issue(10), open_issue(11)]])
        got = rows(snapshot(fake))
        self.assertEqual(got[10]["in_project"], True)
        self.assertEqual(got[10]["project_item_id"], "I1")
        self.assertEqual(got[11]["in_project"], False)
        self.assertIsNone(got[11]["project_item_id"])
        self.assertIsNone(got[11]["archived"])

    def test_null_status_execution_and_agent_stay_null(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 5)]], issues=[[open_issue(5)]])
        row = rows(snapshot(fake))[5]
        self.assertEqual((row["status"], row["execution"], row["agent"]), (None, None, None))
        text = json.dumps(snapshot(fake), sort_keys=True)
        self.assertIn('"execution": null', text)

    def test_closed_issue_on_board_is_kept_with_its_state(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 7, status="Done", state="CLOSED")]], issues=[[]])
        self.assertEqual(rows(snapshot(fake))[7]["state"], "CLOSED")

    def test_skips_pull_requests_drafts_redacted_and_other_repositories(self) -> None:
        fake = FakeGitHub(items=[[
            issue_item("I1", 1),
            pr_item("P1", 2),
            {"id": "D1", "isArchived": False, "status": None, "execution": None, "agent": None,
             "content": {"__typename": "DraftIssue"}},
            {"id": "R1", "isArchived": False, "status": None, "execution": None, "agent": None, "content": None},
            issue_item("O1", 1, repo="someone/else"),
        ]], issues=[[open_issue(1)]])
        snap = snapshot(fake)
        self.assertEqual(sorted(rows(snap)), [1])
        self.assertEqual(rows(snap)[1]["project_item_id"], "I1")
        self.assertEqual(snap["counts"]["skipped_items"],
                         {"draft_issues": 1, "other_repositories": 1, "pull_requests": 1, "redacted_or_unknown": 1})

    def test_duplicate_items_for_one_issue_fail_closed(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 1, status="Ready")], [issue_item("I2", 1, status="Inbox")]],
                          issues=[[open_issue(1)]])
        with self.assertRaisesRegex(pr.Malformed, "more than one Project #3 item"):
            snapshot(fake)

    def test_same_item_twice_fails_closed(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 1)], [issue_item("I1", 1)]], issues=[[open_issue(1)]])
        with self.assertRaisesRegex(pr.Malformed, "returned twice"):
            snapshot(fake)

    def test_output_is_deterministic(self) -> None:
        def make() -> FakeGitHub:
            return FakeGitHub(items=[[issue_item("I9", 9), issue_item("I2", 2, assignees=("b", "a"))]],
                              issues=[[open_issue(3), open_issue(2, ("a", "b"))]])
        first = json.dumps(snapshot(make()), sort_keys=True)
        self.assertEqual(first, json.dumps(snapshot(make()), sort_keys=True))
        self.assertEqual([r["number"] for r in json.loads(first)["issues"]], [2, 3, 9])


class FailClosedTests(unittest.TestCase):
    def fake(self) -> FakeGitHub:
        return FakeGitHub(items=[[issue_item("I1", 1)], [issue_item("I2", 2)]], issues=[[open_issue(1)]])

    def test_private_project_is_refused(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 1, status="Ready")]], issues=[[]], public=False)
        with self.assertRaises(pr.Confidential):
            snapshot(fake)
        # Nothing past the project check was read.
        self.assertEqual([q for q, _ in fake.calls], [pr.PROJECT_QUERY])

    def test_wrong_project_identity_is_refused(self) -> None:
        fake = self.fake()
        bad = fake.project()
        bad["user"]["projectV2"]["id"] = "PVT_other"
        fake.override["project"] = bad
        with self.assertRaisesRegex(pr.ReadinessError, "identity"):
            snapshot(fake)

    def test_missing_or_mistyped_field_is_refused(self) -> None:
        fake = FakeGitHub(items=[[]], issues=[[]], fields={"Status": "SINGLE_SELECT", "Agent": "SINGLE_SELECT"})
        with self.assertRaisesRegex(pr.ReadinessError, "no 'Execution' field"):
            snapshot(fake)
        fake = FakeGitHub(items=[[]], issues=[[]],
                          fields={"Status": "SINGLE_SELECT", "Execution": "TEXT", "Agent": "SINGLE_SELECT"})
        with self.assertRaisesRegex(pr.ReadinessError, "not single select"):
            snapshot(fake)

    def test_count_short_of_total_is_partial(self) -> None:
        fake = self.fake()
        fake.items_total = 3
        with self.assertRaisesRegex(pr.Malformed, "read 2 of totalCount 3"):
            snapshot(fake)

    def test_total_changing_between_pages_fails(self) -> None:
        fake = self.fake()
        original = fake.__call__

        def shifting(query: str, variables: dict) -> dict:
            data = original(query, variables)
            if query is pr.ITEMS_QUERY and variables["after"]:
                data["user"]["projectV2"]["items"]["totalCount"] = 5
            return data

        with self.assertRaisesRegex(pr.ReadinessError, "changed while it was read"):
            pr.build_snapshot(shifting, generated_at=NOW)

    def test_missing_end_cursor_and_repeated_cursor_fail(self) -> None:
        def no_cursor(query: str, variables: dict) -> dict:
            if query is pr.PROJECT_QUERY:
                return FakeGitHub([], []).project()
            return {"user": {"projectV2": {"id": pr.PROJECT_ID, "items": {
                "totalCount": 9, "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}}}}}
        with self.assertRaisesRegex(pr.Malformed, "endCursor"):
            pr.build_snapshot(no_cursor, generated_at=NOW)

        def loop(query: str, variables: dict) -> dict:
            if query is pr.PROJECT_QUERY:
                return FakeGitHub([], []).project()
            return {"user": {"projectV2": {"id": pr.PROJECT_ID, "items": {
                "totalCount": 9, "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "same"}}}}}
        with self.assertRaisesRegex(pr.Malformed, "repeated a cursor"):
            pr.build_snapshot(loop, generated_at=NOW)

    def test_malformed_nodes_fail(self) -> None:
        cases = [
            ("not an object", "x"),
            ("field value", {**issue_item("I1", 1), "status": {"__typename": "ProjectV2ItemFieldTextValue"}}),
            ("number", {**issue_item("I1", 1), "content": {**issue_item("I1", 1)["content"], "number": "1"}}),
            ("assignees", {**issue_item("I1", 1), "content": {
                **issue_item("I1", 1)["content"], "assignees": {"totalCount": 30, "nodes": []}}}),
        ]
        for label, node in cases:
            with self.subTest(label):
                with self.assertRaises(pr.Malformed):
                    snapshot(FakeGitHub(items=[[node]], issues=[[]]))

    def test_missing_data_objects_fail(self) -> None:
        with self.assertRaises(pr.Malformed):
            pr.build_snapshot(lambda q, v: {"user": None}, generated_at=NOW)
        with self.assertRaises(pr.Malformed):
            pr.build_snapshot(lambda q, v: [], generated_at=NOW)  # type: ignore[arg-type,return-value]

    def test_api_error_propagates(self) -> None:
        def boom(query: str, variables: dict) -> dict:
            raise pr.ReadinessError("GraphQL error: something broke")
        with self.assertRaisesRegex(pr.ReadinessError, "something broke"):
            pr.build_snapshot(boom, generated_at=NOW)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def opener(result: object) -> mock.Mock:
    """A stand-in opener whose open() returns or raises ``result``."""
    fake = mock.Mock()
    if isinstance(result, BaseException):
        fake.open.side_effect = result
    else:
        fake.open.return_value = result
    return fake


class TransportTests(unittest.TestCase):
    def test_rejected_token_is_not_configured_and_not_printed(self) -> None:
        for code in (401, 403):
            error = urllib.error.HTTPError(pr.GRAPHQL_URL, code, "nope", {}, None)  # type: ignore[arg-type]
            with self.subTest(code), mock.patch.object(pr, "_https_only_opener", return_value=opener(error)):
                with self.assertRaises(pr.NotConfigured) as caught:
                    pr.http_transport("ghp_secret")("query { viewer { login } }", {})
                self.assertNotIn("ghp_secret", str(caught.exception))

    def test_graphql_errors_and_bad_bodies(self) -> None:
        bodies = {b'{"errors":[{"message":"rate limited"}]}': "rate limited",
                  b"not json": "request failed", b"[1]": "not an object", b'{"data":null}': "no data"}
        for body, message in bodies.items():
            with self.subTest(body), mock.patch.object(pr, "_https_only_opener",
                                                       return_value=opener(FakeResponse(body))):
                with self.assertRaisesRegex(pr.ReadinessError, message):
                    pr.http_transport("t")("query { viewer { login } }", {})

    def test_opener_has_no_file_or_ftp_handler(self) -> None:
        handlers = {type(h) for h in pr._https_only_opener().handlers}
        self.assertNotIn(urllib.request.FileHandler, handlers)
        self.assertNotIn(urllib.request.FTPHandler, handlers)
        self.assertNotIn(urllib.request.HTTPHandler, handlers)
        self.assertTrue(pr.GRAPHQL_URL.startswith("https://"))


class ReadOnlyTests(unittest.TestCase):
    def test_mutations_and_subscriptions_are_refused_before_sending(self) -> None:
        sent: list[str] = []
        gql = pr.ReadOnlyGraphQL(lambda q, v: sent.append(q) or {})
        for document in (
            "mutation { addProjectV2ItemById(input: {}) { item { id } } }",
            "  # comment\n mutation M { updateProjectV2ItemFieldValue(input: {}) { clientMutationId } }",
            "MUTATION { x }",
            "subscription { x }",
            "query { a } mutation { b }",
            "fragment F on User { login }",
        ):
            with self.subTest(document), self.assertRaises(pr.ReadinessError):
                gql.query(document, {})
        self.assertEqual(sent, [])

    def test_every_shipped_query_is_read_only(self) -> None:
        for document in pr.QUERIES:
            self.assertTrue(pr.is_read_only(document))
        source = _SCRIPT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b(add|update|delete|clear|archive)ProjectV2", source))
        self.assertIsNone(re.search(r"\b(closeIssue|reopenIssue|addBlockedBy|removeBlockedBy|mergePullRequest)\b",
                                    source))

    def test_does_not_import_writing_modules(self) -> None:
        tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(imported & {"project_sync", "project3_migrate", "issue_dependency_writer"})

    def test_a_full_run_sends_only_queries(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 1)]], issues=[[open_issue(1)]])
        snapshot(fake)
        self.assertTrue(fake.calls)
        self.assertTrue(all(pr.is_read_only(q) for q, _ in fake.calls))


class FreshnessTests(unittest.TestCase):
    def snap(self) -> dict:
        return snapshot(FakeGitHub(items=[[issue_item("I1", 1)]], issues=[[open_issue(1)]]))

    def test_fresh_snapshot_passes(self) -> None:
        self.assertEqual(pr.check_snapshot(self.snap(), now=NOW + timedelta(hours=29)), NOW)

    def test_stale_snapshot_fails(self) -> None:
        with self.assertRaisesRegex(pr.ReadinessError, "stale"):
            pr.check_snapshot(self.snap(), now=NOW + timedelta(hours=30, seconds=1))
        with self.assertRaisesRegex(pr.ReadinessError, "stale"):
            pr.check_snapshot(self.snap(), now=NOW + timedelta(hours=3), max_age_hours=2)

    def test_future_incomplete_foreign_or_duplicate_snapshots_fail(self) -> None:
        with self.assertRaisesRegex(pr.ReadinessError, "future"):
            pr.check_snapshot(self.snap(), now=NOW - timedelta(minutes=10))
        for change, message in (
            ({"complete": False}, "not marked complete"),
            ({"schema": "other"}, "schema"),
            ({"repository": "x/y"}, "not for Project #3"),
            ({"generated_at": "yesterday"}, "timestamp"),
        ):
            with self.subTest(change), self.assertRaisesRegex(pr.ReadinessError, message):
                pr.check_snapshot({**self.snap(), **change}, now=NOW)
        dup = self.snap()
        dup["issues"] = dup["issues"] * 2
        with self.assertRaisesRegex(pr.Malformed, "twice"):
            pr.check_snapshot(dup, now=NOW)


    def test_structurally_invalid_rows_and_counts_fail(self) -> None:
        def with_row(change: dict, index: int = 0) -> dict:
            snap = snapshot(FakeGitHub(items=[[issue_item("I1", 1, status="Ready")]],
                                       issues=[[open_issue(1), open_issue(2)]]))
            row = snap["issues"][index]
            for key, value in change.items():
                if value is KeyError:
                    del row[key]
                else:
                    row[key] = value
            return snap

        cases = [
            ("bare row", {k: KeyError for k in ("state", "in_project", "project_item_id", "archived",
                                                 "status", "execution", "agent", "assignees")}),
            ("missing status", {"status": KeyError}),
            ("extra key", {"ready": True}),
            ("bad state", {"state": "MERGED"}),
            ("in_project not bool", {"in_project": "true"}),
            ("present without item id", {"project_item_id": None}),
            ("present without archived", {"archived": None}),
            ("empty status", {"status": ""}),
            ("numeric agent", {"agent": 3}),
            ("assignees not list", {"assignees": "amy"}),
            ("assignees unsorted", {"assignees": ["zed", "amy"]}),
        ]
        for label, change in cases:
            with self.subTest(label), self.assertRaises(pr.Malformed):
                pr.check_snapshot(with_row(change), now=NOW)
        for label, change in (("absent with status", {"status": "Ready"}),
                              ("absent with item id", {"project_item_id": "I9"}),
                              ("absent and closed", {"state": "CLOSED"})):
            with self.subTest(label), self.assertRaises(pr.Malformed):
                pr.check_snapshot(with_row(change, index=1), now=NOW)

        snap = with_row({})
        snap["counts"]["in_project"] = 2
        with self.assertRaisesRegex(pr.Malformed, "counts"):
            pr.check_snapshot(snap, now=NOW)
        snap = with_row({})
        snap["issues"].reverse()
        with self.assertRaisesRegex(pr.Malformed, "sorted"):
            pr.check_snapshot(snap, now=NOW)
        snap = with_row({})
        snap["fields"] = {"status": "Status"}
        with self.assertRaisesRegex(pr.Malformed, "fields"):
            pr.check_snapshot(snap, now=NOW)


    def test_download_is_decoded_by_content(self) -> None:
        snap = self.snap()
        text = json.dumps(snap).encode()
        self.assertEqual(pr.decode_download(text), snap)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("project3-readiness.json", text)
        self.assertEqual(pr.decode_download(buffer.getvalue()), snap)
        other = io.BytesIO()
        with zipfile.ZipFile(other, "w") as archive:
            archive.writestr("project3-readiness.json", text)
            archive.writestr("extra.json", b"{}")
        for label, raw in (("two members", other.getvalue()), ("damaged zip", b"PK\x03\x04broken"),
                           ("binary", b"\xff\xfe"), ("html", b"<html>redirect</html>")):
            with self.subTest(label), self.assertRaises(pr.Malformed):
                pr.decode_download(raw)

class MainTests(unittest.TestCase):
    def run_main(self, argv: list[str], env: dict, fake: Optional[FakeGitHub] = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pr.main(argv, env=env, transport_factory=lambda token: fake or FakeGitHub([[]], [[]]),
                           now=lambda: NOW)
        return code, out.getvalue(), err.getvalue()

    def test_missing_token_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "s.json"
            code, _, err = self.run_main(["snapshot", "--out", str(target)], env={})
            self.assertEqual(code, 2)
            self.assertIn("not-configured", err)
            self.assertFalse(target.exists())

    def test_private_project_exits_3_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "s.json"
            summary = Path(tmp) / "summary.md"
            code, _, err = self.run_main(
                ["snapshot", "--out", str(target), "--summary", str(summary)],
                env={"PROJECT3_READINESS_TOKEN": "t"},
                fake=FakeGitHub(items=[[issue_item("I1", 1, status="Ready")]], issues=[[]], public=False))
            self.assertEqual(code, 3)
            self.assertIn("refused", err)
            self.assertFalse(target.exists())
            self.assertFalse(summary.exists())

    def test_snapshot_written_with_summary_and_source(self) -> None:
        env = {"PROJECT3_READINESS_TOKEN": "ghp_secret", "GITHUB_REPOSITORY": "Fooftilly/PRKS",
               "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "abc", "GITHUB_EVENT_NAME": "schedule"}
        fake = FakeGitHub(items=[[issue_item("I1", 1, status="Ready")]], issues=[[open_issue(1), open_issue(2)]])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "s.json"
            summary = Path(tmp) / "summary.md"
            code, out, err = self.run_main(["snapshot", "--out", str(target), "--summary", str(summary)], env, fake)
            self.assertEqual(code, 0, err)
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data["source"]["workflow_run_id"], 123)
            self.assertIn("| Ready | 1 |", summary.read_text(encoding="utf-8"))
            self.assertNotIn("ghp_secret", out + err + target.read_text(encoding="utf-8"))
            code, out, _ = self.run_main(["check", "--snapshot", str(target)], {})
            self.assertEqual(code, 0)

    def test_failed_read_leaves_previous_file_untouched_and_no_temp(self) -> None:
        fake = FakeGitHub(items=[[issue_item("I1", 1)]], issues=[[open_issue(1)]])
        fake.items_total = 4
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "s.json"
            code, _, err = self.run_main(["snapshot", "--out", str(target)], {"PROJECT3_READINESS_TOKEN": "t"}, fake)
            self.assertEqual(code, 1)
            self.assertIn("totalCount", err)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_output_inside_repository_is_refused(self) -> None:
        code, _, err = self.run_main(["snapshot", "--out", str(_ROOT / "snapshot.json")],
                                     {"PROJECT3_READINESS_TOKEN": "t"})
        self.assertEqual(code, 1)
        self.assertIn("outside the repository", err)
        self.assertFalse((_ROOT / "snapshot.json").exists())


    def test_paths_outside_home_and_temp_are_refused(self) -> None:
        for argv in (["snapshot", "--out", "/etc/prks-snapshot.json"],
                     ["snapshot", "--out", str(Path(tempfile.gettempdir()) / "s.json"), "--summary", "/etc/summary.md"],
                     ["check", "--snapshot", "/etc/passwd.json"]):
            with self.subTest(argv):
                code, _, err = self.run_main(argv, {"PROJECT3_READINESS_TOKEN": "t"})
                self.assertEqual(code, 1)
                self.assertIn("must be under", err)


class WorkflowTests(unittest.TestCase):
    text = _WORKFLOW.read_text(encoding="utf-8")

    def test_minimal_permissions_and_no_write_scopes(self) -> None:
        self.assertIn("permissions: {}", self.text)
        self.assertIsNone(re.search(r":\s*write\b", self.text))
        self.assertIn("contents: read", self.text)

    def test_triggers_are_dispatch_and_schedule_only(self) -> None:
        on_block = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        triggers = re.findall(r"^  ([a-z_]+):", on_block, re.MULTILINE)
        self.assertEqual(triggers, ["workflow_dispatch", "schedule"])

    def test_token_only_in_snapshot_step_and_actions_pinned(self) -> None:
        self.assertEqual(self.text.count("secrets."), 1)
        self.assertIn("PROJECT3_READINESS_TOKEN: ${{ secrets.PROJECT_SYNC_TOKEN }}", self.text)
        for ref in re.findall(r"uses:\s*(\S+)", self.text):
            self.assertRegex(ref, r"@[0-9a-f]{40}$")
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("archive: false", self.text)
        self.assertNotIn("echo", self.text)


if __name__ == "__main__":
    unittest.main()
