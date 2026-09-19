#!/usr/bin/env python3
"""Install Playwright Chromium into the repository-local browser cache."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BROWSERS_DIR = REPO / ".playwright-browsers"
INSTALL_HINT = "python tests/e2e/install_browser.py"

_CHROMIUM_RELATIVE = (
    ("chrome-linux", "chrome"),
    ("chrome-linux64", "chrome"),
    ("chrome-win64", "chrome.exe"),
    ("chrome-win", "chrome.exe"),
    ("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
    ("chrome-mac-arm64", "Chromium.app", "Contents", "MacOS", "Chromium"),
    (
        "chrome-mac",
        "Google Chrome for Testing.app",
        "Contents",
        "MacOS",
        "Google Chrome for Testing",
    ),
    (
        "chrome-mac-arm64",
        "Google Chrome for Testing.app",
        "Contents",
        "MacOS",
        "Google Chrome for Testing",
    ),
)


def apply_playwright_browser_env() -> Path:
    """Point Playwright at <repo>/.playwright-browsers before import/install."""
    BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    return BROWSERS_DIR


def install_command() -> str:
    """Install Chromium with the same interpreter that will run E2E."""
    return "%s tests/e2e/install_browser.py" % sys.executable


def pinned_playwright_version() -> str:
    """Exact Playwright pin from requirements-dev.txt (via dependency_gate)."""
    from backend.dependency_gate import pinned_playwright_version as _pinned

    return _pinned(REPO)


def installed_playwright_version() -> str | None:
    """Installed Playwright distribution version, or None if the package is absent."""
    from backend.dependency_gate import installed_distribution_version

    return installed_distribution_version("playwright")


def decide_e2e_browser_setup(installed_version, pinned_version, chromium_cached):
    """Decide whether to reuse, install, or fail before any browser download."""
    if not pinned_version:
        return "fail", "requirements-dev.txt has no playwright== pin"
    if not installed_version:
        return (
            "fail",
            "Playwright is not installed. From the repository root:\n"
            "  %s -m pip install -r requirements-dev.txt\n"
            "  %s" % (sys.executable, install_command()),
        )
    if installed_version != pinned_version:
        return (
            "fail",
            "Installed Playwright %s does not match project pin %s.\n"
            "Install the project's test dependencies with the same Python interpreter:\n"
            "  %s -m pip install -r requirements-dev.txt"
            % (installed_version, pinned_version, sys.executable),
        )
    if chromium_cached:
        return "reuse", None
    return "install", None


def _current_setup_state():
    pinned = pinned_playwright_version()
    installed = installed_playwright_version()
    cached = False
    if installed == pinned:
        try:
            cached = chromium_executable(BROWSERS_DIR, playwright_chromium_revision()) is not None
        except Exception:
            cached = False
    return installed, pinned, cached


def playwright_chromium_revision() -> str:
    """Return the Chromium revision this installed Playwright package expects."""
    import playwright

    manifest = (
        Path(playwright.__file__).resolve().parent
        / "driver"
        / "package"
        / "browsers.json"
    )
    if not manifest.is_file():
        raise RuntimeError("Playwright browsers.json is missing; reinstall playwright")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for browser in data.get("browsers") or []:
        if browser.get("name") == "chromium":
            revision = str(browser.get("revision") or "").strip()
            if revision:
                return revision
    raise RuntimeError("Playwright browsers.json has no chromium revision")


def chromium_executable(browsers_dir: Path, revision: str) -> Path | None:
    root = Path(browsers_dir) / ("chromium-%s" % revision)
    if not root.is_dir():
        return None
    for rel in _CHROMIUM_RELATIVE:
        candidate = root.joinpath(*rel)
        if candidate.is_file():
            return candidate
    return None


def installed_chromium_revisions(browsers_dir: Path) -> list[str]:
    found = []
    if not Path(browsers_dir).is_dir():
        return found
    for path in sorted(Path(browsers_dir).glob("chromium-*")):
        if path.is_dir():
            found.append(path.name.split("-", 1)[-1])
    return found


def install_chromium() -> int:
    """Download this Playwright package's Chromium into .playwright-browsers/.

    Clears PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD only for this subprocess. Does not
    install into the virtualenv, OS user cache, or E2E temp storage.
    Refuses to download when the installed Playwright package does not match
    the project pin.
    """
    apply_playwright_browser_env()
    installed, pinned, _cached = _current_setup_state()
    action, err = decide_e2e_browser_setup(installed, pinned, False)
    if action == "fail":
        print(err, file=sys.stderr)
        return 1
    env = os.environ.copy()
    env.pop("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", None)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    print("PLAYWRIGHT_BROWSERS_PATH=%s" % BROWSERS_DIR, flush=True)
    print(" ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(REPO), env=env)


def ensure_chromium_installed(*, setup=None, install_fn=None, cached_after_install=None):
    """Install Chromium into the repo cache if this Playwright revision is absent.

    Compares importlib.metadata.version("playwright") to the requirements-dev.txt
    pin before any download. setup/install_fn/cached_after_install are test seams.
    """
    apply_playwright_browser_env()
    if setup is None:
        setup = _current_setup_state()
    installed, pinned, cached = setup
    action, err = decide_e2e_browser_setup(installed, pinned, cached)
    if action == "fail":
        raise RuntimeError(err)
    if action == "reuse":
        return
    print(
        "Playwright Chromium is not in .playwright-browsers/; installing...",
        flush=True,
    )
    runner = install_chromium if install_fn is None else install_fn
    rc = runner()
    if rc != 0:
        raise RuntimeError(
            "Failed to install Playwright Chromium (exit %s).\n"
            "Retry: %s" % (rc, install_command())
        )
    if cached_after_install is None:
        still = False
        try:
            still = chromium_executable(BROWSERS_DIR, playwright_chromium_revision()) is not None
        except Exception:
            still = False
    else:
        still = bool(cached_after_install)
    if not still:
        raise RuntimeError(
            "Playwright Chromium is still missing after install.\n"
            "Retry: %s" % install_command()
        )


def main() -> int:
    try:
        ensure_chromium_installed()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
