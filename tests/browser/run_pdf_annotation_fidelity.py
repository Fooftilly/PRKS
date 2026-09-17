#!/usr/bin/env python3
"""Browser fidelity gate for PRKS-managed PDF annotations (Slice A).

Proves: viewer create → PRKS normalize/reconstruct → fresh viewer recreate →
normalize again remains semantically equal for highlight + underline (+ comment).

Also proves: same-id recreate does not duplicate; applying user markup does not
drop Link annotations already in the PDF bytes.

Not collected by python run_tests.py by default discovery of this file's
location under tests/browser/. Invoke explicitly:

    python3 tests/browser/run_pdf_annotation_fidelity.py

Exit 0 on PASS, 1 on FAIL, 0 with SKIP when Playwright is unavailable (unless
PRKS_E2E=1 or PRKS_PDF_FIDELITY=1, which force FAIL on missing Playwright).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVE = ROOT / "tests" / "browser" / "serve.py"
sys.path.insert(0, str(ROOT))

from backend.pdf_annotations import (  # noqa: E402
    annotations_semantically_equal,
    round_trip_annotation,
    semantic_annotation_view,
)


def _python() -> str:
    exe = sys.executable or ""
    if "python" in Path(exe).name.lower():
        return exe
    found = shutil.which("python3") or shutil.which("python")
    if not found:
        raise RuntimeError("python3 not found")
    return found


def _force_fail_on_skip() -> bool:
    return os.environ.get("PRKS_E2E") == "1" or os.environ.get("PRKS_PDF_FIDELITY") == "1"


def _skip(reason: str) -> int:
    if _force_fail_on_skip():
        print(f"FAIL: {reason}", file=sys.stderr)
        return 1
    print(f"SKIP: {reason}")
    return 0


def _wait_http(url: str, timeout: float = 20.0) -> None:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                if res.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = err
        time.sleep(0.1)
    raise RuntimeError(f"fixture server did not start: {last}")


def _start_server() -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(
        [_python(), str(SERVE)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    origin = None
    t0 = time.time()
    assert proc.stdout is not None
    while time.time() - t0 < 15:
        line = proc.stdout.readline()
        if not line:
            break
        if "pdf_viewer.html" in line:
            origin = line.strip().rsplit("/tests/", 1)[0]
            break
    if not origin:
        err = proc.stderr.read() if proc.stderr else ""
        proc.kill()
        raise RuntimeError(f"could not parse serve.py URL\n{err}")
    _wait_http(origin + "/tests/browser/pdf_annotation_fidelity.html")
    return proc, origin


def _diff_views(a: dict, b: dict) -> str:
    keys = sorted(set(a) | set(b))
    parts = []
    for key in keys:
        if a.get(key) != b.get(key):
            parts.append(f"{key}: {a.get(key)!r} != {b.get(key)!r}")
    return "; ".join(parts) or "(no diff)"


def main() -> int:
    os.environ.setdefault("PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL", "0")
    browsers = ROOT / ".playwright-browsers"
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    if os.environ.get("PRKS_E2E") == "1":
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _skip("Playwright is not installed")

    proc = None
    failed: list[str] = []
    material_blockers: list[str] = []

    def ok(name: str, cond: bool, detail: str = "", *, material: bool = False) -> None:
        if cond:
            print(f"PASS  {name}" + (f" — {detail}" if detail else ""))
        else:
            print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))
            failed.append(name)
            if material:
                material_blockers.append(f"{name}: {detail}" if detail else name)

    try:
        proc, origin = _start_server()
        url = origin + "/tests/browser/pdf_annotation_fidelity.html"
        with sync_playwright() as p:
            browser = None
            last_err = None
            launch_attempts = ({"headless": True},)
            if not _force_fail_on_skip():
                launch_attempts = (
                    {"headless": True, "channel": "chrome"},
                    {"headless": True, "channel": "chromium"},
                    {"headless": True},
                )
            for kwargs in launch_attempts:
                try:
                    browser = p.chromium.launch(**kwargs)
                    last_err = None
                    break
                except Exception as err:
                    last_err = err
                    browser = None
            if browser is None:
                return _skip(f"Playwright chromium unavailable ({last_err})")

            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__prksFidelityReady === true", timeout=30000)

            # --- Round-trip: create → canonicalize → recreate on clean PDF ---
            page.evaluate(
                """async () => {
                    const api = window.__prksFidelityApi;
                    window.__fidViewer = await api.mount(api.MINIMAL);
                    const hi = api.highlightSpec('fid-hi-1', 'Highlight comment');
                    const un = api.underlineSpec('fid-un-1', 'Underline comment');
                    window.__fidViewer.createAnnotation(0, hi);
                    window.__fidViewer.createAnnotation(0, un);
                    await api.waitFor(
                        () => api.rawAnnotations(window.__fidViewer).length >= 2,
                        8000,
                        'created two annotations'
                    );
                    window.__fidOriginalRaws = api.rawAnnotations(window.__fidViewer);
                }"""
            )
            original_raws = page.evaluate("() => window.__fidOriginalRaws")
            ok(
                "viewer created highlight+underline",
                isinstance(original_raws, list) and len(original_raws) >= 2,
                f"count={len(original_raws) if isinstance(original_raws, list) else None}",
                material=True,
            )
            if not isinstance(original_raws, list) or len(original_raws) < 2:
                browser.close()
                return 1

            reconstructed = [round_trip_annotation(item) for item in original_raws]
            # Only PRKS-managed user markup kinds we create in this spike.
            by_id = {item["id"]: item for item in reconstructed}
            ok("round_trip kept highlight id", "fid-hi-1" in by_id, material=True)
            ok("round_trip kept underline id", "fid-un-1" in by_id, material=True)
            if "fid-hi-1" in by_id:
                hi_v = semantic_annotation_view(by_id["fid-hi-1"])
                ok(
                    "highlight comment survives canonicalize",
                    hi_v.get("content") == "Highlight comment"
                    and (hi_v.get("custom") or {}).get("prksComment") == "Highlight comment",
                    json.dumps(hi_v.get("custom")),
                    material=True,
                )
                ok(
                    "highlight geometry survives canonicalize",
                    "rect" in hi_v and "segmentRects" in hi_v,
                    material=True,
                )

            page.evaluate(
                """async (reconstructed) => {
                    const api = window.__prksFidelityApi;
                    await api.destroy(window.__fidViewer);
                    window.__fidViewer = await api.mount(api.MINIMAL);
                    for (const item of reconstructed) {
                        const pageIndex = Number.isFinite(item.pageIndex) ? item.pageIndex : 0;
                        window.__fidViewer.createAnnotation(pageIndex, item);
                    }
                    await api.waitFor(
                        () => api.rawAnnotations(window.__fidViewer).length >= reconstructed.length,
                        8000,
                        'recreated annotations'
                    );
                    window.__fidRecreatedRaws = api.rawAnnotations(window.__fidViewer);
                }""",
                reconstructed,
            )
            recreated_raws = page.evaluate("() => window.__fidRecreatedRaws")
            ok(
                "recreate produced annotations",
                isinstance(recreated_raws, list) and len(recreated_raws) >= 2,
                f"count={len(recreated_raws) if isinstance(recreated_raws, list) else None}",
                material=True,
            )

            orig_by_id = {str(a.get("id")): a for a in original_raws}
            rec_by_id = {
                str(a.get("id")): a for a in (recreated_raws or []) if isinstance(a, dict)
            }
            for ann_id in ("fid-hi-1", "fid-un-1"):
                left = orig_by_id.get(ann_id)
                right = rec_by_id.get(ann_id)
                if left is None or right is None:
                    ok(
                        f"fidelity {ann_id} present after recreate",
                        False,
                        f"orig={ann_id in orig_by_id} rec={ann_id in rec_by_id}",
                        material=True,
                    )
                    continue
                equal = annotations_semantically_equal(left, right)
                detail = ""
                if not equal:
                    detail = _diff_views(
                        semantic_annotation_view(left),
                        semantic_annotation_view(right),
                    )
                ok(
                    f"fidelity {ann_id} semantic equality",
                    equal,
                    detail,
                    material=True,
                )

            # Second canonicalize of recreated state must still match first.
            for ann_id in ("fid-hi-1", "fid-un-1"):
                if ann_id not in rec_by_id:
                    continue
                twice = round_trip_annotation(rec_by_id[ann_id])
                ok(
                    f"fidelity {ann_id} stable under second canonicalize",
                    annotations_semantically_equal(rec_by_id[ann_id], twice),
                    material=True,
                )

            await_destroy = page.evaluate(
                """async () => {
                    const api = window.__prksFidelityApi;
                    await api.destroy(window.__fidViewer);
                    window.__fidViewer = null;
                }"""
            )
            del await_destroy

            # --- Duplicate-id: annotation already in PDF bytes ---
            page.evaluate(
                """async () => {
                    const api = window.__prksFidelityApi;
                    window.__fidViewer = await api.mount(api.MINIMAL);
                    const hi = api.highlightSpec('fid-dup-1', 'dup');
                    window.__fidViewer.createAnnotation(0, hi);
                    await api.waitFor(
                        () => api.rawAnnotations(window.__fidViewer).some((a) => a.id === 'fid-dup-1'),
                        8000,
                        'dup create'
                    );
                    const buf = await window.__fidViewer.saveCopy();
                    await api.destroy(window.__fidViewer);
                    window.__fidViewer = await api.mount(buf);
                    await api.sleep(800);
                    window.__fidBeforeDup = api.rawAnnotations(window.__fidViewer)
                        .filter((a) => a.id === 'fid-dup-1').length;
                    window.__fidViewer.createAnnotation(0, api.highlightSpec('fid-dup-1', 'dup'));
                    await api.sleep(500);
                    window.__fidAfterDup = api.rawAnnotations(window.__fidViewer)
                        .filter((a) => a.id === 'fid-dup-1').length;
                }"""
            )
            before_dup = page.evaluate("() => window.__fidBeforeDup")
            after_dup = page.evaluate("() => window.__fidAfterDup")
            ok(
                "embedded annotation present before same-id create",
                before_dup == 1,
                f"before={before_dup}",
                material=True,
            )
            ok(
                "same-id create does not duplicate",
                after_dup == 1,
                f"before={before_dup} after={after_dup}",
                material=True,
            )

            page.evaluate(
                """async () => {
                    const api = window.__prksFidelityApi;
                    await api.destroy(window.__fidViewer);
                }"""
            )

            # --- Link preservation ---
            page.evaluate(
                """async () => {
                    const api = window.__prksFidelityApi;
                    window.__fidViewer = await api.mount(api.WITH_LINK);
                    await api.sleep(600);
                    const before = api.rawAnnotations(window.__fidViewer);
                    window.__fidLinksBefore = before.filter((a) => {
                        const t = a.type;
                        if (t === 1 || t === '1' || t === 2 || t === '2') return true;
                        const blob = String(a.subtype || a.annotationType || a.type || '').toLowerCase();
                        if (blob.includes('link')) return true;
                        if (a.uri || a.url || (a.action && (a.action.uri || a.action.URL))) return true;
                        return false;
                    }).map((a) => a.id);
                    window.__fidAllBefore = before.map((a) => ({
                        id: a.id, type: a.type, subtype: a.subtype || null
                    }));
                    window.__fidViewer.createAnnotation(
                        0,
                        api.highlightSpec('fid-over-link', 'over link')
                    );
                    await api.waitFor(
                        () => api.rawAnnotations(window.__fidViewer)
                            .some((a) => a.id === 'fid-over-link'),
                        8000,
                        'user markup over link pdf'
                    );
                    await api.sleep(400);
                    const after = api.rawAnnotations(window.__fidViewer);
                    window.__fidLinksAfter = after.filter((a) => {
                        const t = a.type;
                        if (t === 1 || t === '1' || t === 2 || t === '2') return true;
                        const blob = String(a.subtype || a.annotationType || a.type || '').toLowerCase();
                        if (blob.includes('link')) return true;
                        if (a.uri || a.url || (a.action && (a.action.uri || a.action.URL))) return true;
                        return false;
                    }).map((a) => a.id);
                    window.__fidAllAfter = after.map((a) => ({
                        id: a.id, type: a.type, subtype: a.subtype || null
                    }));
                    window.__fidUserPresent = after.some((a) => a.id === 'fid-over-link');
                }"""
            )
            links_before = page.evaluate("() => window.__fidLinksBefore")
            links_after = page.evaluate("() => window.__fidLinksAfter")
            all_before = page.evaluate("() => window.__fidAllBefore")
            all_after = page.evaluate("() => window.__fidAllAfter")
            user_present = page.evaluate("() => window.__fidUserPresent")
            ok(
                "link fixture exposes at least one link annotation",
                isinstance(links_before, list) and len(links_before) >= 1,
                f"before_ids={links_before} all={all_before}",
                material=True,
            )
            ok("user markup created on link PDF", bool(user_present), material=True)
            if isinstance(links_before, list) and links_before:
                preserved = set(links_before).issubset(set(links_after or []))
                ok(
                    "createAnnotation does not delete link annotations",
                    preserved,
                    f"before={links_before} after={links_after} all_after={all_after}",
                    material=True,
                )

            page.evaluate(
                """async () => {
                    const api = window.__prksFidelityApi;
                    await api.destroy(window.__fidViewer);
                }"""
            )
            browser.close()
    finally:
        if proc is not None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    if material_blockers:
        print("\nMATERIAL FIDELITY BLOCKERS:")
        for line in material_blockers:
            print(f"  - {line}")
        print(
            "\nSTOP: do not force local-first annotations on a lossy representation."
        )

    if failed:
        print(f"\n{len(failed)} failure(s)")
        return 1
    print("\nAll fidelity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
