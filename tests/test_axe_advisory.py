"""Pure contracts for the opt-in synthetic axe-core E2E report."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.e2e.axe_advisory import AXE_PIN, sanitized_report, short_summary, write_report


def raw_engine(**extras) -> dict:
    result = {
        "engine": AXE_PIN,
        "passes": 18,
        "incomplete": 2,
        "violations": [
            {
                "id": "button-name",
                "impact": "critical",
                "count": 3,
                "selectors": ["#button-1", 'button[href="fixture-private"]', "<svg>"],
                "html": "document sample must not be retained",
            }
        ],
        "pageHtml": "PRIVATE RESEARCH TEXT",
    }
    result.update(extras)
    return result


class AxeAdvisoryReportTests(unittest.TestCase):
    def test_only_public_bounded_rule_metadata_survives(self):
        report = sanitized_report("shell", raw_engine())
        encoded = json.dumps(report)
        self.assertEqual(report["axe"], AXE_PIN)
        self.assertEqual(report["violating_rules"], 1)
        self.assertEqual(report["violations"][0]["sample_targets"], ["#button-1"])
        for forbidden in (
            "PRIVATE RESEARCH TEXT", "fixture-private", "<svg>",
            "pageHtml", "html", "document sample must not be retained",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_sanitized_selector_count_and_nth_child_are_preserved(self):
        raw = raw_engine(violations=[{
            "id": "aria-required-attr",
            "impact": "serious",
            "count": 2,
            "selectors": ["#sidebar > button:nth-child(2)", 'button[title="fixture-path"]'],
        }])
        info = sanitized_report("shell", raw)["violations"][0]
        self.assertEqual(info["sample_targets"], ["#sidebar > button:nth-child(2)"])
        self.assertEqual(info["suppressed_targets"], 1)

    def test_critical_findings_are_not_truncated_by_minor_rules(self):
        examples = [
            {"id": f"minor-{i:02d}", "impact": "minor", "count": 1, "selectors": []}
            for i in range(25)
        ]
        examples.append({
            "id": "zz-critical", "impact": "critical", "count": 1, "selectors": []
        })
        result = sanitized_report("shell", raw_engine(violations=examples))
        self.assertEqual(result["violating_rules"], 26)
        self.assertEqual(result["violations"][0]["rule"], "zz-critical")
        self.assertEqual(result["truncated_rules"], 8)

    def test_no_engine_or_no_rules_is_not_a_success(self):
        with self.assertRaisesRegex(AssertionError, "axe-core"):
            sanitized_report("shell", raw_engine(engine="0.0.0"))
        with self.assertRaisesRegex(AssertionError, "did not execute"):
            sanitized_report("shell", raw_engine(passes=0, incomplete=0, violations=[]))

    def test_unknown_label_rule_or_bad_nodes_fail_closed(self):
        with self.assertRaisesRegex(AssertionError, "surface"):
            sanitized_report("private-library", raw_engine())
        with self.assertRaisesRegex(AssertionError, "identifier"):
            sanitized_report("shell", raw_engine(violations=[{"id": "<text>"}]))
        with self.assertRaisesRegex(AssertionError, "node count"):
            sanitized_report("shell", raw_engine(violations=[{
                "id": "image-alt", "impact": "moderate", "count": -1, "selectors": []
            }]))

    def test_rule_list_bounded_and_summary_counts_only(self):
        violations = [{
            "id": f"rule-{i}", "impact": "serious", "count": 1, "selectors": ["#foo"]
        } for i in range(30)]
        report = sanitized_report("vue-folders", raw_engine(violations=violations))
        self.assertEqual(report["violating_rules"], 30)
        self.assertEqual(report["truncated_rules"], 12)
        self.assertEqual(len(report["violations"]), 18)
        self.assertIn("vue-folders: 30 violating rules", short_summary([report]))

    def test_write_report_requires_exact_five_fixture_surfaces(self):
        labels = ("shell", "vue-folders", "work-detail", "pdf-viewer", "settings-dialog")
        reports = [sanitized_report(label, raw_engine()) for label in labels]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "axe.json"
            write_report(reports, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["format"], "prks-synthetic-axe-smoke-v1")
            self.assertTrue(data["advisory"])
            self.assertEqual(len(data["surfaces"]), 5)
            with self.assertRaisesRegex(AssertionError, "required surface"):
                write_report(reports[:-1], path)


if __name__ == "__main__":
    unittest.main()
