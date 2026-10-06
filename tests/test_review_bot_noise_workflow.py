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
    "> <details>\n> <summary>⚙️ Run configuration</summary>\n> \n"
    "> - **Configuration used**: Repository: Fooftilly/PRKS/.coderabbit.yaml\n"
    "> - **Review profile**: ASSERTIVE\n> - **Plan**: Advanced\n> \n> </details>\n\n"
    "<!-- end of auto-generated comment: skip review by coderabbit.ai -->\n\n"
    "<!-- autopilot:start -->\n- [ ] <strong title=\"Keep fixing CodeRabbit findings and "
    "required CI, and resolving merge conflicts\">Autopilot</strong>\n<!-- autopilot:end -->\n"
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
CODERABBIT_REVIEW_LIMIT = (
    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
    "<!-- This is an auto-generated comment: rate limited by coderabbit.ai -->\n\n"
    "> [!WARNING]\n> ## Review limit reached\n> \n"
    "> You've used all free OSS reviews for now. Wait for the free limit to reset to keep "
    "reviewing this public repository.\n> \n> **Next included review available in 41 minutes.**\n"
    "> \n> [Check out review usage here](https://app.coderabbit.ai/dashboard/review-capacity).\n"
    "> \n> <details>\n> <summary>View limit details</summary>\n> \n"
    "> **Limit details:** You’ve used the included review currently available.\n> \n"
    "> **Review configuration:**\n> \n> <details>\n> <summary>⚙️ Run configuration</summary>\n"
    "> \n> - **Review profile**: ASSERTIVE\n> </details>\n> \n> <details>\n"
    "> <summary>📥 Commits</summary>\n> \n> Reviewing files that changed from the base of the PR "
    "and between 1e94d7f and fe61bbf.\n> </details>\n> \n> <details>\n"
    "> <summary>📒 Files selected for processing (3)</summary>\n> \n"
    "> * `.github/workflows/review-event-signal.yml`\n> </details>\n> </details>\n\n"
    "<!-- end of auto-generated comment: rate limited by coderabbit.ai -->\n\n"
    "<!-- autopilot:start -->\n- [ ] <strong title=\"Keep fixing CodeRabbit findings and "
    "required CI, and resolving merge conflicts\">Autopilot</strong>\n<!-- autopilot:end -->\n"
    "Thanks for using [CodeRabbit](https://coderabbit.ai)! It's free for OSS."
)

# The <10-stars banner and substantive review output in one comment, as
# CodeRabbit can produce when it edits its summary comment in place.
CODERABBIT_BANNER_WITH_REVIEW = (
    CODERABBIT_NO_AUTO_REVIEW.replace("<!-- tips_start -->", "")
    + "\n<!-- walkthrough_start -->\n\n## Walkthrough\n\nRoutes mounted views through one "
    "dispatcher.\n\n## Changes\n\n| File | Summary |\n|---|---|\n| `app.js` | Adds a helper. |\n\n"
    "## Pre-merge checks\n\n✅ Passed checks (3 passed)\n\n**Merge risk:** low.\n"
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
    "coderabbit-review-limit": (_CODERABBIT, CODERABBIT_REVIEW_LIMIT),
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
            for other in (*impostors, *(bot for bot, _ in POSITIVE_CASES.values() if bot != login)):
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
            "coderabbit rate-limit marker only": (
                _CODERABBIT,
                "<!-- This is an auto-generated comment: rate limited by coderabbit.ai -->",
            ),
            "coderabbit limit heading only": (
                _CODERABBIT,
                "## Review limit reached\nYou've used all free OSS reviews for now.",
            ),
            "qodo marker text without marker": (_QODO, "qodo:trial-expiring Your Qodo trial ends soon."),
        }
        for name, (login, body) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(_classify(_subject(login, body)), "none")

    def test_substantive_reviews_stay_visible(self) -> None:
        cases = {
            "coderabbit walkthrough": (_CODERABBIT, CODERABBIT_WALKTHROUGH),
            "coderabbit banner with walkthrough": (_CODERABBIT, CODERABBIT_BANNER_WITH_REVIEW),
            "coderabbit rate-limit notice with walkthrough": (
                _CODERABBIT,
                CODERABBIT_REVIEW_LIMIT + CODERABBIT_BANNER_WITH_REVIEW[CODERABBIT_BANNER_WITH_REVIEW.index("<!-- walkthrough_start"):],
            ),
            "coderabbit review that mentions limits": (
                _CODERABBIT,
                CODERABBIT_WALKTHROUGH + "\n\nReview limit reached for nitpicks; rate limit and "
                "review limits apply to free reviews.",
            ),
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

    def test_coderabbit_notice_with_any_review_marker_stays_visible(self) -> None:
        coderabbit = [r for r in self._rules() if r["login"] == _CODERABBIT]
        self.assertEqual(
            {r["rule"] for r in coderabbit},
            {"coderabbit-auto-review-unavailable", "coderabbit-review-limit"},
        )
        for rule in coderabbit:
            self.assertTrue(rule["excluded"])
            _, notice = POSITIVE_CASES[rule["rule"]]
            for marker in rule["excluded"]:
                self.assertEqual(marker, marker.lower())
                self.assertNotIn(marker, notice.lower())
                for variant in (marker, marker.upper(), marker.title()):
                    with self.subTest(rule=rule["rule"], marker=variant):
                        body = f"{notice}\n\n{variant}: details follow."
                        self.assertEqual(_classify(_subject(_CODERABBIT, body)), "none")

    def test_comment_job_prefilter_lists_exactly_the_rule_bots(self) -> None:
        section = _job_section(_TEXT, "minimize-noise-comment")
        listed = json.loads(re.search(r"fromJSON\('(\[.*?\])'\)", section).group(1))
        self.assertEqual(set(listed), {rule["login"] for rule in self._rules()})


class WorkflowSecurityTests(unittest.TestCase):
    def test_no_content_deletion_path(self) -> None:
        self.assertNotRegex(_TEXT, r"delete[A-Z]")
        for forbidden in (
            "DELETE",
            "deletePullRequestReview",
            "deleteIssueComment",
            "deletePullRequestReviewComment",
            "--method",
            "-X ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, _TEXT)
        self.assertEqual(_TEXT.count("classifier: OFF_TOPIC"), 1)

    def test_unminimize_is_limited_to_edited_comments(self) -> None:
        # One mutation definition, used only by the comment job's edited path.
        self.assertEqual(_TEXT.count("unminimizeComment("), 1)
        self.assertNotIn("UNMINIMIZE_MUTATION", _job_section(_TEXT, "minimize-noisy-review"))
        comment_script = _job_run_script(_TEXT, "minimize-noise-comment")
        self.assertEqual(comment_script.count('query="${UNMINIMIZE_MUTATION}"'), 1)
        self.assertIn('if [[ "${rule}" == "none" && "${action}" != "edited" ]]; then', comment_script)
        self.assertIn('"${reason}" != "off-topic"', comment_script)
        self.assertIn("issue_comment:\n    types: [created, edited]\n", _TEXT)
        self.assertIn("pull_request_review_comment:\n    types: [created, edited]\n", _TEXT)

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
# Stand-in for gh: logs every call, serves the live subject for REST reads,
# and keeps the node's Minimizable state in a JSON file for GraphQL calls.
set -euo pipefail
jq -nc '$ARGS.positional' --args -- "$@" >> "${STUB_LOG}"
if [[ "${1:-}" != "api" ]]; then exit 64; fi
for arg in "$@"; do
  case "${arg}" in
    DELETE|*delete*) echo "forbidden call" >&2; exit 65 ;;
  esac
done
if [[ "${2:-}" != "graphql" ]]; then
  [[ "${*: -1}" == "${STUB_EXPECT_PATH}" ]] || { echo "wrong path ${*: -1}" >&2; exit 68; }
  cat "${STUB_SUBJECT_JSON}"
  exit 0
fi
query=""
subject=""
jq_filter=""
previous=""
for arg in "$@"; do
  [[ "${previous}" == "--jq" ]] && jq_filter="${arg}"
  case "${arg}" in
    query=*) query="${arg#query=}" ;;
    subjectId=*) subject="${arg#subjectId=}" ;;
  esac
  previous="${arg}"
done
[[ "${subject}" == "${STUB_EXPECT_SUBJECT}" ]] || { echo "wrong subject" >&2; exit 66; }
if [[ "${query}" == *unminimizeComment* ]]; then
  if [[ "${STUB_MUTATION}" == "error" ]]; then echo "mutation failed" >&2; exit 1; fi
  if [[ "${STUB_MUTATION}" == "ok" ]]; then
    echo '{"isMinimized":false,"minimizedReason":null}' > "${STUB_STATE}"
  fi
  echo '{}'
  exit 0
fi
if [[ "${query}" == *minimizeComment* ]]; then
  [[ "${query}" == *"classifier: OFF_TOPIC"* ]] || exit 67
  if [[ "${STUB_MUTATION}" == "error" ]]; then echo "mutation failed" >&2; exit 1; fi
  if [[ "${STUB_MUTATION}" == "error-after-concurrent-minimize" ]]; then
    echo '{"isMinimized":true,"minimizedReason":"off-topic"}' > "${STUB_STATE}"
    echo "already minimized" >&2; exit 1
  fi
  if [[ "${STUB_MUTATION}" == "ok" ]]; then
    echo '{"isMinimized":true,"minimizedReason":"off-topic"}' > "${STUB_STATE}"
  fi
  echo '{}'
  exit 0
fi
jq -c '{data: {node: .}}' "${STUB_STATE}" | jq -r "${jq_filter}"
"""

_VISIBLE = {"isMinimized": False, "minimizedReason": None}
_OFF_TOPIC = {"isMinimized": True, "minimizedReason": "off-topic"}


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

    def _run(
        self,
        job: str,
        *,
        subject: dict,
        state: dict | None,
        mutation: str = "ok",
        title: str = "",
        action: str = "created",
        event_name: str = "issue_comment",
        event_subject: dict | None = None,
    ) -> tuple[int, list[list[str]], dict | None]:
        log = self.tmp / "gh.log"
        log.write_text("", encoding="utf-8")
        state_file = self.tmp / "state.json"
        state_file.write_text(json.dumps(state), encoding="utf-8")
        event = self.tmp / "event.json"
        event.write_text(json.dumps({"action": action, "comment": event_subject or subject}), encoding="utf-8")
        live = self.tmp / "subject.json"
        live.write_text(json.dumps(subject), encoding="utf-8")
        script = self.tmp / "step.sh"
        script.write_text(_job_run_script(_TEXT, job), encoding="utf-8")
        if job == "minimize-noisy-review":
            match = re.fullmatch(r"review-([0-9]+)-pr-([0-9]+)", title)
            expect_path = f"repos/Fooftilly/PRKS/pulls/{match.group(2)}/reviews/{match.group(1)}" if match else ""
        else:
            kind = "issues" if event_name == "issue_comment" else "pulls"
            expect_path = f"repos/Fooftilly/PRKS/{kind}/comments/{(event_subject or subject).get('id')}"
        env = {
            "PATH": self.path,
            "HOME": str(self.tmp),
            "GH_TOKEN": "stub",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_EVENT_NAME": event_name if job == "minimize-noise-comment" else "workflow_run",
            "GITHUB_REPOSITORY": "Fooftilly/PRKS",
            "REPOSITORY": "Fooftilly/PRKS",
            "SIGNAL_TITLE": title,
            "NOISE_CLASSIFIER_JQ": _CLASSIFIER,
            "MINIMIZED_STATE_QUERY": _env_block(_TEXT, "MINIMIZED_STATE_QUERY"),
            "MINIMIZE_OFF_TOPIC_MUTATION": _env_block(_TEXT, "MINIMIZE_OFF_TOPIC_MUTATION"),
            "UNMINIMIZE_MUTATION": _env_block(_TEXT, "UNMINIMIZE_MUTATION"),
            "STUB_LOG": str(log),
            "STUB_STATE": str(state_file),
            "STUB_MUTATION": mutation,
            "STUB_SUBJECT_JSON": str(live),
            "STUB_EXPECT_PATH": expect_path,
            "STUB_EXPECT_SUBJECT": str(subject.get("node_id", "")),
        }
        assert _BASH
        result = subprocess.run([_BASH, str(script)], env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
        return result.returncode, calls, json.loads(state_file.read_text(encoding="utf-8"))

    @staticmethod
    def _mutations(calls: list[list[str]], name: str = "minimizeComment") -> int:
        pattern = re.compile(rf"\b{name}\(")
        return sum(any(pattern.search(arg) for arg in call) for call in calls)

    @staticmethod
    def _graphql(calls: list[list[str]]) -> int:
        return sum(call[:2] == ["api", "graphql"] for call in calls)

    def _comment(self, body: str = CODEX_QUOTA, login: str = _CODEX) -> dict:
        return {"user": {"login": login}, "body": body, "node_id": "IC_kwDOabc123", "id": 5966421774}

    def _review(self, body: str = SOURCERY_BUDGET, login: str = _SOURCERY) -> dict:
        return {"user": {"login": login}, "body": body, "node_id": "PRR_kwDOxyz789", "id": 5399399625}

    def test_new_match_is_minimized_and_verified(self) -> None:
        for job, subject, title, event_name in (
            ("minimize-noise-comment", self._comment(), "", "issue_comment"),
            ("minimize-noise-comment", {**self._comment(), "node_id": "PRRC_kwDOabc1"}, "", "pull_request_review_comment"),
            ("minimize-noisy-review", self._review(), "review-5399399625-pr-362", ""),
        ):
            with self.subTest(job=job, event=event_name):
                code, calls, state = self._run(
                    job, subject=subject, state=_VISIBLE, title=title, event_name=event_name or "issue_comment"
                )
                self.assertEqual(code, 0)
                self.assertEqual(self._mutations(calls), 1)
                self.assertEqual(self._mutations(calls, "unminimizeComment"), 0)
                self.assertEqual(state, _OFF_TOPIC)

    def test_already_minimized_or_duplicate_event_succeeds_without_mutation(self) -> None:
        for job, subject, title, action in (
            ("minimize-noise-comment", self._comment(), "", "created"),
            ("minimize-noise-comment", self._comment(), "", "edited"),
            ("minimize-noisy-review", self._review(GREPTILE_TRIAL_ENDED, _GREPTILE), "review-5394711990-pr-349", "created"),
        ):
            with self.subTest(job=job, action=action):
                code, calls, state = self._run(job, subject=subject, state=_OFF_TOPIC, title=title, action=action)
                self.assertEqual(code, 0)
                self.assertEqual(self._graphql(calls), 1)
                self.assertEqual(state, _OFF_TOPIC)

    def test_mutation_error_after_concurrent_minimize_succeeds(self) -> None:
        # A duplicate run minimized the node between the read and the mutation;
        # the mutation errors, but the re-read proves the invariant holds.
        code, calls, state = self._run(
            "minimize-noise-comment",
            subject=self._comment(),
            state=_VISIBLE,
            mutation="error-after-concurrent-minimize",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self._mutations(calls), 1)
        self.assertEqual(state, _OFF_TOPIC)

    def test_failed_minimization_fails_visibly(self) -> None:
        for mutation in ("error", "noop"):
            for job, subject, title in (
                ("minimize-noise-comment", self._comment(), ""),
                ("minimize-noisy-review", self._review(), "review-5399399625-pr-362"),
            ):
                with self.subTest(job=job, mutation=mutation):
                    code, calls, state = self._run(job, subject=subject, state=_VISIBLE, mutation=mutation, title=title)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(state, _VISIBLE)

    def test_missing_or_unminimizable_node_fails(self) -> None:
        for action in ("created", "edited"):
            with self.subTest(action=action):
                code, calls, _ = self._run(
                    "minimize-noise-comment", subject=self._comment(), state=None, action=action
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(self._graphql(calls), 1)

    def test_unmatched_new_content_makes_no_graphql_call(self) -> None:
        code, calls, state = self._run(
            "minimize-noise-comment",
            subject=self._comment(CODERABBIT_WALKTHROUGH, _CODERABBIT),
            state=_OFF_TOPIC,
        )
        # A created comment is never unminimized, even if already minimized.
        self.assertEqual((code, self._graphql(calls), state), (0, 0, _OFF_TOPIC))
        code, calls, _ = self._run(
            "minimize-noisy-review",
            subject=self._review(SOURCERY_FINDINGS),
            state=_VISIBLE,
            title="review-1-pr-2",
        )
        self.assertEqual((code, self._graphql(calls)), (0, 0))

    def test_noisy_comment_edited_into_review_is_restored(self) -> None:
        # The <10 stars banner was minimized; CodeRabbit then edits the same
        # comment into a walkthrough (or the banner plus review output).
        for body in (CODERABBIT_WALKTHROUGH, CODERABBIT_BANNER_WITH_REVIEW):
            with self.subTest(body=body[:60]):
                code, calls, state = self._run(
                    "minimize-noise-comment",
                    subject=self._comment(body, _CODERABBIT),
                    state=_OFF_TOPIC,
                    action="edited",
                )
                self.assertEqual(code, 0)
                self.assertEqual(self._mutations(calls, "unminimizeComment"), 1)
                self.assertEqual(self._mutations(calls), 0)
                self.assertEqual(state, _VISIBLE)

    def test_edit_uses_live_body_not_stale_event_payload(self) -> None:
        # A queued edited event still carries the banner, but the live comment
        # is already the walkthrough: the live body decides.
        code, calls, state = self._run(
            "minimize-noise-comment",
            subject=self._comment(CODERABBIT_WALKTHROUGH, _CODERABBIT),
            event_subject=self._comment(CODERABBIT_NO_AUTO_REVIEW, _CODERABBIT),
            state=_OFF_TOPIC,
            action="edited",
        )
        self.assertEqual((code, state), (0, _VISIBLE))
        # And the reverse: stale review payload, live banner gets minimized.
        code, calls, state = self._run(
            "minimize-noise-comment",
            subject=self._comment(CODERABBIT_NO_AUTO_REVIEW, _CODERABBIT),
            event_subject=self._comment(CODERABBIT_WALKTHROUGH, _CODERABBIT),
            state=_VISIBLE,
            action="edited",
        )
        self.assertEqual((code, state), (0, _OFF_TOPIC))

    def test_edit_that_stays_noise_keeps_or_applies_minimization(self) -> None:
        running = CODERABBIT_NO_AUTO_REVIEW.replace("- [ ] 🔍 Trigger review", "- 🔄 Running review...")
        for state_before in (_OFF_TOPIC, _VISIBLE):
            with self.subTest(state=state_before):
                code, calls, state = self._run(
                    "minimize-noise-comment",
                    subject=self._comment(running, _CODERABBIT),
                    state=state_before,
                    action="edited",
                )
                self.assertEqual((code, state), (0, _OFF_TOPIC))
                self.assertEqual(self._mutations(calls, "unminimizeComment"), 0)

    def test_edit_never_restores_other_minimization_reasons_or_visible_comments(self) -> None:
        for state_before in (
            {"isMinimized": True, "minimizedReason": "spam"},
            {"isMinimized": True, "minimizedReason": "outdated"},
            {"isMinimized": True, "minimizedReason": "resolved"},
            _VISIBLE,
        ):
            with self.subTest(state=state_before):
                code, calls, state = self._run(
                    "minimize-noise-comment",
                    subject=self._comment(CODERABBIT_WALKTHROUGH, _CODERABBIT),
                    state=state_before,
                    action="edited",
                )
                self.assertEqual((code, state), (0, state_before))
                self.assertEqual(self._graphql(calls), 1)

    def test_failed_restore_fails_visibly(self) -> None:
        for mutation in ("error", "noop"):
            with self.subTest(mutation=mutation):
                code, _, state = self._run(
                    "minimize-noise-comment",
                    subject=self._comment(CODERABBIT_WALKTHROUGH, _CODERABBIT),
                    state=_OFF_TOPIC,
                    mutation=mutation,
                    action="edited",
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(state, _OFF_TOPIC)

    def test_live_author_must_match_event(self) -> None:
        code, calls, state = self._run(
            "minimize-noise-comment",
            subject=self._comment(CODEX_QUOTA, "Fooftilly"),
            event_subject=self._comment(CODEX_QUOTA, _CODEX),
            state=_OFF_TOPIC,
            action="edited",
        )
        self.assertNotEqual(code, 0)
        self.assertEqual((self._graphql(calls), state), (0, _OFF_TOPIC))

    def test_invalid_event_or_node_ids_are_rejected_before_any_graphql(self) -> None:
        cases = (
            ("minimize-noise-comment", {**self._comment(), "node_id": "PRR_kwDOxyz789"}, "", "created"),
            ("minimize-noise-comment", {**self._comment(), "node_id": "IC_x; rm -rf /"}, "", "created"),
            ("minimize-noise-comment", self._comment(), "", "deleted"),
            ("minimize-noise-comment", {**self._comment(), "id": "1; id"}, "", "created"),
            ("minimize-noisy-review", {**self._review(), "node_id": "IC_kwDOabc123"}, "review-1-pr-2", "created"),
        )
        for job, subject, title, action in cases:
            with self.subTest(job=job, node_id=subject["node_id"], action=action, id=subject["id"]):
                code, calls, _ = self._run(job, subject=subject, state=_VISIBLE, title=title, action=action)
                self.assertNotEqual(code, 0)
                self.assertEqual(self._graphql(calls), 0)

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
                    "minimize-noisy-review", subject=self._review(), state=_VISIBLE, title=title
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
