"""Work Browsing V1 — shared card/list density, thumb states, quick preview."""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import (
    FOLDER_CHILD_TITLE,
    FOLDER_PARENT_TITLE,
    WORK_A_TITLE,
    WORK_B_TITLE,
    seed_folders_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class WorkBrowsingV1Tests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_folders_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page, server.ids

    def open_folder(self, page, folder_id):
        page.evaluate(
            "id => prksNavigate('#/folders/' + encodeURIComponent(id))",
            folder_id,
        )
        page.wait_for_selector(
            ".prks-tile--main [data-prks-role='folder-detail']", timeout=15000
        )

    def click_list_mode(self, page, *, scope=".prks-tile--main"):
        btn = page.locator(
            f"{scope} [data-prks-role='work-browse-mode'] "
            ".prks-segmented__btn[data-value='List']"
        ).first
        btn.click()
        page.wait_for_selector(
            f"{scope} .work-browse-collection--list", timeout=5000
        )

    def click_cards_mode(self, page, *, scope=".prks-tile--main"):
        btn = page.locator(
            f"{scope} [data-prks-role='work-browse-mode'] "
            ".prks-segmented__btn[data-value='Cards']"
        ).first
        btn.click()
        page.wait_for_selector(
            f"{scope} .work-browse-collection--cards", timeout=5000
        )

    def test_folder_list_preview_open_nav_mode_coherent(self):
        """Acceptance: Folder A list → preview → open → Folder B → mode sticks → cards."""
        server, page, ids = self.start()
        parent = ids["folder_parent"]
        child = ids["folder_child"]

        self.open_folder(page, parent)
        page.wait_for_selector(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']",
            timeout=10000,
        )
        self.assertTrue(
            page.locator(".prks-tile--main .work-browse-collection--cards").count() >= 1
        )
        self.assertTrue(
            page.locator(
                ".prks-tile--main [data-prks-role='work-browse-mode']"
            ).count()
            >= 1
        )

        self.click_list_mode(page)
        mode = page.evaluate("() => prksGetWorkBrowseMode()")
        self.assertEqual(mode, "list")

        card = page.locator(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']"
        ).first
        thumb = card.locator(".work-card__thumb[data-prks-thumb-preview-kind]").first
        self.assertEqual(thumb.count(), 1)

        # Keyboard preview on focused card (P); Escape dismisses.
        card.focus()
        page.keyboard.press("p")
        page.wait_for_selector(
            "#prks-work-thumb-preview.work-card-preview--visible", timeout=5000
        )
        preview = page.locator("#prks-work-thumb-preview")
        self.assertFalse(preview.is_hidden())
        frame = preview.locator(".work-card-preview__frame--pdf")
        self.assertEqual(frame.count(), 1)
        first_src = page.evaluate(
            """() => {
              const img = document.querySelector('#prks-work-thumb-preview .work-card-preview__img');
              return img && img.getAttribute('src') ? img.getAttribute('src') : '';
            }"""
        )
        self.assertTrue(first_src and "/thumbnail" in first_src)

        page.keyboard.press("Escape")
        page.wait_for_function(
            "() => {"
            "  const el = document.getElementById('prks-work-thumb-preview');"
            "  return !el || el.hidden;"
            "}",
            timeout=5000,
        )

        # Same thumb again: hide must not leave a blank frame (WeakMap/src contract).
        card.focus()
        page.keyboard.press("p")
        page.wait_for_selector(
            "#prks-work-thumb-preview.work-card-preview--visible", timeout=5000
        )
        second_src = page.evaluate(
            """() => {
              const el = document.getElementById('prks-work-thumb-preview');
              if (!el || el.hidden) return '';
              const img = el.querySelector('.work-card-preview__img');
              return img && img.getAttribute('src') ? img.getAttribute('src') : '';
            }"""
        )
        self.assertTrue(
            second_src and "/thumbnail" in second_src,
            "second preview of the same thumb must reassign img src",
        )
        self.assertEqual(second_src, first_src)
        page.keyboard.press("Escape")
        page.wait_for_function(
            "() => {"
            "  const el = document.getElementById('prks-work-thumb-preview');"
            "  return !el || el.hidden;"
            "}",
            timeout=5000,
        )

        # P then navigate away (no Escape): route paint must dismiss preview.
        card.focus()
        page.keyboard.press("p")
        page.wait_for_selector(
            "#prks-work-thumb-preview.work-card-preview--visible", timeout=5000
        )
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_function(
            "() => {"
            "  const el = document.getElementById('prks-work-thumb-preview');"
            "  const srcGone = !window.__prksWorkThumbPreviewSource;"
            "  return srcGone && (!el || el.hidden);"
            "}",
            timeout=10000,
        )
        page.wait_for_selector(
            ".prks-tile--main [data-prks-role='folder-library'], "
            ".prks-tile--main .prks-folder-library",
            timeout=10000,
        )

        # Folder A → P → Folder B (preserveFolderWorkspace): preview must clear.
        self.open_folder(page, parent)
        page.wait_for_selector(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']",
            timeout=10000,
        )
        card = page.locator(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']"
        ).first
        card.focus()
        page.keyboard.press("p")
        page.wait_for_selector(
            "#prks-work-thumb-preview.work-card-preview--visible", timeout=5000
        )
        # Navigate to child while folder-detail shell is live (preserve path).
        self.open_folder(page, child)
        page.wait_for_function(
            "() => {"
            "  const el = document.getElementById('prks-work-thumb-preview');"
            "  const srcGone = !window.__prksWorkThumbPreviewSource;"
            "  return srcGone && (!el || el.hidden);"
            "}",
            timeout=10000,
        )
        page.wait_for_selector(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_b']}']",
            timeout=10000,
        )

        # Return to Folder A for the rest of the acceptance path.
        self.open_folder(page, parent)
        page.wait_for_selector(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']",
            timeout=10000,
        )
        card = page.locator(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']"
        ).first

        # P then switch main workspace tab: parked/suspended pane must clear preview.
        folder_tab = page.evaluate("() => prksWorkspaceSnapshot().mainTabId")
        page.evaluate(
            "() => prksNavigate('#/folders', { target: 'new-tab', activate: true })"
        )
        page.wait_for_function(
            "id => {"
            "  const s = prksWorkspaceSnapshot();"
            "  return s && s.mainTabId && s.mainTabId !== id;"
            "}",
            arg=folder_tab,
            timeout=10000,
        )
        other_tab = page.evaluate("() => prksWorkspaceSnapshot().mainTabId")
        page.locator(
            f'.prks-workspace-tab[data-tab-id="{folder_tab}"] .prks-workspace-tab__activate'
        ).click()
        page.wait_for_function(
            "id => prksWorkspaceSnapshot().mainTabId === id",
            arg=folder_tab,
            timeout=10000,
        )
        page.wait_for_selector(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']",
            timeout=10000,
        )
        card = page.locator(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']"
        ).first
        card.focus()
        page.keyboard.press("p")
        page.wait_for_selector(
            "#prks-work-thumb-preview.work-card-preview--visible", timeout=5000
        )
        page.locator(
            f'.prks-workspace-tab[data-tab-id="{other_tab}"] .prks-workspace-tab__activate'
        ).click()
        page.wait_for_function(
            "() => {"
            "  const el = document.getElementById('prks-work-thumb-preview');"
            "  const srcGone = !window.__prksWorkThumbPreviewSource;"
            "  return srcGone && (!el || el.hidden);"
            "}",
            timeout=10000,
        )

        # Back to Folder A for Enter-to-open.
        page.locator(
            f'.prks-workspace-tab[data-tab-id="{folder_tab}"] .prks-workspace-tab__activate'
        ).click()
        page.wait_for_function(
            "id => prksWorkspaceSnapshot().mainTabId === id",
            arg=folder_tab,
            timeout=10000,
        )
        page.wait_for_selector(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']",
            timeout=10000,
        )
        card = page.locator(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_a']}']"
        ).first

        # Open Work via Enter — preview must not replace navigation.
        card.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            "id => location.hash === '#/works/' + encodeURIComponent(id)",
            arg=ids["work_a"],
            timeout=15000,
        )
        page.wait_for_selector(".prks-tile--main .work-detail", timeout=15000)

        # Folder B via in-app Folder detail / Library Nav tree (child).
        self.open_folder(page, child)
        page.wait_for_selector(
            f".prks-tile--main .project-card--work-card[data-work-id='{ids['work_b']}']",
            timeout=10000,
        )
        # Preference survived navigation — still list.
        self.assertEqual(page.evaluate("() => prksGetWorkBrowseMode()"), "list")
        self.assertTrue(
            page.locator(".prks-tile--main .work-browse-collection--list").count() >= 1
        )
        self.assertIn(FOLDER_CHILD_TITLE, page.locator(".prks-tile--main .prks-page-title").first.inner_text())

        self.click_cards_mode(page)
        self.assertEqual(page.evaluate("() => prksGetWorkBrowseMode()"), "cards")
        self.assertTrue(
            page.locator(".prks-tile--main .work-browse-collection--cards").count() >= 1
        )

        # Thumb lifecycle classes exist on PDF cards (loading or ready; not blank ambiguity).
        states = page.evaluate(
            """() => Array.from(
                document.querySelectorAll(
                  '.prks-tile--main .work-card__thumb[data-prks-thumb-state]'
                )
              ).map(el => el.getAttribute('data-prks-thumb-state'))"""
        )
        self.assertTrue(states, "expected thumb state attributes")
        for s in states:
            self.assertIn(s, ("loading", "ready", "error", "empty"))

        # Recent also inherits the shared preference without a second renderer.
        page.evaluate("() => prksNavigate('#/recent')")
        page.wait_for_selector(
            ".prks-tile--main [data-prks-role='work-browse-mode']", timeout=10000
        )
        self.assertEqual(page.evaluate("() => prksGetWorkBrowseMode()"), "cards")
        # work_a was opened earlier — Recent must show its card in the shared collection.
        page.wait_for_selector(
            f".prks-tile--main .work-browse-collection--cards "
            f"[data-work-id='{ids['work_a']}']",
            timeout=10000,
        )

        # Mode switch does not issue a new Works catalog fetch.
        # Switching list↔cards is class-only; prove no /api/works burst.
        self.open_folder(page, parent)
        page.wait_for_selector(".prks-tile--main .work-browse-collection", timeout=10000)

        seen = []

        def on_req(req):
            url = req.url or ""
            if (
                "/api/works" in url
                and "/thumbnail" not in url
                and "/opened" not in url
                and "/metadata-state" not in url
            ):
                seen.append(url)

        page.on("request", on_req)
        self.addCleanup(lambda: page.remove_listener("request", on_req))
        self.click_list_mode(page)
        self.click_cards_mode(page)
        # Let any async mode-change work settle before asserting silence.
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)))"
        )
        self.assertEqual(seen, [], "mode switch must not refetch Works")

        _ = (FOLDER_PARENT_TITLE, WORK_A_TITLE, WORK_B_TITLE)  # titles used via locators/ids


if __name__ == "__main__":
    unittest.main()
