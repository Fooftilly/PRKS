#!/usr/bin/env python3
"""Opt-in local visual regression for five isolated, synthetic PRKS states.

Run from a disposable working directory. Existing E2E server/browser fixtures
provide isolation; full PNGs stay in short-lived local Actions artifacts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.e2e.fixtures import seed_folders_library  # noqa: E402
from tests.e2e.harness import AppServer, PageCollector, require_chromium  # noqa: E402
from tests.e2e.test_app import _wait_pdf_tab, _wait_pdf_viewer  # noqa: E402
from tests.visual.compare import (  # noqa: E402
    SCENES,
    candidate,
    compare,
    fingerprint,
    verify,
    write_json,
)

BASELINE = ROOT / "tests" / "visual" / "baseline.json"
STYLE = (
    "*, *::before, *::after { caret-color: transparent !important; "
    "animation-duration: 0s !important; transition-duration: 0s !important; }"
)


class VisualCaptureFailure(RuntimeError):
    """Only a bounded, fixture-known capture stage and exception class."""

    def __init__(self, stage: str, cause_type: str) -> None:
        self.stage = stage
        self.cause_type = cause_type
        super().__init__(f"visual/{stage} ({cause_type})")


def capture(page, name: str, output: Path, selector: str | None = None) -> dict:
    if name not in SCENES:
        raise AssertionError("unknown visual scene")
    page.evaluate("async () => { await document.fonts.ready; }")
    if selector:
        target = page.locator(selector)
        target.wait_for(state="visible", timeout=15000)
        png = target.screenshot(animations="disabled", caret="hide", style=STYLE)
    else:
        png = page.screenshot(animations="disabled", caret="hide", style=STYLE)
    (output / f"prks-visual-{name}.png").write_bytes(png)
    return fingerprint(png)


def assert_split_pdf_ownership(page, *, main_tab: str, secondary_tab: str,
                               main_work: str, secondary_work: str) -> None:
    """An attractive screenshot is not proof that both panes mounted distinct Works.

    Fail before capturing a visual baseline if tab routing, Work resources, or
    PDF ownership silently point at the same document.
    """
    page.wait_for_function(
        """({mainTab, secondaryTab, mainWork, secondaryWork}) => {
            if (!mainTab || !secondaryTab || mainTab === secondaryTab ||
                String(mainWork) === String(secondaryWork)) return false;
            const get = window.prksGetTabContext;
            const a = get && get(mainTab);
            const b = get && get(secondaryTab);
            const wa = a && a.getEntity && a.getEntity('work');
            const wb = b && b.getEntity && b.getEntity('work');
            const pa = a && a.getResource && a.getResource('pdf');
            const pb = b && b.getResource && b.getResource('pdf');
            return !!(wa && wb && pa && pb &&
                String(wa.id) === String(mainWork) &&
                String(wb.id) === String(secondaryWork) &&
                String(pa.workId) === String(mainWork) &&
                String(pb.workId) === String(secondaryWork));
        }""",
        arg={
            "mainTab": main_tab, "secondaryTab": secondary_tab,
            "mainWork": main_work, "secondaryWork": secondary_work,
        },
        timeout=30000,
    )


def collect(output: Path) -> dict[str, dict]:
    # Never reads a live library. AppServer creates a fresh temporary PRKS
    # storage directory, binds loopback and deletes it on close.
    server = AppServer(seed_fn=seed_folders_library)
    browser_api = None
    browser = None
    context = None
    samples = None
    failure = None
    stage = "server_start"
    try:
        server.start()
        stage = "browser_start"
        browser_api, browser = require_chromium()
        stage = "page_start"
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            device_scale_factor=1,
            locale="en-US",
            timezone_id="UTC",
            color_scheme="light",
            reduced_motion="reduce",
            service_workers="block",
        )
        page = context.new_page()
        collector = PageCollector(page, server.origin)
        stage = "folders_ready"
        page.goto(server.origin + "/#/folders", wait_until="domcontentloaded")
        page.locator("#sidebar").wait_for(state="visible")
        page.locator("[data-prks-folder-library-view]").wait_for(state="visible")
        stage = "capture_folders"
        samples = {"folders": capture(page, "folders", output)}

        stage = "work_ready"
        page.evaluate("id => window.prksNavigate('#/works/' + id)", server.ids["work_a"])
        page.locator(".work-detail").first.wait_for(state="visible", timeout=20000)
        page.locator(".CodeMirror").first.wait_for(state="visible", timeout=30000)
        _wait_pdf_viewer(page)
        stage = "capture_work"
        samples["work"] = capture(page, "work", output)
        stage = "capture_pdf"
        samples["pdf"] = capture(page, "pdf", output, '[data-prks-role="pdf-viewer"]')

        stage = "split_ready"
        page.evaluate(
            "id => window.prksNavigate('#/works/' + id, { target: 'tile' })",
            server.ids["work_b"],
        )
        page.wait_for_function(
            "() => window.prksWorkspaceSnapshot().mode === 'tiled' "
            "&& document.querySelectorAll('.work-detail').length >= 2",
            timeout=30000,
        )
        page.locator(".prks-tile--secondary .work-detail").wait_for(state="visible")
        # A visible Work wrapper is not a settled editor or mounted PDF.
        # Reuse the same readiness contract as the established split E2Es.
        ids = page.evaluate("() => window.prksWorkspaceSnapshot()")
        _wait_pdf_tab(page, ids["mainTabId"])
        _wait_pdf_tab(page, ids["secondaryTree"]["tabId"])
        assert_split_pdf_ownership(
            page, main_tab=ids["mainTabId"],
            secondary_tab=ids["secondaryTree"]["tabId"],
            main_work=server.ids["work_a"],
            secondary_work=server.ids["work_b"],
        )
        page.locator(".prks-tile--secondary .CodeMirror").wait_for(
            state="visible", timeout=30000
        )
        page.locator(".prks-tile--secondary .prks-pdf-render-image").first.wait_for(
            state="visible", timeout=30000
        )
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('.work-detail'))"
            ".filter(el => el.getBoundingClientRect().width > 0).length >= 2"
        )
        stage = "capture_split"
        samples["split"] = capture(page, "split", output)

        stage = "settings_ready"
        settings = page.locator("button.settings-btn")
        settings.click()
        page.locator("#settings-modal:not(.hidden)").wait_for(state="visible")
        stage = "capture_settings"
        samples["settings"] = capture(page, "settings", output)
        stage = "browser_errors"
        collector.assert_clean()
    except Exception as exc:
        # Playwright errors can contain routes, selectors and DOM text. Never
        # leak the raw exception into CI; preserve a useful fixed scene label.
        failure = VisualCaptureFailure(stage, type(exc).__name__)
    finally:
        # Cleanup must not replace the primary scene failure. Nonetheless an
        # otherwise successful run fails if a resource cannot be released.
        for cleanup_stage, resource, method in (
            ("close_context", context, "close"),
            ("close_browser", browser, "close"),
            ("stop_playwright", browser_api, "stop"),
            ("stop_server", server, "stop"),
        ):
            if resource is None:
                continue
            try:
                getattr(resource, method)()
            except Exception as exc:
                if failure is None:
                    failure = VisualCaptureFailure(
                        cleanup_stage, type(exc).__name__,
                    )
    if failure is not None:
        # Persist only fixed stage/exception identifiers and scene names.
        # Playwright's raw error, DOM text, IDs, paths and URLs are forbidden.
        # A failed write must never mask the original capture failure.
        try:
            write_json(output / "prks-visual-failure.json", {
                "format": "prks-local-visual-failure-v1",
                "status": "capture_failed",
                "stage": failure.stage,
                "exception_type": failure.cause_type,
                "completed_scenes": [name for name in SCENES if samples and name in samples],
            })
        except OSError:
            pass
        raise failure from None
    if samples is None:
        raise VisualCaptureFailure("internal_result", "RuntimeError")
    return samples


def prove_mutation_detection(sample: dict) -> None:
    """Fail closed unless controlled major layout/pixel mutation is detected."""
    changed = sample.copy()
    # Real image source is quantized RGB; the synthetic mutation is a different
    # screenshot with inverted colors. No need to change PRKS production CSS.
    import base64
    import zlib
    rgb = zlib.decompress(base64.b64decode(sample["rgb48x30_zlib"]))
    modified = bytes(248 - channel for channel in rgb)
    changed["rgb48x30_zlib"] = base64.b64encode(zlib.compress(modified, 9)).decode("ascii")
    if compare(changed, sample)["passed"]:
        raise AssertionError("controlled visual deviation was not detected")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "propose", "check"), default="auto")
    args = parser.parse_args(argv)
    work = Path.cwd()
    # Artifacts must be written only into the caller's disposable directory.
    if work.resolve() == ROOT.resolve():
        raise RuntimeError("run visual capture from a disposable directory, not the repo root")
    mode = args.mode
    if mode == "auto":
        mode = "check" if BASELINE.is_file() else "propose"

    samples = collect(work)
    proof = candidate(samples)
    write_json(work / "prks-visual-candidate.json", proof)
    prove_mutation_detection(samples["folders"])
    print("[visual] Controlled full-contrast deviation correctly fails comparison")

    if mode == "propose":
        print("[visual] Candidate-only baseline. Review all five synthetic PNGs and approve before committing baseline.json.")
        return 0
    if not BASELINE.is_file():
        raise ValueError("no approved baseline.json: capture in propose mode first")
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    outcomes = verify(samples, expected)
    write_json(work / "prks-visual-results.json", outcomes)
    for name, result in outcomes.items():
        print(f"[visual] {name}: {'PASS' if result['passed'] else 'DIFF'} "
              f"reason={result['reason']} mean={result['mean_delta']} "
              f"hot={result['hot_fraction']} size_changed={result['size_changed']}")
    return 0 if all(result["passed"] for result in outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
