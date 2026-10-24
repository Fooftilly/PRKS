"""Synthetic, deterministic regression tests for focused changed-line coverage."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.changed_line_coverage import added_lines, changed_line_report, executable_lines

PYTHON_SOURCE = "backend/domain/text_identity.py"
TS_SOURCE = "frontend-app/src/workspace/tree.ts"


def _diff(path: str, first: int, count: int) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,0 +{first},{count} @@\n"
        + "".join("+line\n" for _ in range(count))
    )


class ChangedLineCoverageTests(unittest.TestCase):
    def test_zero_context_hunks_multiple_files_and_deletions(self):
        patch = (
            _diff(PYTHON_SOURCE, 8, 3)
            + f"@@ -16 +19 @@\n+replacement\n"
            + f"diff --git a/docs/readme.md b/docs/readme.md\n"
            + "--- a/docs/readme.md\n+++ b/docs/readme.md\n"
            + "@@ -10 +10,0 @@\n-deleted\n"
            + _diff(TS_SOURCE, 30, 1)
        )
        self.assertEqual(
            added_lines(patch),
            {PYTHON_SOURCE: {8, 9, 10, 19}, TS_SOURCE: {30}},
        )

    def test_python_ignores_nonexecutables_and_reports_partial_coverage(self):
        patch = _diff(PYTHON_SOURCE, 8, 4)
        report = {
            "files": {
                PYTHON_SOURCE: {
                    "executed_lines": [8, 14],
                    "missing_lines": [10, 15],
                }
            }
        }
        result = changed_line_report(patch, report, "coveragepy", [PYTHON_SOURCE])
        self.assertIn("1/2 (50%)", result)
        self.assertIn("excludes comments", result)

    def test_istanbul_report_uses_line_hits_and_path_suffix(self):
        patch = _diff(TS_SOURCE, 30, 4)
        report = {
            "/runner/work/prks/" + TS_SOURCE: {
                "l": {"30": 5, "31": 0, "33": 2, "40": 0},
            },
            "total": {"lines": {}},
        }
        result = changed_line_report(patch, report, "istanbul", [TS_SOURCE])
        self.assertIn("2/3 (67%)", result)

    def test_vitest_actual_coverage_final_statement_map_without_line_hits(self):
        # Istanbul's JSON reporter usually supplies statementMap + s, not l.
        patch = _diff(TS_SOURCE, 30, 4)
        report = {
            "/runner/prks/" + TS_SOURCE: {
                "statementMap": {
                    "0": {"start": {"line": 30, "column": 0}, "end": {"line": 30, "column": 8}},
                    "1": {"start": {"line": 31, "column": 0}, "end": {"line": 31, "column": 8}},
                    "2": {"start": {"line": 31, "column": 9}, "end": {"line": 31, "column": 20}},
                    "3": {"start": {"line": 33, "column": 0}, "end": {"line": 33, "column": 8}},
                },
                "s": {"0": 4, "1": 0, "2": 3, "3": 0},
            }
        }
        result = changed_line_report(patch, report, "istanbul", [TS_SOURCE])
        self.assertIn("2/3 (67%)", result)

    def test_missing_istanbul_statement_hits_are_unavailable(self):
        incomplete = {
            "statementMap": {
                "0": {"start": {"line": 30, "column": 0}, "end": {"line": 30, "column": 3}},
            },
            "s": {},
        }
        self.assertIsNone(executable_lines(incomplete, "istanbul"))

    def test_unchanged_pilot_sources_do_not_claim_zero_coverage(self):
        report = {"files": {}}
        result = changed_line_report(_diff("docs/note.md", 1, 1), report, "coveragepy", [PYTHON_SOURCE])
        self.assertIn("No added executable lines in pilot scope", result)
        self.assertNotIn("(100%)", result)

    def test_missing_changed_file_coverage_is_explicitly_unavailable(self):
        result = changed_line_report(
            _diff(PYTHON_SOURCE, 2, 1), {"files": {}}, "coveragepy", [PYTHON_SOURCE]
        )
        self.assertIn("Unavailable for some changed pilot files", result)
        self.assertNotIn("0/1", result)

    def test_coverage_without_line_level_detail_is_unavailable(self):
        self.assertIsNone(executable_lines({"covered_lines": 1}, "coveragepy"))
        self.assertIsNone(executable_lines({"lines": {"pct": 100}}, "istanbul"))

    def test_ambiguous_coverage_paths_raise(self):
        report = {
            "files": {
                "/runner/one/" + PYTHON_SOURCE: {"executed_lines": [], "missing_lines": [2]},
                "/runner/two/" + PYTHON_SOURCE: {"executed_lines": [], "missing_lines": [2]},
            }
        }
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            changed_line_report(
                _diff(PYTHON_SOURCE, 2, 1), report, "coveragepy", [PYTHON_SOURCE]
            )


if __name__ == "__main__":
    unittest.main()
