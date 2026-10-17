"""Guards for the isolated six-shard temporary classic-script bundle canary."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import ModuleType
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts.benchmark_classic_script_bundle import HEAD_URL, BODY_URL
from scripts.e2e_classic_bundle_canary import (
    SHARD_COUNT, _browser_preflight, _require_boot_exports,
    _temporary_service_worker, run_canary,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/e2e-classic-bundle-canary.yml"


def _fixture(root: Path) -> Path:
    frontend = root / "frontend"
    js = frontend / "js"
    js.mkdir(parents=True)
    (js / "head.js").write_text("window.headTest = 1;\n", encoding="utf-8")
    (js / "body.js").write_text("window.bodyTest = 1;\n", encoding="utf-8")
    (js / "tail.js").write_text("window.tailTest = 1;\n", encoding="utf-8")
    (js / "module.js").write_text("export const test = 1;\n", encoding="utf-8")
    (frontend / "index.html").write_text(
        '<!doctype html><html>\n<head>\n'
        '<script src="/js/head.js"></script>\n'
        '</head>\n<body>\n'
        '<script src="/js/body.js"></script>\n'
        '<script type="module" src="/js/module.js"></script>\n'
        '<script src="/js/tail.js"></script>\n'
        '</body>\n</html>\n',
        encoding="utf-8",
    )
    (frontend / "sw.js").write_text(
        "const STATIC_PRECACHE_PATHS = [\n"
        "        '/vue/prks-vue.js',\n"
        "    ];\n"
        "const STATIC_EXTRA_PATHS = "
        "['/manifest.webmanifest', '/favicon.svg', '/logo.svg'];\n",
        encoding="utf-8",
    )
    return root


class ClassicBundleCanaryTests(unittest.TestCase):
    def test_shard_uses_full_suite_single_worker_and_isolated_bundle_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            index = root / "frontend/index.html"
            before = index.read_bytes()
            worker = root / "frontend/sw.js"
            worker_before = worker.read_bytes()
            seen = []

            def fake_run(command, *, cwd, check, env=None):
                self.assertEqual(cwd, root)
                self.assertTrue(check)
                seen.append((command, env))
                if env is not None:
                    self.assertIn(HEAD_URL, worker.read_text(encoding="utf-8"))
                    self.assertIn(BODY_URL, worker.read_text(encoding="utf-8"))
                    self.assertIn(HEAD_URL, index.read_text(encoding="utf-8"))
                    self.assertIn(BODY_URL, index.read_text(encoding="utf-8"))
                    self.assertEqual(command[1:], [
                        "tests/e2e/run.py", "--jobs", "1", "--shard",
                        f"4/{SHARD_COUNT}", "--no-pointer-capture",
                    ])
                    self.assertNotIn("--feature", command)
                    self.assertEqual(env["PRKS_E2E_ROUTE_FASTPATH"], "0")
                    self.assertEqual(env["PRKS_E2E_MODE"], "full")
                    self.assertEqual(env["PRKS_E2E"], "1")
                    self.assertEqual(env["PRKS_E2E_PROFILE"], "0")
                return None

            with (
                patch("scripts.e2e_classic_bundle_canary.shutil.which", return_value="/usr/bin/node"),
                patch("scripts.e2e_classic_bundle_canary.subprocess.run", side_effect=fake_run),
            ):
                run_canary(root, 4)
            self.assertEqual(len(seen), 4)
            self.assertEqual(index.read_bytes(), before)
            self.assertEqual(worker.read_bytes(), worker_before)
            self.assertFalse((root / "frontend" / HEAD_URL.lstrip("/")).exists())
            self.assertFalse((root / "frontend" / BODY_URL.lstrip("/")).exists())

    def test_restores_index_after_real_candidate_run_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            index = root / "frontend/index.html"
            before = index.read_bytes()
            worker = root / "frontend/sw.js"
            worker_before = worker.read_bytes()

            def fail_after_bundle(command, *, cwd, check, env=None):
                if env is not None:
                    self.assertNotEqual(index.read_bytes(), before)
                    self.assertIn(BODY_URL, index.read_text(encoding="utf-8"))
                    raise RuntimeError("simulated E2E failure")
                return None

            with (
                patch("scripts.e2e_classic_bundle_canary.shutil.which", return_value="/usr/bin/node"),
                patch("scripts.e2e_classic_bundle_canary.subprocess.run", side_effect=fail_after_bundle),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated E2E failure"):
                    run_canary(root, 2)
            self.assertEqual(index.read_bytes(), before)
            self.assertEqual(worker.read_bytes(), worker_before)
            self.assertFalse((root / "frontend" / HEAD_URL.lstrip("/")).exists())
            self.assertFalse((root / "frontend" / BODY_URL.lstrip("/")).exists())

    def test_invalid_shards_and_preexisting_assets_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            index = root / "frontend/index.html"
            before = index.read_bytes()
            for bad in (0, 7, -1, 1.0, "1"):
                with self.subTest(shard=bad), self.assertRaises(ValueError):
                    run_canary(root, bad)
            self.assertEqual(index.read_bytes(), before)
            bundle = root / "frontend" / HEAD_URL.lstrip("/")
            bundle.write_text("sentinel", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                run_canary(root, 1)
            self.assertEqual(bundle.read_text(encoding="utf-8"), "sentinel")
            self.assertEqual(index.read_bytes(), before)

    def test_missing_node_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            index = root / "frontend/index.html"
            before = index.read_bytes()
            with patch("scripts.e2e_classic_bundle_canary.shutil.which", return_value=None):
                with self.assertRaisesRegex(ValueError, "Node.js"):
                    run_canary(root, 1)
            self.assertEqual(index.read_bytes(), before)
            self.assertFalse((root / "frontend" / HEAD_URL.lstrip("/")).exists())

    def test_preflight_requires_critical_exports_and_script_responses(self):
        available = {
            "prksNavigate": "function",
            "createPrksOfflineStore": "function",
            "prksOfflineNoteRequestFailure": "function",
            "prksGetResearchGraphDebug": "function",
        }
        responses = {HEAD_URL: 200, BODY_URL: 200}
        _require_boot_exports(available, responses, [])
        broken = dict(available, prksNavigate="undefined")
        with self.assertRaisesRegex(RuntimeError, "prksNavigate"):
            _require_boot_exports(broken, responses, [])
        with self.assertRaisesRegex(RuntimeError, "bundle responses"):
            _require_boot_exports(available, {HEAD_URL: 200}, [])
        with self.assertRaisesRegex(RuntimeError, "500"):
            _require_boot_exports(available, {HEAD_URL: 200, BODY_URL: 500}, [])
        with self.assertRaisesRegex(RuntimeError, "script failure"):
            _require_boot_exports(available, responses, ["script failure"])

    def test_preflight_failure_restores_both_temporary_bundles(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            index = root / "frontend/index.html"
            original = index.read_bytes()
            worker = root / "frontend/sw.js"
            worker_before = worker.read_bytes()
            invoked = []

            def fail_after_browser_open():
                invoked.append("browser")
                self.assertNotEqual(index.read_bytes(), original)
                self.assertIn(BODY_URL, index.read_text(encoding="utf-8"))
                self.assertTrue((root / "frontend" / BODY_URL.lstrip("/")).exists())
                self.assertIn(BODY_URL, worker.read_text(encoding="utf-8"))
                raise RuntimeError("missing browser export")

            def fake_check(command, *, cwd, check, env=None):
                invoked.append("node")
                self.assertTrue(check)
                self.assertIsNone(env)
                return None

            with (
                patch("scripts.e2e_classic_bundle_canary.shutil.which", return_value="/usr/bin/node"),
                patch("scripts.e2e_classic_bundle_canary.subprocess.run", side_effect=fake_check),
                patch("scripts.e2e_classic_bundle_canary._browser_preflight",
                      side_effect=fail_after_browser_open),
            ):
                with self.assertRaisesRegex(RuntimeError, "missing browser export"):
                    run_canary(root, preflight=True)
            self.assertEqual(invoked, ["node", "node", "node", "browser"])
            self.assertEqual(index.read_bytes(), original)
            self.assertEqual(worker.read_bytes(), worker_before)
            self.assertFalse((root / "frontend" / HEAD_URL.lstrip("/")).exists())
            self.assertFalse((root / "frontend" / BODY_URL.lstrip("/")).exists())
            with self.assertRaisesRegex(ValueError, "preflight may not"):
                run_canary(root, 1, preflight=True)

    def test_browser_preflight_exercises_offline_reload_and_sw_cache(self):
        """Do not regress to an online-only check: that missed run 39023728966."""
        pw = MagicMock()
        browser = pw.__enter__.return_value.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        server = MagicMock(origin="http://127.0.0.1:9876")
        server.start.return_value = server
        listeners = {}
        page.on.side_effect = lambda name, cb: listeners.__setitem__(name, cb)

        def navigate(*, wait_until, timeout):
            self.assertEqual(wait_until, "domcontentloaded")
            self.assertEqual(timeout, 30000)
            context.set_offline.assert_called_once_with(True)
            for url in (HEAD_URL, BODY_URL):
                response = MagicMock(
                    url=server.origin + url, status=200, from_service_worker=True
                )
                listeners["response"](response)

        page.reload.side_effect = navigate
        page.evaluate.side_effect = lambda expr: (
            {key: "function" for key in (
                "prksNavigate", "createPrksOfflineStore",
                "prksOfflineNoteRequestFailure", "prksGetResearchGraphDebug",
            )}
            if "Object.fromEntries" in expr else True
        )

        # The ordinary unit job deliberately does not install Playwright.
        # Supply only its import seam so this contract test stays lightweight.
        fake_playwright = ModuleType("playwright")
        fake_playwright.__path__ = []
        fake_sync_api = ModuleType("playwright.sync_api")
        fake_sync_api.sync_playwright = lambda: pw
        with (
            patch.dict("sys.modules", {
                "playwright": fake_playwright,
                "playwright.sync_api": fake_sync_api,
            }),
            patch("tests.e2e.install_browser.apply_playwright_browser_env"),
            patch("tests.e2e.harness.AppServer", return_value=server),
        ):
            _browser_preflight()
            browser.new_context.assert_called_once_with(service_workers="allow")
            context.set_offline.assert_called_once_with(True)
            page.reload.assert_called_once()
            page.wait_for_function.assert_called_once()
            self.assertIn("serviceWorker.controller", page.wait_for_function.call_args.args[0])
            server.stop.assert_called_once()

            # The online script responses must NOT satisfy the offline gate.
            page.reload.side_effect = lambda **kw: None
            with self.assertRaisesRegex(RuntimeError, "bundle responses"):
                _browser_preflight()

    def test_worker_never_controls_page_fails_promptly_with_diagnostics(self):
        """A broken installer must not await serviceWorker.ready indefinitely."""
        pw = MagicMock()
        browser = pw.__enter__.return_value.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        server = MagicMock(origin="http://127.0.0.1:9876")
        server.start.return_value = server
        page.wait_for_function.side_effect = TimeoutError("worker control timeout")
        captured = {}
        page.on.side_effect = lambda kind, callback: captured.__setitem__(
            kind, callback
        )

        def navigation(*, wait_until, timeout):
            self.assertEqual(wait_until, "domcontentloaded")
            captured["pageerror"](ValueError("bundle failed to initialize"))

        page.goto.side_effect = navigation
        fake_playwright = ModuleType("playwright")
        fake_playwright.__path__ = []
        fake_sync_api = ModuleType("playwright.sync_api")
        fake_sync_api.sync_playwright = lambda: pw

        report = StringIO()
        with (
            patch.dict("sys.modules", {
                "playwright": fake_playwright,
                "playwright.sync_api": fake_sync_api,
            }),
            patch("tests.e2e.install_browser.apply_playwright_browser_env"),
            patch("tests.e2e.harness.AppServer", return_value=server),
            redirect_stdout(report),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not complete offline reload"):
                _browser_preflight()

        self.assertIn("bundle failed to initialize", report.getvalue())
        self.assertIn("worker control timeout", report.getvalue())
        self.assertIn("service-worker-controlled=False", report.getvalue())
        page.wait_for_function.assert_called_once_with(
            "() => !!(navigator.serviceWorker && navigator.serviceWorker.controller)",
            timeout=30000,
        )
        page.evaluate.assert_not_called()
        context.set_offline.assert_not_called()
        context.close.assert_called_once()
        browser.close.assert_called_once()
        server.stop.assert_called_once()

    def test_temporary_sw_includes_bundles_in_required_cache_and_fetch_allowlist(self):
        source = (ROOT / "frontend/sw.js").read_text(encoding="utf-8")
        candidate = _temporary_service_worker(source)
        self.assertNotEqual(candidate, source)
        after_required = candidate.split("const STATIC_PRECACHE_PATHS = [", 1)[1]
        for url in (HEAD_URL, BODY_URL):
            self.assertNotIn(url, source)
            self.assertIn("'" + url + "'", after_required)
            self.assertIn("'" + url + "'", candidate.split("const STATIC_EXTRA_PATHS = [", 1)[1])
        with self.assertRaisesRegex(ValueError, "already lists"):
            _temporary_service_worker(candidate)
        with self.assertRaisesRegex(ValueError, "manifest structure"):
            _temporary_service_worker(source.replace(
                "const STATIC_EXTRA_PATHS = ", "const STATIC_CHANGED_EXTRA_PATHS = "
            ))

    def test_service_worker_staging_failure_preserves_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            sw = root / "frontend/sw.js"
            index = root / "frontend/index.html"
            sw.write_text("unrecognized worker", encoding="utf-8")
            before = index.read_bytes()
            with self.assertRaisesRegex(ValueError, "manifest structure"):
                run_canary(root, 1)
            self.assertEqual(sw.read_text(encoding="utf-8"), "unrecognized worker")
            self.assertEqual(index.read_bytes(), before)

    def test_workflow_is_explicit_six_shard_manual_canary(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotIn("push:", source)
        self.assertIn("shard: [1, 2, 3, 4, 5, 6]", source)
        self.assertIn("needs: preflight", source)
        self.assertIn("python -m scripts.e2e_classic_bundle_canary --preflight", source)
        self.assertIn("fail-fast: false", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("python -m scripts.e2e_classic_bundle_canary --shard", source)


if __name__ == "__main__":
    unittest.main()
