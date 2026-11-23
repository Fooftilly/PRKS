"""Fail-closed diff classification and documentation validator regression tests."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess  # nosec B404 - fixed local test fixture commands
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.ci import check_docs_only, docs_only
from scripts.ci.check_docs_only import _anchors, check_markdown
from scripts.ci.docs_only import (
    AUDITED_DOC_FILES,
    AUDITED_CONFIG_FILES,
    POSTGRES_SCOPE,
    _is_plain_relative,
    changed_documentation,
    effective_base,
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


def _main_in(root: Path, main: Any, argv: list[str], event_head: str | None = None) -> tuple[int, str]:
    """Run a CI entrypoint's ``main()`` in-process from ``root`` (so mutation testing sees it).

    ``event_head`` writes a pull_request event naming that head SHA, as Actions does.
    """
    out = io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(contextlib.chdir(root))
        stack.enter_context(mock.patch.object(sys, "argv", ["prog", *argv]))
        env = stack.enter_context(mock.patch.dict(os.environ))
        env.pop("GITHUB_EVENT_PATH", None)
        if event_head is not None:
            event = root.parent / f"{root.name}-event.json"
            event.write_text(json.dumps({"pull_request": {"head": {"sha": event_head}}}), encoding="utf-8")
            env["GITHUB_EVENT_PATH"] = str(event)
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        try:
            code = main()
        except SystemExit as exit_:
            code = exit_.code if isinstance(exit_.code, int) else 1
    return code, out.getvalue()


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
            "docs/wiki/Architecture.md", "docs/e2e-performance.md",
            "docs/agent-workflows/agent-queue.md",
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

    def test_audited_docs_are_plain_paths_and_never_agent_guidance(self):
        # is_approved_markdown is exact set membership, so the set itself must
        # never hold an AGENTS.md or a path that escapes or aliases the tree.
        for path in AUDITED_DOC_FILES | AUDITED_CONFIG_FILES:
            with self.subTest(path=path):
                self.assertTrue(_is_plain_relative(path))
                self.assertNotEqual(path.rsplit("/", 1)[-1].lower(), "agents.md")

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
                # An approved diff reports exactly its changed paths.
                self.assertEqual(result, tuple(sorted(changes)) if expected else None)
                self.assertIsNone(changed_documentation(root, "not-a-valid-sha"))
                # A well-formed SHA git does not have refuses, never raises.
                self.assertIsNone(changed_documentation(root, "0" * 40))

    def test_audited_config_files_are_exact_root_paths(self):
        for path in (".codacy.yml", ".coderabbit.yaml", ".pr_agent.toml"):
            with self.subTest(approved=path):
                self.assertTrue(is_audited_config(path))
        for path in (
            ".mergify.yml", ".coderabbit.yml", "docs/.coderabbit.yaml", ".greptile/config.json",
            ".codacy.yaml", "docs/.codacy.yml", "./.codacy.yml", "/.codacy.yml",
            ".github/workflows/test-gate.yml", "eslint.config.mjs", "ruff.toml",
            "setup.cfg", "pyrightconfig.json", ".bandit",
            "sonar-project.properties", ".sonarcloud.properties",
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
                    "eslint.config.mjs", "ruff.toml",
                    "pyrightconfig.json", "pyrightconfig.typed-slice.json",
                )
                for script in root_files:
                    if name in (ROOT / script).read_text(encoding="utf-8"):
                        hits.append(script)
                self.assertEqual(hits, [])

    def test_audited_docs_exist_and_are_read_by_no_suite_or_runtime(self):
        # A docs page may skip the unit job only while no code, test, script
        # or workflow reads it. A new reader must remove it from
        # AUDITED_DOC_FILES. Its own name or full path counts as a read.
        readers = (".github", "backend", "frontend", "frontend-app", "scripts", "tests", "tools")
        suffixes = {".py", ".js", ".mjs", ".ts", ".vue", ".sh", ".json", ".toml", ".cfg", ".yml", ".yaml"}
        allowed = {ROOT / "scripts/ci/docs_only.py", Path(__file__).resolve()}
        texts = {}
        for top in readers:
            for path in (ROOT / top).rglob("*"):
                if (
                    path.is_file() and path not in allowed
                    and "node_modules" not in path.parts and path.suffix in suffixes
                ):
                    try:
                        texts[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue
        for script in ("run_tests.py", "prks_app.py", "Dockerfile", "docker-compose.yml"):
            texts[script] = (ROOT / script).read_text(encoding="utf-8")
        for name in sorted(AUDITED_DOC_FILES):
            with self.subTest(doc=name):
                self.assertTrue((ROOT / name).is_file(), "audited page is missing")
                basename = name.rsplit("/", 1)[-1]
                hits = sorted(
                    path for path, text in texts.items()
                    if name in text or basename in text
                )
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
            "./README.md", "docs/./a.md",
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
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = _repo(root)
            _write(root, "AGENTS.md", "Changed\n")
            _git(root, "commit", "-qam", "change")

            def run(*extra: str) -> tuple[int, str]:
                return _main_in(root, docs_only.main, ["--base", base, *extra])

            self.assertEqual(run(), (0, "false\n"))
            self.assertEqual(run("--scope", "postgres"), (0, "true\n"))
            self.assertEqual(run("--scope", "everything")[0], 2)
            # The default scope still accepts an audited file.
            base = _git(root, "rev-parse", "HEAD")
            _write(root, ".codacy.yml", "---\nexclude_paths: []\n")
            _git(root, "commit", "-qam", "config")
            self.assertEqual(run(), (0, "true\n"))
            self.assertEqual(_main_in(root, docs_only.main, [])[0], 2)

    def test_the_markdown_validator_cli_reports_each_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = _repo(root)

            def check(*argv: str) -> tuple[int, str]:
                return _main_in(root, check_docs_only.main, list(argv))

            self.assertEqual(check()[0], 2)
            _write(root, "backend/app.py", "print('changed')\n")
            _git(root, "commit", "-qam", "code")
            self.assertEqual(check("--base", base),
                             (1, "Docs validation refused: not a verified docs-only PR\n"))
            base = _git(root, "rev-parse", "HEAD")
            _write(root, "docs/job-worker.md", "# Jobs\n[gone](gone.md)\n")
            _git(root, "add", ".")
            _git(root, "commit", "-qm", "broken link")
            self.assertEqual(check("--base", base),
                             (1, "docs/job-worker.md:2: missing relative target gone.md\n"))
            base = _git(root, "rev-parse", "HEAD")
            _write(root, "docs/job-worker.md", "# Jobs\n")
            _write(root, ".codacy.yml", "---\nexclude_paths: []\n")
            _git(root, "commit", "-qam", "fix and config")
            self.assertEqual(check("--base", base), (0, (
                "Validated 1 changed Markdown files (links, anchors, fences)\n"
                "1 audited analysis-tool config file(s) changed; no CI suite reads them\n"
            )))

    def test_the_entrypoints_run_as_scripts(self):
        # The only subprocess test left: the in-process tests above cannot
        # prove the files still start as scripts (mutation testing deselects this).
        for argv in (
            [str(ROOT / "scripts/ci/docs_only.py"), "--help"],
            ["-m", "scripts.ci.check_docs_only", "--help"],
        ):
            with self.subTest(argv=argv):
                result = subprocess.run(  # nosec B603 - fixed interpreter and script
                    [sys.executable, *argv], cwd=ROOT, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--base", result.stdout)

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
        code, out = _main_in(root, docs_only.main, ["--base", base, "--scope", scope], head_sha)
        self.assertEqual(code, 0)
        return out.strip()

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
            code, out = _main_in(root, check_docs_only.main, ["--base", stale], head)
            self.assertEqual(code, 0, out)
            self.assertIn("Validated 1 changed Markdown files", out)

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

    def test_an_event_head_that_is_not_a_sha_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            stale, head = self.stale_base_merge(root, {"docs/guide.md": "Changed\n"})
            self.assertEqual(self.run_cli(root, stale, head), "true")
            self.assertEqual(self.run_cli(root, stale, head[:12]), "false")
            self.assertEqual(self.run_cli(root, stale, "main"), "false")

    def test_a_base_that_is_not_a_sha_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            _, head = self.stale_base_merge(root, {"docs/guide.md": "Changed\n"})
            event = Path(folder) / "event.json"
            event.write_text(json.dumps({"pull_request": {"head": {"sha": head}}}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}):
                self.assertEqual(effective_base(root, "master"), "master")

    def test_a_missing_first_parent_is_fetched_shallow_without_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            stale, head = self.stale_base_merge(root, {"docs/guide.md": "Changed\n"})
            first = _git(root, "rev-parse", "HEAD^1")
            shallow = Path(folder) / "shallow"
            _git(Path(folder), "clone", "-q", "--depth=1", f"file://{root}", str(shallow))
            missing = subprocess.run(  # nosec B603,B607 - fixture git, shell=False
                ["git", "cat-file", "-e", f"{first}^{{commit}}"], cwd=shallow, capture_output=True, check=False)
            self.assertNotEqual(missing.returncode, 0)
            calls: list[tuple[str, ...]] = []
            real_git = docs_only._git

            def recording_git(where: Path, *args: str) -> Any:
                calls.append(args)
                return real_git(where, *args)

            with mock.patch.object(docs_only, "_git", recording_git):
                self.assertEqual(merge_base_parent(shallow, stale, head), first)
            # Both flags matter only for cost (one commit, no tag refs), which
            # the result cannot show, so the fetch command itself is pinned.
            (fetch,) = [call for call in calls if call[0] == "fetch"]
            self.assertEqual(fetch[-2:], ("origin", first))
            self.assertTrue({"--no-tags", "--depth=1"}.issubset(fetch), fetch)
            self.assertEqual(_git(shallow, "cat-file", "-t", first), "commit")

    def test_the_event_base_is_kept_when_git_cannot_answer(self):
        # Not a repository, or a merge whose first parent is neither present
        # nor fetchable: the immutable event base, never an error.
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(merge_base_parent(Path(folder), "a" * 40, "b" * 40), "a" * 40)
            root = Path(folder) / "repo"
            root.mkdir()
            stale, head = self.stale_base_merge(root, {"docs/guide.md": "Changed\n"})
            first = _git(root, "rev-parse", "HEAD^1")
            merge = _git(root, "rev-parse", "HEAD")
            shallow = Path(folder) / "shallow"
            _git(Path(folder), "clone", "-q", "--depth=1", f"file://{root}", str(shallow))
            _git(shallow, "remote", "remove", "origin")
            self.assertEqual(_git(shallow, "rev-parse", "HEAD"), merge)
            self.assertNotEqual(first, stale)
            self.assertEqual(merge_base_parent(shallow, stale, head), stale)

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
        self.assertIn('python-version: "3.14"', docs_python)
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

    def test_html_blocks_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/guide.md", '<div>\n<a href="missing.md">Bad</a>\n</div>\n')
            self.assertEqual(check_markdown(root, "docs/guide.md"), [
                "docs/guide.md:1: missing relative target missing.md",
            ])

    def test_html_inside_inline_code_is_text(self):
        tick = chr(96)
        content = f'Use {tick}<a id="fake"></a>{tick} here.\n\n[x](#fake)\n'
        self.assertEqual(_anchors(content), set())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/guide.md", content)
            self.assertEqual(check_markdown(root, "docs/guide.md"), [
                "docs/guide.md:3: missing anchor #fake in docs/guide.md",
            ])

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
            self.assertEqual(check_markdown(root, "docs/guide.md"), [
                # Both links share one paragraph, which starts on line 1.
                "docs/guide.md:1: missing relative target not-here.md",
                "docs/guide.md:1: missing anchor #absent in ../README.md",
                f"docs/guide.md: unclosed {fence} fence",
            ])

    def test_errors_name_the_line_of_every_link_kind(self):
        # Markdown images, raw HTML blocks and inline HTML are all checked,
        # and each error names its own source line.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/guide.md", (
                "# Guide\n"
                "![logo](missing.png)\n\n"
                '<a href="block.md">block</a>\n\n'
                'Text <img src="inline.png"> here.\n'
                "[self](#nowhere)\n"
            ))
            self.assertEqual(check_markdown(root, "docs/guide.md"), [
                "docs/guide.md:2: missing relative target missing.png",
                "docs/guide.md:4: missing relative target block.md",
                # Markdown links of a paragraph come before its inline HTML.
                "docs/guide.md:6: missing anchor #nowhere in docs/guide.md",
                "docs/guide.md:6: missing relative target inline.png",
            ])

    def test_external_protocol_relative_and_site_root_links_are_not_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, "docs/guide.md", (
                "[a](https://example.org/x.md) [b](//example.org/x.md) "
                "[c](/settings/x.md) [d](mailto:x@example.org)\n"
            ))
            self.assertEqual(check_markdown(root, "docs/guide.md"), [])

    def test_an_unreadable_anchor_target_has_no_anchors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs").mkdir()
            (root / "docs/latin1.md").write_bytes(b"# Caf\xe9\n")
            _write(root, "docs/guide.md", "[x](latin1.md#caf)\n")
            self.assertEqual(check_markdown(root, "docs/guide.md"), [
                "docs/guide.md:1: missing anchor #caf in latin1.md",
            ])

    def test_heading_anchors_use_visible_text_breaks_and_repeat_suffixes(self):
        tick = chr(96)
        content = (
            f"# Run {tick}prks{tick} now\n"
            "## ![Logo](logo.png) Brand\n"
            "Two\nlines\n===\n"
            "Hard\\\nbreak\n---\n"
            "## Same\n## Same\n## Same\n"
            'Some <span id="inline-anchor">text</span>.\n'
        )
        self.assertEqual(_anchors(content), {
            "run-prks-now", "logo-brand", "two-lines", "hard-break",
            "same", "same-1", "same-2", "inline-anchor",
        })

    def test_published_wiki_page_must_pass_publisher_link_check(self):
        # The skipped unit suite runs tests/test_wiki_source_links.py; a
        # relative link to an existing repo file resolves in the checkout but
        # 404s on the wiki, so the docs validator must reject it too.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            link = "[readme](../../README.md)\n"
            _write(root, "README.md", "# Present\n")
            _write(root, "docs/wiki/Page.md", link)
            _write(root, "docs/wiki/README.md", link)
            _write(root, "docs/other/guide.md", link)
            errors = check_markdown(root, "docs/wiki/Page.md")
            self.assertEqual(errors, ["docs/wiki/Page.md: ](../../README.md)"])
            # README is not published; non-wiki pages keep checkout semantics.
            self.assertEqual(check_markdown(root, "docs/wiki/README.md"), [])
            self.assertEqual(check_markdown(root, "docs/other/guide.md"), [])


if __name__ == "__main__":
    unittest.main()
