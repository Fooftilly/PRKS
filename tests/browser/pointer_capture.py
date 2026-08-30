#!/usr/bin/env python3
"""Dev-only real-mouse pointer-capture checks for the PDF viewer fixture.

Not collected by python run_tests.py. Playwright is already used by
scripts/capture_demo_screenshots.py; it is not a PRKS runtime or Docker
dependency. If Playwright is missing, this script prints SKIP and exits 0.

Starts tests/browser/serve.py, opens pdf_viewer.html?pointer=1, and uses a
real mouse for:

1. select → leave page → release → isSelecting() is false → second select ends
2. pan → drag beyond viewport → release → further move does not keep scrolling
3. real mouse selection must not fire native HTML dragstart (page thumbnail ghost)

Synthetic dispatchEvent(PointerEvent) does not prove capture; this script does.
"""
from __future__ import annotations

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


def _python() -> str:
    exe = sys.executable or ""
    if "python" in Path(exe).name.lower() and not exe.lower().endswith(".appimage"):
        return exe
    found = shutil.which("python3") or shutil.which("python")
    if not found:
        raise RuntimeError("python3 not found")
    return found


def _skip(reason: str) -> int:
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
        [ _python(), str(SERVE)],
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
    _wait_http(origin + "/tests/browser/pdf_viewer.html")
    return proc, origin


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _skip("Playwright is not installed")

    # Headless shell is optional; bundled Chromium is enough for these checks.
    os.environ.setdefault("PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL", "0")

    proc = None
    try:
        proc, origin = _start_server()
        url = origin + "/tests/browser/pdf_viewer.html?pointer=1"
        with sync_playwright() as p:
            page = None
            browser = None
            last_err = None
            for kwargs in (
                {"headless": True, "channel": "chrome"},
                {"headless": True, "channel": "chromium"},
                {"headless": True},
            ):
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
            page.wait_for_function("() => window.__prksPointerReady === true", timeout=30000)
            failed = []

            def ok(name: str, cond: bool, detail: str = "") -> None:
                if cond:
                    print(f"PASS  {name}" + (f" — {detail}" if detail else ""))
                else:
                    print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))
                    failed.append(name)

            page.wait_for_selector(".prks-pdf-page", timeout=20000)
            page.wait_for_selector(".prks-pdf-render-image", timeout=20000)
            page.evaluate(
                """() => {
                    window.__prksNativeDragStarts = 0;
                    document.addEventListener(
                        'dragstart',
                        () => window.__prksNativeDragStarts++,
                        true
                    );
                }"""
            )
            box = page.locator(".prks-pdf-page").first.bounding_box()
            if not box:
                ok("page bounds", False)
            else:
                sx = box["x"] + min(90, box["width"] * 0.25)
                sy = box["y"] + min(80, box["height"] * 0.2)
                page.mouse.move(sx, sy)
                page.mouse.down()
                page.mouse.move(sx + 180, sy, steps=8)
                page.mouse.move(box["x"] - 50, box["y"] - 50, steps=8)
                page.mouse.up()
                page.wait_for_timeout(150)
                selecting = page.evaluate("() => window.__prksPointerViewer.isSelecting()")
                # parity: selection-outside-release
                ok("select leave-page release", selecting is False, str(selecting))
                drags = page.evaluate("() => window.__prksNativeDragStarts")
                # parity: no-native-drag
                ok("no native dragstart during select", drags == 0, str(drags))

                page.mouse.move(sx, sy + 24)
                page.mouse.down()
                page.mouse.move(sx + 160, sy + 24, steps=6)
                page.mouse.up()
                page.wait_for_timeout(150)
                selecting2 = page.evaluate("() => window.__prksPointerViewer.isSelecting()")
                ok("second selection ends", selecting2 is False, str(selecting2))
                drags2 = page.evaluate("() => window.__prksNativeDragStarts")
                ok("no native dragstart after second select", drags2 == 0, str(drags2))

            page.evaluate(
                """() => {
                    const v = window.__prksPointerViewer;
                    v.setInteractionMode('pan');
                    v.zoomIn();
                    v.zoomIn();
                    v.zoomIn();
                }"""
            )
            page.wait_for_timeout(400)
            vp = page.locator(".prks-pdf-viewport").first
            vbox = vp.bounding_box()
            if not vbox:
                ok("viewport bounds", False)
            else:
                cx = vbox["x"] + vbox["width"] / 2
                cy = vbox["y"] + vbox["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx - 120, cy - 80, steps=10)
                page.mouse.move(vbox["x"] - 40, vbox["y"] - 40, steps=6)
                page.mouse.up()
                page.wait_for_timeout(120)
                before = page.evaluate(
                    """() => {
                        const el = document.querySelector('.prks-pdf-viewport');
                        return { l: el.scrollLeft, t: el.scrollTop };
                    }"""
                )
                page.mouse.move(vbox["x"] - 80, vbox["y"] - 80, steps=8)
                page.wait_for_timeout(120)
                after = page.evaluate(
                    """() => {
                        const el = document.querySelector('.prks-pdf-viewport');
                        return { l: el.scrollLeft, t: el.scrollTop };
                    }"""
                )
                ok(
                    "pan leave-viewport release",
                    before == after,
                    f"before={before} after={after}",
                )
                # parity: pan-outside-release

            browser.close()
            if failed:
                print("FAILED: " + ", ".join(failed))
                return 1
            print("PASS pointer capture")
            return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
