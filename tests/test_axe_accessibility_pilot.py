"""Contract checks for the isolated, nonblocking axe-core accessibility pilot."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from scripts.ci.axe_accessibility_pilot import (
    KEYBOARD_CHECKS, SURFACES, axe_run, pinned_axe_version,
    safe_findings, verify_axe_canary, verify_isolation, verify_report,
)


def synthetic_report():
    return {
        "format": "prks-axe-pilot-v1",
        "fixture": "disposable_appserver_synthetic_sqlite",
        "axe_version": pinned_axe_version(),
        "engine": "chromium",
        "keyboard_checks": list(KEYBOARD_CHECKS),
        "surfaces": [
            {"surface": surface, "rules_checked": 15, "incomplete": 0,
             "violations": []}
            for surface in SURFACES
        ],
    }


class AxePilotContracts(unittest.TestCase):
    def test_never_inherits_real_database_or_auth(self):
        verify_isolation({})
        for name in (
            "PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_DATABASE_URL_FILE",
            "PRKS_TEST_DATABASE_URL",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_isolation({name: "private"})

    def test_axe_is_explicitly_version_pinned(self):
        self.assertRegex(pinned_axe_version(), r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_requires_complete_fixture_only_evidence(self):
        verify_report(synthetic_report())
        for mutation in ("missing_surface", "missing_keyboard", "no_rules",
                         "wrong_engine", "wrong_fixture", "wrong_axe", "incomplete"):
            report = synthetic_report()
            if mutation == "missing_surface":
                report["surfaces"].pop()
            elif mutation == "missing_keyboard":
                report["keyboard_checks"].clear()
            elif mutation == "no_rules":
                report["surfaces"][0]["rules_checked"] = 0
            elif mutation == "wrong_engine":
                report["engine"] = "webkit"
            elif mutation == "wrong_fixture":
                report["fixture"] = "real-library"
            elif mutation == "wrong_axe":
                report["axe_version"] = "0.0.0"
            else:
                report["surfaces"][0]["incomplete"] = -1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_report(report)

    def test_rule_summary_contains_no_dom_selectors_html_or_text(self):
        findings = safe_findings([
            {
                "id": "button-name", "impact": "critical",
                "nodes": [
                    {"target": ["#private-note"], "html": "<button>secret</button>"},
                    {"target": ["#other-private-note"], "failureSummary": "secret"},
                ],
            },
            {"id": "aria-required-attr", "impact": "serious",
             "nodes": [{"target": [".sensitive"]}]},
        ])
        self.assertEqual(findings, [
            {"rule": "aria-required-attr", "impact": "serious", "nodes": 1},
            {"rule": "button-name", "impact": "critical", "nodes": 2},
        ])
        self.assertNotIn("private", repr(findings))
        self.assertNotIn("secret", repr(findings))
        with self.assertRaises(ValueError):
            safe_findings([{"id": "selector#private", "impact": "serious",
                            "nodes": [1]}])
        with self.assertRaises(ValueError):
            safe_findings([{"id": "button-name", "nodes": []}])

    def test_missing_axe_execution_never_passes(self):
        page = Mock()
        page.evaluate.return_value = None
        with self.assertRaises(RuntimeError):
            axe_run(page, "body")
        page.evaluate.return_value = {
            "rules_checked": 0, "incomplete": 0, "violations": [],
        }
        with self.assertRaises(RuntimeError):
            axe_run(page, "body")

    def test_canary_must_prove_a_real_missing_name_violation(self):
        page = Mock()
        page.evaluate.return_value = False
        with self.assertRaises(RuntimeError):
            verify_axe_canary(page)
        page.evaluate.return_value = True
        verify_axe_canary(page)


if __name__ == "__main__":
    unittest.main()
