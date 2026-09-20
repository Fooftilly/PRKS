#!/usr/bin/env python3
"""Warn when committed documentation screenshots may be older than UI sources."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "screenshots" / "manifest.json"

AFFECTING_PATHS = {
    "DESIGN.md",
    "backend/db_manager.py",
    "backend/research_graph.py",
    "backend/research_network.py",
    "backend/server.py",
    "scripts/capture_demo_screenshots.py",
    "scripts/seed_demo_library.py",
}
AFFECTING_PREFIXES = ("frontend/",)


def _warn(message: str) -> None:
    if "GITHUB_ACTIONS" in __import__("os").environ:
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
    return path in AFFECTING_PATHS or path.startswith(AFFECTING_PREFIXES)


def main() -> int:
    if not MANIFEST.is_file():
        _warn("docs/screenshots/manifest.json is missing; screenshot freshness is unknown.")
        return 0

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _warn(f"Screenshot manifest is unreadable: {exc}")
        return 0

    source = str(manifest.get("source_commit") or "").strip()
    if not source:
        _warn(
            "Existing screenshots predate the freshness manifest. Run "
            "python scripts/update_demo_screenshots.py to establish a capture revision."
        )
        return 0

    try:
        _git("cat-file", "-e", f"{source}^{{commit}}")
        changed = _git("diff", "--name-only", f"{source}..HEAD").splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        _warn(f"Could not compare screenshot capture revision {source}: {exc}")
        return 0

    affecting = sorted(path for path in changed if _affects_screenshots(path))
    if not affecting:
        print(f"Screenshots are not stale relative to tracked UI sources ({source[:12]}).")
        return 0

    preview = ", ".join(affecting[:8])
    if len(affecting) > 8:
        preview += f", +{len(affecting) - 8} more"
    _warn(
        "Documentation screenshots may be stale: screenshot-affecting files changed "
        f"after capture revision {source[:12]} ({preview}). "
        "Regenerate with: python scripts/update_demo_screenshots.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
