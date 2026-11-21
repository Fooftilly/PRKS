"""Static guards for the SonarCloud CI analysis workflow (#1373)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/sonarcloud.yml"
COVERAGE_INI = ROOT / "scripts/ci/sonar-coverage.ini"
SONAR_PROPERTIES = ROOT / "sonar-project.properties"


def _jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([a-z0-9-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2]))


class SonarCloudWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.jobs = _jobs(self.text)

    def test_token_reaches_only_the_scan_step(self):
        self.assertEqual(self.text.count("secrets."), 1)
        self.assertIn("SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}", self.jobs["scan"])
        for name, body in self.jobs.items():
            if name != "scan":
                self.assertNotIn("secrets.", body, name)

    def test_scan_job_restores_no_cache_and_runs_no_tests(self):
        scan = self.jobs["scan"]
        self.assertNotIn("cache:", scan)
        steps = scan.split("    steps:\n", 1)[1]
        for command in ("run_tests.py", "test-postgres", "vitest run", "npm "):
            self.assertNotIn(command, steps)

    def test_workflow_is_read_only_and_every_action_is_pinned(self):
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read\n")
        self.assertNotRegex(self.text, r"(?m)^ +permissions:")
        for ref in re.findall(r"uses: (\S+)", self.text):
            self.assertRegex(ref, r"@[0-9a-f]{40}$", ref)

    def test_coverage_jobs_skip_fork_pull_requests(self):
        for name in ("python-unit-coverage", "postgres-coverage", "vitest-coverage"):
            self.assertIn(
                "github.event.pull_request.head.repo.full_name == github.repository",
                self.jobs[name],
                name,
            )
        self.assertIn("needs: [python-unit-coverage, postgres-coverage, vitest-coverage]", self.jobs["scan"])

    def test_coverage_jobs_run_the_unchanged_test_gate_commands(self):
        self.assertRegex(self.jobs["python-unit-coverage"], r"(?m)^\s+python run_tests\.py$")
        self.assertRegex(self.jobs["postgres-coverage"], r"(?m)^\s+scripts/test-postgres$")

    def test_report_paths_match_the_files_the_scan_job_writes(self):
        props = SONAR_PROPERTIES.read_text(encoding="utf-8")
        self.assertIn("sonar.python.coverage.reportPaths=coverage.xml", props)
        self.assertIn("sonar.javascript.lcov.reportPaths=frontend-app/coverage/lcov.info", props)
        self.assertIn("-o coverage.xml", self.jobs["scan"])
        self.assertIn("frontend-app/coverage/lcov.info", self.jobs["scan"])

    def test_coverage_config_measures_backend_only_and_in_parallel(self):
        ini = COVERAGE_INI.read_text(encoding="utf-8")
        self.assertRegex(ini, r"(?m)^parallel = True$")
        self.assertRegex(ini, r"(?m)^branch = True$")
        self.assertRegex(ini, r"(?ms)^source =\n    backend\n    prks_app\n")
        # A root .coveragerc would change the bounded coverage pilots.
        self.assertFalse((ROOT / ".coveragerc").exists())


if __name__ == "__main__":
    unittest.main()
