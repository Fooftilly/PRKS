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
    """
    apply_playwright_browser_env()
    try:
        import playwright  # noqa: F401
    except ImportError:
        print(
            "Playwright is not installed. From the repository root:\n"
            "  python -m pip install -r requirements-dev.txt\n"
            "  %s" % install_command(),
            file=sys.stderr,
        )
        return 1
    env = os.environ.copy()
    env.pop("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", None)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    print("PLAYWRIGHT_BROWSERS_PATH=%s" % BROWSERS_DIR, flush=True)
    print(" ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(REPO), env=env)


def ensure_chromium_installed() -> None:
    """Install Chromium into the repo cache if this Playwright revision is absent."""
    apply_playwright_browser_env()
    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. From the repository root:\n"
            "  python -m pip install -r requirements-dev.txt\n"
            "  %s" % install_command()
        ) from exc
    revision = playwright_chromium_revision()
    if chromium_executable(BROWSERS_DIR, revision) is not None:
        return
    print(
        "Playwright Chromium revision %s is not in .playwright-browsers/; installing..."
        % revision,
        flush=True,
    )
    rc = install_chromium()
    if rc != 0:
        raise RuntimeError(
            "Failed to install Playwright Chromium (exit %s).\n"
            "Retry: %s" % (rc, install_command())
        )
    if chromium_executable(BROWSERS_DIR, revision) is None:
        raise RuntimeError(
            "Playwright Chromium revision %s is still missing after install.\n"
            "Retry: %s" % (revision, install_command())
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
