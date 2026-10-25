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


# In-memory, fixture-only WebKit diagnostics. Captures *only* known error
# categories, static script paths and bounded line numbers; never messages,
# URLs, stacks, request bodies or content. Installed again on each reload.
_SMOKE_ERROR_PROBE = r"""(() => {
    if (window.__prksSmokeErrorProbe) return;
    const entries = [];
    Object.defineProperty(window, '__prksSmokeErrorProbe', { value: entries });
    const names = new Set([
        'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'Error',
        'AbortError', 'InvalidStateError', 'SecurityError', 'NotFoundError',
        'NetworkError', 'UnknownError'
    ]);
    const source = (url, lineno) => {
        try {
            const u = new URL(url, location.href);
            const path = u.pathname;
            const match = new RegExp('^/(js|vue|assets|vendor)/([A-Za-z0-9_./-]+[.]js)$').exec(path);
            if (u.origin !== location.origin || !match || path.includes('..')) return 'unknown';
            const line = Number(lineno);
            return match[1] + '-' + match[2].replaceAll('/', '-') +
                (Number.isInteger(line) && line > 0 && line <= 999999 ? '-' + line : '');
        } catch (_) { return 'unknown'; }
    };
    const record = (type, reason, url, line) => {
        if (entries.length >= 5) return;
        const name = reason && typeof reason.name === 'string' ? reason.name : '';
        const ctor = reason && reason.constructor && reason.constructor.name;
        entries.push({
            kind: type, name: names.has(name) ? name : names.has(ctor) ? ctor : 'unknown',
            source: source(url, line)
        });
    };
    window.addEventListener('error', e =>
        record('window', e.error, e.filename || '', e.lineno || 0));
    window.addEventListener('unhandledrejection', e =>
        record('promise', e.reason, '', 0));
})()"""


def probe_error_summary(page) -> str:
    """Read only bounded metadata pre-sanitized by the synthetic page probe."""
    try:
        events = page.evaluate("() => window.__prksSmokeErrorProbe || []")
        if not isinstance(events, list):
            return "none"
        permitted = {"window", "promise"}
        names = {
            "TypeError", "ReferenceError", "SyntaxError", "RangeError", "Error",
            "AbortError", "InvalidStateError", "SecurityError", "NotFoundError",
            "NetworkError", "UnknownError",
        }
        labels = []
        for event in events[:5]:
            if not isinstance(event, dict):
                continue
            kind = event.get("kind")
            name = event.get("name")
            src = event.get("source")
            if kind not in permitted or name not in names | {"unknown"}:
                continue
            if not isinstance(src, str) or len(src) > 115 or not all(
                ch.isascii() and (ch.isalnum() or ch in "-_.") for ch in src
            ):
                src = "unknown"
            labels.append(kind + "-" + name.lower() + "-" + src)
        return "_".join(labels) if labels else "none"
    except Exception:
        return "unavailable"


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
        # The E2E collector captures Playwright's exception class separately
        # from its potentially sensitive message. Only fixed names escape.
        allowed = frozenset((
            "TypeError", "ReferenceError", "SyntaxError", "RangeError",
            "AbortError", "InvalidStateError", "NotFoundError", "Error",
        ))
        labels = []
        names = getattr(collector, "pageerror_names", None)
        for name in (names[:5] if isinstance(names, list) else []):
            labels.append(name.lower() if name in allowed else "unknown")
        if len(labels) < min(len(collector.pageerrors), 5):
            labels.extend(["unknown"] * (min(len(collector.pageerrors), 5) - len(labels)))
        flagged.append("kinds-" + "_".join(labels))
        # Only normalized loopback JS source paths recorded by PageCollector,
        # never stack frames or arbitrary browser URLs.
        sites = getattr(collector, "pageerror_sites", None)
        if isinstance(sites, list) and sites:
            safe_sites = [
                site.replace("/", "-").replace(":", "-")
                if isinstance(site, str) and len(site) <= 110
                and all(ch.isascii() and (ch.isalnum() or ch in "-_./:") for ch in site)
                and site.startswith(("js/", "assets/", "vue/", "vendor/"))
                else "unknown"
                for site in sites[:5]
            ]
            flagged.append("sites-" + "_".join(safe_sites))
    return "_".join(flagged) or "unknown"


def check_browser_errors(collector, *, engine: str, phase: str, page=None) -> None:
    """Fail fast with only synthetic phase + allowlisted collector metadata."""
    try:
        collector.assert_clean()
    except AssertionError:
        label = "browser_errors_" + phase + "_" + collector_failure_categories(collector)
        if page is not None:
            label += "_events-" + probe_error_summary(page)
        raise SmokeFailure(engine, label, "AssertionError") from None


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
        # Current page plus any fresh document opened during the smoke.
        context.add_init_script(_SMOKE_ERROR_PROBE)
        page.evaluate(_SMOKE_ERROR_PROBE)
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
        check_browser_errors(collector, engine=engine, phase="after_notes", page=page)
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
        check_browser_errors(collector, engine=engine, phase="after_settings", page=page)
        stage = "pdf_render"
        # Existing Chromium E2E readiness contract: a real page image, not
        # merely a PDF toolbar or a completed HTTP response.
        _wait_pdf_viewer(page)

        check_browser_errors(collector, engine=engine, phase="after_pdf", page=page)
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
        check_browser_errors(collector, engine=engine, phase="after_split", page=page)
        stage = "split_reload"
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
        check_browser_errors(collector, engine=engine, phase="after_split_reload", page=page)
    except SmokeFailure:
        failed = True
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
