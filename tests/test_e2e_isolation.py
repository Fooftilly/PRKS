"""Isolation guards for the real-browser E2E layer. Does not launch Chromium."""
import os
import tempfile
import unittest
from pathlib import Path

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HARNESS = os.path.join(_PROJECT_DIR, "tests", "e2e", "harness.py")
_FIXTURES = os.path.join(_PROJECT_DIR, "tests", "e2e", "fixtures.py")
_RUN = os.path.join(_PROJECT_DIR, "tests", "e2e", "run.py")
_INSTALL = os.path.join(_PROJECT_DIR, "tests", "e2e", "install_browser.py")
_APP = os.path.join(_PROJECT_DIR, "tests", "e2e", "test_app.py")
_REQ = os.path.join(_PROJECT_DIR, "requirements.txt")
_REQ_DEV = os.path.join(_PROJECT_DIR, "requirements-dev.txt")
_DOCKER = os.path.join(_PROJECT_DIR, "Dockerfile")
_SERVER = os.path.join(_PROJECT_DIR, "backend", "server.py")
_GITIGNORE = os.path.join(_PROJECT_DIR, ".gitignore")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class E2EIsolationTests(unittest.TestCase):
    def test_harness_uses_testing_temp_storage_and_loopback(self):
        src = _read(_HARNESS)
        self.assertIn('env["PRKS_TESTING"] = "1"', src)
        self.assertIn('env["PRKS_STORAGE"] = self.storage_root', src)
        self.assertIn("tempfile.TemporaryDirectory", src)
        self.assertIn('HOST = "127.0.0.1"', src)
        self.assertIn('"--host"', src)
        self.assertIn("HOST", src)
        self.assertIn("proc.terminate()", src)
        self.assertIn("proc.kill()", src)
        self.assertIn("did not exit", src)
        self.assertIn("_tmpdir.cleanup()", src)
        self.assertNotIn("/data", src)
        self.assertNotIn('data_testing', src)
        self.assertNotIn('"./data"', src)
        self.assertNotIn("0.0.0.0", src)

    def test_no_repo_data_paths_in_e2e_helpers(self):
        for path in (_HARNESS, _FIXTURES, _RUN, _INSTALL, _APP):
            src = _read(path)
            self.assertNotIn("PRKS_STORAGE\"] = \"data", src, path)
            self.assertNotIn("/home/", src, path)
            self.assertNotRegex(src, r'(?m)^\s*[^#\n]*["\']data/["\']')

    def test_runtime_requirements_do_not_include_playwright(self):
        req = _read(_REQ)
        self.assertNotIn("playwright", req.lower())
        docker = _read(_DOCKER)
        self.assertIn("requirements.txt", docker)
        self.assertNotIn("requirements-dev.txt", docker)
        self.assertNotIn("playwright", docker.lower())

    def test_dev_requirements_pin_playwright(self):
        dev = _read(_REQ_DEV)
        self.assertRegex(dev, r"(?m)^playwright==\d+\.\d+\.\d+\s*$")

    def test_no_production_fake_e2e_api(self):
        server = _read(_SERVER)
        self.assertNotIn("/api/e2e", server)
        self.assertNotIn("E2E mode", server)
        self.assertNotIn("fake backend", server.lower())

    def test_browser_cache_is_repo_local(self):
        install = _read(_INSTALL)
        run = _read(_RUN)
        harness = _read(_HARNESS)
        gitignore = _read(_GITIGNORE)
        self.assertIn(".playwright-browsers", install)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", install)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", run)
        self.assertIn("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", harness)
        self.assertIn(".playwright-browsers/", gitignore)

    def test_e2e_installs_missing_chromium_into_repo_cache(self):
        run = _read(_RUN)
        harness = _read(_HARNESS)
        install = _read(_INSTALL)
        self.assertIn("ensure_chromium_installed", run)
        self.assertIn("ensure_chromium_installed", install)
        self.assertIn("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", run)
        self.assertIn("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", harness)
        self.assertIn('"-m", "playwright", "install", "chromium"', install)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", install)
        self.assertNotIn("chromium.launch", install)
        self.assertIn("INSTALL_HINT", harness)
        self.assertIn("playwright_chromium_revision", harness)
        self.assertIn("chromium_executable", harness)

    def test_run_tests_flags_delegate_e2e_without_default_chromium(self):
        runner = _read(os.path.join(_PROJECT_DIR, "run_tests.py"))
        self.assertIn("--e2e", runner)
        self.assertIn("--all", runner)
        self.assertIn("run_e2e_tests", runner)
        self.assertIn('"tests"', runner)
        self.assertIn('"e2e"', runner)
        self.assertIn('"run.py"', runner)
        self.assertNotIn('PRKS_E2E"] = "1"', runner)

    def test_chromium_executable_matches_revision_layout(self):
        from tests.e2e.install_browser import chromium_executable, installed_chromium_revisions

        with tempfile.TemporaryDirectory(prefix="prks-e2e-browsers-") as raw:
            root = Path(raw)
            chrome_dir = root / "chromium-1187" / "chrome-linux"
            chrome_dir.mkdir(parents=True)
            chrome = chrome_dir / "chrome"
            chrome.write_bytes(b"")
            self.assertEqual(chromium_executable(root, "1187"), chrome)
            self.assertIsNone(chromium_executable(root, "1234"))
            self.assertEqual(installed_chromium_revisions(root), ["1187"])
