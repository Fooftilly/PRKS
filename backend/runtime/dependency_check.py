"""The runtime dependency check: does this Python environment satisfy the
pins PRKS needs to start, run its unit suite, or run its browser tests?

Offline: never contacts PyPI, npm, or GitHub. ``prks_app.py`` calls
:func:`ensure_runtime_or_exit` before storage, database or server startup.
The repository consistency gate (vendor hashes, manifests, locks, the
service-worker revision) is tooling, in ``scripts/dependency_gate.py``.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import sysconfig
from dataclasses import dataclass, field
import importlib.util
from importlib.metadata import PackageNotFoundError, version as dist_version
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

# Exact pin only: name==version with optional trailing comment. Fail closed otherwise.
_REQ_PIN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*==\s*([^#\s]+)\s*(?:#.*)?$"
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
    raw = inv.get("python_min_version") or [3, 14]
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
    # Installer availability for remediation hints. Many venvs (``uv venv``,
    # ``python -m venv --without-pip``) have no pip, and distributions such as
    # Arch Linux no longer expect pip to be used at all.
    has_pip: bool
    has_ensurepip: bool
    uv_available: bool


def module_available(name: str) -> bool:
    """True when *name* can be imported by the running interpreter."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


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
    has_pip: bool | None = None,
    has_ensurepip: bool | None = None,
    uv_available: bool | None = None,
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
        has_pip=module_available("pip") if has_pip is None else bool(has_pip),
        has_ensurepip=(
            module_available("ensurepip")
            if has_ensurepip is None
            else bool(has_ensurepip)
        ),
        uv_available=(
            shutil.which("uv") is not None
            if uv_available is None
            else bool(uv_available)
        ),
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


def format_uv_install_command(
    executable: str,
    requirements_file: str,
    *,
    is_windows: bool,
) -> str:
    exe = quote_shell_path(executable, is_windows=is_windows)
    req = quote_shell_path(requirements_file, is_windows=is_windows)
    return f"uv pip install --python {exe} -r {req}"


def format_ensurepip_command(executable: str, *, is_windows: bool) -> str:
    exe = quote_shell_path(executable, is_windows=is_windows)
    return f"{exe} -m ensurepip --upgrade"


def interpreter_install_lines(
    ctx: PlatformContext, requirements_file: str
) -> tuple[str, list[str]]:
    """Intro line and commands that install *requirements_file* into ``ctx.executable``.

    Picks an installer that exists: pip in the interpreter, else ``uv`` on
    PATH, else bootstrapping pip with ``ensurepip``.
    """
    exe, win = ctx.executable, ctx.is_windows
    pip_cmd = format_pip_install_command(exe, requirements_file, is_windows=win)
    if ctx.has_pip:
        return "Install the pinned dependencies with:", [pip_cmd]
    if ctx.uv_available:
        return (
            "This Python has no pip. Install the pinned dependencies with uv:",
            [format_uv_install_command(exe, requirements_file, is_windows=win)],
        )
    if ctx.has_ensurepip:
        return (
            "This Python has no pip. Bootstrap pip, then install the pinned dependencies:",
            [format_ensurepip_command(exe, is_windows=win), pip_cmd],
        )
    return (
        "This Python has neither pip nor ensurepip. Install uv"
        " (https://docs.astral.sh/uv/, also packaged by most distributions), then run:",
        [format_uv_install_command(exe, requirements_file, is_windows=win)],
    )


def default_venv_python_cmd(*, is_windows: bool) -> str:
    """Platform-appropriate interpreter name for remediation examples.

    Windows installs typically expose ``py`` / ``python``, not ``python3``.
    """
    return "py -3" if is_windows else "python3"


def format_venv_create_commands(
    *,
    is_windows: bool,
    python_cmd: str | None = None,
    use_uv: bool = False,
    python_min: Sequence[int] = (3, 14),
) -> list[str]:
    venv_python = r".venv\Scripts\python.exe" if is_windows else "./.venv/bin/python"
    if use_uv:
        request = ">=" + ".".join(str(x) for x in python_min)
        return [
            # Double quotes keep ">" from being a redirect in sh, cmd and PowerShell.
            f'uv venv --python "{request}" .venv',
            f"uv pip install --python {venv_python} -r requirements.txt",
        ]
    cmd = (
        python_cmd
        if python_cmd is not None
        else default_venv_python_cmd(is_windows=is_windows)
    )
    return [
        f"{cmd} -m venv .venv",
        f"{venv_python} -m pip install -r requirements.txt",
    ]


def _venv_create_lines(ctx: PlatformContext) -> list[str]:
    """Commands for a new project venv, using uv when ``python -m venv`` cannot add pip.

    Without ensurepip, ``python -m venv`` fails (or yields a venv with no pip),
    so the uv commands are the only ones printed, after a hint to install uv
    when it is not on PATH.
    """
    use_uv = not ctx.has_ensurepip
    try:
        python_min: Sequence[int] = python_min_version()
    except (OSError, ValueError):
        python_min = (3, 14)
    lines: list[str] = []
    if use_uv and not ctx.uv_available:
        lines.append(
            "  # This Python has no ensurepip, so `-m venv` cannot add pip."
            " Install uv (https://docs.astral.sh/uv/), then:"
        )
    lines.extend(
        f"  {cmd}"
        for cmd in format_venv_create_commands(
            is_windows=ctx.is_windows, use_uv=use_uv, python_min=python_min
        )
    )
    return lines


def remediation_message(
    *,
    missing: Sequence[str] | None = None,
    mismatched: Sequence[tuple[str, str, str]] | None = None,
    python_too_old: tuple[tuple[int, ...], tuple[int, ...]] | None = None,
    ctx: PlatformContext | None = None,
    requirements_file: str = "requirements.txt",
) -> str:
    """Build an actionable, platform-aware remediation message.

    Never recommends sudo pip or --break-system-packages, and never
    recommends pip when the interpreter has none.
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

    if python_too_old is not None:
        # Reinstalling packages cannot change this interpreter's version, so a
        # fresh virtual environment with a supported Python is the only fix.
        lines.extend(
            [
                "Reinstalling packages cannot change this interpreter's version.",
                "Install uv (https://docs.astral.sh/uv/) outside the old .venv if it"
                " is not on PATH; uv can download a supported Python. Then remove"
                " the old .venv and run:",
            ]
        )
        lines.extend(
            f"  {cmd}"
            for cmd in format_venv_create_commands(
                is_windows=ctx.is_windows, use_uv=True, python_min=python_too_old[1]
            )
        )
        venv_python = r".venv\Scripts\python.exe" if ctx.is_windows else "./.venv/bin/python"
        lines.append(
            "For the test suite, also run: "
            f"uv pip install --python {venv_python} -r requirements-dev.txt"
        )
        if ctx.is_windows:
            lines.append(r"Then run PRKS with: .venv\Scripts\python.exe prks_app.py")
        else:
            lines.append("Then run PRKS with: ./.venv/bin/python prks_app.py")
        return "\n".join(lines)

    if ctx.in_venv:
        intro, cmds = interpreter_install_lines(ctx, requirements_file)
        lines.append("You are in a virtual environment. " + intro)
        lines.extend(f"  {cmd}" for cmd in cmds)
        return "\n".join(lines)

    if ctx.externally_managed:
        lines.extend(
            [
                "This Python is marked EXTERNALLY-MANAGED (PEP 668).",
                "Do not use sudo pip or pip --break-system-packages.",
                "Create a project-local virtual environment and install into it:",
            ]
        )
        lines.extend(_venv_create_lines(ctx))
        if ctx.is_windows:
            lines.append(r"Then run PRKS with: .venv\Scripts\python.exe prks_app.py")
        else:
            lines.append("Then run PRKS with: ./.venv/bin/python prks_app.py")
        return "\n".join(lines)

    # Generic host Python (not venv, not PEP 668, not Docker).
    lines.append("Install into a project-local virtual environment (recommended):")
    lines.extend(_venv_create_lines(ctx))
    intro, cmds = interpreter_install_lines(ctx, requirements_file)
    lines.extend(["", "Or, if this interpreter is already dedicated to PRKS:"])
    if not ctx.has_pip:
        lines.append("  # " + intro)
    lines.extend(f"  {cmd}" for cmd in cmds)
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
    """Runtime pins plus openapi-core and Hypothesis (no Playwright / browser deps).

    Unit discovery imports ``openapi_core`` and ``hypothesis`` from tests;
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
        pins = read_requirements_pins(dev_path)
    except RequirementsPinError as exc:
        result.fail("non_exact_requirement", str(exc), str(dev_path))
        return result
    required = ("openapi-core", "hypothesis")
    missing_pins = [name for name in required if not pins.get(name)]
    if missing_pins:
        for name in missing_pins:
            result.fail("missing_unit_pin", f"requirements-dev.txt has no {name}== pin", str(dev_path))
        return result
    extra = validate_installed_pins(
        {name: pins[name] for name in required},
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
# Aggregate modes
# ---------------------------------------------------------------------------


def run_runtime_gate(**kwargs: Any) -> GateResult:
    return validate_runtime_python(**kwargs)


def run_unit_contract_gate(**kwargs: Any) -> GateResult:
    return validate_unit_contract_python(**kwargs)


def run_test_gate(**kwargs: Any) -> GateResult:
    return validate_test_python(**kwargs)



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
