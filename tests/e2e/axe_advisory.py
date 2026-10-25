"""Fixture-only axe-core accessibility diagnostics for the existing Python Playwright E2E harness.

Manual/advisory: it reports accessibility debt but does not turn known issues into
required CI failures. No page HTML, accessible names, research text or network
requests are sent to the report.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AXE_JS = REPO / "frontend-app" / "node_modules" / "axe-core" / "axe.min.js"
AXE_PIN = "4.13.0"
MAX_RULES = 18
MAX_TARGETS = 2
MAX_SELECTOR = 100
VALID_RULE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
VALID_IMPACT = frozenset(("critical", "serious", "moderate", "minor", "unknown"))
# Simple public selectors only. Reject attribute values, URLs and arbitrary
# content-bearing strings rather than publishing synthetic library text.
VALID_SELECTOR = re.compile(r"[A-Za-z0-9#.>_:\[\] -]{1,100}\Z")

AXE_QUERY = """async (selector) => {
    const root = document.querySelector(selector);
    if (!root) throw new Error('Missing axe target surface');
    if (!window.axe || typeof window.axe.run !== 'function') {
        throw new Error('axe-core engine was not injected');
    }
    const data = await window.axe.run(root, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    });
    return {
        engine: window.axe.version,
        passes: data.passes.length,
        incomplete: data.incomplete.length,
        violations: data.violations.map(item => ({
            id: item.id, impact: item.impact || 'unknown',
            count: item.nodes.length,
            selectors: item.nodes.slice(0, 2).map(node => (node.target || [])[0] || ''),
        })),
    };
}"""


def sanitized_report(label: str, raw: dict) -> dict:
    """Allowlist serializable scanner metadata, excluding snippets and titles."""
    if raw.get("engine") != AXE_PIN:
        raise AssertionError("axe-core is missing or differs from the lockfile pin")
    if not isinstance(raw.get("passes"), int) or not isinstance(raw.get("incomplete"), int):
        raise AssertionError("missing axe rule-evaluation counts")
    rules = raw.get("violations")
    if not isinstance(rules, list):
        raise AssertionError("axe did not return a violations list")
    if raw["passes"] + raw["incomplete"] + len(rules) == 0:
        raise AssertionError("axe did not execute any accessibility rule")
    known_surfaces = {"shell", "vue-folders", "work-detail", "pdf-viewer", "settings-dialog"}
    if label not in known_surfaces:
        raise AssertionError("unrecognized synthetic accessibility surface")
    violations = []
    for value in rules:
        rule_id = value.get("id")
        if not isinstance(rule_id, str) or not VALID_RULE.fullmatch(rule_id):
            raise AssertionError("unexpected axe rule identifier")
        impact = value.get("impact")
        if impact not in VALID_IMPACT:
            impact = "unknown"
        count = value.get("count")
        if not isinstance(count, int) or count < 0:
            raise AssertionError("invalid axe node count")
        selectors = value.get("selectors")
        if not isinstance(selectors, list):
            raise AssertionError("invalid axe target collection")
        violations.append({
            "rule": rule_id,
            "impact": impact,
            "nodes": count,
            "sample_targets": [
                target for target in selectors[:MAX_TARGETS]
                if isinstance(target, str) and VALID_SELECTOR.fullmatch(target)
                and len(target) <= MAX_SELECTOR
            ],
        })
    violations.sort(key=lambda v: (v["rule"], v["impact"]))
    return {
        "surface": label,
        "axe": AXE_PIN,
        "rules_passed": raw["passes"],
        "rules_incomplete": raw["incomplete"],
        "violating_rules": len(violations),
        "violations": violations[:MAX_RULES],
        "truncated_rules": max(0, len(violations) - MAX_RULES),
    }


def scan(page, selector: str, label: str) -> dict:
    """Perform an actual axe-core scan on an app DOM subtree."""
    if not AXE_JS.is_file():
        raise RuntimeError(
            "Missing pinned axe-core JS. Run npm ci --ignore-scripts in frontend-app first."
        )
    if not page.evaluate("() => !!(window.axe && window.axe.run)"):
        page.add_script_tag(path=str(AXE_JS))
    return sanitized_report(label, page.evaluate(AXE_QUERY, selector))


def write_report(surfaces: list[dict], path: Path) -> None:
    if len(surfaces) != 5 or {s["surface"] for s in surfaces} != {
        "shell", "vue-folders", "work-detail", "pdf-viewer", "settings-dialog"
    }:
        raise AssertionError("accessibility smoke missing a required surface")
    report = {"format": "prks-synthetic-axe-smoke-v1", "advisory": True, "surfaces": surfaces}
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def short_summary(surfaces: list[dict]) -> str:
    return "\n".join(
        f"{s['surface']}: {s['violating_rules']} violating rules, "
        f"{sum(v['nodes'] for v in s['violations'])} sampled rule-node matches, "
        f"{s['rules_incomplete']} incomplete checks"
        for s in surfaces
    )
