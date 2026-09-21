"""Unit coverage for documentation screenshot freshness helpers."""
from __future__ import annotations

import importlib
import os
import shutil
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
            # Expected extras stay listed even when the prior manifest omitted them,
            # and remain explicitly revisionless (do not inherit global aaa111).
            self.assertIn("person.png", by_file)
            self.assertIn("source_commit", by_file["person.png"])
            self.assertIsNone(by_file["person.png"]["source_commit"])
            check = _load_check()
            self.assertEqual(
                check._entry_revision(by_file["person.png"], "aaa111"),
                "",
            )
            self.assertEqual(
                check._entry_revision(
                    {"file": "legacy.png"},  # key absent → legacy global fallback
                    "aaa111",
                ),
                "aaa111",
            )

    def test_seeded_null_revision_not_blessed_by_global(self):
        """Non-null global revision must not cover newly seeded expected files."""
        capture = _load_capture()
        check = _load_check()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screenshots"
            out.mkdir()
            for name in capture.EXPECTED_ALL_FILES:
                (out / name).write_bytes(b"png")
            manifest = out / "manifest.json"
            manifest.write_text(
                """{
  "schema_version": 1,
  "source_commit": "global999",
  "capture_set": "all",
  "screenshots": [
    {"file": "folders.png", "scenario": "public-domain-folder", "source_commit": "global999"}
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
                    "partial111",
                )
            data = __import__("json").loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["source_commit"], "global999")
            by_file = {row["file"]: row for row in data["screenshots"]}
            self.assertEqual(by_file["folders.png"]["source_commit"], "partial111")
            self.assertIsNone(by_file["tags.png"]["source_commit"])
            self.assertEqual(
                check._entry_revision(by_file["tags.png"], "global999"),
                "",
            )
            with mock.patch.object(check, "MANIFEST", manifest):
                with mock.patch.object(check, "_warn") as warn:
                    with mock.patch.object(check, "_affecting_after", return_value=[]):
                        with mock.patch.dict(os.environ, {}, clear=False):
                            code = check.main()
            self.assertEqual(code, 0)
            joined = " ".join(str(c.args[0]) for c in warn.call_args_list)
            self.assertIn("untracked or revisionless", joined)
            self.assertIn("tags.png", joined)

    def test_legacy_readme_partial_keeps_extras_visible_to_freshness(self):
        capture = _load_capture()
        check = _load_check()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screenshots"
            out.mkdir()
            # Legacy-shaped manifest: only readme trio, but extras exist on disk.
            for name in capture.EXPECTED_ALL_FILES:
                (out / name).write_bytes(b"png")
            manifest = out / "manifest.json"
            manifest.write_text(
                """{
  "schema_version": 1,
  "source_commit": null,
  "capture_set": "legacy-existing",
  "screenshots": [
    {"file": "folders.png", "scenario": "public-domain-folder"},
    {"file": "work.png", "scenario": "origin-of-species-work-pdf"},
    {"file": "people.png", "scenario": "people-library"}
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
                    [
                        {"file": "folders.png", "scenario": "public-domain-folder"},
                        {"file": "work.png", "scenario": "origin-of-species-work-pdf"},
                        {"file": "people.png", "scenario": "people-library"},
                    ],
                    "bbb222",
                )
            data = __import__("json").loads(manifest.read_text(encoding="utf-8"))
            by_file = {row["file"]: row for row in data["screenshots"]}
            for name in capture.EXPECTED_EXTRA_FILES:
                self.assertIn(name, by_file)
                self.assertIn("source_commit", by_file[name])
                self.assertIsNone(by_file[name]["source_commit"])

            with mock.patch.object(check, "MANIFEST", manifest):
                with mock.patch.object(check, "_warn") as warn:
                    with mock.patch.dict(os.environ, {}, clear=False):
                        code = check.main()
            self.assertEqual(code, 0)
            self.assertTrue(warn.called)
            joined = " ".join(str(c.args[0]) for c in warn.call_args_list)
            self.assertIn("untracked or revisionless", joined)
            self.assertIn("tags.png", joined)

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


class ScreenshotPromotionRollbackTests(unittest.TestCase):
    def test_manifest_write_failure_restores_pngs_and_manifest(self):
        capture = _load_capture()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            out = parent / "screenshots"
            out.mkdir()
            stage = parent / "stage"
            stage.mkdir()
            manifest = out / "manifest.json"
            original = b"old-png"
            new = b"new-png"
            (out / "folders.png").write_bytes(original)
            (stage / "folders.png").write_bytes(new)
            manifest.write_text(
                '{"schema_version":1,"source_commit":"old","screenshots":[]}\n',
                encoding="utf-8",
            )
            before = manifest.read_text(encoding="utf-8")
            real_write = Path.write_text

            def write_text_fail(self, data, encoding="utf-8", errors=None, newline=None):
                if Path(self).resolve() == manifest.resolve():
                    raise OSError("manifest write failed")
                return real_write(
                    self, data, encoding=encoding, errors=errors, newline=newline
                )

            with mock.patch.object(capture, "OUT_DIR", out), mock.patch.object(
                capture, "MANIFEST", manifest
            ), mock.patch.object(Path, "write_text", write_text_fail):
                with self.assertRaises(OSError):
                    capture._promote_capture(
                        stage,
                        "readme",
                        [{"file": "folders.png", "scenario": "public-domain-folder"}],
                        "newhead",
                    )
            self.assertEqual((out / "folders.png").read_bytes(), original)
            self.assertEqual(manifest.read_text(encoding="utf-8"), before)

    def test_replace_failure_restores_already_promoted_pngs(self):
        capture = _load_capture()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            out = parent / "screenshots"
            out.mkdir()
            stage = parent / "stage"
            stage.mkdir()
            manifest = out / "manifest.json"
            (out / "folders.png").write_bytes(b"old-folders")
            (out / "work.png").write_bytes(b"old-work")
            (stage / "folders.png").write_bytes(b"new-folders")
            (stage / "work.png").write_bytes(b"new-work")
            manifest.write_text(
                '{"schema_version":1,"source_commit":"old","screenshots":[]}\n',
                encoding="utf-8",
            )
            before = manifest.read_text(encoding="utf-8")
            real_replace = os.replace

            def flaky_replace(src, dst):
                src_path = Path(src)
                dst_path = Path(dst)
                if src_path.parent == stage and dst_path.name == "work.png":
                    raise OSError("replace failed")
                return real_replace(src, dst)

            with mock.patch.object(capture, "OUT_DIR", out), mock.patch.object(
                capture, "MANIFEST", manifest
            ), mock.patch.object(capture.os, "replace", flaky_replace):
                with self.assertRaises(OSError):
                    capture._promote_capture(
                        stage,
                        "readme",
                        [
                            {
                                "file": "folders.png",
                                "scenario": "public-domain-folder",
                            },
                            {
                                "file": "work.png",
                                "scenario": "origin-of-species-work-pdf",
                            },
                        ],
                        "newhead",
                    )
            self.assertEqual((out / "folders.png").read_bytes(), b"old-folders")
            self.assertEqual((out / "work.png").read_bytes(), b"old-work")
            self.assertEqual(manifest.read_text(encoding="utf-8"), before)

    def test_restore_failure_preserves_backup_dir(self):
        capture = _load_capture()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            out = parent / "screenshots"
            out.mkdir()
            stage = parent / "stage"
            stage.mkdir()
            manifest = out / "manifest.json"
            (out / "folders.png").write_bytes(b"old-png")
            (stage / "folders.png").write_bytes(b"new-png")
            manifest.write_text(
                '{"schema_version":1,"source_commit":"old","screenshots":[]}\n',
                encoding="utf-8",
            )
            real_replace = os.replace
            real_write = Path.write_text

            def write_text_fail(self, data, encoding="utf-8", errors=None, newline=None):
                if Path(self).resolve() == manifest.resolve():
                    raise OSError("manifest write failed")
                return real_write(
                    self, data, encoding=encoding, errors=errors, newline=newline
                )

            def replace_fail_on_restore(src, dst):
                src_path = Path(src)
                dst_path = Path(dst)
                # Fail when restoring the backed-up PNG into OUT_DIR.
                if (
                    dst_path.resolve() == (out / "folders.png").resolve()
                    and "prks-shot-backup-" in str(src_path)
                ):
                    raise OSError("restore replace failed")
                return real_replace(src, dst)

            with mock.patch.object(capture, "OUT_DIR", out), mock.patch.object(
                capture, "MANIFEST", manifest
            ), mock.patch.object(Path, "write_text", write_text_fail), mock.patch.object(
                capture.os, "replace", replace_fail_on_restore
            ), mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()) as err:
                with self.assertRaises(OSError) as ctx:
                    capture._promote_capture(
                        stage,
                        "readme",
                        [{"file": "folders.png", "scenario": "public-domain-folder"}],
                        "newhead",
                    )
            self.assertIn("manifest write failed", str(ctx.exception))
            stderr = err.getvalue()
            self.assertIn("backups retained at", stderr)
            marker = "backups retained at "
            start = stderr.index(marker) + len(marker)
            path_text = stderr[start:].split(" (", 1)[0].strip()
            backup_dir = Path(path_text)
            self.assertTrue(backup_dir.is_dir(), stderr)
            self.assertTrue((backup_dir / "folders.png").is_file())
            shutil.rmtree(backup_dir, ignore_errors=True)


class ScreenshotFreshnessStrictTests(unittest.TestCase):
    def test_compare_failed_returns_nonzero_when_strict(self):
        check = _load_check()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screenshots"
            out.mkdir()
            (out / "folders.png").write_bytes(b"png")
            manifest = out / "manifest.json"
            manifest.write_text(
                """{
  "schema_version": 1,
  "source_commit": null,
  "screenshots": [
    {"file": "folders.png", "scenario": "public-domain-folder", "source_commit": "deadbeef"}
  ]
}
""",
                encoding="utf-8",
            )
            with mock.patch.object(check, "MANIFEST", manifest):
                with mock.patch.object(
                    check,
                    "_affecting_after",
                    side_effect=subprocess.CalledProcessError(128, ["git"]),
                ):
                    with mock.patch.object(check, "_warn"):
                        with mock.patch.dict(
                            os.environ,
                            {"PRKS_SCREENSHOT_FRESHNESS_STRICT": "1"},
                            clear=False,
                        ):
                            code = check.main()
            self.assertEqual(code, 1)


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
