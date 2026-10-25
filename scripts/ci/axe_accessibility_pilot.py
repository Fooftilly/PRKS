#!/usr/bin/env python3
"""Advisory axe-core smoke using PRKS's disposable Chromium Playwright harness.

Only fixed synthetic fixtures are permitted. Findings intentionally stay
nonblocking pending triage; failure to run the audit or a broken canary is
blocking. No DOM, selectors, URLs or user research content leave the runner.
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
from tests.e2e.harness import AppServer, open_app_page  # noqa: E402
from tests.e2e.install_browser import apply_playwright_browser_env  # noqa: E402
from tests.e2e.pdf_readiness import _wait_pdf_viewer  # noqa: E402

SURFACES = ("folders", "work_pdf", "settings_modal")
KEYBOARD_CHECKS = ("settings_escape_returns_focus",)
TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa")
AXE_JS = ROOT / "frontend-app" / "node_modules" / "axe-core" / "axe.min.js"
PACKAGE_JSON = ROOT / "frontend-app" / "package.json"
RULE_ID = re.compile(r"^[a-z][a-z0-9-]{0,75}$")
IMPACTS = frozenset(("minor", "moderate", "serious", "critical"))
INHERITED_DB = (
    "PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_DATABASE_URL_FILE",
    "PRKS_TEST_DATABASE_URL",
)


def verify_isolation(environ: dict[str, str]) -> None:
    if any(environ.get(key, "").strip() for key in INHERITED_DB):
        raise ValueError("The synthetic accessibility pilot refuses live auth/database config")


def pinned_axe_version() -> str:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    version = package["devDependencies"]["axe-core"]
    if not re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", version):
        raise ValueError("axe-core must have an exact npm pin")
    return version


def safe_findings(violations: object) -> list[dict]:
    """Only trusted axe rule identifiers, impacts and node counts can escape."""
    if not isinstance(violations, list):
        raise ValueError("axe produced no violation inventory")
    result = []
    seen = set()
    for item in violations:
        if not isinstance(item, dict):
            raise ValueError("malformed axe violation")
        rule = item.get("id")
        nodes = item.get("nodes")
        if not isinstance(rule, str) or not RULE_ID.fullmatch(rule) or rule in seen:
            raise ValueError("invalid or duplicated axe rule ID")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("malformed axe nodes")
        seen.add(rule)
        result.append({
            "rule": rule,
            "impact": item.get("impact") if item.get("impact") in IMPACTS else "unknown",
            "nodes": min(len(nodes), 999),
        })
    return sorted(result, key=lambda entry: entry["rule"])


def verify_report(report: dict) -> None:
    if report.get("format") != "prks-axe-pilot-v1":
        raise ValueError("unexpected accessibility report format")
    if report.get("fixture") != "disposable_appserver_synthetic_sqlite":
        raise ValueError("accessibility audit did not use disposable fixtures")
    if report.get("axe_version") != pinned_axe_version():
        raise ValueError("accessibility audit used an unexpected axe version")
    if report.get("engine") != "chromium":
        raise ValueError("accessibility pilot must use existing Chromium E2E")
    if report.get("keyboard_checks") != list(KEYBOARD_CHECKS):
        raise ValueError("keyboard check missing")
    surfaces = report.get("surfaces")
    if not isinstance(surfaces, list) or len(surfaces) != len(SURFACES):
        raise ValueError("accessibility audit skipped a surface")
    if [item.get("surface") for item in surfaces if isinstance(item, dict)] != list(SURFACES):
        raise ValueError("accessibility audit skipped or reordered a surface")
    for item in surfaces:
        if not isinstance(item.get("rules_checked"), int) or item["rules_checked"] <= 0:
            raise ValueError("axe did not evaluate rules")
        if not isinstance(item.get("incomplete"), int) or item["incomplete"] < 0:
            raise ValueError("axe incomplete results missing")
        findings = item.get("violations")
        if not isinstance(findings, list) or safe_findings([
            {"id": x["rule"], "impact": x["impact"], "nodes": [None] * x["nodes"]}
            for x in findings
        ]) != findings:
            raise ValueError("invalid sanitized axe findings")


def axe_run(page, scope: str) -> dict:
    # Serialize only counts and rule metadata in the browser, never raw axe
    # nodes, HTML snippets, CSS selectors or failure text.
    data = page.evaluate(
        """async ({scope, tags}) => {
            if (!window.axe || typeof window.axe.run !== 'function') return null;
            const root = document.querySelector(scope);
            if (!root) return null;
            const report = await window.axe.run(root, {
                runOnly: { type: 'tag', values: tags },
                resultTypes: ['violations', 'incomplete']
            });
            return {
                violations: report.violations.map(v => ({
                    id: v.id, impact: v.impact,
                    nodes: Array.from({length: v.nodes.length}, () => null)
                })),
                incomplete: report.incomplete.length,
                rules_checked: report.passes.length +
                    report.violations.length + report.incomplete.length
            };
        }""",
        {"scope": scope, "tags": list(TAGS)},
    )
    if not isinstance(data, dict) or not isinstance(data.get("rules_checked"), int):
        raise RuntimeError("axe did not audit the expected DOM surface")
    if data["rules_checked"] <= 0:
        raise RuntimeError("axe did not evaluate any rules")
    return {
        "rules_checked": data["rules_checked"],
        "incomplete": data["incomplete"],
        "violations": safe_findings(data["violations"]),
    }


def verify_axe_canary(page) -> None:
    # A missing-name button is an intentional *fixture-only* violation.
    # This proves axe can detect a known defect rather than just load.
    detected = page.evaluate(
        """async () => {
            if (!window.axe || typeof window.axe.run !== 'function') return false;
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('data-prks-axe-canary', '1');
            button.style.cssText = 'position:fixed;left:2px;top:2px;z-index:999999';
            document.body.appendChild(button);
            try {
                const result = await window.axe.run(button, {
                    runOnly: { type: 'rule', values: ['button-name'] }
                });
                return result.violations.some(v => v.id === 'button-name' &&
                    v.nodes.length > 0);
            } finally {
                button.remove();
            }
        }"""
    )
    if detected is not True:
        raise RuntimeError("axe failed the synthetic missing-name canary")


def run() -> dict:
    verify_isolation(os.environ)
    expected = pinned_axe_version()
    if not AXE_JS.is_file():
        raise FileNotFoundError("Install pinned frontend-app devDependencies via npm ci")
    apply_playwright_browser_env()
    from playwright.sync_api import sync_playwright

    server = AppServer(seed_fn=seed_library)
    report = {
        "format": "prks-axe-pilot-v1",
        "fixture": "disposable_appserver_synthetic_sqlite",
        "axe_version": expected,
        "engine": "chromium",
        "keyboard_checks": [],
        "surfaces": [],
    }
    stage = "server_start"
    try:
        server.start()
        stage = "browser_start"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = None
            try:
                stage = "app_open"
                page, context, collector = open_app_page(browser, server.origin)
                stage = "axe_inject"
                page.add_script_tag(path=str(AXE_JS))
                if page.evaluate("() => window.axe && window.axe.version") != expected:
                    raise RuntimeError("Unexpected axe version")
                verify_axe_canary(page)
                stage = "folders"
                page.locator("[data-prks-folder-library-view]").wait_for(
                    state="visible", timeout=15000,
                )
                report["surfaces"].append({
                    "surface": "folders", **axe_run(page, "body"),
                })
                stage = "work_pdf"
                page.evaluate(
                    "id => window.prksNavigate('#/works/' + id)", server.ids["work_a"],
                )
                page.locator(".work-detail").first.wait_for(
                    state="visible", timeout=20000,
                )
                _wait_pdf_viewer(page)
                report["surfaces"].append({
                    "surface": "work_pdf", **axe_run(page, ".work-detail"),
                })
                stage = "settings_modal"
                opener = page.locator("button.settings-btn")
                opener.focus()
                page.keyboard.press("Enter")
                page.locator("#settings-modal:not(.hidden)").wait_for(
                    state="visible", timeout=15000,
                )
                report["surfaces"].append({
                    "surface": "settings_modal", **axe_run(page, "#settings-modal"),
                })
                stage = "keyboard"
                page.keyboard.press("Escape")
                page.locator("#settings-modal").wait_for(
                    state="hidden", timeout=15000,
                )
                if not page.evaluate(
                    "() => !!document.activeElement && "
                    "document.activeElement.classList.contains('settings-btn')"
                ):
                    raise AssertionError("Settings did not restore keyboard focus")
                report["keyboard_checks"] = list(KEYBOARD_CHECKS)
                stage = "browser_errors"
                collector.assert_clean()
            finally:
                if context is not None:
                    context.close()
                browser.close()
    except Exception as exc:
        # Browser exception messages can contain URLs, titles and DOM details.
        raise RuntimeError(f"a11y pilot failed at {stage}: {type(exc).__name__}") from None
    finally:
        server.stop()
    verify_report(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8",
            )
        for surface in result["surfaces"]:
            rules = ",".join(v["rule"] for v in surface["violations"])
            print(f"{surface['surface']}: rules={surface['rules_checked']} "
                  f"violations={len(surface['violations'])} ids={rules or '-'}")
        return 0
    except Exception as exc:
        print(f"Accessibility pilot failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
