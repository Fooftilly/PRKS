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

from tests.e2e.fixtures import seed_library  # noqa: E402
from tests.e2e.harness import AppServer, PageCollector, require_chromium  # noqa: E402
from tests.e2e.test_app import _wait_pdf_viewer  # noqa: E402
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


def collect(output: Path) -> dict[str, dict]:
    # Never reads a live library. AppServer creates a fresh temporary PRKS
    # storage directory, binds loopback and deletes it on close.
    server = AppServer(seed_fn=seed_library)
    browser_api = None
    browser = None
    context = None
    try:
        server.start()
        browser_api, browser = require_chromium()
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
        page.goto(server.origin + "/#/folders", wait_until="domcontentloaded")
        page.locator("#sidebar").wait_for(state="visible")
        page.locator("[data-prks-folder-library-view]").wait_for(state="visible")
        samples = {"folders": capture(page, "folders", output)}

        page.evaluate("id => window.prksNavigate('#/works/' + id)", server.ids["work_a"])
        page.locator(".work-detail").first.wait_for(state="visible", timeout=20000)
        page.locator(".CodeMirror").first.wait_for(state="visible", timeout=30000)
        _wait_pdf_viewer(page)
        samples["work"] = capture(page, "work", output)
        samples["pdf"] = capture(page, "pdf", output, '[data-prks-role="pdf-viewer"]')

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
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('.work-detail'))"
            ".filter(el => el.getBoundingClientRect().width > 0).length >= 2"
        )
        samples["split"] = capture(page, "split", output)

        settings = page.locator("button.settings-btn")
        settings.click()
        page.locator("#settings-modal:not(.hidden)").wait_for(state="visible")
        samples["settings"] = capture(page, "settings", output)
        collector.assert_clean()
        return samples
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        if browser_api is not None:
            browser_api.stop()
        server.stop()


def prove_mutation_detection(sample: dict) -> None:
    """Fail closed unless controlled major layout/pixel mutation is detected."""
    changed = sample.copy()
    # Real image source is quantized RGB; the synthetic mutation is a different
    # screenshot with inverted colors. No need to change PRKS production CSS.
    from tests.visual.compare import SAMPLE_SIZE
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
              f"mean={result['mean_delta']} hot={result['hot_fraction']} size_changed={result['size_changed']}")
    return 0 if all(result["passed"] for result in outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
