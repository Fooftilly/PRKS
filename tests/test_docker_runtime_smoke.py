"""Regression tests for non-required Docker runtime CI smoke (#339)."""

from __future__ import annotations

import hashlib
import io
import re
import unittest
from pathlib import Path
from unittest import mock

from scripts.ci.check_runtime_smoke import (
    main, valid_browser_asset, valid_initial_works_catalog, valid_vue_entry_html,
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
    "frontend/index.html",
    "frontend/sw.js",
    "frontend/vue/prks-vue.js",
    "scripts/build_classic_distribution.py",
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

    def test_vue_entry_requires_exact_served_bytes(self):
        # Vite has no classic-bundle marker; byte equality against the image
        # artifact proves the HTTP server returned the intended module.
        vue = b"const vueApp = { mounted: true };\n" * 20
        sha = hashlib.sha256(vue).hexdigest()
        self.assertTrue(valid_browser_asset(vue, "text/javascript; charset=utf-8", "vue", sha))
        for label, raw, mime in (
            ("HTML fallback", b"<!doctype html><script>not Vue</script>", "text/javascript"),
            ("HTML content type", vue, "text/html"),
            ("truncated", vue[:180], "text/javascript"),
            ("same-size corruption", vue[:-1] + b"X", "text/javascript"),
        ):
            with self.subTest(case=label):
                self.assertFalse(valid_browser_asset(raw, mime, "vue", sha))
        for body, mime, code in (
            (vue, "application/javascript", 0),
            (vue[:180], "application/javascript", 1),
            (b"<html>" + vue, "text/html", 1),
        ):
            with self.subTest(cli_mime=mime, length=len(body)):
                with io.TextIOWrapper(io.BytesIO(body), encoding="utf-8") as stdin:
                    with mock.patch("sys.stdin", stdin):
                        self.assertEqual(main(["--asset", "vue", mime, sha]), code)

    def test_served_html_requires_active_vue_module_not_comment_or_inert_markup(self):
        tag = '<script type="module" src="/vue/prks-vue.js"></script>'
        html = f"<!doctype html><html><body>{tag}</body></html>"
        self.assertTrue(valid_vue_entry_html(html))
        # The original reviewer finding: string search accepts the comment
        # even though a browser would not load the Vue application.
        for label, body in (
            ("commented-out script", f"<!-- {tag} -->"),
            ("commented script in actual page", None),
            ("template", f"<template>{tag}</template>"),
            ("noscript", f"<noscript>{tag}</noscript>"),
            ("textarea", f"<textarea>{tag}</textarea>"),
            ("style", f"<style>{tag}</style>"),
            ("wrong type", '<script type="text/plain" src="/vue/prks-vue.js"></script>'),
            ("wrong source", '<script type="module" src="/vue/other.js"></script>'),
            ("duplicate script", tag + tag),
            ("self-closing script", tag.replace("></script>", "/>")),
            ("invalid integrity", tag.replace(
                "></script>", ' integrity="sha384-invalid"></script>'
            )),
            ("self-closing template before script", "<template/>" + tag),
            ("missing closing tag", tag.replace("</script>", "")),
            ("duplicate type attributes", '<script type="text/plain" type="module" src="/vue/prks-vue.js"></script>'),
        ):
            if body is None:
                # Exercise the exact reviewed HTML mutation against the real
                # source page, including all its other scripts and markup.
                original = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
                self.assertEqual(original.count(tag), 1)
                body = original.replace(tag, f"<!-- {tag} -->")
            with self.subTest(case=label):
                self.assertFalse(valid_vue_entry_html(body))
        source_html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertTrue(valid_vue_entry_html(source_html))
        self.assertEqual(source_html.count(tag), 1)
        for broken_entry in (
            tag.replace("></script>", "/>"),
            tag.replace("></script>", ' integrity="sha384-invalid"></script>'),
        ):
            # A real release asset with the mutated entry must fail, not
            # merely a synthetic string containing one tag.
            self.assertFalse(valid_vue_entry_html(source_html.replace(tag, broken_entry)))
        # Attribute order and quoting are not executable semantics.
        self.assertTrue(valid_vue_entry_html(
            "<script src='/vue/prks-vue.js' type='MODULE'></script>"
        ))
        self.assertTrue(valid_vue_entry_html(f"<!-- {tag} -->" + html))
        self.assertFalse(valid_vue_entry_html("x" * (256 * 1024 + 1)))

    def test_html_cli_fails_closed_when_vue_script_is_commented_out(self):
        tag = '<script type="module" src="/vue/prks-vue.js"></script>'
        for body, expected in (
            (f"<html><body>{tag}</body></html>", 0),
            (f"<!-- {tag} -->", 1),
            (f"<template>{tag}</template>", 1),
            (tag.replace("></script>", "/>"), 1),
            (tag.replace("></script>", ' integrity="sha256-wrong"></script>'), 1),
            ("<html><body>no Vue module</body></html>", 1),
            ("x" * (256 * 1024 + 1), 1),
        ):
            with self.subTest(body=body[:60]):
                with mock.patch("sys.stdin", io.StringIO(body)):
                    self.assertEqual(main(["--html"]), expected)

    def test_asset_validation_invocations_are_live_and_fail_closed(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        # Pin executable commands and their continuation arguments together:
        # checking only "--asset ..." would also accept a no-op in place
        # of the validator. The loop's command is indented two spaces deeper.
        for kind, indent in (("bundle", "            "), ("worker", "          "), ("vue", "          ")):
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

    def test_html_validation_invocation_cannot_revert_to_text_grep(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        invocation = '          python scripts/ci/check_runtime_smoke.py --html < "$html"'
        self.assertEqual(source.count(invocation), 1)
        # Regression for Devin's finding: a grep substring check would pass
        # when the module tag is inside an HTML comment.
        self.assertNotIn('grep -Fq', source)
        without_validator = source.replace(invocation, '          : < "$html"')
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
            # PostgreSQL is the only backend (PG-C2 #641): a private database,
            # an operator's first-run setup and a signed-in catalogue read.
            '--env PRKS_DATABASE_URL="postgresql://prks:${db_password}@db:5432/prks"',
            "--tmpfs /var/lib/postgresql:rw,size=256m",
            'docker network rm "$network"',
            '"${admin[@]}" issue-token --root /data',
            '"${admin[@]}" complete --root /data --login owner --password-file /tmp/owner-password',
            "http://127.0.0.1:18080/readyz",
            "http://127.0.0.1:18080/api/login",
            '--cookie "$jar"',
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
            "http://127.0.0.1:18080/vue/prks-vue.js",
            "/app/frontend/vue/prks-vue.js",
            'python scripts/ci/check_runtime_smoke.py --html < "$html"',
            '--asset vue "$mime" "$expected_sha" < "$asset"',
            '--asset bundle "$mime" "$expected_sha" < "$asset"',
            '--asset worker "$mime" "$expected_sha" < "$asset"',
            "trap cleanup EXIT",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)



if __name__ == "__main__":
    unittest.main()
