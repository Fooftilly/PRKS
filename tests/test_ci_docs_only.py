"""Fail-closed diff classification and documentation validator regression tests."""

from __future__ import annotations

import subprocess  # nosec B404 - fixed local test fixture commands
import tempfile
import unittest
from pathlib import Path

from scripts.ci.check_docs_only import check_markdown
from scripts.ci.docs_only import changed_documentation, is_approved_markdown


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(  # nosec B603,B607 - fixture git, shell=False
        ["git", *args],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _repo(root: Path) -> str:
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI test")
    _write(root, "README.md", "# Welcome\n")
    _write(root, "docs/guide.md", "# Guide\n")
    _write(root, "backend/app.py", "print('ok')\n")
    _write(root, "AGENTS.md", "Rules\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    return _git(root, "rev-parse", "HEAD")


class DocsOnlyCIClassifierTests(unittest.TestCase):
    def test_approved_paths_only(self):
        approved = ("README.md", "CONTRIBUTING.md", "docs/guide.md", "docs/wiki/One.md")
        rejected = (
            "AGENTS.md", "docs/AGENTS.md", "DESIGN.md", "backend/notes.md",
            ".github/workflows/test-gate.yml", "requirements.txt",
            "tests/a.py", "docs/diagram.png", "docs/../backend/a.md", "../README.md",
        )
        for path in approved:
            with self.subTest(approved=path):
                self.assertTrue(is_approved_markdown(path))
        for path in rejected:
            with self.subTest(rejected=path):
                self.assertFalse(is_approved_markdown(path))

    def test_actual_git_changes_fail_closed(self):
        cases = (
            ("root docs", {"README.md": "# Changed\n"}, True),
            ("docs folder", {"docs/guide.md": "# Updated\n"}, True),
            ("new docs", {"docs/new.md": "# New\n"}, True),
            ("mixed", {"README.md": "# Changed\n", "backend/app.py": "print('new')\n"}, False),
            ("code only", {"backend/app.py": "print('new')\n"}, False),
            ("AGENTS", {"AGENTS.md": "New policy\n"}, False),
            ("empty", {}, False),
            ("workflow", {".github/workflows/ci.yml": "name: CI\n"}, False),
        )
        for label, changes, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                base = _repo(root)
                for path, body in changes.items():
                    _write(root, path, body)
                if changes:
                    _git(root, "add", ".")
                    _git(root, "commit", "-qm", "change")
                result = changed_documentation(root, base)
                self.assertEqual(result is not None, expected)
                self.assertIsNone(changed_documentation(root, "not-a-valid-sha"))

    def test_deleted_or_renamed_markdown_is_not_safe_skip(self):
        for kind in ("delete", "rename"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                base = _repo(root)
                if kind == "delete":
                    _git(root, "rm", "docs/guide.md")
                else:
                    _git(root, "mv", "docs/guide.md", "docs/other.md")
                _git(root, "commit", "-qm", "change")
                self.assertIsNone(changed_documentation(root, base))


class DocsOnlyWorkflowTests(unittest.TestCase):
    def test_required_unit_context_kept_with_step_level_skip(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/test-gate.yml"
        ).read_text(encoding="utf-8")
        unit = workflow.split("  unit-api-contract:\n", 1)[1].split(
            "  postgres-helpers:\n", 1
        )[0]
        self.assertIn("name: Unit / API / contract tests", unit)
        self.assertIn("Classify documentation-only PR (fail closed)", unit)
        self.assertIn("git fetch --no-tags --depth=1 origin", unit)
        self.assertIn("Validate changed documentation links, anchors, and fences", unit)
        self.assertNotIn("needs:", unit)
        # Checkout + required job are unconditional; actual heavy commands
        # remain unchanged but guarded by the shared classifier output.
        self.assertEqual(unit.count("if: steps.docs-scope.outputs.docs_only != 'true'"), 4)
        self.assertIn("run: python run_tests.py", unit)
        self.assertIn("run: python -m scripts.ci.check_docs_only", unit)


class DocsOnlyLinkValidationTests(unittest.TestCase):
    def test_valid_links_headings_duplicates_and_fences(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "README.md", "# Top\n## Repeated\n## Repeated\n## my_func\n## Foo & Bar\n")
            _write(root, "docs/guide.md", (
                "# Guide\n"
                "[readme](../README.md#top)\n"
                "[repeated](../README.md#repeated-1)\n"                "[underscore](../README.md#my_func)\n"
                "[punctuation](../README.md#foo--bar)\n"

                "[here](#guide)\n"
                "```md\n[invalid](missing.md)\n```\n"
                "[external](https://example.org/missing.md)\n"
            ))
            self.assertEqual(check_markdown(root, "docs/guide.md"), [])

    def test_broken_relative_link_and_anchor_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "README.md", "# Present\n")
            _write(root, "docs/guide.md", (
                "[missing](not-here.md)\n"
                "[anchor](../README.md#absent)\n"
                "```python\nprint('no closing fence')\n"
            ))
            errors = check_markdown(root, "docs/guide.md")
            self.assertEqual(len(errors), 3)
            self.assertTrue(any("missing relative target" in x for x in errors))
            self.assertTrue(any("missing anchor" in x for x in errors))
            self.assertTrue(any("unclosed" in x for x in errors))


if __name__ == "__main__":
    unittest.main()
