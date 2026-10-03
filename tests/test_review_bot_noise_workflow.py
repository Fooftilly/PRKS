"""Regression tests for the review-bot noise minimization workflow (#363).

The classifier is the jq program stored in the workflow's top-level
``NOISE_CLASSIFIER_JQ`` env value. These tests run that exact program against
real and synthetic bot bodies, then run each job's ``run:`` script against a
stub ``gh`` to prove the minimize-only, idempotent behaviour. No live GitHub
API is called.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "suppress-noisy-bot-comments.yml"
_SIGNAL = _ROOT / ".github" / "workflows" / "review-event-signal.yml"

_JQ = shutil.which("jq")
_BASH = shutil.which("bash")
# GitHub-hosted runners ship jq and bash; locally a missing tool skips, but
# in CI it must fail so the classifier is never silently untested.
_TOOLS_REQUIRED = bool(os.environ.get("CI"))


def _require_tools(test: unittest.TestCase) -> None:
    if _JQ and _BASH:
        return
    if _TOOLS_REQUIRED:
        test.fail("jq and bash are required to test the noise workflow in CI")
    test.skipTest("jq and bash are required to test the noise workflow")


def _block(lines: list[str], header: int) -> str:
    """Return the YAML literal block (``key: |``) that starts after ``header``."""
    header_indent = len(lines[header]) - len(lines[header].lstrip())
    body: list[str] = []
    for line in lines[header + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= header_indent:
            break
        body.append(line)
    while body and not body[-1].strip():
        body.pop()
    indent = min(len(line) - len(line.lstrip()) for line in body if line.strip())
    return "\n".join(line[indent:] for line in body) + "\n"


def _env_block(text: str, key: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line == f"  {key}: |":
            return _block(lines, index)
    raise AssertionError(f"workflow env {key} not found")


def _job_section(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)", text, re.M | re.S)
    if not match:
        raise AssertionError(f"job {job} not found")
    return match.group(1)


def _job_run_script(text: str, job: str) -> str:
    lines = _job_section(text, job).splitlines()
    runs = [index for index, line in enumerate(lines) if line.strip() == "run: |"]
    if len(runs) != 1:
        raise AssertionError(f"job {job} must have exactly one run block")
    return _block(lines, runs[0])


_TEXT = _WORKFLOW.read_text(encoding="utf-8")
_CLASSIFIER = _env_block(_TEXT, "NOISE_CLASSIFIER_JQ")

_CODEX = "chatgpt-codex-connector[bot]"
_QODO = "qodo-code-review[bot]"
_GREPTILE = "greptile-apps[bot]"
_SOURCERY = "sourcery-ai[bot]"
_CODERABBIT = "coderabbitai[bot]"

# Bodies copied from real bot output on this repository (PRs #349, #359, #362)
# where available; the rest are reconstructed from the fragments the
# pre-#363 workflow already matched.
CODEX_QUOTA = (
    "You have reached your Codex usage limits for code reviews. You can see your "
    "limits in the [Codex usage dashboard](https://chatgpt.com/codex/settings/usage)."
)
QODO_NO_FINDINGS = (
    "## Code Review by Qodo\n\nGreat, no issues found!\n\n"
    "Qodo reviewed your code and found no material issues that require review"
)
GREPTILE_CREDIT_LIMIT = (
    "Fooftilly has reached the 50-credit limit for trial accounts. "
    "To continue receiving code reviews, upgrade your plan."
)
GREPTILE_TRIAL_ENDED = (
    "Your trial has ended. [Reactivate Greptile](https://app.greptile.com/-/pull-requests) "
    "to resume code reviews."
)
SOURCERY_BUDGET = (
    "Sorry @Fooftilly, you've used your own review budget of 250,000 diff characters "
    "for the last 7 days.\n\nYou can request another review in 4 days and 10 hours by "
    "commenting `@sourcery-ai review`. [Upgrade](https://app.sourcery.ai/login) to get "
    "a review now."
)
QODO_TRIAL_EXPIRING = (
    "<!-- qodo:trial-expiring -->\n\n**ⓘ Your Qodo trial ends soon.** Ask your workspace "
    "admin to set up billing to keep reviews running after the trial. "
    "[Manage billing](https://app.qodo.ai/account/billing/manage-subscription)"
)
CODERABBIT_NO_AUTO_REVIEW = (
    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
    "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n\n"
    "> [!IMPORTANT]\n> - [ ] 🔍 Trigger review\n> \n"
    "> This repository does not receive automatic reviews because it has fewer than 10 stars.\n"
    "<!-- end of auto-generated comment: skip review by coderabbit.ai -->\n"
    "Thanks for using [CodeRabbit](https://coderabbit.ai)! It's free for OSS.\n"
    "<sub>Comment `@coderabbitai help` to get the list of available commands.</sub>"
)

CODERABBIT_WALKTHROUGH = (
    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
    "<!-- walkthrough_start -->\n\n## Walkthrough\n\nThe change routes mounted views "
    "through one dispatcher.\n\n## Changes\n\n| Cohort / File(s) | Summary |\n"
    "|---|---|\n| `frontend/js/app.js` | Adds prksDeliverVueRoute. |\n\n"
    "## Estimated code review effort\n\n🎯 3 (Moderate) | ⏱️ ~25 minutes\n\n"
    "## Pre-merge checks\n\n✅ Passed checks (3 passed)\n\n"
    "No actionable comments were generated in the recent review. 🎉\n\n"
    "Thanks for using [CodeRabbit](https://coderabbit.ai)! Upgrade to Pro for more. "
    "Your trial includes unlimited reviews.\n"
    "<!-- walkthrough_end -->"
)
GREPTILE_SUMMARY = (
    "<!-- greptile_summary -->\n\n<h2>Confidence Score: 5/5</h2>\n\n"
    "**[Medium risk]** Consolidates feature route entry points into a single window "
    "dispatcher.\n\nThe reviewed changes appear safe to merge from a code-correctness "
    "perspective.\n\n<!-- greptile_confidence_score:5 -->"
)
GREPTILE_FINDING = (
    "**P1** `prksDeliverVueRoute` drops the pending request when the host is replaced "
    "before registration. To continue receiving code reviews on this branch, fix this."
)
SOURCERY_FINDINGS = (
    "Hey @Fooftilly - I've reviewed your changes and found some issues that need to be "
    "addressed.\n\n## Individual Comments\n\n### Comment 1\n<location> `backend/db.py:12` "
    "</location>\n<issue_to_address>**bug:** The budget calculation counts diff "
    "characters twice.</issue_to_address>"
)
QODO_GENERIC_REVIEW = (
    "<h3>PR Summary by Qodo</h3>\n\nDeliver mounted routes through one window dispatcher\n\n"
    "<details><summary>High-Level Assessment</summary>\n\n>Use the existing early-presenter "
    "registry for mounted requests, as this PR does.\n\n</details>"
)
GENERIC_WORDS = (
    "No issues found in the trial run. Consider an upgrade of the review budget; the "
    "quota is close. Your trial is ending soon."
)

POSITIVE_CASES = {
    "codex-usage-limit": (_CODEX, CODEX_QUOTA),
    "qodo-no-findings": (_QODO, QODO_NO_FINDINGS),
    "greptile-credit-limit": (_GREPTILE, GREPTILE_CREDIT_LIMIT),
    "greptile-trial-ended": (_GREPTILE, GREPTILE_TRIAL_ENDED),
    "sourcery-review-budget": (_SOURCERY, SOURCERY_BUDGET),
    "qodo-trial-expiring": (_QODO, QODO_TRIAL_EXPIRING),
    "coderabbit-auto-review-unavailable": (_CODERABBIT, CODERABBIT_NO_AUTO_REVIEW),
}


def _classify(subject: object) -> str:
    assert _JQ
    result = subprocess.run(
        [_JQ, "-r", _CLASSIFIER],
        input=json.dumps(subject),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _subject(login: str | None, body: object) -> dict:
    return {"user": {"login": login}, "body": body}


class NoiseClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_tools(self)

    def test_every_supported_noise_class_matches_its_rule(self) -> None:
        for rule, (login, body) in POSITIVE_CASES.items():
            with self.subTest(rule=rule):
                self.assertEqual(_classify(_subject(login, body)), rule)

    def test_same_body_from_wrong_account_stays_visible(self) -> None:
        impostors = ("Fooftilly", "github-actions[bot]", "coderabbitai", "sourcery-ai", "")
        for rule, (login, body) in POSITIVE_CASES.items():
            for other in (*impostors, *(l for l, _ in POSITIVE_CASES.values() if l != login)):
                with self.subTest(rule=rule, login=other):
                    self.assertEqual(_classify(_subject(other, body)), "none")

    def test_only_one_required_fragment_stays_visible(self) -> None:
        cases = {
            "codex usage only": (_CODEX, "You have reached your Codex usage limits."),
            "codex reviews only": (_CODEX, "Codex finished its code reviews."),
            "qodo great only": (_QODO, "Great, no issues found!"),
            "qodo reviewed only": (
                _QODO,
                "Qodo reviewed your code and found no material issues that require review",
            ),
            "greptile credit only": (_GREPTILE, "has reached the 50-credit limit for trial accounts"),
            "greptile continue only": (_GREPTILE, "To continue receiving code reviews, add a key."),
            "greptile trial only": (_GREPTILE, "Your trial has ended."),
            "greptile reactivate only": (_GREPTILE, "Reactivate Greptile for this repository."),
            "sourcery budget only": (_SOURCERY, "You've used your own review budget."),
            "sourcery diff only": (_SOURCERY, "This PR has 900 diff characters."),
            "coderabbit auto only": (_CODERABBIT, "This repository does not receive automatic reviews."),
            "coderabbit stars only": (_CODERABBIT, "Repositories with fewer than 10 stars."),
            "qodo marker text without marker": (_QODO, "qodo:trial-expiring Your Qodo trial ends soon."),
        }
        for name, (login, body) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(_classify(_subject(login, body)), "none")

    def test_substantive_reviews_stay_visible(self) -> None:
        cases = {
            "coderabbit walkthrough": (_CODERABBIT, CODERABBIT_WALKTHROUGH),
            "greptile summary": (_GREPTILE, GREPTILE_SUMMARY),
            "greptile finding": (_GREPTILE, GREPTILE_FINDING),
            "sourcery findings": (_SOURCERY, SOURCERY_FINDINGS),
            "qodo generic review": (_QODO, QODO_GENERIC_REVIEW),
            "codex review": (_CODEX, "**P1** Guard the null host before registration."),
        }
        for name, (login, body) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(_classify(_subject(login, body)), "none")

    def test_generic_trial_upgrade_budget_quota_words_stay_visible(self) -> None:
        for login in (_CODEX, _QODO, _GREPTILE, _SOURCERY, _CODERABBIT):
            for body in (GENERIC_WORDS, "trial", "upgrade", "budget", "quota", "no issues"):
                with self.subTest(login=login, body=body):
                    self.assertEqual(_classify(_subject(login, body)), "none")

    def test_malformed_subjects_classify_as_none(self) -> None:
        for subject in ({}, {"user": None, "body": None}, _subject(_QODO, None), _subject(_QODO, 5)):
            with self.subTest(subject=subject):
                self.assertEqual(_classify(subject), "none")


class NoiseRuleTableTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_tools(self)

    def _rules(self) -> list[dict]:
        assert _JQ
        program = _CLASSIFIER.split("] as $rules", 1)[0] + "]"
        result = subprocess.run([_JQ, "-c", "-n", program], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_every_rule_requires_exact_bot_and_distinctive_text(self) -> None:
        rules = self._rules()
        self.assertEqual({rule["rule"] for rule in rules}, set(POSITIVE_CASES))
        for rule in rules:
            with self.subTest(rule=rule["rule"]):
                self.assertRegex(rule["login"], r"^[a-z0-9-]+\[bot\]$")
                fragments = rule["fragments"]
                self.assertTrue(fragments and all(f.strip() == f and f for f in fragments))
                # At least one fragment is a long, product-specific sentence.
                self.assertTrue(any(len(f) >= 20 for f in fragments))
                marker_only = len(fragments) == 1 and re.fullmatch(r"<!-- [a-z]+:[a-z-]+ -->", fragments[0])
                self.assertTrue(len(fragments) >= 2 or marker_only)

    def test_comment_job_prefilter_lists_exactly_the_rule_bots(self) -> None:
        section = _job_section(_TEXT, "minimize-noise-comment")
        listed = json.loads(re.search(r"fromJSON\('(\[.*?\])'\)", section).group(1))
        self.assertEqual(set(listed), {rule["login"] for rule in self._rules()})


class WorkflowSecurityTests(unittest.TestCase):
    def test_no_content_deletion_or_unminimize_path(self) -> None:
        self.assertNotRegex(_TEXT, r"delete[A-Z]")
        for forbidden in (
            "DELETE",
            "deletePullRequestReview",
            "deleteIssueComment",
            "deletePullRequestReviewComment",
            "unminimizeComment",
            "--method",
            "-X ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, _TEXT)
        self.assertEqual(_TEXT.count("classifier: OFF_TOPIC"), 1)

    def test_names_describe_minimization(self) -> None:
        self.assertIn("name: Minimize noisy review bot comments\n", _TEXT)
        self.assertRegex(_TEXT, r"(?m)^jobs:\n  minimize-noise-comment:\n")
        self.assertRegex(_TEXT, r"(?m)^  minimize-noisy-review:\n")
        self.assertNotRegex(_TEXT.lower(), r"(?m)^  [a-z-]*greptile[a-z-]*:")

    def test_permissions_are_minimal(self) -> None:
        self.assertRegex(_TEXT, r"(?m)^permissions: \{\}$")
        comment_job = _job_section(_TEXT, "minimize-noise-comment")
        self.assertRegex(
            comment_job,
            r"\n    permissions:\n      issues: write( #[^\n]*)?\n      pull-requests: write( #[^\n]*)?\n\n",
        )
        review_job = _job_section(_TEXT, "minimize-noisy-review")
        self.assertRegex(review_job, r"\n    permissions:\n      pull-requests: write( #[^\n]*)?\n\n")

    def test_no_pr_code_checkout_or_untrusted_text_in_shell(self) -> None:
        for text in (_TEXT, _SIGNAL.read_text(encoding="utf-8")):
            self.assertNotIn("actions/checkout", text)
            self.assertNotIn("comment.body", text)
            self.assertNotIn("review.body", text)
        for job in ("minimize-noise-comment", "minimize-noisy-review"):
            with self.subTest(job=job):
                self.assertNotIn("${{", _job_run_script(_TEXT, job))

    def test_review_path_trusts_only_numeric_signal_ids(self) -> None:
        signal = _SIGNAL.read_text(encoding="utf-8")
        self.assertIn("name: Review event signal\n", signal)
        self.assertIn(
            "run-name: review-${{ github.event.review.id }}-pr-${{ github.event.pull_request.number }}\n",
            signal,
        )
        self.assertIn("pull_request_review:\n    types: [submitted]\n", signal)
        self.assertRegex(signal, r"(?m)^permissions: \{\}$")
        self.assertIn("        run: ':'\n", signal)
        self.assertEqual(signal.count("run:"), 1)

        self.assertIn('workflows: ["Review event signal"]', _TEXT)
        review_job = _job_section(_TEXT, "minimize-noisy-review")
        self.assertIn("github.event.workflow_run.event == 'pull_request_review'", review_job)
        self.assertIn('[[ ! "${SIGNAL_TITLE}" =~ ^review-([0-9]+)-pr-([0-9]+)$ ]]', review_job)


_STUB_GH = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${STUB_LOG}"
if [[ "${1:-}" != "api" ]]; then exit 64; fi
for arg in "$@"; do
  case "${arg}" in
    DELETE|*delete*|*unminimize*) echo "forbidden call" >&2; exit 65 ;;
  esac
done
if [[ "${2:-}" != "graphql" ]]; then
  cat "${STUB_REVIEW_JSON}"
  exit 0
fi
query=""
subject=""
for arg in "$@"; do
  case "${arg}" in
    query=*) query="${arg#query=}" ;;
    subjectId=*) subject="${arg#subjectId=}" ;;
  esac
done
[[ "${subject}" == "${STUB_EXPECT_SUBJECT}" ]] || { echo "wrong subject" >&2; exit 66; }
if [[ "${query}" == *minimizeComment* ]]; then
  [[ "${query}" == *"classifier: OFF_TOPIC"* ]] || exit 67
  if [[ "${STUB_MUTATION}" == "error" ]]; then echo "mutation failed" >&2; exit 1; fi
  if [[ "${STUB_MUTATION}" == "error-after-concurrent-minimize" ]]; then
    echo true > "${STUB_STATE}"; echo "already minimized" >&2; exit 1
  fi
  if [[ "${STUB_MUTATION}" == "ok" ]]; then echo true > "${STUB_STATE}"; fi
  echo '{"data":{"minimizeComment":{"minimizedComment":{"isMinimized":true}}}}'
  exit 0
fi
cat "${STUB_STATE}"
"""


class MinimizeScriptTests(unittest.TestCase):
    """Run each job's real run: script against a stub gh."""

    def setUp(self) -> None:
        _require_tools(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(_STUB_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, job: str, *, subject: dict, state: str, mutation: str = "ok", title: str = "") -> tuple[int, list[str], str]:
        log = self.tmp / "gh.log"
        log.write_text("", encoding="utf-8")
        state_file = self.tmp / "state"
        state_file.write_text(state + "\n", encoding="utf-8")
        event = self.tmp / "event.json"
        event.write_text(json.dumps({"comment": subject}), encoding="utf-8")
        review = self.tmp / "review.json"
        review.write_text(json.dumps(subject), encoding="utf-8")
        script = self.tmp / "step.sh"
        script.write_text(_job_run_script(_TEXT, job), encoding="utf-8")
        env = {
            "PATH": self.path,
            "HOME": str(self.tmp),
            "GH_TOKEN": "stub",
            "GITHUB_EVENT_PATH": str(event),
            "REPOSITORY": "Fooftilly/PRKS",
            "SIGNAL_TITLE": title,
            "NOISE_CLASSIFIER_JQ": _CLASSIFIER,
            "MINIMIZED_STATE_QUERY": _env_block(_TEXT, "MINIMIZED_STATE_QUERY"),
            "MINIMIZE_OFF_TOPIC_MUTATION": _env_block(_TEXT, "MINIMIZE_OFF_TOPIC_MUTATION"),
            "STUB_LOG": str(log),
            "STUB_STATE": str(state_file),
            "STUB_MUTATION": mutation,
            "STUB_REVIEW_JSON": str(review),
            "STUB_EXPECT_SUBJECT": str(subject.get("node_id", "")),
        }
        assert _BASH
        result = subprocess.run([_BASH, str(script)], env=env, capture_output=True, text=True)
        calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        return result.returncode, calls, state_file.read_text(encoding="utf-8").strip()

    @staticmethod
    def _mutations(calls: list[str]) -> int:
        return sum("minimizeComment" in call for call in calls)

    def _comment(self, body: str = CODEX_QUOTA, login: str = _CODEX) -> dict:
        return {"user": {"login": login}, "body": body, "node_id": "IC_kwDOabc123", "id": 5966421774}

    def _review(self, body: str = SOURCERY_BUDGET, login: str = _SOURCERY) -> dict:
        return {"user": {"login": login}, "body": body, "node_id": "PRR_kwDOxyz789", "id": 5399399625}

    def test_new_match_is_minimized_and_verified(self) -> None:
        for job, subject, title in (
            ("minimize-noise-comment", self._comment(), ""),
            ("minimize-noisy-review", self._review(), "review-5399399625-pr-362"),
        ):
            with self.subTest(job=job):
                code, calls, state = self._run(job, subject=subject, state="false", title=title)
                self.assertEqual(code, 0)
                self.assertEqual(self._mutations(calls), 1)
                self.assertEqual(state, "true")

    def test_already_minimized_or_duplicate_event_succeeds_without_mutation(self) -> None:
        for job, subject, title in (
            ("minimize-noise-comment", self._comment(), ""),
            ("minimize-noisy-review", self._review(GREPTILE_TRIAL_ENDED, _GREPTILE), "review-5394711990-pr-349"),
        ):
            with self.subTest(job=job):
                code, calls, state = self._run(job, subject=subject, state="true", title=title)
                self.assertEqual(code, 0)
                self.assertEqual(self._mutations(calls), 0)
                self.assertEqual(state, "true")

    def test_mutation_error_after_concurrent_minimize_succeeds(self) -> None:
        # A duplicate run minimized the node between the read and the mutation;
        # the mutation errors, but the re-read proves the invariant holds.
        code, calls, state = self._run(
            "minimize-noise-comment",
            subject=self._comment(),
            state="false",
            mutation="error-after-concurrent-minimize",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self._mutations(calls), 1)
        self.assertEqual(state, "true")

    def test_failed_minimization_fails_visibly(self) -> None:
        for mutation in ("error", "noop"):
            for job, subject, title in (
                ("minimize-noise-comment", self._comment(), ""),
                ("minimize-noisy-review", self._review(), "review-5399399625-pr-362"),
            ):
                with self.subTest(job=job, mutation=mutation):
                    code, calls, state = self._run(job, subject=subject, state="false", mutation=mutation, title=title)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(state, "false")

    def test_missing_or_unminimizable_node_fails(self) -> None:
        code, calls, _ = self._run("minimize-noise-comment", subject=self._comment(), state="null")
        self.assertNotEqual(code, 0)
        self.assertEqual(self._mutations(calls), 0)

    def test_unmatched_content_makes_no_minimize_call(self) -> None:
        code, calls, _ = self._run(
            "minimize-noise-comment",
            subject=self._comment(CODERABBIT_WALKTHROUGH, _CODERABBIT),
            state="false",
        )
        self.assertEqual((code, calls), (0, []))
        code, calls, _ = self._run(
            "minimize-noisy-review",
            subject=self._review(SOURCERY_FINDINGS),
            state="false",
            title="review-1-pr-2",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self._mutations(calls), 0)
        self.assertFalse(any("graphql" in call for call in calls))

    def test_invalid_node_ids_are_rejected_before_any_graphql(self) -> None:
        for job, subject, title in (
            ("minimize-noise-comment", {**self._comment(), "node_id": "PRR_kwDOxyz789"}, ""),
            ("minimize-noise-comment", {**self._comment(), "node_id": "IC_x; rm -rf /"}, ""),
            ("minimize-noisy-review", {**self._review(), "node_id": "IC_kwDOabc123"}, "review-1-pr-2"),
        ):
            with self.subTest(job=job, node_id=subject["node_id"]):
                code, calls, _ = self._run(job, subject=subject, state="false", title=title)
                self.assertNotEqual(code, 0)
                self.assertFalse(any("graphql" in call for call in calls))

    def test_signal_title_must_be_strictly_numeric(self) -> None:
        for title in (
            "review-1-pr-2 ",
            "review-1-pr-2\nreview-3-pr-4",
            "review-abc-pr-2",
            "review--1-pr-2",
            "review-1-pr-2;id",
            "Review-1-pr-2",
            "review-1-pr-$(id)",
            "",
        ):
            with self.subTest(title=title):
                code, calls, _ = self._run(
                    "minimize-noisy-review", subject=self._review(), state="false", title=title
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
