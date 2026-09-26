"""Regression tests for scripts/check_e2e_wait_for_timeout.py (#189)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


class AstDetectionTests(unittest.TestCase):
    def test_detects_page_and_self_page_calls(self):
        src = (
            "page.wait_for_timeout(250)\n"
            "self.page.wait_for_timeout(50)\n"
        )
        sites = checker.iter_wait_sites(src)
        self.assertEqual([s.lineno for s in sites], [1, 2])

    def test_detects_multiline_backslash_call(self):
        src = "page.wait_for_timeout\\\n(250)\n"
        sites = checker.iter_wait_sites(src)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].lineno, 1)

    def test_detects_parenthesized_multiline_call(self):
        src = "page.wait_for_timeout(\n    250\n)\n"
        sites = checker.iter_wait_sites(src)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].lineno, 1)

    def test_ignores_string_literals_and_comments(self):
        src = (
            'example = ".wait_for_timeout("\n'
            '"""page.wait_for_timeout(1)"""\n'
            "# page.wait_for_timeout(500)\n"
            "page.wait_for_timeout_ms(1)\n"
            "wait_for_timeout(1)\n"
        )
        self.assertEqual(checker.iter_wait_sites(src), [])

    def test_hash_inside_string_does_not_hide_real_call(self):
        src = 'page.locator("#save").click(); page.wait_for_timeout(500)\n'
        sites = checker.iter_wait_sites(src)
        self.assertEqual(len(sites), 1)


class MarkerExemptionTests(unittest.TestCase):
    def test_same_line_comment_marker_with_reason(self):
        lines = [
            "    page.wait_for_timeout(250)  # prks-allow-wait-for-timeout: absence window"
        ]
        self.assertTrue(checker.line_is_exempt(lines, 1))

    def test_previous_line_comment_marker_with_reason(self):
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
        self.assertTrue(checker.line_is_exempt(lines, 3))

    def test_marker_in_assignment_string_does_not_exempt(self):
        lines = [
            '    reason = "prks-allow-wait-for-timeout: temporary"',
            "    page.wait_for_timeout(100)",
        ]
        self.assertFalse(checker.line_is_exempt(lines, 2))
        self.assertIsNone(checker.comment_marker_reason(lines[0]))

    def test_marker_in_same_line_string_does_not_exempt(self):
        lines = [
            '    page.wait_for_timeout(100); x = "prks-allow-wait-for-timeout: no"'
        ]
        # No COMMENT token carries the marker.
        self.assertFalse(checker.line_is_exempt(lines, 1))


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

    def test_multiline_new_call_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            updated = HISTORICAL_E2E + "\n    page.wait_for_timeout\\\n    (999)\n"
            (root / "tests/e2e/test_hist.py").write_text(updated, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual(len(findings), 1)
            self.assertIn("new page.wait_for_timeout", findings[0].reason)

    def test_string_literal_mention_does_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            updated = HISTORICAL_E2E + '\n    note = "page.wait_for_timeout(1)"\n'
            (root / "tests/e2e/test_hist.py").write_text(updated, encoding="utf-8")
            self.assertEqual(checker.collect_findings(root, base), [])

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
            self.assertEqual(checker.collect_findings(root, base), [])

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
            edited = HISTORICAL_E2E.replace("assert True", "assert True  # unrelated")
            (root / "tests/e2e/test_hist.py").write_text(edited, encoding="utf-8")
            self.assertEqual(checker.collect_findings(root, base), [])

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
            self.assertEqual(checker.collect_findings(root, base), [])

    def test_string_marker_does_not_approve_new_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            fake = (
                HISTORICAL_E2E
                + "\n"
                + '    reason = "prks-allow-wait-for-timeout: temporary"\n'
                + "    page.wait_for_timeout(400)\n"
            )
            (root / "tests/e2e/test_hist.py").write_text(fake, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual(len(findings), 1)
            self.assertIn("new page.wait_for_timeout", findings[0].reason)

    def test_removed_marker_while_addition_remains_fails(self):
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
            without_marker = HISTORICAL_E2E + "\n" + "    page.wait_for_timeout(400)\n"
            (root / "tests/e2e/test_hist.py").write_text(without_marker, encoding="utf-8")
            findings = checker.collect_findings(root, base)
            self.assertEqual(len(findings), 1)
            self.assertIn("lost its approved exemption", findings[0].reason)
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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/helper_timing.py": "page.wait_for_timeout(1)\n", "README": "x\n"},
                "base",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
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
            findings = checker.collect_findings(
                root,
                base,
                path_allowlist=frozenset(),
                base_path_allowlist=helper,
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].path, "tests/e2e/helper_timing.py")
            self.assertIn("lost its approved exemption", findings[0].reason)
            self.assertTrue(all(f.path != "tests/e2e/test_hist.py" for f in findings))

    def test_rename_into_e2e_from_outside_scans_full_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {
                    "tests/unit/sleep.py": "page.wait_for_timeout(10)\n",
                    "README": "x\n",
                },
                "outside e2e",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            (root / "tests" / "e2e").mkdir(parents=True, exist_ok=True)
            _git(root, "mv", "tests/unit/sleep.py", "tests/e2e/sleep.py")
            findings = checker.collect_findings(root, base)
            self.assertEqual([f.path for f in findings], ["tests/e2e/sleep.py"])
            self.assertIn("new page.wait_for_timeout", findings[0].reason)

    def test_rename_within_e2e_keeps_historical_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/old_name.py": HISTORICAL_E2E, "README": "x\n"},
                "inside e2e",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip()
            _git(root, "mv", "tests/e2e/old_name.py", "tests/e2e/new_name.py")
            self.assertEqual(checker.collect_findings(root, base), [])

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

    def test_empty_tree_baseline_scans_tracked_e2e(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "tip",
            )
            findings = checker.collect_findings(root, checker.EMPTY_TREE_SHA)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].path, "tests/e2e/test_hist.py")

    def test_main_ok_on_clean_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(
                root,
                {"tests/e2e/test_hist.py": HISTORICAL_E2E, "README": "x\n"},
                "base",
            )
            self.assertEqual(checker.main(["--root", str(root), "--base", "HEAD"]), 0)

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
            self.assertEqual(checker.main(["--root", str(root), "--base", base]), 1)

    def test_invalid_base_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _commit_tree(root, {"README": "x\n"}, "base")
            self.assertEqual(
                checker.main(["--root", str(root), "--base", "origin/does-not-exist"]),
                2,
            )


class SanitizeRevisionTests(unittest.TestCase):
    def test_accepts_head_sha_and_ref_names(self):
        self.assertEqual(checker.sanitize_git_revision("HEAD"), "HEAD")
        self.assertEqual(
            checker.sanitize_git_revision("origin/master"), "origin/master"
        )
        sha = "a" * 40
        self.assertEqual(checker.sanitize_git_revision(sha), sha)
        self.assertEqual(
            checker.resolve_base(Path("."), checker.EMPTY_TREE_SHA),
            checker.EMPTY_TREE_SHA,
        )

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
        sha = checker.resolve_base(_ROOT, "HEAD")
        findings = checker.collect_findings(_ROOT, sha)
        self.assertEqual(
            findings,
            [],
            "\n".join(f.render() for f in findings),
        )


if __name__ == "__main__":
    unittest.main()
