#!/usr/bin/env python3
"""Capture PRKS documentation screenshots from a running --testing server."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "screenshots"
MANIFEST = OUT_DIR / "manifest.json"
VIEWPORT = {"width": 1440, "height": 900}
THEME = "light"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.e2e.install_browser import (  # noqa: E402
    apply_playwright_browser_env,
    ensure_chromium_installed,
)


def _get(base: str, path: str):
    req = urllib.request.Request(base.rstrip("/") + path)
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode())


def _folder_by_title(folders, title: str):
    for f in folders or []:
        if (f.get("title") or "") == title:
            return f
        found = _folder_by_title(f.get("children") or [], title)
        if found:
            return found
    return None


def _work_by_title(works, title: str):
    for w in works or []:
        if (w.get("title") or "") == title:
            return w
    return None


def _by_last_name(persons, last: str):
    for p in persons or []:
        if (p.get("last_name") or p.get("first_name") or "") == last:
            return p
    return None


def _group_by_name(groups, name: str):
    for g in groups or []:
        if (g.get("name") or "") == name:
            return g
    return None


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_manifest(set_name: str, captured: list[dict]) -> None:
    payload = {
        "schema_version": 1,
        "source_commit": _git_head(),
        "capture_set": set_name,
        "viewport": VIEWPORT,
        "device_scale_factor": 1,
        "theme": THEME,
        "generator": "scripts/capture_demo_screenshots.py",
        "seed": "scripts/seed_demo_library.py",
        "screenshots": captured,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("wrote", MANIFEST)


def capture(base_url: str, set_name: str = "all") -> list[dict]:
    """Capture a named screenshot set and update its reproducibility manifest."""
    base = base_url.rstrip("/")

    folders = _get(base, "/api/folders")
    works = _get(base, "/api/works")
    persons = _get(base, "/api/persons")
    groups = _get(base, "/api/person-groups")
    seminar = _folder_by_title(folders, "Public domain library")
    work = _work_by_title(works, "On the Origin of Species")
    note = _work_by_title(works, "Commonplace: Darwin on variation")
    darwin = _by_last_name(persons, "Darwin")
    group = _group_by_name(groups, "Nineteenth century")
    if not seminar or not work:
        raise RuntimeError(
            "Demo library missing. Run scripts/seed_demo_library.py first."
        )

    folder_hash = f"#/folders/{seminar['id']}"
    work_hash = f"#/works/{work['id']}"
    people_hash = "#/people"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Match E2E: exact Playwright pin + repository-local Chromium cache.
    apply_playwright_browser_env()
    ensure_chromium_installed()
    from playwright.sync_api import sync_playwright

    captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
        )
        context.add_init_script(
            f"localStorage.setItem('prks-theme', {THEME!r});"
        )
        page = context.new_page()

        def shot(
            hash_path: str,
            dest: Path,
            wait_selector: str,
            *,
            scenario: str,
            wait_pdf: bool = False,
        ):
            page.goto(base + "/" + hash_path, wait_until="domcontentloaded")
            page.wait_for_selector(wait_selector, timeout=20000)
            page.evaluate(
                """() => {
                  document.querySelectorAll('img[data-prks-thumb-src]').forEach((img) => {
                    const u = img.getAttribute('data-prks-thumb-src');
                    if (u) img.src = u;
                  });
                }"""
            )
            if wait_pdf:
                page.wait_for_selector(".prks-pdf-viewer", timeout=20000)
                page.wait_for_timeout(6000)
            else:
                page.wait_for_timeout(2500)
            page.screenshot(path=str(dest), full_page=False)
            captured.append({"file": dest.name, "scenario": scenario})
            print("wrote", dest)

        if set_name in ("readme", "all"):
            shot(
                folder_hash,
                OUT_DIR / "folders.png",
                ".project-card--work-card",
                scenario="public-domain-folder",
            )
            shot(
                work_hash,
                OUT_DIR / "work.png",
                ".document-view",
                scenario="origin-of-species-work-pdf",
                wait_pdf=True,
            )
            shot(
                people_hash,
                OUT_DIR / "people.png",
                ".prks-people-list__row",
                scenario="people-library",
            )

        if set_name in ("extra", "all"):
            shot(
                "#/folders",
                OUT_DIR / "all-folders.png",
                ".prks-folder-library",
                scenario="folder-library",
            )
            shot(
                "#/tags",
                OUT_DIR / "tags.png",
                ".tag--page",
                scenario="tags",
            )
            shot(
                "#/search?q=Darwin",
                OUT_DIR / "search.png",
                ".page-header--search",
                scenario="search-darwin",
            )
            shot(
                "#/progress?status=In%20Progress",
                OUT_DIR / "progress.png",
                ".project-card--work-card",
                scenario="progress-in-progress",
            )
            shot(
                "#/types",
                OUT_DIR / "types.png",
                ".types-page",
                scenario="file-types",
            )
            if darwin:
                shot(
                    f"#/people/{darwin['id']}",
                    OUT_DIR / "person.png",
                    ".document-view--person",
                    scenario="darwin-person",
                )
            if note:
                shot(
                    f"#/works/{note['id']}",
                    OUT_DIR / "note.png",
                    ".document-view",
                    scenario="commonplace-note",
                    wait_pdf=True,
                )
            if group:
                shot(
                    f"#/people/groups/{group['id']}",
                    OUT_DIR / "group.png",
                    ".document-view--group-detail",
                    scenario="nineteenth-century-group",
                )

        browser.close()

    _write_manifest(set_name, captured)
    return captured


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8070")
    parser.add_argument("--set", choices=("readme", "extra", "all"), default="all")
    args = parser.parse_args()
    try:
        capture(args.base_url, args.set)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
