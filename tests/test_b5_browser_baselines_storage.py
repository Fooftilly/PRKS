"""Storage-safety guard for scripts/b5_browser_baselines.py (#455 / #454)."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "b5_browser_baselines.py"
_SPEC = importlib.util.spec_from_file_location("prks_b5_browser_baselines", _SCRIPT)
assert _SPEC and _SPEC.loader
harness = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = harness
_SPEC.loader.exec_module(harness)


class TestB5StorageGuard(unittest.TestCase):
    def test_refuses_inherited_live_root_and_descendants(self):
        with tempfile.TemporaryDirectory(prefix="prks-live-") as live:
            live_root = Path(live).resolve()
            with self.assertRaises(harness.B5StorageGuardError) as equal_ctx:
                harness.assert_b5_storage_allowed(live_root, inherited_raw=str(live_root))
            self.assertIn("inherited live PRKS_STORAGE", str(equal_ctx.exception))
            with self.assertRaises(harness.B5StorageGuardError) as child_ctx:
                harness.assert_b5_storage_allowed(
                    live_root / "b5-run", inherited_raw=str(live_root)
                )
            self.assertIn("inherited live PRKS_STORAGE", str(child_ctx.exception))

    def test_allows_sibling_of_inherited_live_root(self):
        with tempfile.TemporaryDirectory(prefix="prks-live-") as live:
            live_root = Path(live).resolve()
            sibling = live_root.parent / "prks-b5-isolated-guard"
            resolved = harness.assert_b5_storage_allowed(
                sibling, inherited_raw=str(live_root)
            )
            self.assertEqual(resolved, sibling.resolve())

    def test_live_root_uses_path_containment_not_string_prefix(self):
        with tempfile.TemporaryDirectory(prefix="prks-lib") as live:
            live_root = Path(live).resolve()
            lookalike = Path(str(live_root) + "-other")
            resolved = harness.assert_b5_storage_allowed(
                lookalike, inherited_raw=str(live_root)
            )
            self.assertEqual(resolved, lookalike.resolve())

    def test_refuses_slash_data_and_repo_data(self):
        with self.assertRaises(harness.B5StorageGuardError) as data_ctx:
            harness.assert_b5_storage_allowed("/data", inherited_raw=None)
        self.assertIn("/data", str(data_ctx.exception))
        with self.assertRaises(harness.B5StorageGuardError) as child_ctx:
            harness.assert_b5_storage_allowed("/data/library", inherited_raw=None)
        self.assertIn("/data", str(child_ctx.exception))
        repo_data = harness.REPO / "data"
        with self.assertRaises(harness.B5StorageGuardError) as repo_ctx:
            harness.assert_b5_storage_allowed(repo_data, inherited_raw=None)
        self.assertIn("repository data directory", str(repo_ctx.exception))
        with self.assertRaises(harness.B5StorageGuardError):
            harness.assert_b5_storage_allowed(repo_data / "subdir", inherited_raw=None)

    def test_allows_default_temp_when_inherited_is_unset_or_other_live_root(self):
        default = harness.DEFAULT_STORAGE
        self.assertEqual(
            harness.assert_b5_storage_allowed(default, inherited_raw=None),
            default.resolve(),
        )
        self.assertEqual(
            harness.assert_b5_storage_allowed(default, inherited_raw="  "),
            default.resolve(),
        )
        with tempfile.TemporaryDirectory(prefix="prks-live-") as live:
            self.assertEqual(
                harness.assert_b5_storage_allowed(default, inherited_raw=live),
                default.resolve(),
            )

    def test_refuses_artifact_outside_repo_or_under_repo_data(self):
        with tempfile.TemporaryDirectory(prefix="prks-out-") as outside:
            with self.assertRaises(harness.B5StorageGuardError) as ctx:
                harness.assert_b5_artifact_path(Path(outside) / "browser-baselines.json")
            self.assertIn("outside the repository", str(ctx.exception))
        with self.assertRaises(harness.B5StorageGuardError):
            harness.assert_b5_artifact_path(harness.REPO / "data" / "browser-baselines.json")
        resolved = harness.assert_b5_artifact_path(harness.DEFAULT_OUTPUT)
        self.assertEqual(resolved, harness.DEFAULT_OUTPUT.resolve())

    def test_loopback_http_url_refuses_remote_and_file(self):
        with self.assertRaises(ValueError):
            harness._assert_loopback_http_url("https://example.com/api/works")
        with self.assertRaises(ValueError):
            harness._assert_loopback_http_url("file:///etc/passwd")
        rebuilt = harness._assert_loopback_http_url("http://127.0.0.1:9/api/works")
        self.assertEqual(rebuilt, "http://127.0.0.1:9/api/works")


if __name__ == "__main__":
    unittest.main()
