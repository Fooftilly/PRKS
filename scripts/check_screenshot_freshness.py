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
    "requirements.txt",
    "scripts/capture_demo_screenshots.py",
    "scripts/check_screenshot_freshness.py",
    "scripts/seed_demo_library.py",
    "scripts/update_demo_screenshots.py",
    "tests/e2e/install_browser.py",
}
AFFECTING_PREFIXES = ("frontend/",)

# Keep in sync with scripts/capture_demo_screenshots.py scenario names.
EXPECTED_README_FILES = frozenset({"folders.png", "work.png", "people.png"})
EXPECTED_EXTRA_FILES = frozenset(
    {
        "all-folders.png",
        "tags.png",
        "search.png",
        "progress.png",
        "types.png",
        "person.png",
        "note.png",
        "group.png",
    }
)
EXPECTED_ALL_FILES = EXPECTED_README_FILES | EXPECTED_EXTRA_FILES
SCENARIO_BY_FILE = {
    "folders.png": "public-domain-folder",
    "work.png": "origin-of-species-work-pdf",
    "people.png": "people-library",
    "all-folders.png": "folder-library",
    "tags.png": "tags",
    "search.png": "search-darwin",
    "progress.png": "progress-in-progress",
    "types.png": "file-types",
    "person.png": "darwin-person",
    "note.png": "commonplace-note",
    "group.png": "nineteenth-century-group",
}

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
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Could not inspect screenshot-affecting working-tree changes."
        ) from exc
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
    # Explicit null/empty per-file revision means revisionless — do not inherit
    # the global source_commit. Fall back only when the key is absent (legacy).
    if "source_commit" in entry:
        return str(entry.get("source_commit") or "").strip()
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

    by_file = {
        str(entry.get("file") or "").strip(): entry
        for entry in screenshots
        if isinstance(entry, dict) and str(entry.get("file") or "").strip()
    }

    # PNGs that exist on disk or are expected but lack a capture revision stay
    # visible to freshness (legacy / never-regenerated extras).
    screenshot_dir = MANIFEST.parent
    on_disk = {
        path.name
        for path in screenshot_dir.glob("*.png")
        if path.is_file()
    }
    missing_expected = sorted(EXPECTED_ALL_FILES - on_disk)
    untracked = sorted(
        name
        for name in (EXPECTED_ALL_FILES | on_disk)
        if name.endswith(".png")
        and (
            name not in by_file
            or not _entry_revision(by_file.get(name) or {}, global_source)
        )
    )

    checked: list[tuple[str, str]] = []
    for name, entry in by_file.items():
        rev = _entry_revision(entry, global_source)
        if name and rev:
            checked.append((name, rev))

    stale_notes: list[str] = []
    if missing_expected:
        preview = ", ".join(missing_expected[:8])
        if len(missing_expected) > 8:
            preview += f", +{len(missing_expected) - 8} more"
        stale_notes.append(f"missing expected screenshots ({preview})")
    if untracked:
        preview = ", ".join(untracked[:8])
        if len(untracked) > 8:
            preview += f", +{len(untracked) - 8} more"
        stale_notes.append(
            f"untracked or revisionless screenshots ({preview})"
        )

    if not checked and not untracked and not missing_expected:
        if not global_source:
            _warn(
                "Existing screenshots predate the freshness manifest. Run "
                "python scripts/update_demo_screenshots.py to establish a capture revision."
            )
            return 0
        checked.append(("*", global_source))

    affecting_by_rev: dict[str, list[str]] = {}
    compare_failed = False
    for label, source in checked:
        if source not in affecting_by_rev:
            try:
                affecting_by_rev[source] = _affecting_after(source)
            except (OSError, subprocess.CalledProcessError) as exc:
                _warn(f"Could not compare screenshot capture revision {source}: {exc}")
                # Keep going so revisionless/untracked notes collected above are
                # still reported; do not treat a bad/missing SHA as "all fresh".
                compare_failed = True
                affecting_by_rev[source] = []
                continue
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
        if compare_failed:
            # Freshness is unknown; strict mode must not treat that as clean.
            return 1 if strict else 0
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
        f"after capture, some screenshots lack a capture revision, or expected "
        f"screenshot files are missing ({joined}). "
        "Regenerate with: python scripts/update_demo_screenshots.py"
    )
    # Default remains advisory (exit 0) for PR CI; opt into failing locally/CI
    # with PRKS_SCREENSHOT_FRESHNESS_STRICT=1.
    return 1 if strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
