"""Search and Saved View detail share one Vue result boundary.

Uses the real workspace/TabContext path and the real search API. Owner and
stale-generation isolation that can be proven with mocked owners lives in the
search and saved-views Vitest files.
"""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


_MAIN_RESULT_IDS = """(root) => {
    const tile = document.querySelector('.prks-tile--main');
    const view = tile && tile.querySelector(root);
    const results = view && view.querySelector('[data-prks-search-results]');
    return Array.from(results ? results.querySelectorAll('[data-work-id]') : [])
        .map((node) => node.getAttribute('data-work-id'));
}"""


class SearchSavedViewsRouteSurfaceTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page

    def test_search_save_view_detail_and_delete_round_trip(self):
        server, page = self.start()
        work_b = server.ids["work_b"]
        person = server.ids["person"]

        page.evaluate("() => prksNavigate('#/search?q=Related')")
        page.wait_for_selector(
            f".prks-tile--main [data-prks-search-view] [data-work-id='{work_b}']",
            timeout=15000,
        )
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-search-view] .prks-page-title").inner_text(),
            "Search results for “Related”",
        )
        search_ids = page.evaluate(_MAIN_RESULT_IDS, "[data-prks-search-view]")
        self.assertIn(work_b, search_ids)

        # Submitting the form re-runs the route on the same Main owner.
        query = page.locator(".prks-tile--main #search-q-input")
        query.fill("Related Work")
        query.press("Enter")
        page.wait_for_function("() => location.hash === '#/search?q=Related+Work'")
        page.wait_for_selector(
            f".prks-tile--main [data-prks-search-view] [data-work-id='{work_b}']",
            timeout=15000,
        )
        self.assertEqual(
            page.locator(".prks-tile--main #search-q-input").input_value(), "Related Work"
        )
        search_ids = page.evaluate(_MAIN_RESULT_IDS, "[data-prks-search-view]")

        page.locator(".prks-tile--main #prks-save-view-btn").click()
        page.wait_for_selector("#saved-view-modal:not(.hidden)", timeout=15000)
        self.assertEqual(page.locator("#saved-view-q").input_value(), "Related Work")
        page.locator("#saved-view-name").fill("E2E Saved Search")
        page.locator("#save-saved-view-btn").click()
        page.wait_for_function("() => location.hash.indexOf('#/views/') === 0")
        page.wait_for_selector(
            f".prks-tile--main .saved-view-detail [data-work-id='{work_b}']",
            timeout=15000,
        )
        self.assertEqual(
            page.locator(".prks-tile--main .saved-view-detail .prks-page-title").inner_text(),
            "E2E Saved Search",
        )
        self.assertEqual(page.evaluate(_MAIN_RESULT_IDS, ".saved-view-detail"), search_ids)
        self.assertEqual(
            page.locator(".prks-tile--main .saved-view-detail a", has_text="Open as Search").get_attribute("href"),
            "#/search?q=Related+Work",
        )

        # A Secondary navigation leaves the Main Saved View tree alone.
        page.evaluate(
            """() => {
                const view = document.querySelector('.prks-tile--main .saved-view-detail');
                if (view) view.setAttribute('data-prks-surface-marker', 'main-view');
            }"""
        )
        page.evaluate("(id) => prksNavigate('#/people/' + id, { target: 'tile' })", person)
        page.wait_for_selector(".prks-tile--secondary .person-profile", timeout=15000)
        self.assertEqual(
            page.locator(".prks-tile--main .saved-view-detail").get_attribute("data-prks-surface-marker"),
            "main-view",
        )
        self.assertEqual(page.locator(".prks-tile--secondary .saved-view-detail").count(), 0)

        view_hash = page.evaluate("() => location.hash")

        # The index reads the list itself; Edit opens the shared modal from a
        # fresh record read, and the save refreshes the list.
        page.evaluate("() => prksNavigate('#/views')")
        row = page.locator(".prks-tile--main .saved-views-page__list-item", has_text="E2E Saved Search")
        row.wait_for(timeout=15000)
        row.locator("[data-sv-index-edit]").click()
        page.wait_for_selector("#saved-view-modal:not(.hidden)", timeout=15000)
        self.assertEqual(page.locator("#saved-view-name").input_value(), "E2E Saved Search")
        page.locator("#saved-view-name").fill("E2E Renamed Search")
        page.locator("#save-saved-view-btn").click()
        page.wait_for_selector("#saved-view-modal", state="hidden", timeout=15000)
        page.locator(
            ".prks-tile--main .saved-views-page__list-item", has_text="E2E Renamed Search"
        ).wait_for(timeout=15000)
        self.assertEqual(
            page.locator(".prks-tile--main .saved-views-page__list-item").count(), 1
        )

        page.evaluate("(hash) => prksNavigate(hash)", view_hash)
        page.wait_for_selector(".prks-tile--main .saved-view-detail .prks-page-title", timeout=15000)
        self.assertEqual(
            page.locator(".prks-tile--main .saved-view-detail .prks-page-title").inner_text(),
            "E2E Renamed Search",
        )

        # An open detail follows writes made by any other surface through the
        # shared records service: a rename re-resolves it in place and a
        # delete leaves it not-found, with no navigation. (Saved View routes
        # are Main-only, so the other surface writes through the bridge.)
        view_id = view_hash.rsplit("/", 1)[1]
        page.evaluate(
            """(id) => window.prksSavedViewRecords.update(id, {
                name: 'E2E Cross-Surface Search',
                search: { mode: 'all', q: 'Related Work', tag: '', author: '', publisher: '' },
            })""",
            view_id,
        )
        page.wait_for_function(
            """() => {
                const title = document.querySelector('.prks-tile--main .saved-view-detail .prks-page-title');
                return !!title && title.textContent.trim() === 'E2E Cross-Surface Search';
            }""",
            timeout=15000,
        )
        self.assertEqual(page.evaluate("() => location.hash"), view_hash)
        page.evaluate("(id) => window.prksSavedViewRecords.remove(id)", view_id)
        page.wait_for_selector(
            ".prks-tile--main [data-prks-saved-view-not-found]", timeout=15000
        )
        self.assertEqual(page.evaluate("() => location.hash"), view_hash)

        # Deleting from the detail itself still sends that owner to the index.
        second_id = page.evaluate(
            """async () => (await window.prksSavedViewRecords.create({
                name: 'E2E Second Search',
                search: { mode: 'all', q: 'Related Work', tag: '', author: '', publisher: '' },
            })).id"""
        )
        page.evaluate("(id) => prksNavigate('#/views/' + encodeURIComponent(id))", second_id)
        page.wait_for_function(
            """() => {
                const title = document.querySelector('.prks-tile--main .saved-view-detail .prks-page-title');
                return !!title && title.textContent.trim() === 'E2E Second Search';
            }""",
            timeout=15000,
        )
        page.locator(".prks-tile--main #prks-saved-view-delete").click()
        page.wait_for_selector("#prks-modal-confirm-ok", state="visible", timeout=15000)
        page.locator("#prks-modal-confirm-ok").click()
        page.wait_for_function("() => location.hash === '#/views'")
        page.wait_for_selector(".prks-tile--main .saved-views-page__empty", timeout=15000)
        self.assertEqual(
            page.locator(".prks-tile--main .saved-views-page__list-item").count(), 0
        )
        self.assertIn(
            "No Saved Views yet.",
            page.locator(".prks-tile--main .saved-views-page").inner_text(),
        )

if __name__ == "__main__":
    unittest.main()
