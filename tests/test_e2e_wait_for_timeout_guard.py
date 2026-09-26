"""Regression tests for scripts/check_e2e_wait_for_timeout.py (#189)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "check_e2e_wait_for_timeout.py"
_SPEC = importlib.util.spec_from_file_location("prks_check_e2e_wait_for_timeout", _SCRIPT)
assert _SPEC and _SPEC.loader
checker = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "prks-test@example.com")
    _git(root, "config", "user.name", "PRKS Test")
    # Avoid depending on the developer's default branch name.
    _git(root, "checkout", "-b", "master")


def _commit_tree(root: Path, files: dict[str, str], message: str) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(root, "add", "--", rel)
    _git(root, "commit", "-m", message)


HISTORICAL_E2E = (
    "from playwright.sync_api import Page\n"
    "\n"
    "def test_historical(page: Page):\n"
    "    page.wait_for_timeout(300)\n"
    "    assert True\n"
)


class LineDetectionTests(unittest.TestCase):
    def test_detects_page_wait_for_timeout(self):
        self.assertTrue(checker.line_has_wait_for_timeout("    page.wait_for_timeout(250)\n"))

    def test_detects_self_page_wait_for_timeout(self):
        self.assertTrue(
            checker.line_has_wait_for_timeout("        self.page.wait_for_timeout(50)")
        )

    def test_ignores_comment_only_mentions(self):
        self.assertFalse(
            checker.line_has_wait_for_timeout("# page.wait_for_timeout(500) after mutation")
        )
        self.assertFalse(
            checker.line_has_wait_for_timeout("    # avoid page.wait_for_timeout here")
        )

    def test_ignores_unrelated_calls(self):
        self.assertFalse(checker.line_has_wait_for_timeout("    page.wait_for_timeout_ms(1)"))
        self.assertFalse(checker.line_has_wait_for_timeout("    wait_for_timeout(1)"))


class MarkerExemptionTests(unittest.TestCase):
    def test_same_line_marker_with_reason(self):
        lines = [
            "    page.wait_for_timeout(250)  # prks-allow-wait-for-timeout: absence window"
        ]
        self.assertTrue(checker.line_is_exempt(lines, 1))

    def test_previous_line_marker_with_reason(self):
        lines = [
            "    # prks-allow-wait-for-timeout: debounce under test",
            "    page.wait_for_timeout(100)",
        ]
        self.assertTrue(checker.line_is_exempt(lines, 2))

    def test_marker_without_reason_is_not_exempt(self):
        lines = [
            "    # prks-allow-wait-for-timeout:",
            "    page.wait_for_timeout(100)",
        ]
        self.assertFalse(checker.line_is_exempt(lines, 2))

    def test_blank_line_does_not_break_previous_marker(self):
        lines = [
            "    # prks-allow-wait-for-timeout: settle window",
            "",
            "    page.wait_for_timeout(100)",
        ]
        # Previous *non-blank* line carries the marker.
        self.assertTrue(checker.line_is_exempt(lines, 3))


class DiffParseTests(unittest.TestCase):
    def test_parses_added_wait_with_correct_lineno(self):
        diff = (
            "diff --git a/tests/e2e/test_x.py b/tests/e2e/test_x.py\n"
            "--- a/tests/e2e/test_x.py\n"
            "+++ b/tests/e2e/test_x.py\n"
            "@@ -10,0 +11,2 @@\n"
            "+    page.wait_for_timeout(100)\n"
            "+    assert True\n"
        )
        self.assertEqual(
            checker.parse_unified_diff_added_waits(diff),
            [("tests/e2e/test_x.py", 11, "    page.wait_for_timeout(100)")],
        )

    def test_ignores_added_waits_outside_e2e(self):
        diff = (
            "diff --git a/tests/browser/x.py b/tests/browser/x.py\n"
            "--- a/tests/browser/x.py\n"
            "+++ b/tests/browser/x.py\n"
            "@@ -1,0 +2 @@\n"
            "+page.wait_for_timeout(1)\n"
        )
        self.assertEqual(checker.parse_unified_diff_added_waits(diff), [])


class RepoScenarioTests(unittest.TestCase):
    def test_new_unapproved_timeout_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            updated = HISTORICAL_E2E + "\n    page.wait_for_timeout(999)\n"
            (root / "tests/e2e/test_hist.py").write_text(updated, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual([f.path for f in findings], ["tests/e2e/test_hist.py"])
            self.assertIn("new page.wait_for_timeout", findings[0].reason)
            rendered = findings[0].render()
            self.assertIn("No arbitrary sleeps", rendered)
            self.assertIn("wait_for_async", rendered)

    def test_historical_unchanged_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            findings = checker.collect_findings(root, base)
            self.assertEqual(findings, [])

    def test_unrelated_edit_near_historical_timeout_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            # Touch a non-timeout line in the same file.
            edited = HISTORICAL_E2E.replace("assert True", "assert True  # unrelated")
            (root / "tests/e2e/test_hist.py").write_text(edited, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual(findings, [])

    def test_approved_marker_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            approved = (
                HISTORICAL_E2E
                + "\n"
                + "    # prks-allow-wait-for-timeout: absence window\n"
                + "    page.wait_for_timeout(400)\n"
            )
            (root / "tests/e2e/test_hist.py").write_text(approved, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual(findings, [])

    def test_removed_marker_while_addition_remains_fails(self):
        """Same-diff: new sleep without marker fails (still required)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            without_marker = HISTORICAL_E2E + "\n    page.wait_for_timeout(400)\n"
            (root / "tests/e2e/test_hist.py").write_text(without_marker, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual(len(findings), 1)
            self.assertIn("new page.wait_for_timeout", findings[0].reason)

    def test_later_pr_removes_marker_call_unchanged_fails(self):
        """#189 ratchet: delete marker in a later PR while sleep stays → fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            approved = (
                HISTORICAL_E2E
                + "\n"
                + "    # prks-allow-wait-for-timeout: absence window\n"
                + "    page.wait_for_timeout(400)\n"
            )
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": approved, "README": "x\n"},
                "approved sleep landed",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            # Later PR: only the exemption marker is deleted; call unchanged.
            without_marker = (
                HISTORICAL_E2E + "\n" + "    page.wait_for_timeout(400)\n"
            )
            (root / "tests/e2e/test_hist.py").write_text(without_marker, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual(len(findings), 1)
            self.assertIn("lost its approved exemption", findings[0].reason)
            # Historical unexempted sleep in the same file must not also fail.
            self.assertIn("wait_for_timeout(400)", findings[0].snippet)

    def test_later_pr_removes_same_line_marker_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            approved = (
                "page.wait_for_timeout(250)  "
                "# prks-allow-wait-for-timeout: debounce under test\n"
            )
            _commit_tree(
                root,
                {"tests/e2e/test_timing.py": approved, "README": "x\n"},
                "same-line marker",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            (root / "tests/e2e/test_timing.py").write_text(
                "page.wait_for_timeout(250)\n",
                encoding="utf-8",
            )
            findings = checker.collect_findings(root, base)
            self.assertEqual(len(findings), 1)
            self.assertIn("lost its approved exemption", findings[0].reason)

    def test_path_allowlist_then_removal_fails(self):
        """Same-diff: new sleep while allowlisted, then un-allowlisted → fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/helper_timing.py": "page.wait_for_timeout(1)\n", "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            # New additional sleep in an allowlisted helper path.
            (root / "tests/e2e/helper_timing.py").write_text(
                "page.wait_for_timeout(1)\npage.wait_for_timeout(2)\n",
                encoding="utf-8",
            )
            helper = frozenset({"tests/e2e/helper_timing.py"})
            self.assertEqual(
                checker.collect_findings(
                    root,
                    base,
                    path_allowlist=helper,
                    base_path_allowlist=helper,
                ),
                [],
            )
            # Removing the allowlist entry: previously allowlisted wait(1) lost
            # its exemption, and wait(2) is still a new unapproved call.
            findings = checker.collect_findings(
                root,
                base,
                path_allowlist=frozenset(),
                base_path_allowlist=helper,
            )
            self.assertEqual(len(findings), 2)
            self.assertTrue(all(f.path == "tests/e2e/helper_timing.py" for f in findings))
            reasons = sorted(f.reason for f in findings)
            self.assertTrue(any("lost its approved exemption" in r for r in reasons))
            self.assertTrue(any("new page.wait_for_timeout" in r for r in reasons))

    def test_later_pr_removes_allowlist_call_unchanged_fails(self):
        """#189 ratchet: drop PATH_ALLOWLIST while helper sleep unchanged → fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {
                    "tests/e2e/helper_timing.py": "page.wait_for_timeout(1)\n",
                    "tests/e2e/test_hist.py": HISTORICAL_E2E,
                    "README": "x\n",
                },
                "allowlisted helper landed",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            helper = frozenset({"tests/e2e/helper_timing.py"})
            # Working tree unchanged; only the allowlist shrinks vs base.
            findings = checker.collect_findings(
                root,
                base,
                path_allowlist=frozenset(),
                base_path_allowlist=helper,
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].path, "tests/e2e/helper_timing.py")
            self.assertIn("lost its approved exemption", findings[0].reason)
            # Untouched historical path must not be pulled into the failure set.
            self.assertTrue(
                all(f.path != "tests/e2e/test_hist.py" for f in findings)
            )

    def test_untracked_e2e_module_with_timeout_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(root, {"README": "x\n"}, "base")
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            new_mod = root / "tests" / "e2e" / "test_brand_new.py"
            new_mod.parent.mkdir(parents=True, exist_ok=True)
            new_mod.write_text("page.wait_for_timeout(10)\n", encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual([f.path for f in findings], ["tests/e2e/test_brand_new.py"])

    def test_main_ok_on_clean_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            code = checker.main(["--root", str(root), "--base", "HEAD"])
            self.assertEqual(code, 0)

    def test_main_fails_on_new_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            (root / "tests/e2e/test_hist.py").write_text(
                HISTORICAL_E2E + "\npage.wait_for_timeout(1)\n",
                encoding="utf-8",
            )
            code = checker.main(["--root", str(root), "--base", base])
            self.assertEqual(code, 1)

    def test_invalid_base_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(root, {"README": "x\n"}, "base")
            code = checker.main(["--root", str(root), "--base", "origin/does-not-exist"])
            self.assertEqual(code, 2)


class SanitizeRevisionTests(unittest.TestCase):
    def test_accepts_head_sha_and_ref_names(self):
        self.assertEqual(checker.sanitize_git_revision("HEAD"), "HEAD")
        self.assertEqual(
            checker.sanitize_git_revision("origin/master"), "origin/master"
        )
        sha = "a" * 40
        self.assertEqual(checker.sanitize_git_revision(sha), sha)

    def test_rejects_options_ranges_and_metacharacters(self):
        for bad in (
            "--upload-pack=evil",
            "a..b",
            "HEAD;rm",
            "origin/master space",
            "",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(checker.DiscoveryError):
                    checker.sanitize_git_revision(bad)


class AllowlistParseTests(unittest.TestCase):
    def test_parses_empty_and_populated_frozenset(self):
        self.assertEqual(
            checker.parse_path_allowlist_from_source(
                "PATH_ALLOWLIST: frozenset[str] = frozenset()\n"
            ),
            frozenset(),
        )
        self.assertEqual(
            checker.parse_path_allowlist_from_source(
                'PATH_ALLOWLIST: frozenset[str] = frozenset({"tests/e2e/a.py"})\n'
            ),
            frozenset({"tests/e2e/a.py"}),
        )


class CurrentRepoSmokeTests(unittest.TestCase):
    def test_current_repo_vs_head_is_clean(self):
        """Working tree vs HEAD must not already introduce unapproved sleeps."""
        # collect_findings requires a resolved SHA (resolve_base sanitizes CLI).
        sha = checker.resolve_base(_ROOT, "HEAD")
        findings = checker.collect_findings(_ROOT, sha)
        self.assertEqual(
            findings,
            [],
            "\n".join(f.render() for f in findings),
        )


if __name__ == "__main__":
    unittest.main()
