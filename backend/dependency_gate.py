"""PRKS dependency consistency and runtime environment validation.

Offline by default: never contacts PyPI, npm, or GitHub during --runtime,
--test, --repo, application startup, or ordinary tests.

Freshness discovery (Dependabot / --check-latest) is a separate concern.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import sysconfig
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as dist_version
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPO_ROOT / "dependency-inventory.json"
VENDOR_ROOT = REPO_ROOT / "frontend" / "vendor"
DEPENDENCY_MANIFEST_PATH = VENDOR_ROOT / "DEPENDENCY-MANIFEST.json"
SW_PATH = REPO_ROOT / "frontend" / "sw.js"
INDEX_HTML_PATH = REPO_ROOT / "frontend" / "index.html"

CDN_RUNTIME_MARKERS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
)

# Provenance URLs in VERSION files are allowed; production loaders must not load them.
CDN_LOADER_MARKERS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net/npm/easymde",
    "cdn.jsdelivr.net/npm/codemirror",
    "cdn.jsdelivr.net/npm/lucide",
    "cdn.jsdelivr.net/npm/@embedpdf/snippet",
    "cdn.jsdelivr.net/npm/cytoscape",
    "unpkg.com",
)

NPM_ISLANDS = (
    {
        "name": "pdf-viewer",
        "dir": REPO_ROOT / "tools" / "pdf-viewer",
        "direct_keys": ("dependencies", "devDependencies"),
        "react_guard": True,
        "types_guard": True,
    },
    {
        "name": "research-graph",
        "dir": REPO_ROOT / "tools" / "research-graph",
        "direct_keys": ("dependencies", "devDependencies"),
        "react_guard": False,
        "types_guard": False,
    },
    {
        "name": "frontend-vendor",
        "dir": REPO_ROOT / "tools" / "frontend-vendor",
        "direct_keys": ("dependencies", "devDependencies"),
        "react_guard": False,
        "types_guard": False,
    },
)

# Runtime vendor files that must appear in DEPENDENCY-MANIFEST and on disk.
REGISTERED_VENDOR_RUNTIME_FILES = (
    "inter/inter.css",
    "inter/InterVariable.woff2",
    "easymde/easymde.min.css",
    "easymde/easymde.min.js",
    "codemirror/codemirror.js",
    "codemirror/show-hint.js",
    "codemirror/show-hint.css",
    "dompurify/purify.min.js",
    "lucide/lucide.min.js",
    "cytoscape/cytoscape.min.js",
    "idb/idb.min.js",
    "prks-pdf-viewer/prks-pdf-viewer.js",
    "prks-pdf-viewer/prks-pdf-viewer.css",
    "prks-pdf-viewer/pdfium.wasm",
)

# Exact pin only: name==version with optional trailing comment. Fail closed otherwise.
_REQ_PIN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*==\s*([^#\s]+)\s*(?:#.*)?$"
)
_SW_REV_RE = re.compile(
    r"const\s+DEPENDENCY_REVISION\s*=\s*['\"]([0-9a-fA-F]+)['\"]\s*;"
)
_SW_REV_ANY_RE = re.compile(
    r"const\s+DEPENDENCY_REVISION\s*=\s*['\"]([^'\"]+)['\"]\s*;"
)

# Inventory entries that are not derived from requirements*.txt / package.json /
# Dockerfile. Docker base image + apt packages come from the Dockerfile itself.
_INVENTORY_STRUCTURAL_NAMES = frozenset(
    {
        "inter",
        "idb",
        "python",
    }
)

# First-stage (or any) `FROM python:X.Y…` image tag.
_DOCKER_FROM_PYTHON_RE = re.compile(
    r"(?im)^\s*FROM\s+(?:--\S+\s+)*python:(\d+)\.(\d+)(?:[^\s]*)?"
)
# Collapse Dockerfile line continuations before apt token scans.
_DOCKER_LINE_CONT_RE = re.compile(r"\\\s*\n")
_DOCKER_APT_INSTALL_RE = re.compile(
    r"apt-get\s+install\b([^&\n|;]*)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class GateIssue:
    code: str
    message: str
    path: str | None = None


@dataclass
class GateResult:
    ok: bool
    issues: list[GateIssue] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def extend(self, other: "GateResult") -> None:
        if not other.ok:
            self.ok = False
        self.issues.extend(other.issues)
        self.details.extend(other.details)

    def fail(self, code: str, message: str, path: str | None = None) -> None:
        self.ok = False
        self.issues.append(GateIssue(code=code, message=message, path=path))


# ---------------------------------------------------------------------------
# Inventory + requirements parsing
# ---------------------------------------------------------------------------


def load_inventory(repo_root: Path | None = None) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    path = root / "dependency-inventory.json"
    return json.loads(path.read_text(encoding="utf-8"))


class RequirementsPinError(ValueError):
    """Raised when a requirements file contains a non-exact or malformed pin."""


def parse_requirements_pins(text: str, *, source: str = "requirements") -> dict[str, str]:
    """Parse exact name==version pins. Fail closed on any other active line.

    Blank lines and full-line comments are ignored. Every other non-empty line
    must be an exact ``name==version`` pin (optional trailing ``#`` comment).
    Operators such as ``>=``, ``~=``, unpinned names, and includes are rejected.
    """
    pins: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _REQ_PIN_RE.match(line)
        if not m:
            raise RequirementsPinError(
                f"{source}:{lineno}: requirement must be an exact name==version "
                f"pin; got {line!r}"
            )
        name, ver = m.group(1), m.group(2)
        if name in pins:
            raise RequirementsPinError(
                f"{source}:{lineno}: duplicate pin for {name!r}"
            )
        pins[name] = ver
    return pins


def read_requirements_pins(path: Path) -> dict[str, str]:
    return parse_requirements_pins(
        path.read_text(encoding="utf-8"),
        source=str(path),
    )


def validate_requirements_file(path: Path) -> tuple[GateResult, dict[str, str]]:
    """Return GateResult + pins for one requirements file (fail closed)."""
    result = GateResult(ok=True)
    if not path.is_file():
        result.fail(
            "missing_requirements",
            f"requirements file missing: {path.name}",
            str(path),
        )
        return result, {}
    try:
        pins = read_requirements_pins(path)
    except RequirementsPinError as exc:
        result.fail("non_exact_requirement", str(exc), str(path))
        return result, {}
    return result, pins


def runtime_requirement_pins(repo_root: Path | None = None) -> dict[str, str]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    return read_requirements_pins(root / "requirements.txt")


def test_requirement_pins(repo_root: Path | None = None) -> dict[str, str]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    pins = dict(runtime_requirement_pins(root))
    pins.update(read_requirements_pins(root / "requirements-dev.txt"))
    return pins


def pinned_playwright_version(repo_root: Path | None = None) -> str:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    pins = read_requirements_pins(root / "requirements-dev.txt")
    pin = pins.get("playwright")
    if not pin:
        raise RuntimeError("requirements-dev.txt has no playwright== pin")
    return pin


def pinned_openapi_core_version(repo_root: Path | None = None) -> str:
    """Exact openapi-core pin from requirements-dev (unit-contract preflight)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    pins = read_requirements_pins(root / "requirements-dev.txt")
    pin = pins.get("openapi-core")
    if not pin:
        raise RuntimeError("requirements-dev.txt has no openapi-core== pin")
    return pin


def python_min_version(repo_root: Path | None = None) -> tuple[int, ...]:
    inv = load_inventory(repo_root)
    raw = inv.get("python_min_version") or [3, 12]
    return tuple(int(x) for x in raw)


# ---------------------------------------------------------------------------
# Platform detection + remediation messages (pure / testable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlatformContext:
    in_venv: bool
    is_windows: bool
    is_posix: bool
    externally_managed: bool
    in_prks_docker: bool
    executable: str
    prefix: str
    base_prefix: str


def detect_externally_managed(sysconfig_get_path: Callable[[str], str] | None = None) -> bool:
    """True when the interpreter is marked PEP 668 EXTERNALLY-MANAGED."""
    getter = sysconfig_get_path or sysconfig.get_path
    try:
        purelib = getter("purelib")
    except Exception:
        return False
    if not purelib:
        return False
    marker = Path(purelib).parent / "EXTERNALLY-MANAGED"
    # Debian/Ubuntu place the marker next to stdlib, not always under purelib.
    candidates = [
        marker,
        Path(purelib).resolve().parent / "EXTERNALLY-MANAGED",
    ]
    # Also check the scheme's stdlib-ish locations used by CPython 3.12+.
    try:
        stdlib = getter("stdlib")
        if stdlib:
            candidates.append(Path(stdlib) / "EXTERNALLY-MANAGED")
            candidates.append(Path(stdlib).parent / "EXTERNALLY-MANAGED")
    except Exception:
        pass
    for path in candidates:
        try:
            if path.is_file():
                return True
        except OSError:
            continue
    return False


def detect_prks_docker(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    val = (env.get("PRKS_CONTAINER") or "").strip().lower()
    return val in ("1", "true", "yes")


def detect_platform_context(
    *,
    prefix: str | None = None,
    base_prefix: str | None = None,
    executable: str | None = None,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    externally_managed: bool | None = None,
) -> PlatformContext:
    pref = sys.prefix if prefix is None else prefix
    base = sys.base_prefix if base_prefix is None else base_prefix
    exe = sys.executable if executable is None else executable
    plat = platform.system() if system is None else system
    in_venv = os.path.normcase(pref) != os.path.normcase(base)
    is_windows = plat.lower().startswith("win")
    is_posix = (not is_windows) and os.name == "posix"
    ext = (
        detect_externally_managed()
        if externally_managed is None
        else bool(externally_managed)
    )
    return PlatformContext(
        in_venv=in_venv,
        is_windows=is_windows,
        is_posix=is_posix,
        externally_managed=ext and not in_venv,
        in_prks_docker=detect_prks_docker(environ),
        executable=exe,
        prefix=pref,
        base_prefix=base,
    )


def quote_shell_path(path: str, *, is_windows: bool) -> str:
    if is_windows:
        if re.search(r'[\s"]', path):
            return '"' + path.replace('"', '\\"') + '"'
        return path
    if re.search(r"[^\w@%+=:,./-]", path):
        return "'" + path.replace("'", "'\"'\"'") + "'"
    return path


def format_pip_install_command(
    executable: str,
    requirements_file: str,
    *,
    is_windows: bool,
) -> str:
    exe = quote_shell_path(executable, is_windows=is_windows)
    req = quote_shell_path(requirements_file, is_windows=is_windows)
    return f"{exe} -m pip install -r {req}"


def default_venv_python_cmd(*, is_windows: bool) -> str:
    """Platform-appropriate interpreter name for remediation examples.

    Windows installs typically expose ``py`` / ``python``, not ``python3``.
    """
    return "py -3" if is_windows else "python3"


def format_venv_create_commands(
    *,
    is_windows: bool,
    python_cmd: str | None = None,
) -> list[str]:
    cmd = (
        python_cmd
        if python_cmd is not None
        else default_venv_python_cmd(is_windows=is_windows)
    )
    if is_windows:
        return [
            f"{cmd} -m venv .venv",
            r".venv\Scripts\python.exe -m pip install -r requirements.txt",
        ]
    return [
        f"{cmd} -m venv .venv",
        "./.venv/bin/python -m pip install -r requirements.txt",
    ]


def remediation_message(
    *,
    missing: Sequence[str] | None = None,
    mismatched: Sequence[tuple[str, str, str]] | None = None,
    python_too_old: tuple[tuple[int, ...], tuple[int, ...]] | None = None,
    ctx: PlatformContext | None = None,
    requirements_file: str = "requirements.txt",
) -> str:
    """Build an actionable, platform-aware remediation message.

    Never recommends sudo pip or --break-system-packages.
    """
    ctx = ctx or detect_platform_context()
    lines: list[str] = ["PRKS dependency check failed."]

    if python_too_old is not None:
        have, need = python_too_old
        lines.append(
            "Python %s is below the required minimum %s."
            % (".".join(str(x) for x in have), ".".join(str(x) for x in need))
        )

    if missing:
        lines.append("Missing packages: " + ", ".join(missing))
    if mismatched:
        for name, have, want in mismatched:
            lines.append(
                "Package %s is version %s but requirements pin %s."
                % (name, have, want)
            )

    lines.append("")

    if ctx.in_prks_docker:
        lines.extend(
            [
                "This process is running inside the PRKS container (PRKS_CONTAINER=1).",
                "Do not pip-install into the running container.",
                "Rebuild the image so it picks up the pinned requirements.txt:",
                "  ./docker-build.sh",
                "  # or: docker compose build && docker compose up",
            ]
        )
        return "\n".join(lines)

    if ctx.in_venv:
        cmd = format_pip_install_command(
            ctx.executable, requirements_file, is_windows=ctx.is_windows
        )
        lines.extend(
            [
                "You are in a virtual environment. Install the pinned dependencies with:",
                f"  {cmd}",
            ]
        )
        return "\n".join(lines)

    if ctx.externally_managed:
        lines.extend(
            [
                "This Python is marked EXTERNALLY-MANAGED (PEP 668).",
                "Do not use sudo pip or pip --break-system-packages.",
                "Create a project-local virtual environment and install into it:",
            ]
        )
        for cmd in format_venv_create_commands(is_windows=ctx.is_windows):
            lines.append(f"  {cmd}")
        if ctx.is_windows:
            lines.append(r"Then run PRKS with: .venv\Scripts\python.exe prks_app.py")
        else:
            lines.append("Then run PRKS with: ./.venv/bin/python prks_app.py")
        return "\n".join(lines)

    # Generic host Python (not venv, not PEP 668, not Docker).
    lines.extend(
        [
            "Install into a project-local virtual environment (recommended):",
        ]
    )
    for cmd in format_venv_create_commands(is_windows=ctx.is_windows):
        lines.append(f"  {cmd}")
    lines.extend(
        [
            "",
            "Or, if this interpreter is already dedicated to PRKS:",
            "  "
            + format_pip_install_command(
                ctx.executable, requirements_file, is_windows=ctx.is_windows
            ),
        ]
    )
    return "\n".join(lines)


def assert_remediation_is_safe(message: str) -> None:
    """Raise AssertionError if a remediation message suggests unsafe pip.

    Negated warnings ("Do not use sudo pip") are allowed; affirmative command
    lines are not. Uses simple token checks rather than backtracking regexes.
    """
    for line in message.splitlines():
        stripped = line.strip().lstrip("#").strip()
        lowered = stripped.lower()
        negated = (
            lowered.startswith("do not ")
            or lowered.startswith("don't ")
            or lowered.startswith("never ")
        )
        if not negated and "sudo" in lowered and "pip" in lowered:
            raise AssertionError("remediation must never suggest sudo pip")
        if not negated and "--break-system-packages" in lowered:
            raise AssertionError("remediation must never suggest --break-system-packages")


# ---------------------------------------------------------------------------
# Installed Python package validation
# ---------------------------------------------------------------------------


def installed_distribution_version(name: str) -> str | None:
    try:
        return dist_version(name)
    except PackageNotFoundError:
        return None


def validate_python_version(
    *,
    current: tuple[int, ...] | None = None,
    minimum: tuple[int, ...] | None = None,
    repo_root: Path | None = None,
) -> GateResult:
    result = GateResult(ok=True)
    have = current if current is not None else sys.version_info[:3]
    need = minimum if minimum is not None else python_min_version(repo_root)
    # Compare major.minor only for the floor (patch floats with distro builds).
    have_mm = tuple(have[:2])
    need_mm = tuple(need[:2])
    if have_mm < need_mm:
        result.fail(
            "python_too_old",
            "Python %s < required %s"
            % (".".join(str(x) for x in have_mm), ".".join(str(x) for x in need_mm)),
        )
    return result


def validate_installed_pins(
    pins: Mapping[str, str],
    *,
    version_lookup: Callable[[str], str | None] | None = None,
) -> GateResult:
    """Verify every exact pin is installed at the pinned version via metadata."""
    result = GateResult(ok=True)
    lookup = version_lookup or installed_distribution_version
    missing: list[str] = []
    mismatched: list[tuple[str, str, str]] = []
    for name, want in pins.items():
        have = lookup(name)
        if have is None:
            missing.append(name)
            result.fail(
                "missing_package",
                f"{name} is not installed (required {want})",
            )
        elif have != want:
            mismatched.append((name, have, want))
            result.fail(
                "version_mismatch",
                f"{name} installed {have} != pinned {want}",
            )
    result.details.append(f"checked {len(pins)} pin(s)")
    # Stash structured lists for remediation builders.
    result.details.append("missing:" + ",".join(missing))
    result.details.append(
        "mismatched:"
        + ";".join(f"{n}|{h}|{w}" for n, h, w in mismatched)
    )
    return result


def validate_runtime_python(
    *,
    repo_root: Path | None = None,
    ctx: PlatformContext | None = None,
    version_lookup: Callable[[str], str | None] | None = None,
    current_python: tuple[int, ...] | None = None,
) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    py = validate_python_version(current=current_python, repo_root=root)
    result.extend(py)
    req_path = root / "requirements.txt"
    req_result, pins = validate_requirements_file(req_path)
    result.extend(req_result)
    if req_result.ok:
        pin_result = validate_installed_pins(pins, version_lookup=version_lookup)
        result.extend(pin_result)

    if not result.ok:
        missing = []
        mismatched = []
        for issue in result.issues:
            if issue.code == "missing_package":
                missing.append(issue.message.split(" ", 1)[0])
            elif issue.code == "version_mismatch":
                # "Name installed X != pinned Y"
                parts = issue.message.split()
                if len(parts) >= 6:
                    mismatched.append((parts[0], parts[2], parts[5]))
        py_old = None
        if any(i.code == "python_too_old" for i in result.issues):
            have = current_python or sys.version_info[:3]
            py_old = (have[:2], python_min_version(root)[:2])
        # Non-exact requirements still get a remediation block when packages fail;
        # parser failures are self-describing via the issue message.
        if missing or mismatched or py_old is not None:
            msg = remediation_message(
                missing=missing or None,
                mismatched=mismatched or None,
                python_too_old=py_old,
                ctx=ctx or detect_platform_context(),
                requirements_file=str(req_path),
            )
            assert_remediation_is_safe(msg)
            result.details.append(msg)
    return result


def validate_unit_contract_python(
    *,
    repo_root: Path | None = None,
    ctx: PlatformContext | None = None,
    version_lookup: Callable[[str], str | None] | None = None,
    current_python: tuple[int, ...] | None = None,
) -> GateResult:
    """Runtime pins plus openapi-core only (no Playwright / browser deps).

    Unit discovery imports ``openapi_core`` from the Positions contract tests;
    the default unit preflight must refuse a runtime-only install before
    discovery raises ``ModuleNotFoundError``.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = validate_runtime_python(
        repo_root=root,
        ctx=ctx,
        version_lookup=version_lookup,
        current_python=current_python,
    )
    dev_path = root / "requirements-dev.txt"
    # Check existence before reading pins so a missing file is
    # ``missing_requirements``, not an uncaught FileNotFoundError.
    if not dev_path.is_file():
        result.fail(
            "missing_requirements",
            f"requirements file missing: {dev_path.name}",
            str(dev_path),
        )
        return result
    try:
        pin = pinned_openapi_core_version(root)
    except RuntimeError as exc:
        result.fail("missing_openapi_core_pin", str(exc), str(dev_path))
        return result
    except RequirementsPinError as exc:
        result.fail("non_exact_requirement", str(exc), str(dev_path))
        return result
    extra = validate_installed_pins(
        {"openapi-core": pin},
        version_lookup=version_lookup,
    )
    if not extra.ok and result.ok:
        missing = [
            i.message.split(" ", 1)[0]
            for i in extra.issues
            if i.code == "missing_package"
        ]
        mismatched = []
        for i in extra.issues:
            if i.code == "version_mismatch":
                parts = i.message.split()
                if len(parts) >= 6:
                    mismatched.append((parts[0], parts[2], parts[5]))
        msg = remediation_message(
            missing=missing or None,
            mismatched=mismatched or None,
            ctx=ctx or detect_platform_context(),
            requirements_file=str(dev_path),
        )
        assert_remediation_is_safe(msg)
        extra.details.append(msg)
    result.extend(extra)
    return result


def validate_test_python(
    *,
    repo_root: Path | None = None,
    ctx: PlatformContext | None = None,
    version_lookup: Callable[[str], str | None] | None = None,
    current_python: tuple[int, ...] | None = None,
) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = validate_runtime_python(
        repo_root=root,
        ctx=ctx,
        version_lookup=version_lookup,
        current_python=current_python,
    )
    # Always also check playwright even if runtime already failed — collect all.
    dev_path = root / "requirements-dev.txt"
    dev_result, pins = validate_requirements_file(dev_path)
    result.extend(dev_result)
    if not dev_result.ok:
        return result
    extra = validate_installed_pins(pins, version_lookup=version_lookup)
    if not extra.ok:
        # Rebuild remediation pointing at requirements-dev.txt when only test deps fail.
        if result.ok:
            missing = [
                i.message.split(" ", 1)[0]
                for i in extra.issues
                if i.code == "missing_package"
            ]
            mismatched = []
            for i in extra.issues:
                if i.code == "version_mismatch":
                    parts = i.message.split()
                    if len(parts) >= 6:
                        mismatched.append((parts[0], parts[2], parts[5]))
            msg = remediation_message(
                missing=missing or None,
                mismatched=mismatched or None,
                ctx=ctx or detect_platform_context(),
                requirements_file=str(dev_path),
            )
            assert_remediation_is_safe(msg)
            extra.details.append(msg)
        result.extend(extra)
    else:
        result.extend(extra)
    return result


# ---------------------------------------------------------------------------
# npm package.json ↔ lockfile
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def package_direct_pins(pkg: Mapping[str, Any]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        section = pkg.get(key) or {}
        if isinstance(section, dict):
            for name, ver in section.items():
                pins[str(name)] = str(ver).lstrip("^~=")
    return pins


def lockfile_resolved_version(lock: Mapping[str, Any], package_name: str) -> str | None:
    packages = lock.get("packages")
    if isinstance(packages, dict):
        entry = packages.get(f"node_modules/{package_name}")
        if isinstance(entry, dict) and entry.get("version"):
            return str(entry["version"])
    # lockfileVersion 1 fallback
    deps = lock.get("dependencies")
    if isinstance(deps, dict):
        entry = deps.get(package_name)
        if isinstance(entry, dict) and entry.get("version"):
            return str(entry["version"]).split("-", 1)[0]
    return None


def validate_npm_island(island: Mapping[str, Any]) -> GateResult:
    result = GateResult(ok=True)
    directory = Path(island["dir"])
    pkg_path = directory / "package.json"
    lock_path = directory / "package-lock.json"
    name = island["name"]

    if not pkg_path.is_file():
        result.fail("missing_package_json", f"{name}: package.json missing", str(pkg_path))
        return result
    if not lock_path.is_file():
        result.fail("missing_lockfile", f"{name}: package-lock.json missing", str(lock_path))
        return result

    pkg = load_json(pkg_path)
    lock = load_json(lock_path)
    pins = package_direct_pins(pkg)

    for dep_name, want in pins.items():
        have = lockfile_resolved_version(lock, dep_name)
        if have is None:
            result.fail(
                "lock_missing_package",
                f"{name}: {dep_name}@{want} not resolved in lockfile",
                str(lock_path),
            )
        elif have != want:
            result.fail(
                "lock_version_mismatch",
                f"{name}: {dep_name} package.json={want} lockfile={have}",
                str(lock_path),
            )

    if island.get("react_guard"):
        react = pins.get("react")
        react_dom = pins.get("react-dom")
        if react and react_dom and react != react_dom:
            result.fail(
                "react_dom_mismatch",
                f"{name}: react ({react}) != react-dom ({react_dom})",
                str(pkg_path),
            )

    if island.get("types_guard"):
        types_react = pins.get("@types/react")
        types_dom = pins.get("@types/react-dom")
        # Major.minor of @types/react should match react's major.minor when both present.
        react = pins.get("react")
        if types_react and react:
            tr_mm = ".".join(types_react.split(".")[:2])
            r_mm = ".".join(react.split(".")[:2])
            if tr_mm != r_mm:
                result.fail(
                    "types_react_stack_mismatch",
                    f"{name}: @types/react {types_react} does not match react {react}",
                    str(pkg_path),
                )
        if types_react and types_dom:
            # @types/react-dom major should match @types/react major
            if types_react.split(".")[0] != types_dom.split(".")[0]:
                result.fail(
                    "types_dom_stack_mismatch",
                    f"{name}: @types/react {types_react} vs @types/react-dom {types_dom}",
                    str(pkg_path),
                )

    return result


def validate_all_npm_islands(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    for raw in NPM_ISLANDS:
        island = dict(raw)
        island["dir"] = root / Path(raw["dir"]).relative_to(REPO_ROOT)
        result.extend(validate_npm_island(island))
    return result


# ---------------------------------------------------------------------------
# Vendor VERSION / hash helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_version_file(text: str) -> dict[str, str]:
    """Parse KEY: value / bare first-line version VERSION files."""
    data: dict[str, str] = {}
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return data
    first = lines[0].strip()
    if ":" not in first and " " not in first:
        data["version"] = first
    elif re.match(r"^[\d]", first) and ":" not in first.split()[0]:
        data["version"] = first.split()[0] if " " in first else first
    for line in lines:
        if ":" in line:
            key, _, val = line.partition(":")
            data[key.strip().lower()] = val.strip()
    # pdf-viewer VERSION uses "embedpdf 2.15.1" style
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("embedpdf", "react", "react-dom", "fontFallback"):
            data[parts[0].lower()] = parts[1]
    return data


def validate_cytoscape_vendor(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    pkg = load_json(root / "tools" / "research-graph" / "package.json")
    want = str(pkg["dependencies"]["cytoscape"])
    version_path = root / "frontend" / "vendor" / "cytoscape" / "VERSION"
    js_path = root / "frontend" / "vendor" / "cytoscape" / "cytoscape.min.js"
    if not version_path.is_file():
        result.fail("missing_version", "cytoscape VERSION missing", str(version_path))
        return result
    if not js_path.is_file():
        result.fail("missing_asset", "cytoscape.min.js missing", str(js_path))
        return result
    meta = parse_version_file(version_path.read_text(encoding="utf-8"))
    ver = meta.get("version") or ""
    if ver != want:
        result.fail(
            "cytoscape_version_mismatch",
            f"cytoscape VERSION={ver} package.json={want}",
            str(version_path),
        )
    recorded = meta.get("sha256")
    actual = sha256_file(js_path)
    if not recorded:
        result.fail("cytoscape_missing_sha", "cytoscape VERSION has no sha256", str(version_path))
    elif recorded != actual:
        result.fail(
            "cytoscape_sha_mismatch",
            f"cytoscape.min.js sha256 {actual} != VERSION {recorded}",
            str(js_path),
        )
    text = version_path.read_text(encoding="utf-8")
    if "fetched:" in text.lower():
        result.fail(
            "nondeterministic_fetched",
            "cytoscape VERSION must not contain fetched: (nondeterministic)",
            str(version_path),
        )
    for marker in ("cdn.jsdelivr.net", "unpkg.com"):
        if marker in text:
            result.fail("cdn_in_version", f"cytoscape VERSION contains {marker}", str(version_path))
    return result


def validate_pdf_viewer_vendor(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    pkg = load_json(root / "tools" / "pdf-viewer" / "package.json")
    manifest_path = root / "frontend" / "vendor" / "prks-pdf-viewer" / "BUILD-MANIFEST.json"
    if not manifest_path.is_file():
        result.fail("missing_manifest", "BUILD-MANIFEST.json missing", str(manifest_path))
        return result
    manifest = load_json(manifest_path)
    if "builtAt" in manifest:
        result.fail(
            "nondeterministic_built_at",
            "BUILD-MANIFEST must not contain builtAt",
            str(manifest_path),
        )

    deps = pkg.get("dependencies") or {}
    embed_versions = {
        v for k, v in deps.items() if str(k).startswith("@embedpdf/")
    }
    if len(embed_versions) != 1:
        result.fail(
            "embedpdf_pin_spread",
            f"@embedpdf/* pins are not uniform: {sorted(embed_versions)}",
            str(root / "tools" / "pdf-viewer" / "package.json"),
        )
    embed = next(iter(embed_versions)) if embed_versions else None
    if embed and manifest.get("embedpdf") != embed:
        result.fail(
            "embedpdf_manifest_mismatch",
            f"BUILD-MANIFEST embedpdf={manifest.get('embedpdf')} package={embed}",
            str(manifest_path),
        )
    if manifest.get("react") != deps.get("react"):
        result.fail(
            "react_manifest_mismatch",
            f"BUILD-MANIFEST react={manifest.get('react')} package={deps.get('react')}",
            str(manifest_path),
        )
    if manifest.get("reactDom") != deps.get("react-dom"):
        result.fail(
            "react_dom_manifest_mismatch",
            f"BUILD-MANIFEST reactDom={manifest.get('reactDom')} package={deps.get('react-dom')}",
            str(manifest_path),
        )
    esbuild_pin = (pkg.get("devDependencies") or {}).get("esbuild")
    if esbuild_pin and manifest.get("esbuild") != esbuild_pin:
        result.fail(
            "esbuild_manifest_mismatch",
            f"BUILD-MANIFEST esbuild={manifest.get('esbuild')} package={esbuild_pin}",
            str(manifest_path),
        )

    out = manifest.get("outputSha256") or {}
    mapping = {
        "js": "prks-pdf-viewer.js",
        "css": "prks-pdf-viewer.css",
        "wasm": "pdfium.wasm",
    }
    vendor_dir = root / "frontend" / "vendor" / "prks-pdf-viewer"
    for key, filename in mapping.items():
        path = vendor_dir / filename
        if not path.is_file():
            result.fail("missing_asset", f"missing {filename}", str(path))
            continue
        actual = sha256_file(path)
        want = out.get(key)
        if not want:
            result.fail("missing_sha", f"BUILD-MANIFEST missing outputSha256.{key}", str(manifest_path))
        elif want != actual:
            result.fail(
                "pdf_sha_mismatch",
                f"{filename} sha256 {actual} != manifest {want}",
                str(path),
            )
    return result


def validate_frontend_vendor_island(repo_root: Path | None = None) -> GateResult:
    """package.json pins agree with VERSION files and on-disk hashes for npm vendors."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    pkg_path = root / "tools" / "frontend-vendor" / "package.json"
    if not pkg_path.is_file():
        result.fail("missing_frontend_vendor", "tools/frontend-vendor/package.json missing", str(pkg_path))
        return result
    pkg = load_json(pkg_path)
    deps = pkg.get("dependencies") or {}

    checks = [
        ("dompurify", "dompurify", ["purify.min.js"], "sha256"),
        ("easymde", "easymde", ["easymde.min.js", "easymde.min.css"], None),
        ("codemirror", "codemirror", ["codemirror.js", "show-hint.js", "show-hint.css"], None),
        ("lucide", "lucide", ["lucide.min.js"], "sha256"),
    ]
    for dep_name, folder, files, primary_sha_key in checks:
        want = deps.get(dep_name)
        if not want:
            result.fail("missing_dep", f"frontend-vendor missing {dep_name}", str(pkg_path))
            continue
        version_path = root / "frontend" / "vendor" / folder / "VERSION"
        if not version_path.is_file():
            result.fail("missing_version", f"{folder}/VERSION missing", str(version_path))
            continue
        text = version_path.read_text(encoding="utf-8")
        if "fetched:" in text.lower():
            result.fail(
                "nondeterministic_fetched",
                f"{folder}/VERSION must not contain fetched:",
                str(version_path),
            )
        meta = parse_version_file(text)
        ver = meta.get("version") or ""
        if ver != want:
            result.fail(
                "vendor_version_mismatch",
                f"{folder} VERSION={ver} package.json={want}",
                str(version_path),
            )
        for filename in files:
            path = root / "frontend" / "vendor" / folder / filename
            if not path.is_file():
                result.fail("missing_asset", f"missing {folder}/{filename}", str(path))
                continue
            actual = sha256_file(path)
            # Prefer specific keys when present.
            recorded = None
            stem = filename.replace(".", "-")
            for key in (
                f"sha256-{filename}",
                f"sha256-{stem}",
                "sha256-js" if filename.endswith(".js") and "show-hint" not in filename else None,
                "sha256-css" if filename.endswith(".css") and "show-hint" not in filename else None,
                "sha256-lib" if filename == "codemirror.js" else None,
                "sha256-show-hint-js" if filename == "show-hint.js" else None,
                "sha256-show-hint-css" if filename == "show-hint.css" else None,
                "sha256" if primary_sha_key == "sha256" and filename.endswith(".js") else None,
            ):
                if key and key in meta:
                    recorded = meta[key]
                    break
            if recorded and recorded != actual:
                result.fail(
                    "vendor_sha_mismatch",
                    f"{folder}/{filename} sha256 {actual} != VERSION {recorded}",
                    str(path),
                )
            elif not recorded and primary_sha_key:
                # Require at least one hash for primary asset.
                if filename.endswith(".min.js") or filename == "purify.min.js":
                    result.fail(
                        "vendor_missing_sha",
                        f"{folder}/VERSION missing sha256 for {filename}",
                        str(version_path),
                    )
    return result


def validate_inter_vendor(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    version_path = root / "frontend" / "vendor" / "inter" / "VERSION"
    woff = root / "frontend" / "vendor" / "inter" / "InterVariable.woff2"
    if not version_path.is_file():
        result.fail("missing_version", "inter VERSION missing", str(version_path))
        return result
    text = version_path.read_text(encoding="utf-8")
    if "fetched:" in text.lower():
        result.fail("nondeterministic_fetched", "inter VERSION must not contain fetched:", str(version_path))
    meta = parse_version_file(text)
    if not meta.get("version"):
        result.fail("inter_missing_version", "inter VERSION missing version", str(version_path))
    if not woff.is_file():
        result.fail("missing_asset", "InterVariable.woff2 missing", str(woff))
        return result
    recorded = meta.get("sha256")
    actual = sha256_file(woff)
    if recorded and recorded != actual:
        result.fail("inter_sha_mismatch", f"woff2 sha256 {actual} != VERSION {recorded}", str(woff))
    elif not recorded:
        result.fail("inter_missing_sha", "inter VERSION missing sha256", str(version_path))
    return result


def validate_idb_vendor(repo_root: Path | None = None) -> GateResult:
    """Raw-vendor pin for jakearchibald/idb (disposable offline-store plumbing)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    version_path = root / "frontend" / "vendor" / "idb" / "VERSION"
    js_path = root / "frontend" / "vendor" / "idb" / "idb.min.js"
    if not version_path.is_file():
        result.fail("missing_version", "idb VERSION missing", str(version_path))
        return result
    text = version_path.read_text(encoding="utf-8")
    if "fetched:" in text.lower():
        result.fail("nondeterministic_fetched", "idb VERSION must not contain fetched:", str(version_path))
    meta = parse_version_file(text)
    if not meta.get("version"):
        result.fail("idb_missing_version", "idb VERSION missing version", str(version_path))
    if meta.get("npm") and meta["npm"] != f"idb@{meta.get('version')}":
        # Soft consistency: npm: idb@X should match the bare version line.
        if not str(meta.get("npm", "")).endswith("@" + str(meta.get("version", ""))):
            result.fail(
                "idb_version_mismatch",
                f"idb VERSION npm={meta.get('npm')} version={meta.get('version')}",
                str(version_path),
            )
    if not js_path.is_file():
        result.fail("missing_asset", "idb.min.js missing", str(js_path))
        return result
    recorded = meta.get("sha256")
    actual = sha256_file(js_path)
    if recorded and recorded != actual:
        result.fail("idb_sha_mismatch", f"idb.min.js sha256 {actual} != VERSION {recorded}", str(js_path))
    elif not recorded:
        result.fail("idb_missing_sha", "idb VERSION missing sha256", str(version_path))
    for marker in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com"):
        if marker in text.lower():
            result.fail("cdn_in_version", f"idb VERSION contains {marker}", str(version_path))
    return result


# ---------------------------------------------------------------------------
# DEPENDENCY-MANIFEST + SW revision
# ---------------------------------------------------------------------------


def build_dependency_manifest(repo_root: Path | None = None) -> dict[str, Any]:
    """Deterministic production-vendor manifest (no timestamps)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    entries: list[dict[str, Any]] = []

    def add(name: str, version: str, files: list[str], source_category: str) -> None:
        runtime = []
        for rel in files:
            path = root / "frontend" / "vendor" / rel
            runtime.append(
                {
                    "path": f"/vendor/{rel.replace(os.sep, '/')}",
                    "sha256": sha256_file(path) if path.is_file() else None,
                }
            )
        entries.append(
            {
                "name": name,
                "version": version,
                "source_category": source_category,
                "runtime_files": runtime,
            }
        )

    # Derive versions from authoritative sources where possible.
    fv = load_json(root / "tools" / "frontend-vendor" / "package.json")["dependencies"]
    add("dompurify", fv["dompurify"], ["dompurify/purify.min.js"], "npm:tools/frontend-vendor")
    add(
        "easymde",
        fv["easymde"],
        ["easymde/easymde.min.js", "easymde/easymde.min.css"],
        "npm:tools/frontend-vendor",
    )
    add(
        "codemirror",
        fv["codemirror"],
        [
            "codemirror/codemirror.js",
            "codemirror/show-hint.js",
            "codemirror/show-hint.css",
        ],
        "npm:tools/frontend-vendor",
    )
    add("lucide", fv["lucide"], ["lucide/lucide.min.js"], "npm:tools/frontend-vendor")

    cy = load_json(root / "tools" / "research-graph" / "package.json")["dependencies"]["cytoscape"]
    add("cytoscape", cy, ["cytoscape/cytoscape.min.js"], "npm:tools/research-graph")

    inter_meta = parse_version_file(
        (root / "frontend" / "vendor" / "inter" / "VERSION").read_text(encoding="utf-8")
    )
    add(
        "inter",
        inter_meta.get("version", ""),
        ["inter/inter.css", "inter/InterVariable.woff2"],
        "raw-vendor",
    )

    idb_meta = parse_version_file(
        (root / "frontend" / "vendor" / "idb" / "VERSION").read_text(encoding="utf-8")
    )
    add(
        "idb",
        idb_meta.get("version", ""),
        ["idb/idb.min.js"],
        "raw-vendor",
    )

    pdf_pkg = load_json(root / "tools" / "pdf-viewer" / "package.json")
    embed = pdf_pkg["dependencies"]["@embedpdf/core"]
    add(
        "prks-pdf-viewer",
        embed,
        [
            "prks-pdf-viewer/prks-pdf-viewer.js",
            "prks-pdf-viewer/prks-pdf-viewer.css",
            "prks-pdf-viewer/pdfium.wasm",
        ],
        "npm:tools/pdf-viewer",
    )

    entries.sort(key=lambda e: e["name"])
    return {
        "schema_version": 1,
        "dependencies": entries,
    }


def dependency_manifest_revision(manifest: Mapping[str, Any] | None = None, repo_root: Path | None = None) -> str:
    """Short content revision derived from the dependency manifest."""
    if manifest is None:
        root = Path(repo_root) if repo_root is not None else REPO_ROOT
        path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
        manifest = load_json(path)
    # Canonical JSON, sorted keys — stable across platforms.
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def write_dependency_manifest(repo_root: Path | None = None) -> Path:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    manifest = build_dependency_manifest(root)
    path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_sw_dependency_revision(sw_text: str) -> str | None:
    m = _SW_REV_RE.search(sw_text)
    return m.group(1) if m else None


def apply_sw_dependency_revision(sw_text: str, revision: str) -> str:
    if not _SW_REV_ANY_RE.search(sw_text):
        raise ValueError("sw.js missing DEPENDENCY_REVISION constant")
    return _SW_REV_ANY_RE.sub(
        f"const DEPENDENCY_REVISION = '{revision}';",
        sw_text,
        count=1,
    )


def write_sw_dependency_revision(repo_root: Path | None = None) -> str:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    revision = dependency_manifest_revision(repo_root=root)
    path = root / "frontend" / "sw.js"
    text = path.read_text(encoding="utf-8")
    path.write_text(apply_sw_dependency_revision(text, revision), encoding="utf-8")
    return revision


def validate_dependency_manifest(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
    if not path.is_file():
        result.fail("missing_dependency_manifest", "DEPENDENCY-MANIFEST.json missing", str(path))
        return result
    on_disk = load_json(path)
    expected = build_dependency_manifest(root)
    if on_disk != expected:
        result.fail(
            "dependency_manifest_stale",
            "DEPENDENCY-MANIFEST.json does not match rebuild from authoritative sources",
            str(path),
        )
    # No timestamps
    blob = path.read_text(encoding="utf-8")
    if "builtAt" in blob or "fetched" in blob.lower() or "generated_at" in blob.lower():
        result.fail(
            "dependency_manifest_nondeterministic",
            "DEPENDENCY-MANIFEST must not contain timestamps",
            str(path),
        )
    return result


def validate_sw_revision(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    sw_path = root / "frontend" / "sw.js"
    text = sw_path.read_text(encoding="utf-8")
    have = read_sw_dependency_revision(text)
    want = dependency_manifest_revision(repo_root=root)
    if have is None:
        result.fail("sw_missing_revision", "sw.js missing DEPENDENCY_REVISION", str(sw_path))
    elif have != want:
        result.fail(
            "sw_revision_mismatch",
            f"sw.js DEPENDENCY_REVISION={have} expected {want}",
            str(sw_path),
        )
    if not re.search(
        r"STATIC_CACHE\s*=\s*['\"]prks-static-['\"]\s*\+\s*DEPENDENCY_REVISION",
        text,
    ):
        result.fail(
            "sw_static_cache_not_revisioned",
            "STATIC_CACHE must incorporate DEPENDENCY_REVISION",
            str(sw_path),
        )
    if not re.search(
        r"SHELL_CACHE\s*=\s*['\"]prks-shell-['\"]\s*\+\s*DEPENDENCY_REVISION",
        text,
    ):
        result.fail(
            "sw_shell_cache_not_revisioned",
            "SHELL_CACHE must incorporate DEPENDENCY_REVISION",
            str(sw_path),
        )
    if "RETIRE_PREFIXES" not in text:
        result.fail("sw_missing_retire", "sw.js missing RETIRE_PREFIXES", str(sw_path))
    return result


# ---------------------------------------------------------------------------
# Vendor asset registration + CDN forbid
# ---------------------------------------------------------------------------


def iter_production_loader_files(repo_root: Path | None = None) -> Iterable[Path]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    frontend = root / "frontend"
    yield frontend / "index.html"
    for sub in ("css", "js"):
        base = frontend / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in (".js", ".css") and path.is_file():
                yield path
    sw = frontend / "sw.js"
    if sw.is_file():
        yield sw


def collect_vendor_refs(repo_root: Path | None = None) -> set[str]:
    """Collect /vendor/... path references from production loaders + SW."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    refs: set[str] = set()
    pattern = re.compile(r"""["'`](/vendor/[^"'`\s?#]+)""")
    for path in iter_production_loader_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            refs.add(m.group(1).split("?")[0])
    # pdf-viewer-runtime default base
    runtime = root / "frontend" / "js" / "pdf-viewer-runtime.js"
    if runtime.is_file():
        text = runtime.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            refs.add(m.group(1).split("?")[0])
    return refs


def validate_vendor_registration(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    manifest_path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
    if not manifest_path.is_file():
        result.fail("missing_dependency_manifest", "DEPENDENCY-MANIFEST.json missing", str(manifest_path))
        return result
    manifest = load_json(manifest_path)
    registered: set[str] = set()
    for dep in manifest.get("dependencies") or []:
        for rf in dep.get("runtime_files") or []:
            p = rf.get("path")
            if p:
                registered.add(p)

    # Every registered asset must exist.
    for rel in REGISTERED_VENDOR_RUNTIME_FILES:
        path = root / "frontend" / "vendor" / rel
        url = "/vendor/" + rel.replace(os.sep, "/")
        if not path.is_file():
            result.fail("missing_registered_asset", f"registered asset missing: {url}", str(path))
        if url not in registered:
            result.fail(
                "unregistered_required_asset",
                f"required vendor asset not in DEPENDENCY-MANIFEST: {url}",
            )

    # Production refs under /vendor/ must be registered (ignore licenses/VERSION/metadata).
    meta_names = {
        "VERSION",
        "LICENSE",
        "BUILD-MANIFEST.json",
        "DEPENDENCY-MANIFEST.json",
        "THIRD_PARTY.md",
    }
    refs = collect_vendor_refs(root)
    for ref in sorted(refs):
        # Directory bases like /vendor/prks-pdf-viewer/ are ok if children registered.
        if ref.endswith("/"):
            continue
        name = Path(ref).name
        if name in meta_names or "/LICENSES/" in ref:
            continue
        if not ref.startswith("/vendor/"):
            continue
        # Only third-party runtime files need registration.
        rel = ref[len("/vendor/") :]
        vendor_file = root / "frontend" / "vendor" / rel
        if vendor_file.is_file() and ref not in registered:
            # Allow only if it's a known metadata file already skipped.
            result.fail(
                "unregistered_vendor_ref",
                f"production code references unregistered vendor file: {ref}",
            )

    # No orphan third-party runtime files under vendor/.
    vendor_root = root / "frontend" / "vendor"
    runtime_suffixes = (".js", ".css", ".wasm", ".woff2", ".woff", ".ttf")
    for path in vendor_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(vendor_root).as_posix()
        if path.name in meta_names or "LICENSES" in path.parts:
            continue
        url = "/vendor/" + rel
        if path.suffix.lower() in runtime_suffixes and url not in registered:
            result.fail(
                "unregistered_vendor_file",
                f"vendor runtime file not in DEPENDENCY-MANIFEST: {url}",
                str(path),
            )
    return result


def validate_no_cdn_in_loaders(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    for path in iter_production_loader_files(root):
        # Skip vendored third-party bundles themselves (may contain dead CDN template strings).
        try:
            path.relative_to(root / "frontend" / "vendor")
            continue
        except ValueError:
            pass
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in CDN_LOADER_MARKERS:
            if marker in text:
                result.fail(
                    "cdn_in_loader",
                    f"{path.relative_to(root)} contains banned CDN marker {marker}",
                    str(path),
                )
    return result


# ---------------------------------------------------------------------------
# Dockerfile (system / base image)
# ---------------------------------------------------------------------------


def dockerfile_python_base_version(text: str) -> tuple[int, int] | None:
    """Return ``(major, minor)`` from the first ``FROM python:X.Y…`` line."""
    match = _DOCKER_FROM_PYTHON_RE.search(text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def dockerfile_apt_packages(text: str) -> list[str]:
    """Direct ``apt-get install`` package names, in first-seen order."""
    collapsed = _DOCKER_LINE_CONT_RE.sub(" ", text)
    packages: list[str] = []
    seen: set[str] = set()
    for match in _DOCKER_APT_INSTALL_RE.finditer(collapsed):
        for token in match.group(1).split():
            if token.startswith("-"):
                continue
            # Drop apt version/arch suffixes: pkg=1.2.3 / pkg:amd64
            name = token.split("=", 1)[0].split(":", 1)[0].strip()
            if not name or name in seen:
                continue
            seen.add(name)
            packages.append(name)
    return packages


def derived_dockerfile_inventory_names(repo_root: Path | None = None) -> set[str]:
    """Inventory names implied by the Dockerfile (base image + apt packages)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    path = root / "Dockerfile"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    names: set[str] = set()
    if dockerfile_python_base_version(text) is not None:
        names.add("python-base-image")
    names.update(dockerfile_apt_packages(text))
    return names


def validate_dockerfile(repo_root: Path | None = None) -> GateResult:
    """Fail closed when the Dockerfile drifts from python_min_version / inventory.

    A missing Dockerfile is OK only when the inventory does not claim any
    Dockerfile-backed entries. Version checks run whenever a Dockerfile exists.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    path = root / "Dockerfile"
    inv_path = root / "dependency-inventory.json"

    if not path.is_file():
        if inv_path.is_file():
            inv = load_inventory(root)
            claimed = [
                d.get("name")
                for d in (inv.get("dependencies") or [])
                if d.get("manifest") == "Dockerfile"
                or d.get("name") == "python-base-image"
            ]
            if claimed:
                result.fail(
                    "dockerfile_missing",
                    (
                        "Dockerfile missing but dependency-inventory.json still "
                        f"lists Dockerfile-backed entries: {', '.join(sorted(map(str, claimed)))}"
                    ),
                    str(path),
                )
        return result

    text = path.read_text(encoding="utf-8")
    image_ver = dockerfile_python_base_version(text)
    if image_ver is None:
        result.fail(
            "dockerfile_python_base",
            "Dockerfile has no FROM python:X.Y image tag",
            str(path),
        )
        return result

    if not inv_path.is_file():
        return result

    need = python_min_version(root)[:2]
    if image_ver < need:
        result.fail(
            "dockerfile_python_version",
            (
                f"Dockerfile FROM python:{image_ver[0]}.{image_ver[1]} is older than "
                f"python_min_version {need[0]}.{need[1]}"
            ),
            str(path),
        )
    return result


# ---------------------------------------------------------------------------
# Inventory coverage
# ---------------------------------------------------------------------------


def derived_inventory_names(repo_root: Path | None = None) -> set[str]:
    """Names that must appear in dependency-inventory.json, from manifests.

    Sources: requirements*.txt exact pins, npm island direct dependencies
    (``@embedpdf/*`` collapsed), Dockerfile (``python-base-image`` + apt
    packages), plus structural entries (Inter, python).
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    names: set[str] = set(_INVENTORY_STRUCTURAL_NAMES)
    names.update(derived_dockerfile_inventory_names(root))

    for rel in ("requirements.txt", "requirements-dev.txt"):
        path = root / rel
        if not path.is_file():
            continue
        # Fail closed is enforced separately; still derive what we can.
        try:
            names.update(read_requirements_pins(path))
        except RequirementsPinError:
            continue

    for raw in NPM_ISLANDS:
        island_dir = root / Path(raw["dir"]).relative_to(REPO_ROOT)
        pkg_path = island_dir / "package.json"
        if not pkg_path.is_file():
            continue
        pkg = load_json(pkg_path)
        for dep_name in package_direct_pins(pkg):
            if dep_name.startswith("@embedpdf/"):
                names.add("@embedpdf/*")
            else:
                names.add(dep_name)
    return names


def validate_inventory(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)

    for rel in ("requirements.txt", "requirements-dev.txt"):
        req_result, _pins = validate_requirements_file(root / rel)
        result.extend(req_result)

    inv_path = root / "dependency-inventory.json"
    if not inv_path.is_file():
        result.fail(
            "inventory_missing_file",
            "dependency-inventory.json missing",
            str(inv_path),
        )
        return result

    result.extend(validate_dockerfile(root))

    inv = load_inventory(root)
    deps = inv.get("dependencies") or []
    declared = {d.get("name") for d in deps if d.get("name")}
    expected = derived_inventory_names(root)

    for name in sorted(expected - declared):
        result.fail(
            "inventory_missing",
            f"dependency-inventory.json missing {name} (required by authoritative manifests)",
        )
    for name in sorted(declared - expected):
        result.fail(
            "inventory_extra",
            f"dependency-inventory.json has {name} not present in authoritative manifests",
        )

    for dep in deps:
        auth = dep.get("authoritative_source")
        if not auth:
            result.fail(
                "inventory_no_source",
                f"{dep.get('name')}: missing authoritative_source",
            )
        if dep.get("exact_installed_version_check") and dep.get("scope") in (
            "runtime",
            "test",
        ):
            manifest = dep.get("manifest")
            if (
                manifest
                and not (root / manifest).is_file()
                and manifest != "dependency-inventory.json"
            ):
                result.fail(
                    "inventory_bad_manifest",
                    f"{dep.get('name')}: manifest {manifest} missing",
                )
    return result


# ---------------------------------------------------------------------------
# Aggregate modes
# ---------------------------------------------------------------------------


def run_runtime_gate(**kwargs: Any) -> GateResult:
    return validate_runtime_python(**kwargs)


def run_unit_contract_gate(**kwargs: Any) -> GateResult:
    return validate_unit_contract_python(**kwargs)


def run_test_gate(**kwargs: Any) -> GateResult:
    return validate_test_python(**kwargs)


def run_repo_gate(repo_root: Path | None = None) -> GateResult:
    """Offline repository consistency. Does not require npm packages installed."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    result.extend(validate_inventory(root))
    result.extend(validate_all_npm_islands(root))
    result.extend(validate_cytoscape_vendor(root))
    result.extend(validate_pdf_viewer_vendor(root))
    result.extend(validate_frontend_vendor_island(root))
    result.extend(validate_inter_vendor(root))
    result.extend(validate_idb_vendor(root))
    result.extend(validate_dependency_manifest(root))
    result.extend(validate_sw_revision(root))
    result.extend(validate_vendor_registration(root))
    result.extend(validate_no_cdn_in_loaders(root))
    return result


def format_gate_report(result: GateResult) -> str:
    if result.ok:
        return "Dependency gate: OK"
    lines = ["Dependency gate: FAILED"]
    for issue in result.issues:
        loc = f" ({issue.path})" if issue.path else ""
        lines.append(f"  [{issue.code}] {issue.message}{loc}")
    # Prefer the last remediation detail block if present.
    remediations = [d for d in result.details if d.startswith("PRKS dependency check failed")]
    if remediations:
        lines.append("")
        lines.append(remediations[-1])
    return "\n".join(lines)


def ensure_runtime_or_exit(repo_root: Path | None = None) -> None:
    """Call from prks_app.py before storage/DB/server startup."""
    result = run_runtime_gate(repo_root=repo_root)
    if not result.ok:
        print(format_gate_report(result), file=sys.stderr)
        raise SystemExit(1)
