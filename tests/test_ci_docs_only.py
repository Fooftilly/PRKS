"""Fail-closed diff classification and documentation validator regression tests."""

from __future__ import annotations

import re
import subprocess  # nosec B404 - fixed local test fixture commands
import tempfile
import unittest
from pathlib import Path

from scripts.ci.check_docs_only import _anchors, check_markdown
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
    _write(root, "docs/wiki/Glossary.md", "# Glossary\n")
    _write(root, "backend/app.py", "print('ok')\n")
    _write(root, "AGENTS.md", "Rules\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    return _git(root, "rev-parse", "HEAD")


class DocsOnlyCIClassifierTests(unittest.TestCase):
    def test_approved_paths_only(self):
        approved = ("docs/wiki/Glossary.md", "docs/wiki/Troubleshooting.md")
        rejected = (
            "AGENTS.md", "docs/AGENTS.md", "DESIGN.md", "README.md",
            "CONTRIBUTING.md", "SECURITY.md", "docs/agent-rules/offline-pwa.md",
            "docs/local-first-rollout-status.md", "docs/guide.md", "docs/wiki/Home.md", "docs/wiki/User-Guide.md",
            "backend/notes.md",
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
            ("root docs test fixture", {"README.md": "# Changed\n"}, False),
            ("implementation docs", {"docs/guide.md": "# Updated\n"}, False),
            ("audited wiki docs", {"docs/wiki/Glossary.md": "# Updated\n"}, True),
            ("new wiki docs", {"docs/wiki/New.md": "# New\n"}, False),
            ("agent rule fixture", {"docs/agent-rules/offline-pwa.md": "# New\n"}, False),
            ("feature docs fixture", {"docs/wiki/User-Guide.md": "# Edited\n"}, False),
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
                    _git(root, "rm", "docs/wiki/Glossary.md")
                else:
                    _git(root, "mv", "docs/wiki/Glossary.md", "docs/wiki/Other.md")
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
        self.assertIn(
            'git show "$PRKS_PR_BASE_SHA:scripts/ci/docs_only.py"', unit
        )
        self.assertIn('python "$base_classifier" --base "$PRKS_PR_BASE_SHA"', unit)
        self.assertNotIn("python -m scripts.ci.docs_only --base", unit)
        self.assertNotIn("needs:", unit)

        def step(name: str) -> str:
            match = re.search(
                r"(?ms)^      - name: " + re.escape(name)
                + r"\n(.*?)(?=^      - name: |\Z)",
                unit,
            )
            self.assertIsNotNone(match, name)
            return match.group(1)

        for name in (
            "Set up Python",
            "Set up Node.js",
            "Install pinned runtime dependencies",
            "Run PRKS unit, API, structural, and Node tests",
        ):
            self.assertIn(
                "if: steps.docs-scope.outputs.docs_only != 'true'", step(name)
            )
        mergify = step("Report unit test results to Mergify")
        self.assertIn(
            "steps.docs-scope.outputs.docs_only != 'true' &&", mergify
        )
        docs = step("Validate changed documentation links, anchors, and fences")
        install = step("Install pinned Markdown validator (docs-only PR)")
        docs_python = step("Set up Python for documentation-only PR")
        self.assertIn("if: steps.docs-scope.outputs.docs_only == 'true'", docs_python)
        self.assertIn('python-version: "3.12"', docs_python)
        classifier = step("Classify documentation-only PR (fail closed)")
        self.assertIn('if base_classifier="$(mktemp)"; then', classifier)
        self.assertIn("if: steps.docs-scope.outputs.docs_only == 'true'", docs)
        self.assertIn("if: steps.docs-scope.outputs.docs_only == 'true'", install)
        self.assertIn("run: python scripts/ci/install_pinned_python.py docs", install)
        self.assertIn("run: python -m scripts.ci.check_docs_only", docs)
        self.assertIn("run: python run_tests.py", unit)


    def test_postgresql_jobs_keep_required_names_and_fail_closed_on_docs_plan(self):
        # These PostgreSQL services used to start even for the two approved
        # wiki-only paths. A separate cheap plan lets them skip provisioning
        # while an unknown/failed plan still executes the full database gate.
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/test-gate.yml"
        ).read_text(encoding="utf-8")
        plan = workflow.split("  docs-only-plan:\n", 1)[1].split(
            "  unit-api-contract:\n", 1
        )[0]
        unit = workflow.split("  unit-api-contract:\n", 1)[1].split(
            "  postgres-helpers:\n", 1
        )[0]
        self.assertIn("name: PostgreSQL docs-only scope", plan)
        self.assertIn("persist-credentials: false", plan)
        self.assertIn("docs_only: ${{ steps.docs-scope.outputs.docs_only }}", plan)
        self.assertIn(
            "Classify docs-only scope for PostgreSQL jobs (fail closed)", plan
        )
        self.assertIn('git show "$PRKS_PR_BASE_SHA:scripts/ci/docs_only.py"', plan)
        self.assertIn('if base_classifier="$(mktemp)"; then', plan)
        self.assertIn('echo "docs_only=$docs_only" >> "$GITHUB_OUTPUT"', plan)
        # The exact script must match the immutable-base unit classifier;
        # otherwise a second looser skip policy could silently grow.
        def script(block: str, title: str) -> str:
            start = block.index("      - name: " + title + "\n")
            end = block.find("\n      - name: ", start + 1)
            return block[start:end if end >= 0 else len(block)].split(
                "\n", 1
            )[1]

        self.assertEqual(
            script(plan, "Classify docs-only scope for PostgreSQL jobs (fail closed)"),
            script(unit, "Classify documentation-only PR (fail closed)"),
        )
        for job_id, required_name in (
            ("postgres-helpers", "PostgreSQL helper tests"),
            ("backend-parity", "Backend parity (SQLite + PostgreSQL)"),
        ):
            with self.subTest(job_id=job_id):
                job = workflow.split("  " + job_id + ":\n", 1)[1].split(
                    "\n  ", 1
                )[0]
                self.assertIn("name: " + required_name, job)
                self.assertIn("needs: docs-only-plan", job)
                self.assertIn("always() && github.event_name != 'schedule'", job)
                self.assertIn("needs.docs-only-plan.result != 'success'", job)
                self.assertIn("needs.docs-only-plan.outputs.docs_only != 'true'", job)
                self.assertIn("services:\n      postgres:", job)
        # The event triggers themselves must NOT filter paths: required
        # contexts need to keep reporting on docs-only pull requests.
        triggers = workflow.split("on:\n", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertNotIn("paths:", triggers)
        self.assertNotIn("paths-ignore:", triggers)


class DocsOnlyLinkValidationTests(unittest.TestCase):
    def test_valid_links_headings_references_and_fences(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fence = chr(96) * 3
            _write(root, "README.md", (
                "# Top\n## Repeated\n## Repeated\n"
                "## my_func\n## Foo & Bar\n"
            ))
            _write(root, "docs/guide(v2).md", "# Contents\n")
            _write(root, "docs/guide.md", (
                "# [Install](https://example.org/setup.md)\n"
                "Getting started\n===============\n"
                "[readme](../README.md#top)\n"
                "[repeated](../README.md#repeated-1)\n"
                "[underscore](../README.md#my_func)\n"
                "[punctuation](../README.md#foo--bar)\n"
                "[here](#install) [setext](#getting-started)\n"
                "[paren](guide(v2).md)\n"
                "[referenced][manual]\n\n"
                "[manual]: guide(v2).md\n"
                f"{fence}md\n[invalid](missing.md)\n{fence}\n"
                "[external](https://example.org/missing.md)\n"
            ))
            self.assertEqual(check_markdown(root, "docs/guide.md"), [])

    def test_fenced_headings_do_not_create_anchors(self):
        fence = chr(96) * 3
        content = (
            "# Setup\n"
            f"{fence}bash\n# Setup\n{fence}\n"
            "## Setup\n"
        )
        self.assertEqual(_anchors(content), {"setup", "setup-1"})

    def test_html_ids_on_arbitrary_elements_and_link_attrs(self):
        anchors = _anchors(
            '<div id="part"></div>\n'
            '<a class="menu" id="another">menu</a>\n'
            '<a class="menu" name="legacy">old</a>\n'
        )
        self.assertTrue({"part", "another", "legacy"}.issubset(anchors))

    def test_html_comments_do_not_create_anchors(self):
        content = '<!-- <div id="ghost"></div> -->\n<div id="real"></div>\n\n'
        self.assertEqual(_anchors(content), {"real"})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/guide.md", content + "[broken](#ghost)\n")
            self.assertTrue(any(
                "missing anchor #ghost" in error
                for error in check_markdown(root, "docs/guide.md")
            ))

    def test_raw_html_local_href_and_src_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/valid.md", "# Valid\n")
            _write(root, "docs/guide.md", (
                '<a href="valid.md#valid">Good</a>\n'
                '<a href="missing.md">Bad</a>\n'
                '<img src="not-there.png" alt="missing image">\n'
                '<!-- <a href="comment-only.md">Ignored</a> -->\n'
            ))
            errors = check_markdown(root, "docs/guide.md")
            self.assertEqual(len(errors), 2)
            self.assertTrue(any("missing.md" in error for error in errors))
            self.assertTrue(any("not-there.png" in error for error in errors))

    def test_broken_references_and_parenthesized_paths_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/guide.md", (
                "[broken][manual]\n\n[manual]: missing(v2).md\n"
                "[missing](not-there(v2).md)\n"
            ))
            errors = check_markdown(root, "docs/guide.md")
            self.assertEqual(len(errors), 2)
            self.assertTrue(all("missing relative target" in x for x in errors))

    def test_list_nested_fences_use_commonmark_containers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/guide.md", (
                "# Commands\n"
                "- ```bash\n"
                "  [not-a-link](missing.md)\n"
                "  ```\n"
            ))
            self.assertEqual(check_markdown(root, "docs/guide.md"), [])
            _write(root, "docs/guide.md", (
                "- ```bash\n"
                "  echo missing end\n"
            ))
            self.assertTrue(any(
                "unclosed" in error
                for error in check_markdown(root, "docs/guide.md")
            ))

    def test_blockquoted_fences_and_indented_fake_closers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            valid = (
                "> ```bash\n> echo hi\n> ```\n"
                "\n- item\n\n  > ```\n  > x\n  > ```\n"
            )
            _write(root, "docs/guide.md", valid)
            self.assertEqual(check_markdown(root, "docs/guide.md"), [])
            for valid_fence in (
                "```\na\u2028b\n```\n",
                "```\na\u2029b\n```\n",
                "```\na\x0cb\n```\n",
                "> ```\n> a\u2028b\n> ```\n",
            ):
                with self.subTest(valid_fence=repr(valid_fence)):
                    _write(root, "docs/guide.md", valid_fence)
                    self.assertEqual(check_markdown(root, "docs/guide.md"), [])
            for invalid in (
                "```",
                "```\nx",
                "```\nx\u2028y",

                "> ```bash\n> echo hi\n",
                "```bash\n    ```\n",
                "- item\n\n  > ```\n  > x\n",
            ):
                with self.subTest(invalid=invalid):
                    _write(root, "docs/guide.md", invalid)
                    self.assertTrue(any(
                        "unclosed" in error
                        for error in check_markdown(root, "docs/guide.md")
                    ))

    def test_broken_relative_link_and_anchor_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fence = chr(96) * 3
            _write(root, "README.md", "# Present\n")
            _write(root, "docs/guide.md", (
                "[missing](not-here.md)\n"
                "[anchor](../README.md#absent)\n"
                f"{fence}python\nprint('no closing fence')\n"
            ))
            errors = check_markdown(root, "docs/guide.md")
            self.assertEqual(len(errors), 3)
            self.assertTrue(any("missing relative target" in x for x in errors))
            self.assertTrue(any("missing anchor" in x for x in errors))
            self.assertTrue(any("unclosed" in x for x in errors))


if __name__ == "__main__":
    unittest.main()
