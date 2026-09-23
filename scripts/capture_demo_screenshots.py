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
PNG_OPTIMIZE_MODE = "lossless"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.e2e.install_browser import (  # noqa: E402
    apply_playwright_browser_env,
    ensure_chromium_installed,
)

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_screenshot_freshness import (  # noqa: E402
    EXPECTED_ALL_FILES,
    EXPECTED_EXTRA_FILES,
    EXPECTED_README_FILES,
    SCENARIO_BY_FILE,
    require_clean_capture_sources,
)


def _png_optimize_meta() -> dict:
    """Record which lossless optimizer produced committed documentation PNGs."""
    from PIL import Image

    return {
        "library": "Pillow",
        "version": Image.__version__,
        "mode": PNG_OPTIMIZE_MODE,
    }


def _pixels_match(a, b) -> bool:
    """True when two PIL images have identical dimensions and pixel values."""
    if a.size != b.size:
        return False
    left = a.convert("RGBA")
    right = b.convert("RGBA")
    return left.tobytes() == right.tobytes()


def _optimize_staged_png(path: Path) -> bool:
    """Losslessly recompress a staged PNG in place.

    Writes through a temporary sibling, verifies dimensions/pixels, and replaces
    only when the optimized file is strictly smaller. Any optimizer failure or
    rejected output leaves the original staged PNG untouched. Returns True when
    the staged file was replaced.
    """
    if not path.is_file():
        return False
    tmp: Path | None = None
    try:
        from PIL import Image

        original_size = path.stat().st_size
        with Image.open(path) as original:
            original.load()
            # Snapshot pixels before writing so we can verify the rewrite.
            baseline = original.copy()
            fd, tmp_name = tempfile.mkstemp(
                prefix=f"{path.stem}-opt-",
                suffix=".png",
                dir=str(path.parent),
            )
            os.close(fd)
            tmp = Path(tmp_name)
            baseline.save(
                tmp,
                format="PNG",
                optimize=True,
                compress_level=9,
            )

        with Image.open(path) as before, Image.open(tmp) as after:
            before.load()
            after.load()
            if not _pixels_match(before, after):
                tmp.unlink(missing_ok=True)
                return False

        optimized_size = tmp.stat().st_size
        if optimized_size >= original_size:
            tmp.unlink(missing_ok=True)
            return False

        os.replace(tmp, path)
        tmp = None
        return True
    except Exception:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return False


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


def _build_manifest_payload(
    set_name: str, captured: list[dict], head: str | None
) -> dict:
    """Build the merged manifest payload without writing it."""
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

    # Keep every expected screenshot visible to freshness, even when a partial
    # run has never regenerated it (legacy extras with no revision yet).
    for name in sorted(EXPECTED_ALL_FILES):
        if name not in by_file:
            by_file[name] = {
                "file": name,
                "scenario": SCENARIO_BY_FILE[name],
                # Explicit null: do not inherit the global source_commit.
                "source_commit": None,
            }

    ordered: list[dict] = []
    seen: set[str] = set()
    preferred = (
        list(previous.get("screenshots") or [])
        + [{"file": name} for name in sorted(EXPECTED_ALL_FILES)]
    )
    for row in preferred:
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

    payload_note = None
    if set_name == "all" and head:
        captured_names = {entry["file"] for entry in captured}
        if captured_names == EXPECTED_ALL_FILES:
            source_commit = head
        else:
            source_commit = previous.get("source_commit")
            if source_commit is not None:
                source_commit = str(source_commit).strip() or None
            payload_note = (
                "Incomplete all capture: global source_commit was not advanced; "
                "only regenerated files carry the new per-file revision."
            )
    else:
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
        "png_optimize": _png_optimize_meta(),
        "screenshots": ordered,
    }
    if set_name != "all":
        payload["note"] = (
            "Partial capture: only listed files with a matching source_commit "
            "were regenerated in this run; other entries keep prior provenance "
            "or remain revisionless until regenerated."
        )
    elif payload_note:
        payload["note"] = payload_note
    elif previous.get("note") and not source_commit:
        payload["note"] = previous.get("note")
    return payload


def _write_manifest(set_name: str, captured: list[dict], head: str | None) -> None:
    """Merge captured entries into the manifest without blessing untouched PNGs."""
    payload = _build_manifest_payload(set_name, captured, head)
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("wrote", MANIFEST)


def _promote_capture(
    stage_dir: Path, set_name: str, captured: list[dict], head: str
) -> None:
    """Replace committed PNGs and manifest rollback-safely.

    Prepare the new manifest first, back up every target that will change,
    promote staged PNGs, write the manifest, and restore every backup if any
    step fails after mutation begins.
    """
    payload = _build_manifest_payload(set_name, captured, head)
    manifest_text = json.dumps(payload, indent=2) + "\n"
    backup_dir = Path(
        tempfile.mkdtemp(prefix="prks-shot-backup-", dir=str(OUT_DIR.parent))
    )
    backed_up: list[tuple[Path, Path | None]] = []
    promoted: list[Path] = []
    manifest_backup: Path | None = None
    try:
        if MANIFEST.is_file():
            manifest_backup = backup_dir / "manifest.json"
            shutil.copy2(MANIFEST, manifest_backup)

        for entry in captured:
            name = entry["file"]
            src = stage_dir / name
            dest = OUT_DIR / name
            if not src.is_file():
                raise RuntimeError(f"Staged screenshot missing: {name}")
            prior: Path | None = None
            if dest.is_file():
                prior = backup_dir / name
                shutil.copy2(dest, prior)
            backed_up.append((dest, prior))

        for entry in captured:
            name = entry["file"]
            os.replace(stage_dir / name, OUT_DIR / name)
            promoted.append(OUT_DIR / name)
            print("wrote", OUT_DIR / name)

        MANIFEST.write_text(manifest_text, encoding="utf-8")
        print("wrote", MANIFEST)
    except Exception:
        # Restore originals for every path we may have mutated.
        restore_errors: list[str] = []
        for dest, prior in backed_up:
            try:
                if prior is not None and prior.is_file():
                    os.replace(prior, dest)
                elif dest.is_file() and dest in promoted:
                    dest.unlink()
            except OSError as restore_exc:
                restore_errors.append(f"{dest.name}: {restore_exc}")
        if manifest_backup is not None and manifest_backup.is_file():
            try:
                os.replace(manifest_backup, MANIFEST)
            except OSError as restore_exc:
                restore_errors.append(f"manifest.json: {restore_exc}")
        if restore_errors:
            # Keep backup_dir so the operator can recover manually, and re-raise
            # the original promotion failure (wrapper prints str(exc) only).
            print(
                "Screenshot promotion failed and rollback could not restore "
                f"all artifacts; backups retained at {backup_dir} "
                f"({'; '.join(restore_errors)})",
                file=sys.stderr,
            )
            raise
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup_dir, ignore_errors=True)


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
    if set_name in ("extra", "all") and (not darwin or not note or not group):
        missing = []
        if not darwin:
            missing.append("person Darwin")
        if not note:
            missing.append("work 'Commonplace: Darwin on variation'")
        if not group:
            missing.append("group 'Nineteenth century'")
        raise RuntimeError(
            "Demo library incomplete for the "
            f"{set_name!r} capture set (missing {', '.join(missing)}). "
            "Re-run scripts/seed_demo_library.py."
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
                      if (typeof prksInitLazyWorkThumbs === 'function') {
                        prksInitLazyWorkThumbs(document);
                        return;
                      }
                      document.querySelectorAll('img[data-prks-thumb-lazy]').forEach((img) => {
                        const thumb = img.closest('.work-card__thumb');
                        const src =
                          typeof prksResolveWorkThumbSrc === 'function' && thumb
                            ? prksResolveWorkThumbSrc(thumb)
                            : '';
                        if (src) img.src = src;
                      });
                    }"""
                )
                if wait_pdf:
                    page.wait_for_selector(".prks-pdf-viewer", timeout=20000)
                    page.wait_for_timeout(6000)
                else:
                    page.wait_for_timeout(2500)
                page.screenshot(path=str(dest), full_page=False)
                if _optimize_staged_png(dest):
                    print("optimized", dest_name)
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
                shot(
                    f"#/people/{darwin['id']}",
                    "person.png",
                    ".document-view--person",
                    scenario="darwin-person",
                )
                shot(
                    f"#/works/{note['id']}",
                    "note.png",
                    ".document-view",
                    scenario="commonplace-note",
                    wait_pdf=True,
                )
                shot(
                    f"#/people/groups/{group['id']}",
                    "group.png",
                    ".document-view--group-detail",
                    scenario="nineteenth-century-group",
                )

            browser.close()

        if not captured:
            raise RuntimeError(f"No screenshots were captured for set {set_name!r}.")

        captured_names = {entry["file"] for entry in captured}
        expected = {
            "readme": EXPECTED_README_FILES,
            "extra": EXPECTED_EXTRA_FILES,
            "all": EXPECTED_ALL_FILES,
        }[set_name]
        if captured_names != expected:
            missing = ", ".join(sorted(expected - captured_names)) or "(none)"
            extra = ", ".join(sorted(captured_names - expected)) or "(none)"
            raise RuntimeError(
                f"Capture set {set_name!r} incomplete: missing {missing}; unexpected {extra}."
            )

        # Promote the complete staged set + manifest rollback-safely.
        _promote_capture(stage_dir, set_name, captured, head)
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
