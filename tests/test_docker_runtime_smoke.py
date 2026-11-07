"""Regression tests for non-required Docker runtime CI smoke (#339)."""

from __future__ import annotations

import io
import re
import unittest
from pathlib import Path
from unittest import mock

from scripts.ci.check_runtime_smoke import (
    main, valid_browser_asset, valid_initial_works_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/docker-runtime-smoke.yml"


EXPECTED_TRIGGER_PATHS = (
    "Dockerfile",
    "docker-entrypoint.sh",
    "docker-compose.yml",
    "requirements.txt",
    "dependency-inventory.json",
    "backend/dependency_gate.py",
    "backend/storage/config.py",
    "backend/storage/resolver.py",
    "backend/storage/root_binding.py",
    "prks_app.py",
    "scripts/ci/check_runtime_smoke.py",
    "tests/test_docker_runtime_smoke.py",
    ".github/workflows/docker-runtime-smoke.yml",
)


def event_paths(source: str, event: str) -> tuple[str, ...]:
    head = (
        "  pull_request:\n    paths:\n"
        if event == "pull_request"
        else "  push:\n    branches:\n      - master\n    paths:\n"
    )
    match = re.search(
        re.escape(head) + r"((?:      - [^\n]+\n)+)", source
    )
    if match is None:
        raise AssertionError("Missing narrow paths on " + event)
    return tuple(line.strip()[2:] for line in match.group(1).splitlines())


class DockerRuntimeSmokeTests(unittest.TestCase):
    def test_response_parser_only_accepts_empty_json_list(self):
        for valid in ("[]", " [ ]\n"):
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
        for body, expected in (
            ("[]", 0),
            ('{"error": "database not ready"}', 1),
            ("<html>not an API</html>", 1),
            ("x" * 65537, 1),
        ):
            with self.subTest(body=body[:40]):
                with mock.patch("sys.stdin", io.StringIO(body)):
                    self.assertEqual(main([]), expected)
        # Caller-supplied file paths and other arguments are not supported.
        self.assertEqual(main(["/etc/passwd"]), 2)

    def test_served_browser_assets_reject_html_fallback_and_wrong_payloads(self):
        bundle = ";\n/* bundled source: /js/icons.js */\n" + "window.prksReady = true;\n" * 20
        worker = "const CLASSIC_BUNDLE_REVISION = 'sample';\n" + "self.addEventListener('install', () => {});\n" * 12
        for content_type in ("text/javascript", "application/javascript; charset=utf-8"):
            with self.subTest(mime=content_type):
                self.assertTrue(valid_browser_asset(bundle, content_type, "bundle"))
                self.assertTrue(valid_browser_asset(worker, content_type, "worker"))
        for kind, mime, raw in (
            ("bundle", "text/html", bundle),  # fallback MIME, even when text looks like JS
            ("bundle", "text/javascript", "<!doctype html>" + bundle),
            ("bundle", "text/javascript", "<html><script>/* bundled source: /js/a.js */</script></html>"),
            ("bundle", "text/javascript", "window.prksReady = true;" * 20),
            ("bundle", "text/javascript", ""),
            ("bundle", "text/javascript", bundle[:30]),  # truncated asset
            ("worker", "text/html", worker),
            ("worker", "text/javascript", "<html>" + worker),
            ("worker", "text/javascript", "self.addEventListener('install', () => {});" * 12),
            ("worker", "text/javascript", ""),
            ("nonsupported", "text/javascript", bundle),
        ):
            with self.subTest(kind=kind, mime=mime, sample=raw[:30]):
                self.assertFalse(valid_browser_asset(raw, mime, kind))

    def test_browser_asset_cli_rejects_fallback_even_if_http_status_was_200(self):
        bundle = ";\n/* bundled source: /js/icons.js */\n" + "window.prksReady = true;\n" * 20
        for body, kind, mime, result in (
            (bundle, "bundle", "text/javascript", 0),
            (bundle, "bundle", "text/html", 1),
            ("<html>" + bundle, "bundle", "text/javascript", 1),
            ("empty", "worker", "text/javascript", 1),
        ):
            with self.subTest(kind=kind, mime=mime, result=result):
                with mock.patch("sys.stdin", io.StringIO(body)):
                    self.assertEqual(main(["--asset", kind, mime]), result)
        self.assertEqual(main(["--asset", "unknown", "text/javascript"]), 2)

    def test_workflow_is_nonrequired_and_narrowly_triggered(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        for event in ("pull_request", "push"):
            with self.subTest(event=event):
                self.assertEqual(event_paths(source, event), EXPECTED_TRIGGER_PATHS)
        self.assertIn("  workflow_dispatch:", source)
        self.assertNotIn("paths-ignore:", source)

    def test_runs_actual_image_on_isolated_loopback_and_checks_api(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "docker build --tag prks-ci-runtime-smoke:latest .",
            "docker run -d --name",
            "--publish 127.0.0.1:18080:8080",
            "--user 1000:1000",
            "--tmpfs /data:rw,size=128m,uid=1000,gid=1000",
            "--env PRKS_STORAGE=/data --env PRKS_PDF_LINEARIZE=1",
            "http://127.0.0.1:18080/api/works",
            "--write-out '%{http_code}' --output \"$response\"",
            '&& [[ "$status" == "200" ]]; then',
            'python scripts/ci/check_runtime_smoke.py < "$response"',
            "http://127.0.0.1:18080/index.html",
            "prks-classic-head.bundle.js",
            "prks-classic-body.bundle.js",
            'docker logs --tail 120 "$container"',
            'docker rm -f "$container"',
            'rm -f "$response" "$html" "$asset"',
            "for bundle in prks-classic-head.bundle.js prks-classic-body.bundle.js; do",
            "http://127.0.0.1:18080/js/${bundle}",
            "http://127.0.0.1:18080/sw.js",
            'python scripts/ci/check_runtime_smoke.py --asset bundle "$mime" < "$asset"',
            'python scripts/ci/check_runtime_smoke.py --asset worker "$mime" < "$asset"',
            "trap cleanup EXIT",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
