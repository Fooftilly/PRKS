"""Unit coverage for the PR review watcher. No GitHub or model calls."""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_PROJECT_DIR, "tools", "pr_review")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

pr_review = importlib.import_module("pr_review")
clients = importlib.import_module("clients")
main = importlib.import_module("main")


def _pull(**overrides):
    data = dict(
        number=7,
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=False,
        labels=(),
        title="Adjust sync",
        body="Untrusted description",
        same_repository=True,
    )
    data.update(overrides)
    return pr_review.Pull(**data)


def _diff(path, old, new):
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def _model(findings=None, resolved=None):
    return json.dumps(
        {
            "summary": "",
            "findings": findings or [],
            "resolved_fingerprints": resolved or [],
        }
    )


def _finding(path="backend/sync_protocol.py", title="Revision not advanced", line=1):
    return {
        "path": path,
        "line": line,
        "side": "RIGHT",
        "severity": "blocking",
        "title": title,
        "body": "The column write leaves the previous revision in place.",
    }


class FakeGitHub:
    def __init__(self, pull=None):
        self.pulls = [pull or _pull()]
        self._pull_index = 0
        self.commits = [self.pulls[0].base_sha, self.pulls[0].head_sha]
        self.full_diff = _diff("backend/sync_protocol.py", "old", "new")
        self.compare = None
        self.compare_calls = []
        self.files = {}
        self.fingerprints = set()
        self.resolved = set()
        self.ledger = None
        self.saved = []
        self.reviews = []
        self.replies = []
        self.save_error = None
        self.fail = None

    def get_pull(self):
        if self.fail == "pull":
            raise pr_review.ReviewServiceError("github")
        index = min(self._pull_index, len(self.pulls) - 1)
        self._pull_index += 1
        return self.pulls[index]

    def load_ledger(self):
        if self.fail == "ledger":
            raise pr_review.ReviewServiceError("github")
        return self.ledger

    def commit_shas(self):
        return list(self.commits)

    def pull_diff(self):
        return self.full_diff

    def compare_diff(self, base, head):
        self.compare_calls.append((base, head))
        return self.compare

    def file_texts(self, paths):
        return {path: self.files[path] for path in paths if path in self.files}

    def existing_fingerprints(self):
        return set(self.fingerprints)

    def existing_resolved(self):
        return set(self.resolved)

    def create_review(self, commit_id, body, comments):
        if self.fail == "review":
            raise pr_review.ReviewServiceError("github", status=422)
        self.reviews.append({"commit_id": commit_id, "body": body, "comments": comments})
        return {comment["fingerprint"]: index + 1 for index, comment in enumerate(comments)}

    def reply(self, comment_id, body):
        self.replies.append((comment_id, body))

    def save_ledger(self, body):
        if self.save_error:
            raise self.save_error
        self.saved.append(body)


class ReviewDecisionTests(unittest.TestCase):
    def test_skip_reasons(self):
        common = dict(
            enabled=True,
            draft=False,
            labels=(),
            same_repository=True,
            review_forks=True,
            head_sha="a" * 40,
            last_sha=None,
            force=False,
        )
        self.assertIsNone(pr_review.skip_reason(**common))
        self.assertEqual(pr_review.skip_reason(**{**common, "enabled": False}), "disabled")
        self.assertEqual(pr_review.skip_reason(**{**common, "draft": True}), "draft")
        self.assertEqual(
            pr_review.skip_reason(**{**common, "labels": (pr_review.SKIP_LABEL,)}),
            "skip-label",
        )
        self.assertEqual(
            pr_review.skip_reason(**{**common, "same_repository": False, "review_forks": False}),
            "fork",
        )
        self.assertEqual(
            pr_review.skip_reason(**{**common, "last_sha": "a" * 40}),
            "already-reviewed",
        )
        self.assertIsNone(pr_review.skip_reason(**{**common, "last_sha": "a" * 40, "force": True}))

    def test_span_is_incremental_only_when_the_previous_sha_is_in_the_pr(self):
        head = "c" * 40
        previous = "b" * 40
        base = "a" * 40
        commits = [base, previous, head]
        span = pr_review.choose_span(
            base_sha=base,
            head_sha=head,
            last_sha=previous,
            commit_shas=commits,
            force=False,
        )
        self.assertEqual(span.mode, "incremental")
        self.assertEqual(span.from_sha, previous)
        forced = pr_review.choose_span(
            base_sha=base,
            head_sha=previous,
            last_sha=previous,
            commit_shas=commits,
            force=True,
        )
        self.assertEqual(forced.reason, "forced")
        rewritten = pr_review.choose_span(
            base_sha=base,
            head_sha=head,
            last_sha="d" * 40,
            commit_shas=commits,
            force=False,
        )
        self.assertEqual(rewritten.reason, "last-review-not-in-pr")
        self.assertEqual(rewritten.mode, "full")

    def test_fingerprint_ignores_line_and_wording_case(self):
        first = pr_review.fingerprint("backend/a.py", "Revision Not Advanced")
        second = pr_review.fingerprint("backend/a.py", "revision not advanced")
        moved = pr_review.fingerprint("backend/a.py", "Revision not advanced")
        other = pr_review.fingerprint("backend/a.py", "Different defect")
        self.assertEqual(first, second)
        self.assertEqual(first, moved)
        self.assertNotEqual(first, other)

    def test_ignored_paths_match_vendor_and_binaries(self):
        self.assertTrue(pr_review.is_ignored_path("frontend/vendor/vue.js"))
        self.assertTrue(pr_review.is_ignored_path("docs/screenshots/home.png"))
        self.assertFalse(pr_review.is_ignored_path("backend/sync_protocol.py"))
        self.assertFalse(pr_review.is_ignored_path(".github/workflows/pr-review.yml"))


class DiffParserTests(unittest.TestCase):
    def test_modified_context_and_added_lines_are_commentable(self):
        text = (
            "diff --git a/backend/a.py b/backend/a.py\n"
            "--- a/backend/a.py\n"
            "+++ b/backend/a.py\n"
            "@@ -10,3 +10,4 @@\n"
            " context\n"
            "-old\n"
            "+new\n"
            " context2\n"
        )
        parsed = pr_review.parse_unified_diff(text)
        self.assertEqual(parsed.commentable["backend/a.py"]["RIGHT"], {10, 11, 12})
        self.assertEqual(parsed.commentable["backend/a.py"]["LEFT"], {11})
        self.assertFalse(parsed.files[0].deleted)

    def test_deleted_file_uses_the_left_side(self):
        text = (
            "diff --git a/backend/old.py b/backend/old.py\n"
            "--- a/backend/old.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-gone\n"
        )
        parsed = pr_review.parse_unified_diff(text)
        self.assertIn("backend/old.py", parsed.deleted_paths)
        self.assertEqual(parsed.commentable["backend/old.py"]["LEFT"], {1})
        self.assertTrue(parsed.files[0].deleted)

    def test_vendor_only_diff_is_not_meaningful(self):
        parsed = pr_review.parse_unified_diff(_diff("frontend/vendor/x.js", "a", "b"))
        self.assertEqual(pr_review.meaningful_files(parsed.files), [])


class LedgerTests(unittest.TestCase):
    def test_round_trip_keeps_a_comment_closer_inside_a_title(self):
        finding = pr_review.stored_finding(
            {**_finding(), "title": "keep a --> b", "fingerprint": "a" * 16},
            status="open",
            fixed_sha=None,
            comment_id=9,
        )
        state = pr_review.advance_state(
            None,
            head_sha="abc1234",
            reviewed_at="2026-09-23T00:00:00Z",
            criteria="criteria text",
            findings=[finding],
        )
        parsed = pr_review.parse_ledger(pr_review.embed_ledger(state))
        self.assertEqual(parsed["head_sha"], "abc1234")
        self.assertEqual(parsed["findings"][0]["title"], "keep a --> b")
        self.assertEqual(parsed["findings"][0]["comment_id"], 9)

    def test_corrupt_ledger_is_ignored(self):
        self.assertIsNone(pr_review.parse_ledger("no marker"))
        self.assertIsNone(
            pr_review.parse_ledger(f"<!-- {pr_review.LEDGER_MARKER} {{not json}} -->")
        )


class PartitionTests(unittest.TestCase):
    def _open(self, path="backend/sync_protocol.py"):
        fingerprint = pr_review.fingerprint(path, "Revision not advanced")
        return {
            "fingerprint": fingerprint,
            "path": path,
            "line": 4,
            "side": "RIGHT",
            "severity": "blocking",
            "title": "Revision not advanced",
            "body": "old",
            "status": "open",
            "fixed_sha": None,
            "comment_id": 15,
        }

    def test_open_finding_is_not_posted_again(self):
        current = self._open()
        incoming = pr_review.parse_model_finding(_finding())
        partition = pr_review.partition_findings(
            previous=[current],
            model_findings=[incoming],
            resolved_fingerprints=[],
            touched_paths={current["path"]},
            known_paths={current["path"]},
            already_posted=set(),
        )
        self.assertEqual(partition.to_post, ())
        self.assertEqual(partition.repeats[0]["fingerprint"], current["fingerprint"])

    def test_resolution_requires_the_delta_to_touch_the_file(self):
        current = self._open()
        untouched = pr_review.partition_findings(
            previous=[current],
            model_findings=[],
            resolved_fingerprints=[current["fingerprint"]],
            touched_paths={"backend/other.py"},
            known_paths={current["path"], "backend/other.py"},
            already_posted=set(),
        )
        self.assertEqual(untouched.resolved, ())
        touched = pr_review.partition_findings(
            previous=[current],
            model_findings=[],
            resolved_fingerprints=[current["fingerprint"]],
            touched_paths={current["path"]},
            known_paths={current["path"]},
            already_posted=set(),
        )
        self.assertEqual(touched.resolved[0]["fingerprint"], current["fingerprint"])

    def test_fixed_finding_that_returns_is_a_regression_even_if_the_old_comment_exists(self):
        current = {**self._open(), "status": "fixed", "fixed_sha": "b" * 40}
        incoming = pr_review.parse_model_finding(_finding())
        partition = pr_review.partition_findings(
            previous=[current],
            model_findings=[incoming],
            resolved_fingerprints=[],
            touched_paths={current["path"]},
            known_paths={current["path"]},
            already_posted={current["fingerprint"]},
        )
        self.assertTrue(partition.to_post[0]["regression"])

    def test_posted_marker_suppresses_a_duplicate_when_the_ledger_was_lost(self):
        incoming = pr_review.parse_model_finding(_finding())
        partition = pr_review.partition_findings(
            previous=[],
            model_findings=[incoming],
            resolved_fingerprints=[],
            touched_paths={incoming["path"]},
            known_paths={incoming["path"]},
            already_posted={incoming["fingerprint"]},
        )
        self.assertEqual(partition.to_post, ())

    def test_unknown_path_and_style_severity_are_dropped(self):
        payload = pr_review.parse_model_payload(
            _model(
                [
                    _finding(path="../secrets.env"),
                    {**_finding(title="Rename this"), "severity": "nit"},
                    _finding(),
                ]
            )
        )
        self.assertEqual([item["title"] for item in payload["findings"]], ["Revision not advanced"])


class ModelPayloadTests(unittest.TestCase):
    def test_fenced_and_prefixed_json_parse(self):
        body = "```json\n" + _model() + "\n```"
        self.assertEqual(pr_review.parse_model_payload(body)["findings"], [])
        prefixed = "Review follows " + _model(resolved=["ab" * 8])
        self.assertEqual(
            pr_review.parse_model_payload(prefixed)["resolved_fingerprints"],
            ["ab" * 8],
        )
        self.assertIsNone(pr_review.parse_model_payload("not json"))
        self.assertIsNone(pr_review.parse_model_payload('{"findings": []}'))


class PromptTests(unittest.TestCase):
    def test_secret_in_the_diff_aborts_before_the_model(self):
        github = FakeGitHub()
        github.full_diff = _diff("backend/sync_protocol.py", "old", "token-secret-value")
        calls = []

        def complete(system, user):
            calls.append(user)
            return _model()

        outcome = pr_review.run_review(
            github,
            complete,
            pr_review.ReviewConfig(secrets=("token-secret-value",), criteria="criteria"),
        )
        self.assertEqual(outcome.reason, "secret-in-prompt")
        self.assertEqual(calls, [])
        self.assertEqual(github.saved, [])

    def test_on_disk_criteria_is_the_system_prompt(self):
        github = FakeGitHub()
        seen = {}

        def complete(system, user):
            seen["system"] = system
            seen["user"] = user
            return _model()

        outcome = pr_review.run_review(
            github,
            complete,
            pr_review.ReviewConfig(criteria="", now="2026-09-23T00:00:00Z"),
        )
        self.assertEqual(outcome.status, "reviewed")
        self.assertIn("CodeRabbit", seen["system"])
        self.assertIn("local-first-rollout-status.md", seen["system"])
        self.assertIn("resolved_fingerprints", seen["system"])
        self.assertIn("Untrusted PR title", seen.get("user", "") or "")


class RunReviewTests(unittest.TestCase):
    def test_same_revision_does_not_call_the_model(self):
        github = FakeGitHub()
        fingerprint = pr_review.fingerprint("backend/sync_protocol.py", "Revision not advanced")
        github.ledger = pr_review.LoadedLedger(
            3,
            pr_review.advance_state(
                None,
                head_sha=github.pulls[0].head_sha,
                reviewed_at="t",
                criteria="c",
                findings=[],
            ),
        )
        calls = []
        outcome = pr_review.run_review(
            github,
            lambda system, user: calls.append(1) or _model(),
            pr_review.ReviewConfig(criteria="c"),
        )
        self.assertEqual(outcome.reason, "already-reviewed")
        self.assertEqual(calls, [])
        self.assertNotIn(fingerprint, github.fingerprints)

    def test_incremental_review_posts_only_a_new_finding(self):
        github = FakeGitHub()
        previous = "b" * 40
        github.commits = [github.pulls[0].base_sha, previous, github.pulls[0].head_sha]
        github.ledger = pr_review.LoadedLedger(
            4,
            {
                "version": 1,
                "head_sha": previous,
                "reviewed_at": "t",
                "criteria": "c",
                "findings": [],
            },
        )
        github.compare = _diff("backend/work_open_sync.py", "old", "stamp read")
        github.full_diff = github.compare
        outcome = pr_review.run_review(
            github,
            lambda system, user: _model([_finding(path="backend/work_open_sync.py", title="Open stamps a read")]),
            pr_review.ReviewConfig(criteria="criteria"),
        )
        self.assertEqual(outcome.status, "posted")
        self.assertEqual(github.compare_calls, [(previous, github.pulls[0].head_sha)])
        self.assertEqual(len(github.reviews), 1)
        self.assertIn("**Blocking.**", github.reviews[0]["comments"][0]["body"])
        self.assertIn("prks-finding:", github.reviews[0]["comments"][0]["body"])
        saved = pr_review.parse_ledger(github.saved[-1])
        self.assertEqual(saved["head_sha"], github.pulls[0].head_sha)
        self.assertEqual(saved["findings"][0]["status"], "open")
        self.assertEqual(saved["findings"][0]["comment_id"], 1)

    def test_repeat_finding_updates_the_ledger_without_a_new_comment(self):
        github = FakeGitHub()
        previous = "b" * 40
        open_finding = {
            "fingerprint": pr_review.fingerprint("backend/sync_protocol.py", "Revision not advanced"),
            "path": "backend/sync_protocol.py",
            "line": 1,
            "side": "RIGHT",
            "severity": "blocking",
            "title": "Revision not advanced",
            "body": "old",
            "status": "open",
            "fixed_sha": None,
            "comment_id": 15,
        }
        github.commits = [github.pulls[0].base_sha, previous, github.pulls[0].head_sha]
        github.ledger = pr_review.LoadedLedger(
            4,
            {
                "version": 1,
                "head_sha": previous,
                "findings": [open_finding],
            },
        )
        github.compare = github.full_diff
        outcome = pr_review.run_review(
            github,
            lambda system, user: _model([_finding()]),
            pr_review.ReviewConfig(criteria="criteria"),
        )
        self.assertEqual(outcome.reason, "no-new-findings")
        self.assertEqual(github.reviews, [])
        saved = pr_review.parse_ledger(github.saved[-1])
        self.assertEqual(saved["findings"][0]["status"], "open")
        self.assertEqual(saved["findings"][0]["comment_id"], 15)

    def test_resolved_finding_is_replied_once_and_not_kept_active(self):
        github = FakeGitHub()
        previous = "b" * 40
        open_finding = {
            "fingerprint": pr_review.fingerprint("backend/sync_protocol.py", "Revision not advanced"),
            "path": "backend/sync_protocol.py",
            "line": 1,
            "side": "RIGHT",
            "severity": "blocking",
            "title": "Revision not advanced",
            "body": "old",
            "status": "open",
            "fixed_sha": None,
            "comment_id": 15,
        }
        github.commits = [github.pulls[0].base_sha, previous, github.pulls[0].head_sha]
        github.ledger = pr_review.LoadedLedger(4, {"version": 1, "head_sha": previous, "findings": [open_finding]})
        github.compare = github.full_diff
        outcome = pr_review.run_review(
            github,
            lambda system, user: _model(resolved=[open_finding["fingerprint"]]),
            pr_review.ReviewConfig(criteria="criteria"),
        )
        self.assertEqual(outcome.status, "posted")
        self.assertEqual(github.replies[0][0], 15)
        self.assertIn("prks-resolved:", github.replies[0][1])
        saved = pr_review.parse_ledger(github.saved[-1])
        self.assertEqual(saved["findings"][0]["status"], "fixed")

    def test_ignored_paths_advance_the_ledger_without_a_model_call(self):
        github = FakeGitHub()
        github.full_diff = _diff("docs/screenshots/home.png", "a", "b")
        calls = []
        outcome = pr_review.run_review(
            github,
            lambda system, user: calls.append(1) or _model(),
            pr_review.ReviewConfig(criteria="criteria"),
        )
        self.assertEqual(outcome.reason, "no-meaningful-changes")
        self.assertEqual(calls, [])
        self.assertEqual(pr_review.parse_ledger(github.saved[-1])["head_sha"], github.pulls[0].head_sha)

    def test_model_failure_does_not_record_the_revision(self):
        github = FakeGitHub()

        def complete(system, user):
            raise pr_review.ReviewServiceError("model", status=503)

        outcome = pr_review.run_review(github, complete, pr_review.ReviewConfig(criteria="criteria"))
        self.assertEqual(outcome.reason, "model-unavailable")
        self.assertEqual(github.saved, [])

    def test_unreadable_model_output_is_retried_once(self):
        github = FakeGitHub()
        calls = []

        def complete(system, user):
            calls.append(1)
            return "no json here"

        outcome = pr_review.run_review(github, complete, pr_review.ReviewConfig(criteria="criteria"))
        self.assertEqual(outcome.reason, "model-output")
        self.assertEqual(calls, [1, 1])
        self.assertEqual(github.saved, [])

    def test_head_move_during_review_posts_nothing(self):
        github = FakeGitHub()
        github.pulls.append(_pull(head_sha="c" * 40))
        outcome = pr_review.run_review(
            github,
            lambda system, user: _model([_finding()]),
            pr_review.ReviewConfig(criteria="criteria"),
        )
        self.assertEqual(outcome.reason, "head-moved")
        self.assertEqual(github.reviews, [])
        self.assertEqual(github.saved, [])

    def test_dry_run_prints_findings_without_posting(self):
        github = FakeGitHub()
        outcome = pr_review.run_review(
            github,
            lambda system, user: _model([_finding()]),
            pr_review.ReviewConfig(criteria="criteria", dry_run=True),
        )
        self.assertEqual(outcome.reason, "dry-run")
        self.assertEqual(github.reviews, [])
        self.assertEqual(github.saved, [])
        self.assertEqual(outcome.report["findings"][0]["severity"], "blocking")

    def test_incremental_deletion_is_not_attached_with_base_coordinates(self):
        github = FakeGitHub()
        previous = "b" * 40
        github.commits = [github.pulls[0].base_sha, previous, github.pulls[0].head_sha]
        github.ledger = pr_review.LoadedLedger(
            4,
            {"version": 1, "head_sha": previous, "findings": []},
        )
        github.compare = (
            "diff --git a/backend/sync_protocol.py b/backend/sync_protocol.py\n"
            "--- a/backend/sync_protocol.py\n"
            "+++ b/backend/sync_protocol.py\n"
            "@@ -1 +0,0 @@\n"
            "-gone\n"
        )
        github.full_diff = github.compare
        finding = _finding()
        finding["side"] = "LEFT"
        finding["title"] = "Deleted the revision check"
        outcome = pr_review.run_review(
            github,
            lambda system, user: _model([finding]),
            pr_review.ReviewConfig(criteria="criteria"),
        )
        self.assertEqual(outcome.status, "posted")
        self.assertEqual(github.reviews[0]["comments"], [])
        self.assertIn("Could not attach these to a changed line:", github.reviews[0]["body"])
        self.assertIn("prks-finding:", github.reviews[0]["body"])

    def test_mention_in_model_text_is_not_left_as_a_mention(self):
        parsed = pr_review.parse_model_finding(
            {**_finding(), "body": "Ask @octocat to look."}
        )
        self.assertNotIn("@octocat", parsed["body"])
        self.assertIn("@\u200boctocat", parsed["body"])


class ClientTests(unittest.TestCase):
    def test_review_falls_back_when_an_inline_line_is_rejected(self):
        transport = ScriptTransport(
            [
                (422, {"message": "diff line"}),
                (200, {"id": 3, "comments": []}),
            ]
        )
        client = clients.GitHubClient(
            transport=transport,
            repository="Fooftilly/PRKS",
            token="github-token-value",
            number=7,
        )
        result = client.create_review(
            "a" * 40,
            "summary",
            [
                {
                    "path": "backend/a.py",
                    "line": 9,
                    "side": "RIGHT",
                    "body": "<!-- prks-finding: abcdefabcdefabcd -->",
                    "fingerprint": "abcdefabcdefabcd",
                }
            ],
        )
        self.assertEqual(result, {})
        second = json.loads(transport.calls[1][3].decode("utf-8"))
        self.assertNotIn("comments", second)
        self.assertIn("prks-finding:", second["body"])
        self.assertNotIn("github-token-value", transport.calls[1][1])

    def test_ledger_save_refuses_to_overwrite_another_run(self):
        first = pr_review.embed_ledger(
            {"version": 1, "head_sha": "a" * 40, "reviewed_at": "", "criteria": "", "findings": []}
        )
        second = pr_review.embed_ledger(
            {"version": 1, "head_sha": "b" * 40, "reviewed_at": "", "criteria": "", "findings": []}
        )
        comment = {
            "id": 5,
            "user": {"login": "github-actions[bot]"},
            "body": first,
        }
        moved = {**comment, "body": second}
        human = {
            "id": 9,
            "user": {"login": "octocat"},
            "body": first,
        }
        transport = ScriptTransport(
            [
                (200, [human, comment]),
                (200, [moved]),
            ]
        )
        client = clients.GitHubClient(
            transport=transport,
            repository="Fooftilly/PRKS",
            token="github-token-value",
            number=7,
        )
        loaded = client.load_ledger()
        self.assertEqual(loaded.comment_id, 5)
        with self.assertRaises(pr_review.LedgerConflict):
            client.save_ledger("updated")
        self.assertEqual(len(transport.calls), 2)

    def test_model_retries_a_server_error_and_a_rejected_reasoning_field(self):
        content = _model()
        busy = ScriptTransport(
            [
                (503, {"error": "busy"}),
                (200, {"choices": [{"message": {"content": content}}]}),
            ]
        )
        slept = []
        client = clients.XaiClient(
            transport=busy,
            api_key="xai-secret-value",
            model="grok-4.6",
            reasoning="medium",
            api_url="https://api.x.ai/v1/chat/completions",
            sleep=lambda seconds: slept.append(seconds),
        )
        self.assertEqual(client.complete("system", "user"), content)
        self.assertEqual(slept, [2])

        rejected = ScriptTransport(
            [
                (400, {"error": "unknown field reasoning_effort"}),
                (200, {"choices": [{"message": {"content": content}}]}),
            ]
        )
        client = clients.XaiClient(
            transport=rejected,
            api_key="xai-secret-value",
            model="grok-4.6",
            reasoning="medium",
            api_url="https://api.x.ai/v1/chat/completions",
            sleep=lambda seconds: None,
        )
        self.assertEqual(client.complete("system", "user"), content)
        second = json.loads(rejected.calls[1][3].decode("utf-8"))
        self.assertNotIn("reasoning_effort", second)
        self.assertEqual(second["model"], "grok-4.6")

    def test_model_auth_failure_is_not_retried(self):
        transport = ScriptTransport([(401, {"error": "bad key"})])
        client = clients.XaiClient(
            transport=transport,
            api_key="xai-secret-value",
            model="grok-4.6",
            reasoning="medium",
            api_url="https://api.x.ai/v1/chat/completions",
            sleep=lambda seconds: (_ for _ in ()).throw(AssertionError("slept")),
        )
        with self.assertRaises(pr_review.ReviewServiceError):
            client.complete("system", "user")
        self.assertEqual(len(transport.calls), 1)

    def test_pull_payload_distinguishes_a_fork(self):
        same = clients.pull_from_api(
            {
                "draft": False,
                "title": "t",
                "body": None,
                "labels": [{"name": "docs"}],
                "head": {"sha": "a" * 40, "repo": {"full_name": "Fooftilly/PRKS"}},
                "base": {"sha": "b" * 40, "repo": {"full_name": "Fooftilly/PRKS"}},
            },
            7,
        )
        self.assertTrue(same.same_repository)
        forked = clients.pull_from_api(
            {
                "draft": True,
                "title": "t",
                "body": "",
                "labels": [],
                "head": {"sha": "c" * 40, "repo": {"full_name": "other/PRKS"}},
                "base": {"sha": "b" * 40, "repo": {"full_name": "Fooftilly/PRKS"}},
            },
            8,
        )
        self.assertFalse(forked.same_repository)
        self.assertTrue(forked.draft)


class ScriptTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        status, payload = self.responses.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        return status, payload, {}


class MainTests(unittest.TestCase):
    def test_missing_configuration_fails_closed(self):
        self.assertEqual(main.main({"GITHUB_REPOSITORY": "nope", "PR_NUMBER": "1"}), 1)
        self.assertEqual(
            main.main({"GITHUB_REPOSITORY": "Fooftilly/PRKS", "PR_NUMBER": "0"}),
            1,
        )
        self.assertEqual(
            main.main(
                {
                    "GITHUB_REPOSITORY": "Fooftilly/PRKS",
                    "PR_NUMBER": "1",
                    "PRKS_REVIEW_API_URL": "http://example.invalid/v1",
                    "GH_TOKEN": "x" * 12,
                }
            ),
            1,
        )

    def test_disabled_and_missing_key_do_not_require_a_network(self):
        env = {
            "GITHUB_REPOSITORY": "Fooftilly/PRKS",
            "PR_NUMBER": "12",
            "GH_TOKEN": "g" * 12,
            "PRKS_REVIEW_ENABLED": "false",
        }
        self.assertEqual(main.main(env), 0)
        env["PRKS_REVIEW_ENABLED"] = "true"
        self.assertEqual(main.main(env), 0)


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.workflow = Path(_PROJECT_DIR, ".github", "workflows", "pr-review.yml").read_text(
            encoding="utf-8"
        )
        self.criteria = Path(_PROJECT_DIR, "tools", "pr_review", "criteria.md").read_text(
            encoding="utf-8"
        )

    def test_workflow_uses_the_safe_pull_request_target_pattern(self):
        text = self.workflow
        self.assertIn("pull_request_target:", text)
        self.assertNotIn("\n  pull_request:\n", text)
        for event in ("opened", "reopened", "ready_for_review", "synchronize"):
            self.assertIn(event, text)
        self.assertIn("contents: read", text)
        self.assertIn("pull-requests: write", text)
        self.assertIn("issues: write", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.repository.default_branch", text)
        self.assertNotIn("head.sha", text)
        self.assertNotIn("pull_request.body", text)
        self.assertNotIn("pull_request.title", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("secrets.XAI_API_KEY", text)
        self.assertIn("python3 tools/pr_review/main.py", text)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", text)

    def test_criteria_keeps_the_ownership_boundary(self):
        self.assertIn("CodeRabbit", self.criteria)
        self.assertIn("local-first-rollout-status.md", self.criteria)
        self.assertIn("blocking", self.criteria)

    def test_review_tooling_does_not_select_browser_e2e(self):
        from tests.e2e.policy import match_affected_path

        for path in (
            "tools/pr_review/pr_review.py",
            "tools/pr_review/criteria.md",
            ".github/workflows/pr-review.yml",
        ):
            _rule, features, skip, _note = match_affected_path(path)
            self.assertTrue(skip, path)
            self.assertEqual(features, ())

    def test_actionlint_accepts_the_workflow(self):
        binary = shutil.which("actionlint")
        if not binary:
            self.skipTest("actionlint is not installed")
        result = subprocess.run(
            [binary, str(Path(_PROJECT_DIR, ".github", "workflows", "pr-review.yml"))],
            cwd=_PROJECT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
