"""Focused tests for the dependency consistency gate."""
from __future__ import annotations

import json
import re
import shlex
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

# Exact pins only for the CI test-gate install argv (see RepoGateLiveTests).
_TEST_GATE_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^#\s]+)$")
_TEST_GATE_APPROVED_OPTIONS = frozenset(
    {
        "--disable-pip-version-check",
        "--only-binary=:all:",
    }
)
_TEST_GATE_APPROVED_OPTIONS_WITH_VALUE = {
    "--only-binary": frozenset({":all:"}),
}
_TEST_GATE_REJECTED_SOURCE_OPTIONS = frozenset(
    {
        "-r",
        "--requirement",
        "-e",
        "--editable",
    }
)
_TEST_GATE_APPROVED_INSTALL_STEP = "Install pinned runtime dependencies"
# Fail-closed: any of these in a non-approved step's run body is refused.
_TEST_GATE_PACKAGE_INSTALL_RE = re.compile(
    r"(?:^|[\s;&|`$()])"
    r"(?:python3?\s+-m\s+pip\s+install|uv\s+pip\s+install|pip3?\s+install)"
    r"\b",
    re.MULTILINE,
)


def parse_test_gate_pip_install_pins(pip_args: list[str]) -> dict[str, str]:
    """Parse `python -m pip install ...` argv into exact name==version pins.

    Every operand after ``install`` must be an approved pip option or an exact
    ``name==version`` pin. Bare names, ``-r``/``-e``, paths/wheels, and
    URL/VCS sources are rejected.
    """
    if pip_args[:4] != ["python", "-m", "pip", "install"]:
        raise ValueError("expected python -m pip install prefix")
    pins: dict[str, str] = {}
    index = 4
    while index < len(pip_args):
        arg = pip_args[index]
        if (
            arg in _TEST_GATE_REJECTED_SOURCE_OPTIONS
            or arg.startswith("--requirement=")
            or arg.startswith("--editable=")
        ):
            raise ValueError(f"disallowed requirement source option: {arg}")
        if arg in _TEST_GATE_APPROVED_OPTIONS:
            index += 1
            continue
        if arg in _TEST_GATE_APPROVED_OPTIONS_WITH_VALUE:
            if index + 1 >= len(pip_args):
                raise ValueError(f"option {arg} missing value")
            value = pip_args[index + 1]
            allowed = _TEST_GATE_APPROVED_OPTIONS_WITH_VALUE[arg]
            if value not in allowed:
                raise ValueError(f"disallowed value for {arg}: {value}")
            index += 2
            continue
        if arg.startswith("-"):
            raise ValueError(f"unapproved pip option: {arg}")
        pin_match = _TEST_GATE_PIN_RE.fullmatch(arg)
        if pin_match is None:
            raise ValueError(f"non-exact package/requirement source: {arg}")
        name, version = pin_match.group(1), pin_match.group(2)
        if name in pins:
            raise ValueError(f"duplicate package pin for {name}")
        pins[name] = version
        index += 1
    return pins


def test_gate_install_argv_from_run_body(
    scalar_style: str, body_lines: list[str]
) -> list[str]:
    """Extract the approved Install-step ``python -m pip install`` argv.

    Literal ``|`` bodies may contain only one non-comment executable command
    (so ``pip install`` / ``python3 -m pip install`` / ``uv pip install``
    after the pinned install cannot hide). Folded ``>`` bodies keep a single
    folded command and the fail-closed argv parse.
    """
    if scalar_style == ">":
        # Folded: YAML turns newlines into spaces — one shell command.
        folded = " ".join(
            line for line in body_lines if line and not line.startswith("#")
        )
        if not folded:
            raise ValueError("empty Install step run body")
        args = shlex.split(folded)
    else:
        executable = [
            line for line in body_lines if line and not line.startswith("#")
        ]
        if len(executable) != 1:
            raise ValueError(
                "literal Install step must contain exactly one non-comment "
                f"executable command (found {len(executable)})"
            )
        args = shlex.split(executable[0])
    if args[:4] != ["python", "-m", "pip", "install"]:
        raise ValueError(
            "Install step command must be python -m pip install "
            f"(got {' '.join(args[:4]) if args else '<empty>'})"
        )
    return args


def _test_gate_run_body_without_shell_comments(body: str) -> str:
    """Drop blank and ``#`` comment lines from a run body before install scans."""
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def run_body_has_package_install(body: str) -> bool:
    """True when a run body contains a package-install command form."""
    text = _test_gate_run_body_without_shell_comments(body)
    return _TEST_GATE_PACKAGE_INSTALL_RE.search(text) is not None


def iter_test_gate_step_run_bodies(
    workflow: str, *, step_indent: int = 6
) -> list[tuple[str | None, str]]:
    """Return ``(step_name, run_body)`` for every steps[] entry that has ``run:``.

    ``step_name`` is None for unnamed ``- run:`` steps. Scoped to GitHub Actions
    list items at ``step_indent`` (PRKS test-gate uses 6).
    """
    dash = f"{' ' * step_indent}- "
    field_indent = step_indent + 2
    field = " " * field_indent
    body_indent = step_indent + 4
    starts = [m.start() for m in re.finditer(rf"(?m)^{re.escape(dash)}", workflow)]
    results: list[tuple[str | None, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(workflow)
        block = workflow[start:end]
        first_nl = block.find("\n")
        first = block[:first_nl] if first_nl >= 0 else block
        name: str | None = None
        name_match = re.match(rf"^{re.escape(dash)}name:\s*(.+)$", first)
        if name_match:
            name = name_match.group(1).strip()
        # `- run: …` on the list item itself (no separate name key).
        inline_run = re.match(rf"^{re.escape(dash)}run:\s*(.*)$", first)
        if inline_run is not None:
            results.append((name, inline_run.group(1)))
            continue
        run_match = re.search(
            rf"(?m)^{re.escape(field)}run:\s*"
            rf"(?:([|>])[-+]?\n((?: {{{body_indent},}}[^\n]*\n)+)|([^\n]*))",
            block,
        )
        if run_match is None:
            continue
        if run_match.group(1) is not None:
            results.append((name, run_match.group(2)))
        else:
            results.append((name, run_match.group(3) or ""))
    return results


def assert_test_gate_no_extra_package_installs(workflow: str) -> None:
    """Refuse package-install commands outside the approved Install step.

    Covers ``pip install``, ``python``/``python3 -m pip install``,
    ``uv pip install``, and their ``-r``/``-e`` forms (all match the install
    verb). Only ``Install pinned runtime dependencies`` may install packages.
    """
    found_approved = False
    for name, body in iter_test_gate_step_run_bodies(workflow):
        if name == _TEST_GATE_APPROVED_INSTALL_STEP:
            found_approved = True
            continue
        if run_body_has_package_install(body):
            label = name if name else "<unnamed step>"
            raise ValueError(
                f"package install outside approved Install step: {label}"
            )
    if not found_approved:
        raise ValueError(
            f"missing approved Install step {_TEST_GATE_APPROVED_INSTALL_STEP!r}"
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
        # Startup runs ensure_runtime_or_exit → load_inventory; the image must
        # ship dependency-inventory.json or every container start fails.
        self.assertRegex(
            docker,
            r"(?m)^\s*COPY\s+dependency-inventory\.json\s+",
        )

    def test_test_gate_workflow_pins_match_requirements(self):
        """CI install must name the same == pins as requirements.txt (Sonar
        rejects unlocked `-r` installs; keep the two sources equal).

        Assert against the install step's executable `run` args only — a pin
        or `--only-binary` mention in a comment must not satisfy the check.
        Package pins collected from that argv must equal requirements.txt;
        bare names, ``-r``/``-e``, wheels, and URL/VCS sources are refused.
        A literal Install body must be exactly one non-comment command — the
        approved ``python -m pip install`` — so ``pip install`` /
        ``python3 -m pip install`` / ``uv pip install`` cannot hide after it.
        Workflow-wide: no other step may run a package-install command.
        """
        pins = parse_requirements_pins((_PROJECT / "requirements.txt").read_text())
        workflow = (_PROJECT / ".github" / "workflows" / "test-gate.yml").read_text(
            encoding="utf-8"
        )
        assert_test_gate_no_extra_package_installs(workflow)
        # Anchor on step name; allow comments / id / other fields before run,
        # and either `|` or `>` block scalars (with optional chomping).
        match = re.search(
            r"(?m)^ {6}- name: Install pinned runtime dependencies\n"
            r"(?: {8,}(?!run:)[^\n]*\n)*"
            r" {8}run: ([|>])[-+]?\n"
            r"((?: {10,}[^\n]*\n)+)",
            workflow,
        )
        self.assertIsNotNone(
            match, "missing Install pinned runtime dependencies run step"
        )
        scalar_style = match.group(1)
        body_lines = [
            line.strip() for line in match.group(2).splitlines() if line.strip()
        ]
        pip_args = test_gate_install_argv_from_run_body(scalar_style, body_lines)
        self.assertIn("--only-binary=:all:", pip_args)
        # Two-way equality via fail-closed operand walk: every argv token after
        # install is an approved option or an exact name==version pin.
        install_pins = parse_test_gate_pip_install_pins(pip_args)
        self.assertEqual(install_pins, pins)

    def test_test_gate_rejects_package_install_in_other_step(self):
        """A later YAML step with `run: pip install requests` must fail closed."""
        workflow = (
            "jobs:\n"
            "  unit-api-contract:\n"
            "    steps:\n"
            "      - name: Install pinned runtime dependencies\n"
            "        run: >-\n"
            "          python -m pip install --disable-pip-version-check "
            '"--only-binary=:all:"\n'
            '          "PyMuPDF==1.28.2" "Pillow==12.3.0"\n'
            "      - name: Prepare test helper\n"
            "        run: pip install requests\n"
        )
        with self.assertRaisesRegex(
            ValueError, r"package install outside approved Install step: Prepare test helper"
        ):
            assert_test_gate_no_extra_package_installs(workflow)

    def test_test_gate_literal_rejects_extra_pip_install_line(self):
        """Later `pip install` / `python3 -m pip` lines must fail the literal check."""
        pinned = (
            "python -m pip install --disable-pip-version-check "
            '"--only-binary=:all:" "PyMuPDF==1.28.2" "Pillow==12.3.0"'
        )
        with self.assertRaisesRegex(
            ValueError, "exactly one non-comment executable command"
        ):
            test_gate_install_argv_from_run_body(
                "|", [pinned, "pip install requests"]
            )
        with self.assertRaisesRegex(
            ValueError, "exactly one non-comment executable command"
        ):
            test_gate_install_argv_from_run_body(
                "|", [pinned, "python3 -m pip install requests"]
            )
        with self.assertRaisesRegex(
            ValueError, "exactly one non-comment executable command"
        ):
            test_gate_install_argv_from_run_body(
                "|", [pinned, "uv pip install requests"]
            )

    def test_test_gate_pip_install_rejects_bare_package(self):
        with self.assertRaisesRegex(ValueError, "non-exact package/requirement source"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "PyMuPDF==1.28.2",
                    "requests",
                ]
            )

    def test_test_gate_pip_install_rejects_requirements_file(self):
        with self.assertRaisesRegex(ValueError, "disallowed requirement source option"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "PyMuPDF==1.28.2",
                    "-r",
                    "extra.txt",
                ]
            )

    def test_test_gate_pip_install_rejects_wheel_and_vcs(self):
        with self.assertRaisesRegex(ValueError, "non-exact package/requirement source"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--only-binary=:all:",
                    "./Pillow-12.3.0-py3-none-any.whl",
                ]
            )
        with self.assertRaisesRegex(ValueError, "non-exact package/requirement source"):
            parse_test_gate_pip_install_pins(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--only-binary=:all:",
                    "git+https://example.invalid/pkg.git",
                ]
            )

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
