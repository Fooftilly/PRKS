"""Regression coverage for the opt-in same-origin E2E route fast path.

No Chromium is required. The existing catch-all route and off-origin rejection
must behave identically with the fast path enabled or disabled.
"""
from __future__ import annotations

import unittest
from unittest import mock

from tests.e2e.harness import PageCollector


ORIGIN = "http://127.0.0.1:43210"


class E2ERouteFastPathTests(unittest.TestCase):
    def _dispatch(self, url: str, fastpath: bool):
        collector = object.__new__(PageCollector)
        collector.origin = ORIGIN
        collector.external = []
        collector._fast_local_route = fastpath
        route = mock.Mock()
        route.request.url = url
        collector._on_route(route)
        return route, collector

    def test_local_requests_continue_in_both_modes(self):
        allowed = (
            ORIGIN,
            ORIGIN + "/",
            ORIGIN + "/api/works?x=1",
            ORIGIN + "?q=1",
            "http://localhost:43210/api/works",
            "http://[::1]:43210/assets/app.js",
        )
        for fastpath in (False, True):
            for url in allowed:
                with self.subTest(fastpath=fastpath, url=url):
                    route, collector = self._dispatch(url, fastpath)
                    route.continue_.assert_called_once_with()
                    route.abort.assert_not_called()
                    self.assertEqual(collector.external, [])

    def test_off_origin_is_always_aborted_even_with_prefix_spoofing(self):
        blocked = (
            "https://example.invalid/file.js",
            "http://127.0.0.1:43211/api",
            "https://127.0.0.1:43210/api",
            "http://127.0.0.1:43210.evil.test/api",
            "http://127.0.0.1:43210@evil.test/api",
            "http://localhost:43211/api",
        )
        for fastpath in (False, True):
            for url in blocked:
                with self.subTest(fastpath=fastpath, url=url):
                    route, collector = self._dispatch(url, fastpath)
                    route.abort.assert_called_once_with()
                    route.continue_.assert_not_called()
                    self.assertEqual(collector.external, [url])

    def test_abort_failure_never_falls_back_to_external_continue(self):
        for fastpath in (False, True):
            with self.subTest(fastpath=fastpath):
                collector = object.__new__(PageCollector)
                collector.origin = ORIGIN
                collector.external = []
                collector._fast_local_route = fastpath
                route = mock.Mock()
                route.request.url = "https://example.invalid/private-data"
                route.abort.side_effect = RuntimeError("abort failed")
                with self.assertRaisesRegex(RuntimeError, "abort failed"):
                    collector._on_route(route)
                route.abort.assert_called_once_with()
                route.continue_.assert_not_called()
                self.assertEqual(collector.external, [route.request.url])

    def test_route_registration_remains_catch_all_in_both_modes(self):
        for flag, expected in (("0", False), ("1", True)):
            page = mock.Mock()
            with mock.patch.dict(
                "os.environ", {"PRKS_E2E_ROUTE_FASTPATH": flag}
            ):
                collector = PageCollector(page, ORIGIN)
            self.assertIs(collector._fast_local_route, expected)
            page.route.assert_called_once_with("**/*", collector._on_route)


if __name__ == "__main__":
    unittest.main()
