"""Fail-closed diff classification and documentation validator regression tests."""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - fixed local test fixture commands
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.check_docs_only import _anchors, check_markdown
from scripts.ci.docs_only import (
    AUDITED_CONFIG_FILES,
    POSTGRES_SCOPE,
    changed_documentation,
    is_approved_markdown,
    is_audited_config,
    is_postgres_irrelevant_markdown,
    merge_base_parent,
)

ROOT = Path(__file__).resolve().parents[1]


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
    _write(root, "docs/wiki/Getting-Started.md", "# Getting Started\n")
    _write(root, "docs/wiki/Offline-and-Sync.md", "# Offline and Sync\n")
    _write(root, "backend/app.py", "print('ok')\n")
    _write(root, "AGENTS.md", "Rules\n")
    _write(root, ".codacy.yml", "---\n")
    _write(root, ".mergify.yml", "rules: []\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    return _git(root, "rev-parse", "HEAD")


# Fixtures that quote a bot's own comment text naming an audited config file.
# They never open the file; only this exact quoted line is exempt.
_QUOTED_CONFIG_NAMES = {
    (".coderabbit.yaml", "tests/test_review_bot_noise_workflow.py"):
        "**Configuration used**: Repository: Fooftilly/PRKS/.coderabbit.yaml",
}


class DocsOnlyCIClassifierTests(unittest.TestCase):
    def test_approved_paths_only(self):
        approved = (
            "docs/wiki/Getting-Started.md", "docs/wiki/Glossary.md",
            "docs/wiki/Offline-and-Sync.md", "docs/wiki/Troubleshooting.md",
        )
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
            ("getting started", {"docs/wiki/Getting-Started.md": "# Updated\n"}, True),
            ("offline sync", {"docs/wiki/Offline-and-Sync.md": "# Updated\n"}, True),
            ("two vetted wiki docs", {
                "docs/wiki/Getting-Started.md": "# Updated\n",
                "docs/wiki/Offline-and-Sync.md": "# Updated\n",
            }, True),
            ("vetted wiki with code", {
                "docs/wiki/Getting-Started.md": "# Updated\n",
                "backend/app.py": "print('changed')\n",
            }, False),
            ("vetted wiki with non-audited doc", {
                "docs/wiki/Offline-and-Sync.md": "# Updated\n",
                "docs/wiki/User-Guide.md": "# Updated\n",
            }, False),
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

    def test_audited_config_files_are_exact_root_paths(self):
        for path in (".codacy.yml", ".coderabbit.yaml", ".pr_agent.toml"):
            with self.subTest(approved=path):
                self.assertTrue(is_audited_config(path))
        for path in (
            ".mergify.yml", ".coderabbit.yml", "docs/.coderabbit.yaml", ".greptile/config.json",
            ".codacy.yaml", "docs/.codacy.yml", "./.codacy.yml", "/.codacy.yml",
            ".github/workflows/test-gate.yml", "eslint.config.mjs", "ruff.toml",
            "setup.cfg", "pyrightconfig.json", ".bandit",
        ):
            with self.subTest(rejected=path):
                self.assertFalse(is_audited_config(path))

    def test_audited_config_files_are_read_by_no_suite_or_runtime(self):
        # The skip is only sound while nothing in the repository reads these
        # files. A new reader must remove the file from AUDITED_CONFIG_FILES.
        readers = (".github", "backend", "frontend", "frontend-app", "scripts", "tests", "tools")
        allowed = {
            ROOT / "scripts/ci/docs_only.py",
            Path(__file__).resolve(),
        }
        for name in sorted(AUDITED_CONFIG_FILES):
            with self.subTest(config=name):
                hits = []
                for top in readers:
                    for path in (ROOT / top).rglob("*"):
                        if (
                            not path.is_file() or path in allowed
                            or "node_modules" in path.parts
                            or path.suffix not in {".py", ".js", ".mjs", ".ts", ".vue", ".sh", ".json", ".toml", ".cfg", ".yml", ".yaml"}
                        ):
                            continue
                        try:
                            text = path.read_text(encoding="utf-8")
                        except (OSError, UnicodeError):
                            continue
                        quoted = _QUOTED_CONFIG_NAMES.get((name, str(path.relative_to(ROOT))))
                        if quoted is not None:
                            # Only quoted bot comment text may name the file here.
                            text = text.replace(quoted, "")
                        if name in text:
                            hits.append(str(path.relative_to(ROOT)))
                root_files = (
                    "run_tests.py", "prks_app.py", "Dockerfile", ".dockerignore",
                    "docker-compose.yml", "docker-build.sh", "docker-entrypoint.sh",
                    "eslint.config.mjs", "ruff.toml", "setup.cfg",
                    "pyrightconfig.json", "pyrightconfig.typed-slice.json",
                )
                for script in root_files:
                    if name in (ROOT / script).read_text(encoding="utf-8"):
                        hits.append(script)
                self.assertEqual(hits, [])

    def test_audited_config_git_changes_fail_closed(self):
        cases = (
            ("codacy only", {".codacy.yml": "---\nexclude_paths: []\n"}, True),
            ("codacy and audited wiki", {
                ".codacy.yml": "---\n# changed\n",
                "docs/wiki/Glossary.md": "# Updated\n",
            }, True),
            ("new pr agent config", {".pr_agent.toml": "[config]\n"}, True),
            ("codacy with code", {
                ".codacy.yml": "---\n# changed\n",
                "backend/app.py": "print('changed')\n",
            }, False),
            ("mergify read by tests", {".mergify.yml": "rules: [1]\n"}, False),
        )
        for scope in (None, POSTGRES_SCOPE):
            for label, changes, expected in cases:
                with self.subTest(scope=scope, label=label), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    base = _repo(root)
                    for path, body in changes.items():
                        _write(root, path, body)
                    _git(root, "add", ".")
                    _git(root, "commit", "-qm", "change")
                    if scope is None:
                        result = changed_documentation(root, base)
                    else:
                        result = changed_documentation(root, base, scope)
                    self.assertEqual(result is not None, expected)

    def test_deleted_audited_config_runs_full_ci(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = _repo(root)
            _git(root, "rm", "-q", ".codacy.yml")
            _git(root, "commit", "-qm", "delete")
            self.assertIsNone(changed_documentation(root, base))
            self.assertIsNone(changed_documentation(root, base, POSTGRES_SCOPE))

    def test_postgres_scope_accepts_only_plain_markdown(self):
        approved = (
            "AGENTS.md", "docs/AGENTS.md", "SECURITY.md", "README.md",
            "docs/guide.md", "docs/wiki/Home.md", "backend/AGENTS.md",
            ".github/pull_request_template.md",
        )
        rejected = (
            "", "/README.md", "../README.md", "docs/../backend/a.md",
            "docs//a.md", "docs\\a.md", "README.MD", "docs/a.markdown",
            "backend/db/schema/999_x.sql", "tests/pg/test_a.py",
            "tests/pg/search_ranking_baseline.json", "requirements.txt",
            ".github/workflows/test-gate.yml", "scripts/test-postgres",
        )
        for path in approved:
            with self.subTest(approved=path):
                self.assertTrue(is_postgres_irrelevant_markdown(path))
        for path in rejected:
            with self.subTest(rejected=path):
                self.assertFalse(is_postgres_irrelevant_markdown(path))

    def test_postgres_scope_git_changes_fail_closed(self):
        cases = (
            ("root docs", {"README.md": "# Changed\n", "AGENTS.md": "New\n"}, True),
            ("implementation docs", {"docs/guide.md": "# Updated\n"}, True),
            ("new markdown", {"docs/new-page.md": "# New\n"}, True),
            ("markdown with code", {
                "docs/guide.md": "# Updated\n",
                "backend/app.py": "print('changed')\n",
            }, False),
            ("code only", {"backend/app.py": "print('new')\n"}, False),
            ("workflow", {".github/workflows/ci.yml": "name: CI\n"}, False),
            ("empty", {}, False),
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
                result = changed_documentation(root, base, POSTGRES_SCOPE)
                self.assertEqual(result is not None, expected)
                self.assertIsNone(
                    changed_documentation(root, "not-a-valid-sha", POSTGRES_SCOPE)
                )
                self.assertIsNone(changed_documentation(root, base, "unknown"))

    def test_postgres_scope_refuses_deleted_or_renamed_markdown(self):
        for kind in ("delete", "rename"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                base = _repo(root)
                if kind == "delete":
                    _git(root, "rm", "-q", "docs/guide.md")
                else:
                    _git(root, "mv", "docs/guide.md", "docs/other.md")
                _git(root, "commit", "-qm", "change")
                self.assertIsNone(changed_documentation(root, base, POSTGRES_SCOPE))

    def test_cli_scope_defaults_narrow_and_rejects_unknown(self):
        script = Path(__file__).resolve().parents[1] / "scripts/ci/docs_only.py"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = _repo(root)
            _write(root, "AGENTS.md", "Changed\n")
            _git(root, "commit", "-qam", "change")

            def run(*extra: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(  # nosec B603 - fixed interpreter and script
                    [sys.executable, str(script), "--base", base, *extra],
                    cwd=root, capture_output=True, text=True, check=False,
                )

            self.assertEqual(run().stdout.strip(), "false")
            self.assertEqual(run("--scope", "postgres").stdout.strip(), "true")
            self.assertNotEqual(run("--scope", "everything").returncode, 0)

    def stale_base_merge(self, root: Path, pr_files: dict[str, str]) -> tuple[str, str]:
        """GitHub's shape: the event base B0 is stale, master moved on with a code
        change, and HEAD is the merge of the PR head onto the new master tip.
        Returns (stale event base, PR head)."""
        stale = _repo(root)
        _git(root, "checkout", "-qb", "pr")
        for path, content in pr_files.items():
            _write(root, path, content)
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "pr change")
        head = _git(root, "rev-parse", "HEAD")
        _git(root, "checkout", "-q", "master")
        _write(root, "backend/moved_on.py", "VALUE = 1\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "master moved on")
        _git(root, "checkout", "-q", "--detach")
        _git(root, "merge", "-q", "--no-ff", "-m", "Merge pr", head)
        return stale, head

    def run_cli(self, root: Path, base: str, head_sha: str | None, scope: str = "postgres") -> str:
        script = Path(__file__).resolve().parents[1] / "scripts/ci/docs_only.py"
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_EVENT_PATH"}
        if head_sha is not None:
            event = root.parent / f"{root.name}-event.json"
            event.write_text(json.dumps({"pull_request": {"head": {"sha": head_sha}}}), encoding="utf-8")
            env["GITHUB_EVENT_PATH"] = str(event)
        return subprocess.run(  # nosec B603 - fixed interpreter and script
            [sys.executable, str(script), "--base", base, "--scope", scope],
            cwd=root, capture_output=True, text=True, check=False, env=env,
        ).stdout.strip()

    def test_a_stale_event_base_uses_the_merge_commits_first_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            stale, head = self.stale_base_merge(root, {"docs/guide.md": "Changed\n"})
            # Against the stale base, master's own code change shows up.
            self.assertIsNone(changed_documentation(root, stale, POSTGRES_SCOPE))
            self.assertEqual(self.run_cli(root, stale, None), "false")
            # Anchored to the merge's first parent, only the PR's Markdown is left.
            self.assertEqual(self.run_cli(root, stale, head), "true")

    def test_the_markdown_validator_uses_the_same_base_as_the_classifier(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            stale, head = self.stale_base_merge(root, {"docs/wiki/Glossary.md": "# Glossary\n\nChanged.\n"})
            self.assertEqual(self.run_cli(root, stale, head, scope="docs"), "true")
            event = root.parent / "validator-event.json"
            event.write_text(json.dumps({"pull_request": {"head": {"sha": head}}}), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_EVENT_PATH"}
            env["GITHUB_EVENT_PATH"] = str(event)
            env["PYTHONPATH"] = os.pathsep.join(p for p in (str(ROOT), env.get("PYTHONPATH")) if p)
            validate = subprocess.run(  # nosec B603 - fixed interpreter and module
                [sys.executable, "-m", "scripts.ci.check_docs_only", "--base", stale],
                cwd=root, capture_output=True, text=True, check=False, env=env,
            )
            self.assertEqual(validate.returncode, 0, validate.stdout + validate.stderr)

    def test_a_stale_base_merge_with_a_pr_code_change_still_runs_full_ci(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            stale, head = self.stale_base_merge(root, {"docs/guide.md": "Changed\n", "backend/x.py": "X = 1\n"})
            self.assertEqual(self.run_cli(root, stale, head), "false")

    def test_the_first_parent_is_used_only_for_a_merge_of_the_event_head(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            stale, head = self.stale_base_merge(root, {"docs/guide.md": "Changed\n"})
            merge = _git(root, "rev-parse", "HEAD")
            # Another head sha, or HEAD being the PR head itself (a PR author's
            # own merge commit), keeps the event base.
            self.assertEqual(merge_base_parent(root, stale, "0" * 40), stale)
            self.assertEqual(merge_base_parent(root, stale, merge), stale)
            self.assertEqual(merge_base_parent(root, stale, None), stale)
            self.assertEqual(merge_base_parent(root, stale, head), _git(root, "rev-parse", "HEAD^1"))
            _git(root, "checkout", "-q", head)
            self.assertEqual(merge_base_parent(root, stale, head), stale)

    def test_deleted_or_renamed_markdown_is_not_safe_skip(self):
        for page in ("Glossary", "Getting-Started", "Offline-and-Sync"):
            for kind in ("delete", "rename"):
                with self.subTest(page=page, kind=kind), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    base = _repo(root)
                    original = f"docs/wiki/{page}.md"
                    if kind == "delete":
                        _git(root, "rm", original)
                    else:
                        _git(root, "mv", original, "docs/wiki/Other.md")
                    _git(root, "commit", "-qm", "change")
                    self.assertIsNone(changed_documentation(root, base))


class DocsOnlyWorkflowTests(unittest.TestCase):
    def test_required_unit_context_kept_with_step_level_skip(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/test-gate.yml"
        ).read_text(encoding="utf-8")
        unit = workflow.split("  unit-api-contract:\n", 1)[1].split(
            "  postgres-helpers-shards:\n", 1
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
            assert match is not None, name
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
        # Docs-only changes skip expensive shards but must report a real
        # required PostgreSQL helper gate. Code changes must run every shard.
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/test-gate.yml"
        ).read_text(encoding="utf-8")
        plan = workflow.split("  docs-only-plan:\n", 1)[1].split(
            "  unit-api-contract:\n", 1
        )[0]
        unit = workflow.split("  unit-api-contract:\n", 1)[1].split(
            "  postgres-helpers-shards:\n", 1
        )[0]
        self.assertIn("name: PostgreSQL docs-only scope", plan)
        self.assertIn("persist-credentials: false", plan)
        self.assertIn("docs_only: ${{ steps.docs-scope.outputs.docs_only }}", plan)

        def script(block: str, title: str) -> str:
            start = block.index("      - name: " + title + "\n")
            end = block.find("\n      - name: ", start + 1)
            return block[start:end if end >= 0 else len(block)].split(
                "\n", 1
            )[1].rstrip()

        def code(text: str) -> str:
            return "\n".join(
                line for line in text.splitlines()
                if not line.lstrip().startswith("#")
            )

        # Identical fail-closed script; only the PostgreSQL jobs ask for the
        # wider Markdown-only scope.
        unit_call = 'python "$base_classifier" --base "$PRKS_PR_BASE_SHA")"'
        plan_call = 'python "$base_classifier" --base "$PRKS_PR_BASE_SHA" --scope postgres)"'
        unit_script = code(script(unit, "Classify documentation-only PR (fail closed)"))
        self.assertIn(unit_call, unit_script)
        self.assertNotIn("--scope", unit_script)
        self.assertEqual(
            code(script(plan, "Classify docs-only scope for PostgreSQL jobs (fail closed)")),
            unit_script.replace(unit_call, plan_call),
        )
        expected_guard = (
            "!cancelled() && github.event_name != 'schedule' && "
            "(needs.docs-only-plan.result != 'success' || "
            "needs.docs-only-plan.outputs.docs_only != 'true')"
        )

        def job(job_id: str) -> str:
            match = re.search(
                r"(?ms)^  " + re.escape(job_id)
                + r":\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
                workflow,
            )
            assert match is not None
            return match.group(1)

        for job_id, required_name in (
            ("postgres-helpers-shards", "PostgreSQL helpers ("),
            ("backend-parity", "Backend parity (SQLite + PostgreSQL)"),
        ):
            with self.subTest(job_id=job_id):
                block = job(job_id)
                self.assertIn("name: " + required_name, block)
                self.assertIn("needs: docs-only-plan", block)
                guard = re.search(r"(?m)^    if: >-\n((?:      .*\n)+)", block)
                assert guard is not None
                self.assertEqual(
                    " ".join(line.strip() for line in guard.group(1).splitlines()),
                    expected_guard,
                )
                self.assertIn("services:\n      postgres:", block)

        required = job("postgres-helpers")
        self.assertIn("name: PostgreSQL helper tests", required)
        self.assertIn("needs: [docs-only-plan, postgres-helpers-shards]", required)
        self.assertIn(
            "if: ${{ always() && !cancelled() && github.event_name != 'schedule' }}",
            required,
        )
        self.assertIn("PLAN_RESULT: ${{ needs.docs-only-plan.result }}", required)
        self.assertIn(
            "DOCS_ONLY: ${{ needs.docs-only-plan.outputs.docs_only }}", required
        )
        self.assertIn('if [[ "$SHARD_RESULT" != "skipped" ]]; then', required)
        self.assertIn('if [[ "$SHARD_RESULT" != "success" ]]; then', required)
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
