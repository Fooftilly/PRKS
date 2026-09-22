"""Shared modal lifecycle: Escape, unsaved confirmation, focus, New Person reset."""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import PERSON_DISPLAY, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_E2E") != "1":
        return unittest.TestSuite()
    return standard_tests


_PW = None
_BROWSER = None


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    global _PW, _BROWSER
    try:
        if _BROWSER is not None:
            _BROWSER.close()
    finally:
        if _PW is not None:
            _PW.stop()
        _PW = None
        _BROWSER = None


def _wait_baseline(page, modal_id):
    page.wait_for_function(
        """(id) => {
            const modal = document.getElementById(id);
            const ready = window.__prksModalBaselineReady;
            return !!(modal && !modal.classList.contains('hidden')
                && ready && ready[id] === true);
        }""",
        arg=modal_id,
    )


def _modal_open(page, modal_id):
    return page.locator("#%s:not(.hidden)" % modal_id).count() == 1


def _active_matches(page, expression):
    return page.evaluate(expression)


class ModalLifecycleTests(unittest.TestCase):
    def _start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin)
        self.addCleanup(context.close)
        self.addCleanup(collector.assert_clean)
        page.wait_for_selector("#sidebar")
        return page

    def _open_menu_action(self, page, action_id):
        page.locator("#prks-ribbon-new-more").click()
        item = page.locator('#prks-create-menu [data-create-id="%s"]' % action_id)
        item.wait_for(state="visible")
        item.click()

    def _assert_focus(self, page, expression, message):
        self.assertTrue(_active_matches(page, expression), message)

    def test_pristine_escape_closes_and_restores_focus(self):
        page = self._start()

        settings = page.locator("button.settings-btn")
        settings.click()
        page.locator("#settings-modal:not(.hidden)").wait_for()
        page.locator("#prks-settings-tab-general").focus()
        page.keyboard.press("Escape")
        page.locator("#settings-modal").wait_for(state="hidden")
        page.locator("#modal-backdrop").wait_for(state="hidden")
        self._assert_focus(
            page,
            "() => !!(document.activeElement && document.activeElement.classList.contains('settings-btn'))",
            "Escape should return focus to Settings",
        )

        settings.click()
        page.locator("#settings-modal:not(.hidden)").wait_for()
        backdrop = page.locator("#modal-backdrop")
        backdrop.click(position={"x": 8, "y": 8})
        page.locator("#settings-modal").wait_for(state="hidden")

        settings.click()
        page.locator("#settings-modal:not(.hidden)").wait_for()
        page.locator("#settings-modal .close-btn").click()
        page.locator("#settings-modal").wait_for(state="hidden")
        self._assert_focus(
            page,
            "() => !!(document.activeElement && document.activeElement.classList.contains('settings-btn'))",
            "Header close should return focus to Settings",
        )

        page.locator("#prks-ribbon-new-file").click()
        page.wait_for_function(
            """() => {
                const modal = document.getElementById('work-modal');
                const ready = window.__prksModalBaselineReady;
                const ae = document.activeElement;
                return !!(modal && !modal.classList.contains('hidden')
                    && ready && ready['work-modal'] === true
                    && ae && ae.id === 'upload-drop-zone');
            }"""
        )
        page.locator("#work-doc-type-trigger").click()
        page.locator("#work-modal .prks-doc-type-menu__panel:not(.hidden)").wait_for()
        page.keyboard.press("Escape")
        page.locator("#work-doc-type-listbox.hidden").wait_for()
        self.assertTrue(_modal_open(page, "work-modal"))
        page.keyboard.press("Escape")
        page.locator("#work-modal").wait_for(state="hidden")
        self._assert_focus(
            page,
            "() => document.activeElement && document.activeElement.id === 'prks-ribbon-new-file'",
            "Escape should return focus to New File",
        )

        self._open_menu_action(page, "new-folder")
        _wait_baseline(page, "folder-modal")
        page.keyboard.press("Escape")
        page.locator("#folder-modal").wait_for(state="hidden")
        self._assert_focus(
            page,
            "() => document.activeElement && document.activeElement.id === 'prks-ribbon-new-more'",
            "Escape should return focus to the create menu chevron",
        )

        page.locator('#sidebar a.nav-link[href="#/people"]').click()
        page.wait_for_function("() => location.hash === '#/people'")
        create = page.locator('[data-prks-role="person-create-control"]', has_text="New Person")
        create.click()
        _wait_baseline(page, "person-modal")
        self.assertEqual(page.locator("#person-fname").input_value(), "")
        self.assertEqual(page.locator("#person-lname").input_value(), "")
        page.keyboard.press("Escape")
        page.locator("#person-modal").wait_for(state="hidden")
        self._assert_focus(
            page,
            """() => {
                const el = document.activeElement;
                return !!(el && el.getAttribute('data-prks-role') === 'person-create-control');
            }""",
            "Escape should return focus to New Person",
        )

    def test_dirty_escape_confirms_and_does_not_fall_through(self):
        page = self._start()

        self._open_menu_action(page, "new-folder")
        _wait_baseline(page, "folder-modal")
        page.locator("#folder-title").fill("Draft folder")
        page.keyboard.press("Escape")
        page.locator("#prks-modal-unsaved-confirm:not(.hidden)").wait_for()
        self.assertTrue(_modal_open(page, "folder-modal"))
        page.keyboard.press("Escape")
        page.locator("#prks-modal-unsaved-confirm").wait_for(state="hidden")
        self.assertTrue(_modal_open(page, "folder-modal"))
        page.locator("#modal-backdrop").click(position={"x": 8, "y": 8})
        page.locator("#prks-modal-unsaved-confirm:not(.hidden)").wait_for()
        self.assertTrue(_modal_open(page, "folder-modal"))
        page.locator("#prks-modal-unsaved-confirm-cancel").click()
        page.locator("#prks-modal-unsaved-confirm").wait_for(state="hidden")
        self.assertTrue(_modal_open(page, "folder-modal"))
        page.locator("#folder-modal .close-btn").click()
        page.locator("#prks-modal-unsaved-confirm:not(.hidden)").wait_for()
        page.locator("#prks-modal-unsaved-confirm-discard").click()
        page.locator("#folder-modal").wait_for(state="hidden")
        page.locator("#modal-backdrop").wait_for(state="hidden")
        self._assert_focus(
            page,
            "() => document.activeElement && document.activeElement.id === 'prks-ribbon-new-more'",
            "Discard should return focus to the create menu chevron",
        )

        page.locator("#prks-ribbon-new-file").click()
        _wait_baseline(page, "work-modal")
        page.locator("#work-title").fill("Draft file")
        page.keyboard.press("Escape")
        page.locator("#prks-modal-unsaved-confirm:not(.hidden)").wait_for()
        self.assertTrue(_modal_open(page, "work-modal"))
        page.keyboard.press("Escape")
        page.locator("#prks-modal-unsaved-confirm").wait_for(state="hidden")
        self.assertTrue(_modal_open(page, "work-modal"))
        page.locator("#work-modal .close-btn").click()
        page.locator("#prks-modal-unsaved-confirm:not(.hidden)").wait_for()
        page.locator("#prks-modal-unsaved-confirm-discard").click()
        page.locator("#work-modal").wait_for(state="hidden")

        page.locator('#sidebar a.nav-link[href="#/people"]').click()
        page.wait_for_function("() => location.hash === '#/people'")
        page.locator('[data-prks-role="person-create-control"]', has_text="New Person").click()
        _wait_baseline(page, "person-modal")
        page.locator("#person-lname").fill("Ada")
        page.locator("#person-birth-date").fill("not-a-date")
        page.locator("#save-person-btn").click()
        page.locator("#prks-modal-confirm:not(.hidden)").wait_for()
        self.assertEqual(page.locator("#prks-modal-confirm-title").inner_text(), "Validation")
        self.assertTrue(_modal_open(page, "person-modal"))
        page.keyboard.press("Escape")
        page.locator("#prks-modal-confirm").wait_for(state="hidden")
        self.assertTrue(_modal_open(page, "person-modal"))
        self.assertEqual(page.locator("#person-lname").input_value(), "Ada")
        page.keyboard.press("Escape")
        page.locator("#prks-modal-unsaved-confirm:not(.hidden)").wait_for()
        self.assertTrue(_modal_open(page, "person-modal"))
        page.keyboard.press("Escape")
        page.locator("#prks-modal-unsaved-confirm").wait_for(state="hidden")
        self.assertTrue(_modal_open(page, "person-modal"))
        page.locator("#person-modal .close-btn").click()
        page.locator("#prks-modal-unsaved-confirm:not(.hidden)").wait_for()
        page.locator("#prks-modal-unsaved-confirm-discard").click()
        page.locator("#person-modal").wait_for(state="hidden")

        page.locator('[data-prks-role="person-create-control"]', has_text="New Person").click()
        _wait_baseline(page, "person-modal")
        self.assertEqual(page.locator("#person-fname").input_value(), "")
        self.assertEqual(page.locator("#person-lname").input_value(), "")
        self.assertEqual(page.locator("#person-birth-date").input_value(), "")
        page.keyboard.press("Escape")
        page.locator("#person-modal").wait_for(state="hidden")

    def test_new_person_create_reopens_blank_and_edit_keeps_existing(self):
        page = self._start()
        page.locator('#sidebar a.nav-link[href="#/people"]').click()
        page.wait_for_function("() => location.hash === '#/people'")
        page.locator('[data-prks-role="person-create-control"]', has_text="New Person").click()
        _wait_baseline(page, "person-modal")
        page.locator("#person-fname").fill("Ada")
        page.locator("#person-lname").fill("Lovelace")
        page.locator("#save-person-btn").click()
        page.locator("#person-modal").wait_for(state="hidden")
        page.wait_for_function(
            """() => {
                const hash = location.hash || '';
                return hash.indexOf('#/people/') === 0
                    && hash.indexOf('#/people/groups') !== 0
                    && hash.indexOf('#/people/role') !== 0;
            }"""
        )
        page.locator("h2.prks-page-title", has_text="Ada Lovelace").wait_for()

        page.locator('#sidebar a.nav-link[href="#/people"]').click()
        page.wait_for_function("() => location.hash === '#/people'")
        page.locator('[data-prks-role="person-create-control"]', has_text="New Person").click()
        _wait_baseline(page, "person-modal")
        self.assertEqual(page.locator("#person-fname").input_value(), "")
        self.assertEqual(page.locator("#person-lname").input_value(), "")
        self.assertEqual(page.locator("#person-aliases").input_value(), "")
        page.keyboard.press("Escape")
        page.locator("#person-modal").wait_for(state="hidden")

        page.locator(".prks-people-list__title", has_text=PERSON_DISPLAY).click()
        page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
        page.locator("h2.prks-page-title", has_text=PERSON_DISPLAY).wait_for()
        page.locator('[data-prks-role="person-edit-control"]', has_text="Edit profile").click()
        page.locator("#pd-first-name").wait_for()
        self.assertEqual(page.locator("#pd-first-name").input_value(), "E2E")
        self.assertEqual(page.locator("#pd-last-name").input_value(), "Author")
        page.locator("#pd-first-name").fill("E2E Edited")

        self._open_menu_action(page, "new-person")
        _wait_baseline(page, "person-modal")
        self.assertEqual(page.locator("#person-fname").input_value(), "")
        self.assertEqual(page.locator("#person-lname").input_value(), "")
        self.assertEqual(page.locator("#pd-first-name").input_value(), "E2E Edited")
        self.assertEqual(page.locator("#pd-last-name").input_value(), "Author")
        page.keyboard.press("Escape")
        page.locator("#person-modal").wait_for(state="hidden")
        self.assertEqual(page.locator("#pd-first-name").input_value(), "E2E Edited")
        self.assertEqual(page.locator("#pd-last-name").input_value(), "Author")
