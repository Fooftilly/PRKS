"""Focused tests for the dependency consistency gate."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROJECT = Path(__file__).resolve().parents[1]
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from backend.dependency_gate import (
    assert_remediation_is_safe,
    detect_platform_context,
    format_pip_install_command,
    format_venv_create_commands,
    parse_requirements_pins,
    pinned_playwright_version,
    python_min_version,
    remediation_message,
    run_repo_gate,
    run_runtime_gate,
    validate_installed_pins,
    validate_npm_island,
    validate_python_version,
)


class RequirementsParsingTests(unittest.TestCase):
    def test_parse_exact_pins_ignores_comments(self):
        text = "# comment\nPyMuPDF==1.28.2\n\nPillow==12.3.0\n"
        self.assertEqual(
            parse_requirements_pins(text),
            {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"},
        )

    def test_playwright_pin_from_repo(self):
        pin = pinned_playwright_version(_PROJECT)
        self.assertRegex(pin, r"^\d+\.\d+\.\d+$")
        self.assertIn(f"playwright=={pin}", (_PROJECT / "requirements-dev.txt").read_text())


class PythonVersionTests(unittest.TestCase):
    def test_minimum_from_inventory(self):
        self.assertEqual(python_min_version(_PROJECT)[:2], (3, 12))

    def test_too_old_fails(self):
        result = validate_python_version(current=(3, 11, 0), minimum=(3, 12))
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "python_too_old")

    def test_current_ok(self):
        result = validate_python_version(current=(3, 12, 0), minimum=(3, 12))
        self.assertTrue(result.ok)


class InstalledPinTests(unittest.TestCase):
    def test_missing_package(self):
        result = validate_installed_pins(
            {"PyMuPDF": "1.28.2"},
            version_lookup=lambda _n: None,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "missing_package")

    def test_wrong_version(self):
        result = validate_installed_pins(
            {"PyMuPDF": "1.28.2"},
            version_lookup=lambda _n: "1.0.0",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "version_mismatch")

    def test_correct_version(self):
        result = validate_installed_pins(
            {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"},
            version_lookup=lambda n: {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"}[n],
        )
        self.assertTrue(result.ok)


class RemediationMessageTests(unittest.TestCase):
    def test_venv_recommends_same_interpreter_pip(self):
        ctx = detect_platform_context(
            prefix="/tmp/.venv",
            base_prefix="/usr",
            executable="/tmp/.venv/bin/python",
            system="Linux",
            environ={},
            externally_managed=False,
        )
        msg = remediation_message(
            missing=["Pillow"],
            ctx=ctx,
            requirements_file="requirements.txt",
        )
        self.assertIn("/tmp/.venv/bin/python -m pip install -r requirements.txt", msg)
        assert_remediation_is_safe(msg)

    def test_windows_quotes_paths(self):
        cmd = format_pip_install_command(
            r"C:\Users\Me\PRKS\.venv\Scripts\python.exe",
            r"C:\Users\Me\PRKS\requirements.txt",
            is_windows=True,
        )
        self.assertIn(".venv", cmd)
        self.assertIn("-m pip install -r", cmd)

    def test_externally_managed_never_sudo_or_break(self):
        ctx = detect_platform_context(
            prefix="/usr",
            base_prefix="/usr",
            executable="/usr/bin/python3",
            system="Linux",
            environ={},
            externally_managed=True,
        )
        msg = remediation_message(missing=["Pillow"], ctx=ctx)
        self.assertIn("EXTERNALLY-MANAGED", msg)
        self.assertIn(".venv", msg)
        self.assertIn("Do not use sudo pip", msg)
        assert_remediation_is_safe(msg)
        for cmd in format_venv_create_commands(is_windows=False):
            self.assertIn(cmd, msg)

    def test_docker_rebuild_not_pip(self):
        ctx = detect_platform_context(
            prefix="/usr",
            base_prefix="/usr",
            executable="/usr/local/bin/python",
            system="Linux",
            environ={"PRKS_CONTAINER": "1"},
            externally_managed=False,
        )
        msg = remediation_message(mismatched=[("Pillow", "1.0", "12.3.0")], ctx=ctx)
        assert_remediation_is_safe(msg)
        self.assertIn("PRKS_CONTAINER", msg)
        self.assertIn("docker-build", msg)
        self.assertNotIn("pip install", msg)

    def test_assert_safe_rejects_bad_advice(self):
        with self.assertRaises(AssertionError):
            assert_remediation_is_safe("  sudo pip install foo")
        with self.assertRaises(AssertionError):
            assert_remediation_is_safe("pip install --break-system-packages foo")
        # Negated warnings are fine.
        assert_remediation_is_safe("Do not use sudo pip or pip --break-system-packages.")


class NpmIslandTests(unittest.TestCase):
    def test_lock_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"cytoscape": "3.34.3"}}),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps(
                    {
                        "packages": {
                            "node_modules/cytoscape": {"version": "3.30.0"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = validate_npm_island(
                {
                    "name": "tmp",
                    "dir": root,
                    "react_guard": False,
                    "types_guard": False,
                }
            )
            self.assertFalse(result.ok)
            self.assertTrue(any(i.code == "lock_version_mismatch" for i in result.issues))

    def test_react_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {"react": "18.3.1", "react-dom": "18.2.0"},
                        "devDependencies": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps(
                    {
                        "packages": {
                            "node_modules/react": {"version": "18.3.1"},
                            "node_modules/react-dom": {"version": "18.2.0"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = validate_npm_island(
                {
                    "name": "tmp",
                    "dir": root,
                    "react_guard": True,
                    "types_guard": False,
                }
            )
            self.assertFalse(result.ok)
            self.assertTrue(any(i.code == "react_dom_mismatch" for i in result.issues))


class RepoGateLiveTests(unittest.TestCase):
    def test_current_repo_passes(self):
        result = run_repo_gate(repo_root=_PROJECT)
        self.assertTrue(result.ok, [i.message for i in result.issues])

    def test_runtime_gate_with_correct_lookup(self):
        pins = parse_requirements_pins((_PROJECT / "requirements.txt").read_text())
        result = run_runtime_gate(
            repo_root=_PROJECT,
            version_lookup=lambda n: pins.get(n),
            current_python=(3, 12, 0),
            ctx=detect_platform_context(
                prefix="/tmp/venv",
                base_prefix="/usr",
                executable="/tmp/venv/bin/python",
                system="Linux",
                environ={},
                externally_managed=False,
            ),
        )
        self.assertTrue(result.ok)

    def test_dockerfile_has_container_marker(self):
        docker = (_PROJECT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("PRKS_CONTAINER=1", docker)
        self.assertIn("requirements.txt", docker)

    def test_inventory_lists_core_deps(self):
        inv = json.loads((_PROJECT / "dependency-inventory.json").read_text(encoding="utf-8"))
        names = {d["name"] for d in inv["dependencies"]}
        for required in (
            "PyMuPDF",
            "Pillow",
            "playwright",
            "cytoscape",
            "dompurify",
            "esbuild",
            "react",
            "inter",
            "qpdf",
        ):
            self.assertIn(required, names)

    def test_dependency_manifest_deterministic(self):
        from backend.dependency_gate import build_dependency_manifest

        a = build_dependency_manifest(_PROJECT)
        b = build_dependency_manifest(_PROJECT)
        self.assertEqual(a, b)
        blob = json.dumps(a)
        self.assertNotIn("builtAt", blob)
        self.assertNotIn("fetched", blob.lower())

    def test_sw_uses_dependency_revision(self):
        from backend.dependency_gate import (
            dependency_manifest_revision,
            read_sw_dependency_revision,
        )

        sw = (_PROJECT / "frontend" / "sw.js").read_text(encoding="utf-8")
        rev = read_sw_dependency_revision(sw)
        self.assertEqual(rev, dependency_manifest_revision(repo_root=_PROJECT))
        self.assertIn("prks-static-' + DEPENDENCY_REVISION", sw)
        self.assertIn("RETIRE_PREFIXES", sw)

    def test_no_cdn_in_index(self):
        html = (_PROJECT / "frontend" / "index.html").read_text(encoding="utf-8")
        for marker in ("cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com"):
            self.assertNotIn(marker, html)


class StartupOrderTests(unittest.TestCase):
    def test_prks_app_validates_before_storage(self):
        src = (_PROJECT / "prks_app.py").read_text(encoding="utf-8")
        gate = src.index("ensure_runtime_or_exit")
        storage = src.index("StorageConfig.from_env")
        self.assertLess(gate, storage)


if __name__ == "__main__":
    unittest.main()
