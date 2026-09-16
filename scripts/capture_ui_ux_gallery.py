#!/usr/bin/env python3
"""Capture additional UI/UX consistency gallery screenshots (seeded AppServer)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.e2e.harness import AppServer, require_chromium
from tests.ux_tour.fixtures import seed_ux_tour_library

OUT = REPO / "docs" / "screenshots" / "ui-ux-consistency"
VIEWPORT = {"width": 1024, "height": 700}
NARROW = {"width": 390, "height": 700}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    pw, browser = require_chromium()
    server = AppServer(seed_fn=seed_ux_tour_library)
    try:
        server.start()
        context = browser.new_context(viewport=VIEWPORT, color_scheme="dark", device_scale_factor=1)
        page = context.new_page()
        page.goto(server.origin + "/", wait_until="domcontentloaded")
        page.wait_for_function("() => !!window.prksWorkspaceSnapshot", timeout=30000)
        page.evaluate(
            """() => {
                localStorage.setItem('prks-theme', 'dark');
                document.documentElement.setAttribute('data-theme', 'dark');
            }"""
        )

        shots = [
            ("list-folders.png", "#/folders", ".prks-folder-library"),
            ("list-people.png", "#/people", ".prks-people-library"),
            ("detail-concept.png", "#/concepts", None),
            ("detail-work.png", "#/works/%s" % server.ids["work_a"], ".work-detail"),
            ("graph.png", "#/graph", ".research-graph, h2"),
        ]
        for name, hash_path, wait_sel in shots:
            page.evaluate("(h) => window.prksNavigate(h)", hash_path)
            page.wait_for_timeout(900)
            if wait_sel:
                try:
                    page.wait_for_selector(wait_sel, timeout=20000)
                except Exception:
                    pass
            if name == "detail-concept":
                # Open first concept if any
                link = page.locator('a[href^="#/concepts/"]').first
                if link.count():
                    link.click()
                    page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / name), full_page=False)
            print("wrote", name)

        # Modal
        page.evaluate("() => window.prksNavigate('#/folders')")
        page.wait_for_timeout(400)
        page.locator("#prks-ribbon-create").click()
        page.wait_for_timeout(200)
        # Open settings as a representative modal
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        page.locator("#sidebar button, #sidebar a").filter(has_text="").first  # no-op keep lint quiet
        settings = page.locator('[data-lucide="settings"], #prks-open-settings, button[aria-label*="Settings"]')
        if settings.count() == 0:
            page.evaluate("() => { if (window.openModal) openModal('settings-modal'); }")
        else:
            settings.first.click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "modal-settings.png"), full_page=False)
        print("wrote modal-settings.png")
        page.keyboard.press("Escape")

        # Narrow / mobile
        page.set_viewport_size(NARROW)
        page.evaluate("() => window.prksNavigate('#/folders')")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "narrow-folders.png"), full_page=False)
        print("wrote narrow-folders.png")
        page.evaluate("(h) => window.prksNavigate(h)", "#/works/%s" % server.ids["work_a"])
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "narrow-work.png"), full_page=False)
        print("wrote narrow-work.png")

        context.close()
        return 0
    finally:
        try:
            server.stop()
        except Exception:
            pass
        try:
            browser.close()
        finally:
            pw.stop()


if __name__ == "__main__":
    raise SystemExit(main())
