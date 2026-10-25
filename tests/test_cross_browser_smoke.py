"""Pure safeguards for the opt-in Firefox/WebKit smoke pilot (#651 C2)."""
from __future__ import annotations

import unittest

from scripts.ci.cross_browser_smoke import (
    CHECKS, ENGINES, verify_isolation, verify_report,
)


def report(engines=("firefox", "webkit")):
    return {
        "format": "prks-cross-browser-smoke-v1",
        "fixture": "disposable_appserver_synthetic_sqlite",
        "browsers": [
            {"engine": engine, "version": "1.2.3", "passed": True, "checks": list(CHECKS)}
            for engine in engines
        ],
    }


class CrossBrowserSmokeContracts(unittest.TestCase):
    def test_disallows_inherited_live_auth_and_database_connections(self):
        verify_isolation({})
        verify_isolation({"PRKS_STORAGE": "/nonexistent-user-storage"})
        for env in (
            "PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_DATABASE_URL_FILE",
            "PRKS_TEST_DATABASE_URL",
        ):
            with self.subTest(env=env), self.assertRaises(RuntimeError):
                verify_isolation({env: "sensitive-secret"})

    def test_complete_two_engine_report_is_accepted(self):
        self.assertEqual(ENGINES, ("firefox", "webkit"))
        verify_report(report(), ENGINES)
        verify_report(report(("webkit",)), ("webkit",))

    def test_missing_or_unproven_checks_fail_closed(self):
        for mutation in ("missing_browser", "missing_check", "wrong_order",
                         "not_passed", "missing_version", "wrong_fixture"):
            data = report()
            if mutation == "missing_browser":
                data["browsers"].pop()
            elif mutation == "missing_check":
                data["browsers"][0]["checks"].pop()
            elif mutation == "wrong_order":
                data["browsers"].reverse()
            elif mutation == "not_passed":
                data["browsers"][0]["passed"] = False
            elif mutation == "missing_version":
                data["browsers"][0]["version"] = ""
            else:
                data["fixture"] = "arbitrary_production_data"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_report(data, ENGINES)

    def test_no_arbitrary_browser_engine_or_unexpected_results(self):
        data = report(("firefox",))
        data["browsers"][0]["engine"] = "chromium"
        with self.assertRaises(ValueError):
            verify_report(data, ("firefox",))


if __name__ == "__main__":
    unittest.main()
