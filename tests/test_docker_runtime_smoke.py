"""Regression tests for non-required Docker runtime CI smoke (#339)."""

from __future__ import annotations

import hashlib
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
        bundle = b";\n/* bundled source: /js/icons.js */\n" + b"window.prksReady = true;\n" * 20
        worker = (
            b"const CLASSIC_BUNDLE_REVISION = 'sample';\n"
            + b"self.addEventListener('install', () => {});\n" * 12
        )
        cases = (
            ("bundle", bundle),
            ("worker", worker),
        )
        for kind, complete in cases:
            expected = hashlib.sha256(complete).hexdigest()
            for content_type in ("text/javascript", "application/javascript; charset=utf-8"):
                with self.subTest(kind=kind, mime=content_type):
                    self.assertTrue(valid_browser_asset(complete, content_type, kind, expected))
            for label, raw, mime, digest in (
                ("html MIME", complete, "text/html", expected),
                ("fallback HTML", b"<!doctype html>" + complete, "text/javascript", expected),
                ("missing marker", b"window.prksReady = true;\n" * 20, "text/javascript", expected),
                ("empty", b"", "text/javascript", expected),
                ("short prefix", complete[:30], "text/javascript", expected),
                # Regression for review finding: a >128-byte valid-looking
                # prefix *past* the marker was previously accepted.
                ("truncated after marker", complete[:180], "text/javascript", expected),
                ("same-size corruption", complete[:-1] + b"X", "text/javascript", expected),
                ("invalid digest", complete, "text/javascript", "x" * 64),
                ("wrong digest", complete, "text/javascript", "0" * 64),
            ):
                with self.subTest(kind=kind, case=label):
                    self.assertFalse(valid_browser_asset(raw, mime, kind, digest))
        self.assertFalse(valid_browser_asset(bundle, "text/javascript", "unknown", hashlib.sha256(bundle).hexdigest()))
        self.assertFalse(valid_browser_asset(bundle.decode(), "text/javascript", "bundle", hashlib.sha256(bundle).hexdigest()))

    def test_browser_asset_cli_rejects_fallback_and_truncation_even_with_http_200(self):
        bundle = b";\n/* bundled source: /js/icons.js */\n" + b"window.prksReady = true;\n" * 20
        worker = (
            b"const CLASSIC_BUNDLE_REVISION = 'sample';\n"
            + b"self.addEventListener('install', () => {});\n" * 12
        )
        for kind, complete in (("bundle", bundle), ("worker", worker)):
            digest = hashlib.sha256(complete).hexdigest()
            for label, body, mime, result in (
                ("full", complete, "text/javascript", 0),
                ("wrong MIME", complete, "text/html", 1),
                ("SPA fallback", b"<html>" + complete, "text/javascript", 1),
                ("truncated after marker", complete[:180], "text/javascript", 1),
                ("modified same length", complete[:-1] + b"X", "text/javascript", 1),
            ):
                with self.subTest(kind=kind, case=label):
                    with io.TextIOWrapper(io.BytesIO(body), encoding="utf-8") as stdin:
                        with mock.patch("sys.stdin", stdin):
                            self.assertEqual(main(["--asset", kind, mime, digest]), result)
        self.assertEqual(main(["--asset", "unknown", "text/javascript", "0" * 64]), 2)

    def test_asset_validation_invocations_are_live_and_fail_closed(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        # Pin executable commands and their continuation arguments together:
        # checking only "--asset ..." would also accept a no-op in place
        # of the validator. The loop's command is indented two spaces deeper.
        for kind, indent in (("bundle", "            "), ("worker", "          ")):
            prefix = indent + "python scripts/ci/check_runtime_smoke.py \\" + "\n"
            invocation = (
                prefix
                + indent
                + f'  --asset {kind} "$mime" "$expected_sha" < "$asset"'
            )
            with self.subTest(kind=kind):
                self.assertEqual(source.count(invocation), 1)
                # A mutation which preserves all arguments but removes the
                # real validator must fail this exact invocation contract.
                without_validator = source.replace(
                    prefix, indent + ": \\" + "\n"
                )
                self.assertNotEqual(without_validator, source)
                self.assertNotIn(invocation, without_validator)

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
            'docker exec "$container" python -c',
            "hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()",
            "/app/frontend/js/${bundle}",
            "/app/frontend/sw.js",
            "http://127.0.0.1:18080/js/${bundle}",
            "http://127.0.0.1:18080/sw.js",
            '--asset bundle "$mime" "$expected_sha" < "$asset"',
            '--asset worker "$mime" "$expected_sha" < "$asset"',
            "trap cleanup EXIT",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)



if __name__ == "__main__":
    unittest.main()
