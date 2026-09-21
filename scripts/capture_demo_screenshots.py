#!/usr/bin/env python3
"""Capture PRKS documentation screenshots from a running --testing server."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse

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

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_screenshot_freshness import require_clean_capture_sources  # noqa: E402


def _assert_loopback_base(base_url: str) -> str:
    """Refuse non-loopback bases so CLI/URL input cannot be used for SSRF."""
    parsed = urlparse((base_url or "").strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "http"
        or host not in ("127.0.0.1", "localhost")
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError(
            "Screenshot capture only accepts http://127.0.0.1 (or localhost) "
            "bases with no path/query; refusing remote or opaque URLs."
        )
    port = parsed.port
    # PRKS `--testing` serves plain HTTP on loopback only; HTTPS is not configured.
    netloc = f"{host}:{port}" if port is not None else host
    origin = urlunparse(("http", netloc, "", "", "", ""))  # NOSONAR python:S5332
    return origin.rstrip("/")


def _get(base: str, path: str):
    if not path.startswith("/"):
        raise RuntimeError("Screenshot API paths must be absolute on the loopback base.")
    req = urllib.request.Request(_assert_loopback_base(base) + path)
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


def _load_manifest() -> dict:
    if not MANIFEST.is_file():
        return {}
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _existing_screenshot_map(manifest: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in manifest.get("screenshots") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("file") or "").strip()
        if name:
            out[name] = dict(row)
    return out


def _write_manifest(set_name: str, captured: list[dict], head: str | None) -> None:
    """Merge captured entries into the manifest without blessing untouched PNGs.

    Partial sets keep prior entries (and their per-file source_commit). The
    directory-wide source_commit advances only after an ``all`` capture.
    """
    previous = _load_manifest()
    by_file = _existing_screenshot_map(previous)
    for entry in captured:
        row = {
            "file": entry["file"],
            "scenario": entry["scenario"],
        }
        if head:
            row["source_commit"] = head
        by_file[entry["file"]] = row

    # Prefer a stable, documented order: prior order first, then new files.
    ordered: list[dict] = []
    seen: set[str] = set()
    for row in previous.get("screenshots") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("file") or "").strip()
        if name in by_file and name not in seen:
            ordered.append(by_file[name])
            seen.add(name)
    for name, row in by_file.items():
        if name not in seen:
            ordered.append(row)
            seen.add(name)

    if set_name == "all" and head:
        source_commit = head
    else:
        # Do not advance the global baseline after a partial run.
        source_commit = previous.get("source_commit")
        if source_commit is not None:
            source_commit = str(source_commit).strip() or None

    payload = {
        "schema_version": 1,
        "source_commit": source_commit,
        "capture_set": set_name,
        "viewport": VIEWPORT,
        "device_scale_factor": 1,
        "theme": THEME,
        "generator": "scripts/capture_demo_screenshots.py",
        "seed": "scripts/seed_demo_library.py",
        "screenshots": ordered,
    }
    if set_name != "all":
        payload["note"] = (
            "Partial capture: only listed files with a matching source_commit "
            "were regenerated in this run; other entries keep prior provenance."
        )
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("wrote", MANIFEST)


def capture(base_url: str, set_name: str = "all") -> list[dict]:
    """Capture a named screenshot set and update its reproducibility manifest."""
    require_clean_capture_sources()
    head = _git_head()
    if not head:
        raise RuntimeError("Could not resolve git HEAD for screenshot provenance.")

    base = _assert_loopback_base(base_url)

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
    stage_dir = Path(tempfile.mkdtemp(prefix="prks-shot-stage-", dir=str(OUT_DIR.parent)))

    try:
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
                dest_name: str,
                wait_selector: str,
                *,
                scenario: str,
                wait_pdf: bool = False,
            ):
                dest = stage_dir / dest_name
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
                captured.append({"file": dest_name, "scenario": scenario})
                print("staged", dest_name)

            if set_name in ("readme", "all"):
                shot(
                    folder_hash,
                    "folders.png",
                    ".project-card--work-card",
                    scenario="public-domain-folder",
                )
                shot(
                    work_hash,
                    "work.png",
                    ".document-view",
                    scenario="origin-of-species-work-pdf",
                    wait_pdf=True,
                )
                shot(
                    people_hash,
                    "people.png",
                    ".prks-people-list__row",
                    scenario="people-library",
                )

            if set_name in ("extra", "all"):
                shot(
                    "#/folders",
                    "all-folders.png",
                    ".prks-folder-library",
                    scenario="folder-library",
                )
                shot(
                    "#/tags",
                    "tags.png",
                    ".tag--page",
                    scenario="tags",
                )
                shot(
                    "#/search?q=Darwin",
                    "search.png",
                    ".page-header--search",
                    scenario="search-darwin",
                )
                shot(
                    "#/progress?status=In%20Progress",
                    "progress.png",
                    ".project-card--work-card",
                    scenario="progress-in-progress",
                )
                shot(
                    "#/types",
                    "types.png",
                    ".types-page",
                    scenario="file-types",
                )
                if darwin:
                    shot(
                        f"#/people/{darwin['id']}",
                        "person.png",
                        ".document-view--person",
                        scenario="darwin-person",
                    )
                if note:
                    shot(
                        f"#/works/{note['id']}",
                        "note.png",
                        ".document-view",
                        scenario="commonplace-note",
                        wait_pdf=True,
                    )
                if group:
                    shot(
                        f"#/people/groups/{group['id']}",
                        "group.png",
                        ".document-view--group-detail",
                        scenario="nineteenth-century-group",
                    )

            browser.close()

        if not captured:
            raise RuntimeError(f"No screenshots were captured for set {set_name!r}.")

        # Promote the complete staged set only after every capture succeeded.
        for entry in captured:
            src = stage_dir / entry["file"]
            if not src.is_file():
                raise RuntimeError(f"Staged screenshot missing: {entry['file']}")
            os.replace(src, OUT_DIR / entry["file"])
            print("wrote", OUT_DIR / entry["file"])

        _write_manifest(set_name, captured, head)
        return captured
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


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
