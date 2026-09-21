"""Unit coverage for documentation screenshot freshness helpers."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

_SCRIPTS = os.path.join(_PROJECT_DIR, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def _load_check():
    return importlib.import_module("check_screenshot_freshness")


def _load_capture():
    # capture imports playwright helpers; stub install_browser for unit tests.
    fake_install = type(sys)("tests.e2e.install_browser")
    fake_install.apply_playwright_browser_env = lambda: None
    fake_install.ensure_chromium_installed = lambda: None
    sys.modules.setdefault("tests.e2e.install_browser", fake_install)
    if "capture_demo_screenshots" in sys.modules:
        del sys.modules["capture_demo_screenshots"]
    return importlib.import_module("capture_demo_screenshots")


class ScreenshotFreshnessPathParityTests(unittest.TestCase):
    def test_workflow_paths_cover_affecting_paths(self):
        check = _load_check()
        workflow = Path(_PROJECT_DIR) / ".github" / "workflows" / "screenshot-freshness.yml"
        text = workflow.read_text(encoding="utf-8")
        for path in sorted(check.AFFECTING_PATHS):
            self.assertIn(f'- "{path}"', text, msg=f"missing workflow path {path}")
        self.assertIn('- "frontend/**"', text)

    def test_orchestrator_and_browser_inputs_are_tracked(self):
        check = _load_check()
        required = {
            "scripts/update_demo_screenshots.py",
            "requirements-dev.txt",
            "tests/e2e/install_browser.py",
            "backend/services/work_pdf_replace.py",
        }
        self.assertTrue(required.issubset(check.AFFECTING_PATHS))


class ScreenshotManifestMergeTests(unittest.TestCase):
    def test_partial_capture_preserves_untouched_provenance(self):
        capture = _load_capture()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screenshots"
            out.mkdir()
            manifest = out / "manifest.json"
            manifest.write_text(
                """{
  "schema_version": 1,
  "source_commit": "aaa111",
  "capture_set": "all",
  "screenshots": [
    {"file": "folders.png", "scenario": "public-domain-folder", "source_commit": "aaa111"},
    {"file": "tags.png", "scenario": "tags", "source_commit": "aaa111"}
  ]
}
""",
                encoding="utf-8",
            )
            with mock.patch.object(capture, "OUT_DIR", out), mock.patch.object(
                capture, "MANIFEST", manifest
            ):
                capture._write_manifest(
                    "readme",
                    [{"file": "folders.png", "scenario": "public-domain-folder"}],
                    "bbb222",
                )
            data = __import__("json").loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["source_commit"], "aaa111")
            self.assertEqual(data["capture_set"], "readme")
            by_file = {row["file"]: row for row in data["screenshots"]}
            self.assertEqual(by_file["folders.png"]["source_commit"], "bbb222")
            self.assertEqual(by_file["tags.png"]["source_commit"], "aaa111")

    def test_all_capture_advances_global_revision(self):
        capture = _load_capture()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screenshots"
            out.mkdir()
            manifest = out / "manifest.json"
            full = [
                {"file": name, "scenario": name}
                for name in sorted(capture.EXPECTED_ALL_FILES)
            ]
            with mock.patch.object(capture, "OUT_DIR", out), mock.patch.object(
                capture, "MANIFEST", manifest
            ):
                capture._write_manifest("all", full, "ccc333")
            data = __import__("json").loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["source_commit"], "ccc333")
            self.assertEqual(data["capture_set"], "all")
            for row in data["screenshots"]:
                self.assertEqual(row["source_commit"], "ccc333")

    def test_incomplete_all_does_not_advance_global_revision(self):
        capture = _load_capture()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screenshots"
            out.mkdir()
            manifest = out / "manifest.json"
            manifest.write_text(
                """{
  "schema_version": 1,
  "source_commit": "aaa111",
  "capture_set": "all",
  "screenshots": [
    {"file": "person.png", "scenario": "darwin-person", "source_commit": "aaa111"}
  ]
}
""",
                encoding="utf-8",
            )
            with mock.patch.object(capture, "OUT_DIR", out), mock.patch.object(
                capture, "MANIFEST", manifest
            ):
                capture._write_manifest(
                    "all",
                    [
                        {"file": "folders.png", "scenario": "public-domain-folder"},
                        {"file": "work.png", "scenario": "origin-of-species-work-pdf"},
                        {"file": "people.png", "scenario": "people-library"},
                    ],
                    "bbb222",
                )
            data = __import__("json").loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["source_commit"], "aaa111")
            by_file = {row["file"]: row for row in data["screenshots"]}
            self.assertEqual(by_file["folders.png"]["source_commit"], "bbb222")
            self.assertEqual(by_file["person.png"]["source_commit"], "aaa111")


class ScreenshotDirtyTreeTests(unittest.TestCase):
    def test_require_clean_sources_refuses_dirty_frontend(self):
        check = _load_check()
        with mock.patch.object(
            check,
            "dirty_screenshot_sources",
            return_value=["frontend/css/app.css"],
        ):
            with self.assertRaises(RuntimeError) as ctx:
                check.require_clean_capture_sources()
        self.assertIn("uncommitted changes", str(ctx.exception))
        self.assertIn("frontend/css/app.css", str(ctx.exception))

    def test_capture_output_paths_are_ignored(self):
        check = _load_check()
        self.assertTrue(check._is_capture_output("docs/screenshots/folders.png"))
        self.assertTrue(check._is_capture_output("docs/screenshots/manifest.json"))
        self.assertFalse(check._is_capture_output("frontend/js/app.js"))

    def test_dirty_sources_fail_closed_when_git_status_fails(self):
        check = _load_check()
        with mock.patch.object(
            check,
            "_git",
            side_effect=subprocess.CalledProcessError(128, ["git", "status"]),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                check.dirty_screenshot_sources()
        self.assertIn("Could not inspect", str(ctx.exception))


class ScreenshotLoopbackGuardTests(unittest.TestCase):
    def test_rejects_remote_base_url(self):
        capture = _load_capture()
        with self.assertRaises(RuntimeError):
            capture._assert_loopback_base("http://example.com:8070")
        with self.assertRaises(RuntimeError):
            capture._assert_loopback_base("https://127.0.0.1:8070")
        with self.assertRaises(RuntimeError):
            capture._assert_loopback_base("http://127.0.0.1:8070/api")

    def test_accepts_loopback_base_url(self):
        capture = _load_capture()
        self.assertEqual(
            capture._assert_loopback_base("http://127.0.0.1:8070"),
            "http://127.0.0.1:8070",
        )
        self.assertEqual(
            capture._assert_loopback_base("http://localhost:9000/"),
            "http://localhost:9000",
        )


class ScreenshotUpdateServerGuardTests(unittest.TestCase):
    def test_wait_for_child_fails_when_process_exits(self):
        # Stub seed_demo_library so this unit test does not need pymupdf.
        if "update_demo_screenshots" in sys.modules:
            del sys.modules["update_demo_screenshots"]
        fake_seed = type(sys)("seed_demo_library")
        fake_seed.seed = lambda *_a, **_k: None
        sys.modules["seed_demo_library"] = fake_seed
        _load_capture()
        update = importlib.import_module("update_demo_screenshots")

        class Dead:
            def poll(self):
                return 1

        with self.assertRaises(RuntimeError) as ctx:
            update._wait_for_child("http://127.0.0.1:9/api/works", Dead(), timeout=0.2)
        self.assertIn("exited before becoming ready", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
