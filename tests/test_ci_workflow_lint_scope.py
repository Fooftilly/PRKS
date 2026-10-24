"""Regression tests for the always-reporting, change-scoped actionlint job."""

from __future__ import annotations

import subprocess  # nosec B404 - fixed git fixture commands, shell=False
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci_workflow_lint_scope import should_lint


def _git(root: Path, *args: str) -> None:
    proc = subprocess.run(  # nosec B603,B607 - test-only fixed git argv
        ["git", *args], cwd=root, capture_output=True, text=True, check=False,
    )
    if proc.returncode:
        raise AssertionError(f"git {args!r}: {proc.stderr}")


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class WorkflowLintScopeTests(unittest.TestCase):
    def test_non_pr_events_always_lint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for event in ("push", "workflow_dispatch", ""):
                with self.subTest(event=event):
                    self.assertTrue(should_lint(event, root))

    def test_checkout_without_merge_parents_fails_open_for_lint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _git(root, "init", "-q", "-b", "master")
            _git(root, "config", "user.email", "ci@example.invalid")
            _git(root, "config", "user.name", "CI Test")
            _write(root, "README.md", "baseline\n")
            _git(root, "add", ".")
            _git(root, "commit", "-qm", "baseline")
            self.assertTrue(should_lint("pull_request", root))

    def test_pr_merge_detects_workflow_and_selector_changes_only(self):
        cases = [
            ("docs only", ["README.md"], False),
            ("unit tests only", ["tests/test_misc.py"], False),
            ("workflow changed", [".github/workflows/check.yml"], True),
            ("selector changed", ["scripts/ci_workflow_lint_scope.py"], True),
            ("mixed docs and workflow", ["README.md", ".github/workflows/check.yml"], True),
        ]
        for label, changed, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _git(root, "init", "-q", "-b", "master")
                _git(root, "config", "user.email", "ci@example.invalid")
                _git(root, "config", "user.name", "CI Test")
                _write(root, "README.md", "baseline\n")
                _write(root, ".github/workflows/check.yml", "name: baseline\n")
                _write(root, "scripts/ci_workflow_lint_scope.py", "# baseline\n")
                _git(root, "add", ".")
                _git(root, "commit", "-qm", "baseline")
                _git(root, "checkout", "-q", "-b", "feature")
                for rel in changed:
                    _write(root, rel, "changed\n")
                _git(root, "add", ".")
                _git(root, "commit", "-qm", "change")
                _git(root, "checkout", "-q", "master")
                _git(root, "merge", "--no-ff", "--no-edit", "-q", "feature")
                self.assertEqual(should_lint("pull_request", root), expected)


if __name__ == "__main__":
    unittest.main()
