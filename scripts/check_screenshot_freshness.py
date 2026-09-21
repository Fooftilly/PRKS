#!/usr/bin/env python3
"""Warn when committed documentation screenshots may be older than UI sources."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "screenshots" / "manifest.json"

# Keep in sync with .github/workflows/screenshot-freshness.yml path filters
# (tests/test_screenshot_freshness.py asserts parity).
AFFECTING_PATHS = {
    "DESIGN.md",
    "backend/db_manager.py",
    "backend/research_graph.py",
    "backend/research_network.py",
    "backend/server.py",
    "backend/services/work_pdf_replace.py",
    "requirements-dev.txt",
    "scripts/capture_demo_screenshots.py",
    "scripts/check_screenshot_freshness.py",
    "scripts/seed_demo_library.py",
    "scripts/update_demo_screenshots.py",
    "tests/e2e/install_browser.py",
}
AFFECTING_PREFIXES = ("frontend/",)

# Generated capture outputs may be dirty while regenerating; everything else that
# can change pixels must be committed so HEAD matches the rendered tree.
CAPTURE_OUTPUT_PREFIXES = ("docs/screenshots/",)


def _warn(message: str) -> None:
    if "GITHUB_ACTIONS" in os.environ:
        print(f"::warning::{message}")
    else:
        print(f"WARNING: {message}")


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def _affects_screenshots(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized in AFFECTING_PATHS or normalized.startswith(AFFECTING_PREFIXES)


def _is_capture_output(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in CAPTURE_OUTPUT_PREFIXES)


def dirty_screenshot_sources() -> list[str]:
    """Return screenshot-affecting paths with uncommitted changes (excl. outputs)."""
    try:
        status = _git("status", "--porcelain", "-uall")
    except (OSError, subprocess.CalledProcessError):
        return []
    dirty: list[str] = []
    for line in status.splitlines():
        if not line or len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        if not path or _is_capture_output(path):
            continue
        if _affects_screenshots(path):
            dirty.append(path)
    return sorted(set(dirty))


def require_clean_capture_sources() -> None:
    """Refuse capture when HEAD would mis-label uncommitted UI/source edits."""
    dirty = dirty_screenshot_sources()
    if not dirty:
        return
    preview = ", ".join(dirty[:8])
    if len(dirty) > 8:
        preview += f", +{len(dirty) - 8} more"
    raise RuntimeError(
        "Refuse screenshot capture while screenshot-affecting sources have "
        f"uncommitted changes ({preview}). Commit those edits first so the "
        "manifest source revision matches the rendered tree. Generated files "
        "under docs/screenshots/ may remain dirty."
    )


def _entry_revision(entry: dict, global_source: str) -> str:
    per_file = str(entry.get("source_commit") or "").strip()
    if per_file:
        return per_file
    return global_source


def _affecting_after(source: str) -> list[str]:
    _git("cat-file", "-e", f"{source}^{{commit}}")
    changed = _git("diff", "--name-only", f"{source}..HEAD").splitlines()
    return sorted(path for path in changed if _affects_screenshots(path))


def main() -> int:
    if not MANIFEST.is_file():
        _warn("docs/screenshots/manifest.json is missing; screenshot freshness is unknown.")
        return 0

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _warn(f"Screenshot manifest is unreadable: {exc}")
        return 0

    global_source = str(manifest.get("source_commit") or "").strip()
    screenshots = manifest.get("screenshots")
    if not isinstance(screenshots, list):
        screenshots = []

    checked: list[tuple[str, str]] = []
    for entry in screenshots:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("file") or "").strip()
        rev = _entry_revision(entry, global_source)
        if name and rev:
            checked.append((name, rev))

    if not checked:
        if not global_source:
            _warn(
                "Existing screenshots predate the freshness manifest. Run "
                "python scripts/update_demo_screenshots.py to establish a capture revision."
            )
            return 0
        checked.append(("*", global_source))

    stale_notes: list[str] = []
    affecting_by_rev: dict[str, list[str]] = {}
    for label, source in checked:
        if source not in affecting_by_rev:
            try:
                affecting_by_rev[source] = _affecting_after(source)
            except (OSError, subprocess.CalledProcessError) as exc:
                _warn(f"Could not compare screenshot capture revision {source}: {exc}")
                return 0
        affecting = affecting_by_rev[source]
        if not affecting:
            continue
        preview = ", ".join(affecting[:8])
        if len(affecting) > 8:
            preview += f", +{len(affecting) - 8} more"
        if label == "*":
            stale_notes.append(f"capture revision {source[:12]} ({preview})")
        else:
            stale_notes.append(f"{label} @ {source[:12]} ({preview})")

    strict = os.environ.get("PRKS_SCREENSHOT_FRESHNESS_STRICT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if not stale_notes:
        revs = sorted({rev for _, rev in checked})
        shown = ", ".join(r[:12] for r in revs[:3])
        if len(revs) > 3:
            shown += f", +{len(revs) - 3} more"
        print(f"Screenshots are not stale relative to tracked UI sources ({shown}).")
        return 0

    joined = "; ".join(stale_notes[:6])
    if len(stale_notes) > 6:
        joined += f"; +{len(stale_notes) - 6} more"
    _warn(
        "Documentation screenshots may be stale: screenshot-affecting files changed "
        f"after capture ({joined}). "
        "Regenerate with: python scripts/update_demo_screenshots.py"
    )
    # Default remains advisory (exit 0) for PR CI; opt into failing locally/CI
    # with PRKS_SCREENSHOT_FRESHNESS_STRICT=1.
    return 1 if strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
