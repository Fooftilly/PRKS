"""Pure safeguards for the passive-only ZAP pilot (#860)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from scripts.ci.zap_passive_pilot import (
    INHERITED_REFUSED, ROOT, STEPS, ZAP_IMAGE, exchanged, require_loopback, run, summarize_alerts,
    summary_path, zap_command,
)


class ZapPassivePilotSafeguards(unittest.TestCase):
    def test_only_its_own_loopback_server_is_a_target(self):
        self.assertEqual(require_loopback("http://127.0.0.1:50271"), 50271)
        for origin in ("http://localhost:50271", "https://127.0.0.1:50271", "http://127.0.0.1",
                       "http://127.0.0.1:50271/api", "http://192.168.1.4:8080"):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "loopback"):
                require_loopback(origin)

    def test_zap_is_pinned_bound_to_loopback_and_silent(self):
        args = zap_command("prks-zap-test", 8090, "key")
        self.assertIn("@sha256:", ZAP_IMAGE)
        self.assertIn(ZAP_IMAGE, args)
        self.assertEqual(args[args.index("-host") + 1], "127.0.0.1")
        self.assertIn("-daemon", args)
        self.assertIn("-silent", args)  # no add-on or update checks
        self.assertIn("api.addrs.addr.name=127.0.0.1", args)
        for forbidden in ("-quickurl", "-cmd", "spider", "ascan", "zap-baseline.py", "zap-api-scan.py", "zap-full-scan.py"):
            self.assertNotIn(forbidden, " ".join(args))

    def test_the_allowlist_is_exactly_these_requests(self):
        self.assertEqual(
            [(s.caller, s.method, s.template, s.expect, s.login, s.wrong_password) for s in STEPS],
            [
                ("anonymous", "GET", "/", 200, None, False),
                ("anonymous", "GET", "/healthz", 200, None, False),
                ("anonymous", "GET", "/api/session", 401, None, False),
                ("anonymous", "GET", "/api/works", 401, None, False),
                ("anonymous", "GET", "/api/works/{work}", 401, None, False),
                ("anonymous", "POST", "/api/login", 401, "owner", True),
                ("owner", "POST", "/api/login", 200, "owner", False),
                ("owner", "GET", "/", 200, None, False),
                ("owner", "GET", "/api/session", 200, None, False),
                ("owner", "GET", "/api/works", 200, None, False),
                ("owner", "GET", "/api/works/{work}", 200, None, False),
                ("owner", "GET", "/api/works/{work}/annotations", 200, None, False),
                ("owner", "GET", "/api/tags", 200, None, False),
                ("owner", "GET", "/api/persons", 200, None, False),
                ("owner", "GET", "/api/publishers", 200, None, False),
                ("owner", "GET", "/api/search?q=Graph", 200, None, False),
                ("owner", "GET", "/api/settings", 200, None, False),
                ("owner", "GET", "/api/research-graph", 200, None, False),
                ("owner", "GET", "/api/works/unknown-work", 404, None, False),
                ("editor", "POST", "/api/login", 200, "editor", False),
                ("editor", "GET", "/api/works", 200, None, False),
                ("editor", "GET", "/api/backups/download", 403, None, False),
            ],
        )

    def test_a_real_servers_settings_are_refused(self):
        self.assertEqual(INHERITED_REFUSED, ("PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_PUBLIC_ORIGIN"))
        for name in INHERITED_REFUSED:
            env = {"PRKS_TEST_DATABASE_URL": "postgresql://postgres@127.0.0.1:1/postgres", name: "x"}
            with self.subTest(name=name), mock.patch.dict(os.environ, env), \
                    self.assertRaisesRegex(RuntimeError, "refusing an inherited"):
                run(None)

    def test_summary_paths_stay_inside_the_working_directory(self):
        root = Path.cwd().resolve()
        self.assertEqual(summary_path(Path("zap-summary.json")), root / "zap-summary.json")
        for bad in ("../outside.json", "/etc/passwd", "summary.txt"):
            with self.subTest(path=bad), self.assertRaisesRegex(ValueError, "inside the working directory"):
                summary_path(Path(bad))

    def test_history_counts_only_messages_that_were_sent(self):
        messages = [
            {"requestHeader": "GET http://127.0.0.1:1/api/works HTTP/1.1\r\nHost: x", "responseHeader": "HTTP/1.0 200 OK\r\n"},
            {"requestHeader": "GET http://127.0.0.1:1/api HTTP/1.1\r\n", "responseHeader": "HTTP/1.0 0\r\n"},
            {"requestHeader": "GET http://127.0.0.1:1/ HTTP/1.1\r\n", "responseHeader": ""},
        ]
        self.assertEqual(exchanged(messages), [("GET", "http://127.0.0.1:1/api/works")])

    def test_alert_summary_keeps_no_urls_evidence_or_parameters(self):
        alerts = [
            {"pluginId": "10038", "alert": "CSP Header Not Set", "risk": "Medium", "confidence": "High",
             "url": "http://127.0.0.1:1/api/works/W-SECRET", "evidence": "secret", "param": "q", "other": "x"},
            {"pluginId": "10038", "alert": "CSP Header Not Set", "risk": "Medium", "confidence": "High",
             "url": "http://127.0.0.1:1/", "evidence": ""},
            {"pluginId": "10036", "alert": "Server Leaks Version", "risk": "Low", "confidence": "High",
             "url": "http://127.0.0.1:1/elsewhere"},
        ]
        rows = summarize_alerts(alerts, {"http://127.0.0.1:1/api/works/W-SECRET": "/api/works/{work}",
                                         "http://127.0.0.1:1/": "/"})
        self.assertEqual(rows, [
            {"plugin_id": "10038", "name": "CSP Header Not Set", "risk": "Medium", "confidence": "High",
             "count": 2, "routes": ["/", "/api/works/{work}"]},
            {"plugin_id": "10036", "name": "Server Leaks Version", "risk": "Low", "confidence": "High",
             "count": 1, "routes": ["(other)"]},
        ])
        self.assertNotIn("SECRET", repr(rows))

    def test_workflow_is_manual_only(self):
        workflow = (ROOT / ".github" / "workflows" / "zap-passive-pilot.yml").read_text(encoding="utf-8")
        triggers = workflow.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(triggers.strip(), "workflow_dispatch:")
        self.assertIn("contents: read", workflow)
        self.assertNotIn("zaproxy/action", workflow)


if __name__ == "__main__":
    unittest.main()
