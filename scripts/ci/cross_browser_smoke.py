#!/usr/bin/env python3
"""Opt-in Firefox/WebKit smoke on PRKS's existing isolated Playwright AppServer.

This is NOT an authentication/PostgreSQL performance probe and does not run as
part of the protected Chromium full-E2E gate. No arbitrary origins or storage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.e2e.fixtures import seed_library  # noqa: E402
from tests.e2e.harness import AppServer, open_app_page  # noqa: E402
from tests.e2e.install_browser import apply_playwright_browser_env  # noqa: E402

ENGINES = ("firefox", "webkit")
CHECKS = ("folders", "work_detail", "notes_editor", "settings_focus")
INHERITED_DB_SETTINGS = (
    "PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_DATABASE_URL_FILE",
    "PRKS_TEST_DATABASE_URL",
)


def verify_isolation(environ: dict[str, str]) -> None:
    """Never let a synthetic cross-browser test inherit a real database."""
    if any(environ.get(name, "").strip() for name in INHERITED_DB_SETTINGS):
        raise RuntimeError("Cross-browser smoke refuses inherited auth/database settings")


def verify_report(report: dict, requested: tuple[str, ...]) -> None:
    """Fail closed if a browser or any required interaction went unrecorded."""
    if report.get("format") != "prks-cross-browser-smoke-v1":
        raise ValueError("cross-browser report has an unknown format")
    if report.get("fixture") != "disposable_appserver_synthetic_sqlite":
        raise ValueError("cross-browser smoke fixture is not isolated")
    cases = report.get("browsers")
    if not isinstance(cases, list) or len(cases) != len(requested):
        raise ValueError("a requested browser was not tested")
    if [item.get("engine") for item in cases if isinstance(item, dict)] != list(requested):
        raise ValueError("cross-browser report missing or duplicating an engine")
    for item in cases:
        if item.get("checks") != list(CHECKS) or item.get("passed") is not True:
            raise ValueError("cross-browser report lacks completed interaction checks")
        version = item.get("version")
        if not isinstance(version, str) or not version or len(version) > 64:
            raise ValueError("cross-browser report has invalid browser version")


def smoke(browser, origin: str, work_id: str) -> None:
    """One real app session; every selector is an observed UI readiness signal."""
    page, context, collector = open_app_page(browser, origin)
    try:
        page.locator("[data-prks-folder-library-view]").wait_for(
            state="visible", timeout=15000
        )
        page.evaluate("id => window.prksNavigate('#/works/' + id)", work_id)
        page.locator(".work-detail").first.wait_for(state="visible", timeout=20000)
        # A visible wrapper alone does not mean the async editor has mounted.
        page.locator(".CodeMirror").first.wait_for(state="visible", timeout=30000)
        settings = page.locator("button.settings-btn")
        settings.focus()
        page.keyboard.press("Enter")
        page.locator("#settings-modal:not(.hidden)").wait_for(
            state="visible", timeout=15000
        )
        page.keyboard.press("Escape")
        page.locator("#settings-modal").wait_for(state="hidden", timeout=15000)
        if not page.evaluate(
            "() => !!(document.activeElement && "
            "document.activeElement.classList.contains('settings-btn'))"
        ):
            raise AssertionError("settings dialog did not restore keyboard focus")
        collector.assert_clean()
    finally:
        context.close()


def run(engines: tuple[str, ...], output: Path | None = None) -> dict:
    verify_isolation(os.environ)
    if not engines or len(set(engines)) != len(engines) or any(
        engine not in ENGINES for engine in engines
    ):
        raise ValueError("select unique, supported Playwright browser engines")
    # Reuse Playwright's exact pinned revision and repository-local browser cache.
    apply_playwright_browser_env()
    from playwright.sync_api import sync_playwright

    server = AppServer(seed_fn=seed_library)
    report = {
        "format": "prks-cross-browser-smoke-v1",
        "fixture": "disposable_appserver_synthetic_sqlite",
        "browsers": [],
    }
    try:
        server.start()  # only a throwaway --testing process bound to 127.0.0.1
        with sync_playwright() as playwright:
            for engine in engines:
                browser = getattr(playwright, engine).launch(headless=True)
                try:
                    smoke(browser, server.origin, server.ids["work_a"])
                    report["browsers"].append({
                        "engine": engine,
                        "version": browser.version,
                        "checks": list(CHECKS),
                        "passed": True,
                    })
                finally:
                    browser.close()
        verify_report(report, engines)
        if output is not None:
            output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        server.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engines", nargs="+", choices=ENGINES, default=list(ENGINES),
        help="Selected Playwright browser engines; default Firefox and WebKit",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        report = run(tuple(args.engines), args.output)
    except Exception as exc:
        # No exception strings: server/Playwright diagnostics can contain
        # arbitrary paths, URLs, or fixture payloads. Keep Actions logs safe.
        print(f"Cross-browser smoke failed ({type(exc).__name__}); "
              "inspect the local isolated run.", file=sys.stderr)
        return 1
    for row in report["browsers"]:
        print(f"{row['engine']} {row['version']}: " + ", ".join(row["checks"]) + " PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
