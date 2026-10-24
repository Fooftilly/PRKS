"""Regression tests for non-required Docker runtime CI smoke (#339)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.ci.check_runtime_smoke import main, valid_initial_works_catalog

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/docker-runtime-smoke.yml"


class DockerRuntimeSmokeTests(unittest.TestCase):
    def test_response_parser_only_accepts_empty_json_list(self):
        for valid in ("[]", " [ ]\\n"):
            with self.subTest(valid=valid):
                self.assertTrue(valid_initial_works_catalog(valid))
        for invalid in (
            "",
            "OK",
            "null",
            "false",
            "{}",
            '{"status":"ok"}',
            '{"error":"unavailable"}',
            '[{"id":1}]',
            "<html>ok</html>",
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(valid_initial_works_catalog(invalid))

    def test_response_cli_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.json"
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(main([str(path)]), 0)
            path.write_text('{"error": "database not ready"}', encoding="utf-8")
            self.assertEqual(main([str(path)]), 1)
            self.assertEqual(main([str(path) + "-missing"]), 1)
            self.assertEqual(main([]), 2)

    def test_workflow_is_nonrequired_and_narrowly_triggered(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  pull_request:\\n    paths:\\n", source)
        self.assertIn("  push:\\n    branches:\\n      - master\\n    paths:\\n", source)
        self.assertIn("  workflow_dispatch:", source)
        self.assertIn("      - requirements.txt", source)
        self.assertIn("      - Dockerfile", source)
        self.assertIn("      - dependency-inventory.json", source)
        self.assertNotIn("backend/**", source)
        self.assertNotIn("paths-ignore:", source)

    def test_runs_actual_image_on_isolated_loopback_and_checks_api(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "docker build --tag prks-ci-runtime-smoke:latest .",
            "docker run -d --name",
            "--publish 127.0.0.1:18080:8080",
            "--tmpfs /data:rw,size=128m",
            "--env PRKS_STORAGE=/data",
            "http://127.0.0.1:18080/api/works",
            'python scripts/ci/check_runtime_smoke.py "$response"',
            "http://127.0.0.1:18080/index.html",
            "prks-classic-head.bundle.js",
            "prks-classic-body.bundle.js",
            'docker logs --tail 120 "$container"',
            'docker rm -f "$container"',
            "trap cleanup EXIT",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
