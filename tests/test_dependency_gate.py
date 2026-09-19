"""Focused tests for the dependency consistency gate."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from backend.dependency_gate import (
    RequirementsPinError,
    assert_remediation_is_safe,
    default_venv_python_cmd,
    derived_inventory_names,
    detect_platform_context,
    dockerfile_apt_packages,
    dockerfile_python_base_version,
    format_pip_install_command,
    format_venv_create_commands,
    parse_requirements_pins,
    pinned_playwright_version,
    python_min_version,
    remediation_message,
    run_repo_gate,
    run_runtime_gate,
    validate_dockerfile,
    validate_installed_pins,
    validate_inventory,
    validate_npm_island,
    validate_python_version,
    validate_requirements_file,
)


class RequirementsParsingTests(unittest.TestCase):
    def test_parse_exact_pins_ignores_comments(self):
        text = "# comment\nPyMuPDF==1.28.2\n\nPillow==12.3.0\n"
        self.assertEqual(
            parse_requirements_pins(text),
            {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"},
        )

    def test_parse_allows_trailing_comment(self):
        text = "Pillow==12.3.0  # keep exact\n"
        self.assertEqual(parse_requirements_pins(text), {"Pillow": "12.3.0"})

    def test_parse_rejects_gte_operator(self):
        with self.assertRaises(RequirementsPinError) as ctx:
            parse_requirements_pins("Pillow>=12.3.0\n", source="requirements.txt")
        self.assertIn("exact name==version", str(ctx.exception))
        self.assertIn("Pillow>=12.3.0", str(ctx.exception))

    def test_parse_rejects_unpinned_and_malformed(self):
        with self.assertRaises(RequirementsPinError):
            parse_requirements_pins("Pillow\n")
        with self.assertRaises(RequirementsPinError):
            parse_requirements_pins("not a requirement!!!\n")
        with self.assertRaises(RequirementsPinError):
            parse_requirements_pins("Pillow~=12.3.0\n")

    def test_validate_requirements_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            path.write_text("Pillow>=12.3.0\n", encoding="utf-8")
            result, pins = validate_requirements_file(path)
            self.assertFalse(result.ok)
            self.assertEqual(pins, {})
            self.assertEqual(result.issues[0].code, "non_exact_requirement")

    def test_runtime_gate_fails_on_non_exact_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text(
                "PyMuPDF==1.28.2\nPillow>=12.3.0\n",
                encoding="utf-8",
            )
            (root / "dependency-inventory.json").write_text(
                json.dumps({"python_min_version": [3, 12], "dependencies": []}),
                encoding="utf-8",
            )
            result = run_runtime_gate(
                repo_root=root,
                version_lookup=lambda _n: "1.28.2",
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
            self.assertFalse(result.ok)
            self.assertTrue(
                any(i.code == "non_exact_requirement" for i in result.issues)
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

    def test_windows_venv_commands_prefer_py_launcher(self):
        self.assertEqual(default_venv_python_cmd(is_windows=True), "py -3")
        self.assertEqual(default_venv_python_cmd(is_windows=False), "python3")
        cmds = format_venv_create_commands(is_windows=True)
        self.assertEqual(cmds[0], "py -3 -m venv .venv")
        self.assertIn(r".venv\Scripts\python.exe", cmds[1])
        self.assertNotIn("python3", cmds[0])
        # Explicit override still honored.
        overridden = format_venv_create_commands(
            is_windows=True, python_cmd=r"C:\Python312\python.exe"
        )
        self.assertTrue(overridden[0].startswith(r"C:\Python312\python.exe"))

    def test_windows_remediation_uses_py_not_python3(self):
        ctx = detect_platform_context(
            prefix=r"C:\Python312",
            base_prefix=r"C:\Python312",
            executable=r"C:\Python312\python.exe",
            system="Windows",
            environ={},
            externally_managed=False,
        )
        msg = remediation_message(missing=["Pillow"], ctx=ctx)
        assert_remediation_is_safe(msg)
        self.assertIn("py -3 -m venv .venv", msg)
        self.assertNotIn("python3 -m venv", msg)

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


class ResolvePythonTests(unittest.TestCase):
    def test_builders_use_resolve_python_not_bare_python3(self):
        for rel in (
            "tools/frontend-vendor/build.mjs",
            "tools/pdf-viewer/build.mjs",
            "tools/research-graph/build.mjs",
        ):
            text = (_PROJECT / rel).read_text(encoding="utf-8")
            self.assertIn("resolvePython()", text)
            self.assertNotIn('spawnSync(\n  "python3"', text)
            self.assertNotIn("spawnSync('python3'", text)
            self.assertNotIn('spawnSync("python3"', text)

    def test_resolve_python_module_windows_candidates(self):
        script = r"""
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const path = require("node:path");
const fs = require("node:fs");
const src = fs.readFileSync(path.resolve("tools/resolve-python3.mjs"), "utf8");
if (!src.includes("Windows") || !src.includes("py.exe")) {
  throw new Error("missing Windows py launcher candidate");
}
if (!src.includes("resolvePython")) throw new Error("missing resolvePython export");
if (src.includes('spawnSync("python3"')) throw new Error("bare python3 spawn");
console.log("ok");
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=str(_PROJECT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_resolve_python_runs_on_host(self):
        proc = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import { resolvePython } from './tools/resolve-python3.mjs'; "
                "const p = resolvePython(); "
                "if (!p.executable.startsWith('/')) process.exit(2); "
                "console.log(p.executable);",
            ],
            cwd=str(_PROJECT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip().startswith("/"))


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


class InventoryCoverageTests(unittest.TestCase):
    def test_derived_matches_live_inventory(self):
        inv = json.loads(
            (_PROJECT / "dependency-inventory.json").read_text(encoding="utf-8")
        )
        declared = {d["name"] for d in inv["dependencies"]}
        self.assertEqual(declared, derived_inventory_names(_PROJECT))

    def test_missing_inventory_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Minimal mirrors of authoritative manifests.
            (root / "requirements.txt").write_text(
                "PyMuPDF==1.28.2\nPillow==12.3.0\n", encoding="utf-8"
            )
            (root / "requirements-dev.txt").write_text(
                "playwright==1.63.0\n", encoding="utf-8"
            )
            for island, deps in (
                ("pdf-viewer", {"react": "18.3.1"}),
                ("research-graph", {"cytoscape": "3.34.3"}),
                ("frontend-vendor", {"dompurify": "3.0.0"}),
            ):
                d = root / "tools" / island
                d.mkdir(parents=True)
                (d / "package.json").write_text(
                    json.dumps({"dependencies": deps}), encoding="utf-8"
                )
            # Inventory omits Pillow and python structural entries.
            (root / "dependency-inventory.json").write_text(
                json.dumps(
                    {
                        "python_min_version": [3, 12],
                        "dependencies": [
                            {
                                "name": "PyMuPDF",
                                "authoritative_source": "requirements.txt",
                                "manifest": "requirements.txt",
                                "exact_installed_version_check": True,
                                "scope": "runtime",
                            },
                            {
                                "name": "playwright",
                                "authoritative_source": "requirements-dev.txt",
                                "manifest": "requirements-dev.txt",
                                "exact_installed_version_check": True,
                                "scope": "test",
                            },
                            {
                                "name": "react",
                                "authoritative_source": "tools/pdf-viewer/package.json",
                                "manifest": "tools/pdf-viewer/package.json",
                                "scope": "build",
                            },
                            {
                                "name": "cytoscape",
                                "authoritative_source": "tools/research-graph/package.json",
                                "manifest": "tools/research-graph/package.json",
                                "scope": "vendor",
                            },
                            {
                                "name": "dompurify",
                                "authoritative_source": "tools/frontend-vendor/package.json",
                                "manifest": "tools/frontend-vendor/package.json",
                                "scope": "vendor",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            missing = {
                i.message.split(" missing ", 1)[-1].split(" ", 1)[0]
                for i in result.issues
                if i.code == "inventory_missing"
            }
            self.assertIn("Pillow", missing)
            self.assertIn("python", missing)
            self.assertIn("inter", missing)

    def test_extra_inventory_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("Pillow==12.3.0\n", encoding="utf-8")
            (root / "requirements-dev.txt").write_text(
                "playwright==1.63.0\n", encoding="utf-8"
            )
            for island in ("pdf-viewer", "research-graph", "frontend-vendor"):
                d = root / "tools" / island
                d.mkdir(parents=True)
                (d / "package.json").write_text(
                    json.dumps({"dependencies": {}}), encoding="utf-8"
                )
            expected_structural = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "ghost-package",
            ]
            (root / "dependency-inventory.json").write_text(
                json.dumps(
                    {
                        "python_min_version": [3, 12],
                        "dependencies": [
                            {
                                "name": n,
                                "authoritative_source": "test",
                                "manifest": "requirements.txt",
                                "scope": "runtime",
                            }
                            for n in expected_structural
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    i.code == "inventory_extra" and "ghost-package" in i.message
                    for i in result.issues
                )
            )


class DockerfileConsistencyTests(unittest.TestCase):
    def _write_minimal_manifests(self, root: Path) -> None:
        (root / "requirements.txt").write_text("Pillow==12.3.0\n", encoding="utf-8")
        (root / "requirements-dev.txt").write_text(
            "playwright==1.63.0\n", encoding="utf-8"
        )
        for island in ("pdf-viewer", "research-graph", "frontend-vendor"):
            d = root / "tools" / island
            d.mkdir(parents=True)
            (d / "package.json").write_text(
                json.dumps({"dependencies": {}}), encoding="utf-8"
            )

    def _inventory(self, *, names: list[str], python_min=(3, 12)) -> str:
        deps = []
        for n in names:
            entry = {
                "name": n,
                "authoritative_source": "test",
                "manifest": (
                    "Dockerfile"
                    if n in ("python-base-image", "qpdf", "curl")
                    else "requirements.txt"
                ),
                "scope": "system" if n in ("qpdf", "curl") else "runtime",
            }
            deps.append(entry)
        return json.dumps(
            {"python_min_version": list(python_min), "dependencies": deps}
        )

    def test_parse_from_and_apt_packages(self):
        text = (
            "FROM python:3.12-slim\n"
            "RUN apt-get update \\\n"
            "    && apt-get install -y --no-install-recommends qpdf curl \\\n"
            "    && rm -rf /var/lib/apt/lists/*\n"
        )
        self.assertEqual(dockerfile_python_base_version(text), (3, 12))
        self.assertEqual(dockerfile_apt_packages(text), ["qpdf", "curl"])

    def test_older_base_image_fails_repo_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_minimal_manifests(root)
            (root / "Dockerfile").write_text(
                "FROM python:3.11-slim\n"
                "RUN apt-get install -y --no-install-recommends qpdf\n",
                encoding="utf-8",
            )
            names = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "python-base-image",
                "qpdf",
            ]
            (root / "dependency-inventory.json").write_text(
                self._inventory(names=names, python_min=(3, 12)),
                encoding="utf-8",
            )
            result = validate_dockerfile(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(i.code == "dockerfile_python_version" for i in result.issues)
            )
            inv_result = validate_inventory(root)
            self.assertFalse(inv_result.ok)
            self.assertTrue(
                any(i.code == "dockerfile_python_version" for i in inv_result.issues)
            )

    def test_removed_apt_package_leaves_inventory_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_minimal_manifests(root)
            # Dockerfile no longer installs qpdf.
            (root / "Dockerfile").write_text(
                "FROM python:3.12-slim\n",
                encoding="utf-8",
            )
            names = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "python-base-image",
                "qpdf",
            ]
            (root / "dependency-inventory.json").write_text(
                self._inventory(names=names),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    i.code == "inventory_extra" and "qpdf" in i.message
                    for i in result.issues
                )
            )

    def test_new_apt_package_requires_inventory_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_minimal_manifests(root)
            (root / "Dockerfile").write_text(
                "FROM python:3.12-slim\n"
                "RUN apt-get install -y --no-install-recommends qpdf curl\n",
                encoding="utf-8",
            )
            names = [
                "Pillow",
                "playwright",
                "inter",
                "python",
                "python-base-image",
                "qpdf",
            ]
            (root / "dependency-inventory.json").write_text(
                self._inventory(names=names),
                encoding="utf-8",
            )
            result = validate_inventory(root)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    i.code == "inventory_missing" and "curl" in i.message
                    for i in result.issues
                )
            )

    def test_live_dockerfile_matches_inventory(self):
        docker = (_PROJECT / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(
            dockerfile_python_base_version(docker),
            tuple(python_min_version(_PROJECT)[:2]),
        )
        self.assertIn("qpdf", dockerfile_apt_packages(docker))
        self.assertTrue(validate_dockerfile(_PROJECT).ok)


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
            "python",
            "python-base-image",
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
