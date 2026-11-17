"""Pure safeguards for the passive-only ZAP pilot (#860)."""
from __future__ import annotations

import unittest

from scripts.ci.zap_passive_pilot import (
    ROOT, STEPS, ZAP_IMAGE, exchanged, require_loopback, summarize_alerts, zap_command,
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

    def test_the_allowlist_only_reads_except_signing_in(self):
        for step in STEPS:
            with self.subTest(step=step):
                if step.method != "GET":
                    self.assertEqual((step.method, step.template), ("POST", "/api/login"))
                self.assertTrue(step.template.startswith("/"))
        callers = {step.caller for step in STEPS}
        self.assertEqual(callers, {"anonymous", "owner", "editor"})
        self.assertIn(("anonymous", "/api/works", 401), {(s.caller, s.template, s.expect) for s in STEPS})
        self.assertIn(("editor", "/api/backups/download", 403), {(s.caller, s.template, s.expect) for s in STEPS})

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
        self.assertIn("workflow_dispatch:", triggers)
        self.assertNotIn("pull_request", triggers)
        self.assertNotIn("schedule", triggers)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("zaproxy/action", workflow)


if __name__ == "__main__":
    unittest.main()
