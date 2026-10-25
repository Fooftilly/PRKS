#!/usr/bin/env python3
"""Opt-in Firefox/WebKit smoke on PRKS's existing isolated Playwright AppServer.

This is NOT an authentication/PostgreSQL performance probe and does not run as
part of the protected Chromium full-E2E gate. No arbitrary origins or storage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.e2e.fixtures import seed_library  # noqa: E402
from tests.e2e.harness import AppServer, open_app_page, wait_for_async  # noqa: E402
from tests.e2e.install_browser import apply_playwright_browser_env  # noqa: E402
from tests.e2e.pdf_readiness import _wait_pdf_tab, _wait_pdf_viewer  # noqa: E402

ENGINES = ("firefox", "webkit")
CHECKS = ("folders", "work_detail", "notes_editor", "notes_save_reload", "settings_focus", "pdf_render", "split_panes", "split_reload")
INHERITED_DB_SETTINGS = (
    "PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_DATABASE_URL_FILE",
    "PRKS_TEST_DATABASE_URL",
)

_SPLIT_PDF_OWNERS = """({mainTabId, secondaryTabId, mainWorkId, secondaryWorkId}) => {
    const get = window.prksGetTabContext;
    const main = get && get(mainTabId);
    const secondary = get && get(secondaryTabId);
    const workA = main && main.getEntity && main.getEntity('work');
    const workB = secondary && secondary.getEntity && secondary.getEntity('work');
    const pdfA = main && main.getResource && main.getResource('pdf');
    const pdfB = secondary && secondary.getResource && secondary.getResource('pdf');
    return !!(workA && workB && pdfA && pdfB &&
        String(workA.id) === String(mainWorkId) &&
        String(workB.id) === String(secondaryWorkId) &&
        String(pdfA.workId) === String(mainWorkId) &&
        String(pdfB.workId) === String(secondaryWorkId));
}"""


class SmokeFailure(RuntimeError):
    """Only synthetic engine/stage and exception type; no browser diagnostics."""

    def __init__(self, engine: str, stage: str, cause_type: str) -> None:
        self.engine = engine
        self.stage = stage
        self.cause_type = cause_type
        super().__init__(f"{engine}/{stage} ({cause_type})")


def close_preserving_failure(resource, *, engine: str, stage: str, failed: bool) -> None:
    """Do not replace the primary failure with an error during cleanup."""
    try:
        resource.close()
    except Exception as exc:
        if not failed:
            raise SmokeFailure(engine, stage, type(exc).__name__) from None


def collector_failure_categories(collector) -> str:
    """Only fixed category names/counts, never URLs, exceptions or page text."""
    categories = (
        ("pageerrors", "page"),
        ("console_errors", "console"),
        ("failed_requests", "request"),
        ("http_5xx", "http5xx"),
        ("external", "external"),
    )
    flagged = []
    for attr, label in categories:
        values = getattr(collector, attr, None)
        if isinstance(values, list) and values:
            flagged.append(f"{label}-{min(len(values), 99)}")
    if collector.pageerrors:
        # Only well-known JavaScript exception *classes* may leave CI. Never
        # print raw page error text, stack frames, paths or URLs.
        allowed = (
            "TypeError", "ReferenceError", "SyntaxError", "RangeError",
            "AbortError", "InvalidStateError", "NotFoundError", "Error",
        )
        labels = []
        for error in collector.pageerrors[:5]:
            match = re.search(
                r"\\b(" + "|".join(allowed) + r")\\s*:",
                str(error).split("\\n", 1)[0],
            )
            labels.append(match.group(1).lower() if match else "unknown")
        flagged.append("kinds-" + "_".join(labels))
    return "_".join(flagged) or "unknown"


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


def smoke(browser, origin: str, work_id: str, related_work_id: str, *, engine: str) -> None:
    """One real app session; label each failure without printing page details."""
    stage = "app_open"
    context = None
    failed = False
    try:
        page, context, collector = open_app_page(browser, origin)
        stage = "folders"
        page.locator("[data-prks-folder-library-view]").wait_for(
            state="visible", timeout=15000
        )
        stage = "work_detail"
        page.evaluate("id => window.prksNavigate('#/works/' + id)", work_id)
        page.locator(".work-detail").first.wait_for(state="visible", timeout=20000)
        stage = "notes_editor"
        # A visible wrapper alone does not mean the async editor has mounted.
        page.locator(".CodeMirror").first.wait_for(state="visible", timeout=30000)
        stage = "notes_save_reload"
        # Real keyboard edit, not a programmatic value assignment. Use a
        # different synthetic value per browser so an unchanged backend body
        # cannot pass the second run against the shared disposable server.
        note_text = f"PRKS cross-browser synthetic notes ({engine})"
        page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(note_text)
        # A green UI label is insufficient: require independent server state.
        page.locator(
            '[data-prks-role="editor-status"]', has_text="All changes saved"
        ).wait_for(state="visible", timeout=30000)
        wait_for_async(
            page,
            """async ({id, text}) => {
                const response = await fetch('/api/works/' + encodeURIComponent(id),
                                             { cache: 'no-store' });
                if (!response.ok) return false;
                const work = await response.json();
                return !!(work && work.text_content === text);
            }""",
            arg={"id": work_id, "text": note_text},
            timeout=30000,
        )
        # A fresh document must load the committed value, not merely keep an
        # unsaved draft in the current CodeMirror instance.
        page.reload(wait_until="domcontentloaded")
        page.locator(".work-detail").first.wait_for(state="visible", timeout=20000)
        page.locator(".CodeMirror").first.wait_for(state="visible", timeout=30000)
        page.wait_for_function(
            """({id, text}) => {
                const ctx = window.prksGetFocusedTabContext &&
                            window.prksGetFocusedTabContext();
                const work = ctx && ctx.getEntity && ctx.getEntity('work');
                const notes = ctx && ctx.getResource && ctx.getResource('workNotes');
                return !!(work && String(work.id) === String(id) &&
                    notes && notes.editor && notes.editor.value() === text);
            }""",
            arg={"id": work_id, "text": note_text},
            timeout=30000,
        )
        stage = "settings_focus"
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
        stage = "pdf_render"
        # Existing Chromium E2E readiness contract: a real page image, not
        # merely a PDF toolbar or a completed HTTP response.
        _wait_pdf_viewer(page)

        stage = "split_panes"
        page.evaluate(
            "id => window.prksNavigate('#/works/' + id, { target: 'tile' })",
            related_work_id,
        )
        page.wait_for_function(
            """() => {
                const s = window.prksWorkspaceSnapshot && window.prksWorkspaceSnapshot();
                return !!(s && s.mode === 'tiled' &&
                    s.secondaryTree && s.secondaryTree.type === 'leaf' &&
                    s.mainTabId && s.secondaryTree.tabId &&
                    s.mainTabId !== s.secondaryTree.tabId);
            }""",
            timeout=30000,
        )
        snap = page.evaluate("() => window.prksWorkspaceSnapshot()")
        # Distinct tab IDs alone are insufficient: confirm two separately
        # owned Work and PDF resources, not two views of the same Work.
        page.wait_for_function(
            _SPLIT_PDF_OWNERS,
            arg={
                "mainTabId": snap["mainTabId"],
                "secondaryTabId": snap["secondaryTree"]["tabId"],
                "mainWorkId": work_id,
                "secondaryWorkId": related_work_id,
            },
            timeout=30000,
        )
        # Independently wait for both mounted PDFs. Merely seeing two panes
        # is not evidence that both reading contexts finished loading.
        _wait_pdf_tab(page, snap["mainTabId"])
        _wait_pdf_tab(page, snap["secondaryTree"]["tabId"])
        page.locator(".prks-tile--main .prks-pdf-render-image").first.wait_for(
            state="visible", timeout=30000,
        )
        page.locator(".prks-tile--secondary .work-detail").wait_for(
            state="visible", timeout=30000,
        )
        page.locator(".prks-tile--secondary .CodeMirror").wait_for(
            state="visible", timeout=30000,
        )
        page.locator(".prks-tile--secondary .prks-pdf-render-image").first.wait_for(
            state="visible", timeout=30000,
        )
        stage = "split_reload"
        # Check for earlier browser exceptions before attributing any new
        # ones to remount. This does not clear or excuse collector failures.
        collector.assert_clean()
        # Match the existing Chromium E2E _flush_workspace test helper.
        # Fail closed if the persistence API was removed or renamed.
        page.evaluate("() => window.prksFlushWorkspacePersistence()")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            """({mainTabId, secondaryTabId}) => {
                if (window.__prksWorkspaceReady !== true) return false;
                const s = window.prksWorkspaceSnapshot && window.prksWorkspaceSnapshot();
                return !!(s && s.mode === 'tiled' &&
                    s.mainTabId === mainTabId &&
                    s.secondaryTree && s.secondaryTree.type === 'leaf' &&
                    s.secondaryTree.tabId === secondaryTabId);
            }""",
            arg={
                "mainTabId": snap["mainTabId"],
                "secondaryTabId": snap["secondaryTree"]["tabId"],
            },
            timeout=30000,
        )
        page.wait_for_function(
            _SPLIT_PDF_OWNERS,
            arg={
                "mainTabId": snap["mainTabId"],
                "secondaryTabId": snap["secondaryTree"]["tabId"],
                "mainWorkId": work_id,
                "secondaryWorkId": related_work_id,
            },
            timeout=30000,
        )
        # Verify both rendered PDF contexts again after remount. A restored
        # layout alone can conceal unloaded or incorrectly owned readers.
        _wait_pdf_tab(page, snap["mainTabId"])
        _wait_pdf_tab(page, snap["secondaryTree"]["tabId"])
        page.locator(".prks-tile--main .prks-pdf-render-image").first.wait_for(
            state="visible", timeout=30000,
        )
        page.locator(".prks-tile--secondary .prks-pdf-render-image").first.wait_for(
            state="visible", timeout=30000,
        )
        stage = "browser_errors"
        try:
            collector.assert_clean()
        except AssertionError:
            stage = "browser_errors_" + collector_failure_categories(collector)
            raise
    except Exception as exc:
        failed = True
        # Playwright exceptions may include URLs, selectors and page text.
        raise SmokeFailure(engine, stage, type(exc).__name__) from None
    finally:
        if context is not None:
            close_preserving_failure(
                context, engine=engine, stage="context_close", failed=failed
            )


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
    engine = "setup"
    stage = "server_start"
    try:
        server.start()  # only a throwaway --testing process bound to 127.0.0.1
        stage = "playwright_start"
        with sync_playwright() as playwright:
            for engine in engines:
                stage = "browser_launch"
                browser = getattr(playwright, engine).launch(headless=True)
                stage = "browser_smoke"
                failed = False
                try:
                    smoke(
                        browser, server.origin, server.ids["work_a"],
                        server.ids["work_b"], engine=engine,
                    )
                    stage = "browser_version"
                    report["browsers"].append({
                        "engine": engine,
                        "version": browser.version,
                        "checks": list(CHECKS),
                        "passed": True,
                    })
                except BaseException:
                    failed = True
                    raise
                finally:
                    close_preserving_failure(
                        browser, engine=engine, stage="browser_close", failed=failed
                    )
        engine = "setup"
        stage = "report_validation"
        verify_report(report, engines)
        if output is not None:
            stage = "report_write"
            output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return report
    except SmokeFailure:
        raise
    except Exception as exc:
        raise SmokeFailure(engine, stage, type(exc).__name__) from None
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
    except SmokeFailure as exc:
        # Print only fixed synthetic engine/stage labels and exception type:
        # Playwright diagnostics may contain URLs, paths or research content.
        print(f"Cross-browser smoke failed at {exc.engine}/{exc.stage} "
              f"({exc.cause_type})", file=sys.stderr)
        return 1
    except Exception as exc:
        # Includes isolation preflight; never expose exception text or paths.
        print(f"Cross-browser smoke failed at setup/preflight "
              f"({type(exc).__name__})", file=sys.stderr)
        return 1
    for row in report["browsers"]:
        print(f"{row['engine']} {row['version']}: " + ", ".join(row["checks"]) + " PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
