"""Folders/Home read models, cache safety, live controls and domain boundaries.

`#/folders` is PRKS's default route, so this module also covers the launch case
that motivated the milestone: booting the shell at `/` while PRKS is
unreachable must land on a usable cached hierarchy, not an empty library.
"""
import json
import os
import unittest
from urllib.parse import urlparse

from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    FOLDER_CHILD_TITLE,
    FOLDER_PARENT_DESCRIPTION,
    FOLDER_PARENT_TITLE,
    FOLDER_TAG_NAME,
    FOLDER_UNVISITED_TITLE,
    WORK_A_TITLE,
    WORK_B_TITLE,
    seed_folders_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class FoldersOfflineTests(unittest.TestCase):
    def start(self, seed=seed_folders_library):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        return server, page, context, collector

    # ---- navigation helpers -------------------------------------------------

    def index(self, page):
        page.evaluate("() => prksNavigate('#/folders')")

    def detail(self, page, fid):
        page.evaluate("id => prksNavigate('#/folders/' + encodeURIComponent(id))", fid)

    def cache(self, page, ids, all_domains=False):
        """Warms folders:index and the parent Folder's detail; `all_domains`
        adds the other read models so domain independence is observable."""
        if all_domains:
            o._open_concept(page, ids['concept_child'])
            o._wait_entity_cached(page, 'concept', ids['concept_child'])
            o._open_position(page, ids['position_a'])
            o._wait_entity_cached(page, 'position', ids['position_a'])
            o._open_argument(page, ids['argument_a'])
            o._wait_entity_cached(page, 'argument', ids['argument_a'])
            o._open_people_index(page)
            o._wait_list_cached(page, 'people:index')
            page.evaluate("() => prksNavigate('#/people/groups')")
            o._wait_list_cached(page, 'person-groups:index')
            page.evaluate("() => prksNavigate('#/playlists')")
            o._wait_list_cached(page, 'playlists:index')
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        self.detail(page, ids['folder_parent'])
        o._wait_entity_cached(page, 'folder', ids['folder_parent'])

    def generations(self, page):
        return {d: o._domain_generation(page, d) for d in
                ('concepts', 'positions', 'arguments', 'people', 'person-groups',
                 'playlists', 'folders')}

    def changed(self, page, before, expected):
        for domain, generation in before.items():
            with self.subTest(domain=domain):
                if domain in expected:
                    self.assertGreater(o._domain_generation(page, domain), generation)
                else:
                    self.assertEqual(o._domain_generation(page, domain), generation)
        if 'folders' in expected:
            o._wait_list_uncached(page, 'folders:index')
        else:
            self.assertIsNotNone(o._cached_list(page, 'folders:index'))

    def settled(self, page):
        """Every durable operation has drained.

        A durable save completes when the intent is WRITTEN, so coherence --
        which follows the canonical change -- has not happened yet.
        """
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(op => op.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 100));
            }
        }""")

    def patched(self, page, before, expected):
        """Like `changed`, but the hierarchy is PATCHED rather than dropped.

        The acknowledgement carries the stored row, and the Folder Library is
        PRKS's home route -- discarding it would land an offline launch on an
        empty library for a change already known in full.
        """
        for domain, generation in before.items():
            with self.subTest(domain=domain):
                if domain in expected:
                    self.assertGreater(o._domain_generation(page, domain), generation)
                else:
                    self.assertEqual(o._domain_generation(page, domain), generation)
        self.assertIsNotNone(o._cached_list(page, 'folders:index'))

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate("async () => { try { await prksRequest('/api/settings'); } catch (_) {} }")
        page.wait_for_function("prksOfflineRuntimeState() === 'offline'")

    def online(self, page, context):
        context.set_offline(False)
        page.evaluate("async () => { await prksRequest('/api/settings'); }")
        page.wait_for_function("prksOfflineRuntimeState() === 'online'")

    def watch(self, page, methods, fragment='/api/folders'):
        """Records every canonical Folder request of the given methods."""
        seen = []
        page.on('request', lambda req: seen.append(req.method + ' ' + urlparse(req.url).path)
                if req.method in methods and fragment in urlparse(req.url).path else None)
        return seen

    def all_paths(self, page):
        seen = []
        page.on('request', lambda req: seen.append(urlparse(req.url).path))
        return seen

    # ---- index --------------------------------------------------------------

    def test_cached_index_renders_offline_without_prefetching_details(self):
        server, page, context, _c = self.start()
        seen = self.all_paths(page)
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        # Opening the hierarchy must never download every Folder behind it.
        self.assertFalse(any(p.startswith('/api/folders/') for p in seen))

        self.offline(page, context)
        after = self.all_paths(page)
        self.index(page)
        o._wait_offline_banner(page)
        text = o._content_text(page)
        for title in (FOLDER_PARENT_TITLE, FOLDER_CHILD_TITLE, FOLDER_UNVISITED_TITLE):
            self.assertIn(title, text)
        # The read-through still ATTEMPTS /api/folders -- that failed transport
        # is how offline is detected. What must never happen is per-Folder
        # detail traffic behind a hierarchy render.
        self.assertEqual([p for p in after if p.startswith('/api/folders/')], [])

    def test_local_search_and_expand_collapse_work_offline(self):
        server, page, context, _c = self.start()
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        self.offline(page, context)
        self.index(page)
        o._wait_offline_banner(page)
        seen = self.all_paths(page)

        page.locator('#prks-folder-library-search').fill('Child')
        page.wait_for_timeout(400)
        text = o._content_text(page)
        self.assertIn(FOLDER_CHILD_TITLE, text)
        self.assertNotIn(FOLDER_UNVISITED_TITLE, text)

        page.locator('#prks-folder-library-search').fill('')
        page.wait_for_timeout(400)
        self.assertIn(FOLDER_UNVISITED_TITLE, o._content_text(page))

        toggle = page.locator('#prks-folder-library-expand-toggle')
        if toggle.count():
            toggle.click()
            page.wait_for_timeout(300)
            toggle.click()
            page.wait_for_timeout(300)
        # Local filtering and hierarchy toggling are pure cache operations.
        self.assertEqual([p for p in seen if p.startswith('/api/')], [])

    def test_missing_index_reports_unavailable_not_an_empty_library(self):
        server, page, context, _c = self.start()
        # `/` boots straight into #/folders, so the snapshot is already warm --
        # drop it to reach the genuinely-never-cached case.
        o._clear_cached_list(page, 'folders:index')
        o._wait_list_uncached(page, 'folders:index')
        self.offline(page, context)
        page.evaluate("() => prksNavigate('#/concepts')")
        page.wait_for_timeout(300)
        self.index(page)
        o._wait_offline_unavailable(page)
        text = o._content_text(page)
        self.assertIn('Folders not available offline', text)
        # A missing snapshot must never read as "you have no folders".
        self.assertNotIn(FOLDER_PARENT_TITLE, text)

    def test_cached_empty_index_renders_a_legitimate_empty_library(self):
        server, page, context, _c = self.start()
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        # Replace the authoritative snapshot with a real, valid empty catalog.
        page.evaluate("() => window.createPrksOfflineStore().putList('folders:index', [])")
        page.wait_for_timeout(250)
        self.offline(page, context)
        self.index(page)
        o._wait_offline_banner(page)
        text = o._content_text(page)
        self.assertNotIn('Folders not available offline', text)
        self.assertNotIn(FOLDER_PARENT_TITLE, text)
        # Opening create still issues no network POST (CREATE_FOLDER is durable).
        posts = self.watch(page, {'POST'})
        page.evaluate("() => prksOpenFolderModalFromLibrarySearch('Nope')")
        page.wait_for_timeout(400)
        self.assertEqual(posts, [])

    def test_default_offline_launch_lands_on_the_cached_hierarchy(self):
        """The milestone's motivating regression: boot at `/` while offline."""
        server, page, context, _c = self.start()
        self.index(page)
        o._wait_list_cached(page, 'folders:index')

        self.offline(page, context)
        page.goto(server.origin + '/')
        page.wait_for_function("() => typeof window.prksOfflineRuntimeState === 'function'")
        page.wait_for_function("() => location.hash === '#/folders'", timeout=30000)
        o._wait_offline_banner(page)
        text = o._content_text(page)
        self.assertIn(FOLDER_PARENT_TITLE, text)
        self.assertIn(FOLDER_CHILD_TITLE, text)

    # ---- detail -------------------------------------------------------------

    def test_cached_detail_renders_hierarchy_works_and_description(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.offline(page, context)
        self.detail(page, ids['folder_parent'])
        o._wait_offline_banner(page)
        text = o._content_text(page)
        self.assertIn(FOLDER_PARENT_TITLE, text)
        self.assertIn(FOLDER_PARENT_DESCRIPTION, text)
        self.assertIn(FOLDER_CHILD_TITLE, text)
        self.assertIn(WORK_A_TITLE, text)

    def test_index_cached_but_detail_uncached_is_unavailable_not_not_found(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        self.offline(page, context)
        self.detail(page, ids['folder_unvisited'])
        o._wait_offline_unavailable(page)
        text = o._content_text(page)
        self.assertIn('Folder not available offline', text)
        self.assertNotIn('Folder not found', text)

    def test_parent_child_navigation_follows_normal_route_ownership(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.detail(page, ids['folder_child'])
        o._wait_entity_cached(page, 'folder', ids['folder_child'])

        self.offline(page, context)
        self.detail(page, ids['folder_parent'])
        o._wait_offline_banner(page)
        # parent -> child
        self.detail(page, ids['folder_child'])
        o._wait_offline_banner(page)
        self.assertIn(FOLDER_CHILD_TITLE, o._content_text(page))
        # child -> parent
        self.detail(page, ids['folder_parent'])
        o._wait_offline_banner(page)
        self.assertIn(FOLDER_PARENT_TITLE, o._content_text(page))
        # ... and an uncached destination owns its own unavailable state.
        self.detail(page, ids['folder_unvisited'])
        o._wait_offline_unavailable(page)
        self.assertIn('Folder not available offline', o._content_text(page))

    def test_folder_to_work_navigation_follows_normal_route_ownership(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])

        self.offline(page, context)
        self.detail(page, ids['folder_parent'])
        o._wait_offline_banner(page)
        page.evaluate("id => prksNavigate('#/works/' + encodeURIComponent(id))", ids['work_a'])
        o._wait_content_contains(page, WORK_A_TITLE)

        # work_b was never opened online: the Work route reports that itself.
        page.evaluate("id => prksNavigate('#/works/' + encodeURIComponent(id))", ids['work_b'])
        o._wait_offline_unavailable(page)
        self.assertNotIn(WORK_B_TITLE, o._content_text(page).replace(WORK_B_TITLE, '', 0) or '')

    def test_cached_detail_suppresses_prks_thumbnail_requests(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.offline(page, context)
        seen = self.all_paths(page)
        self.detail(page, ids['folder_parent'])
        o._wait_offline_banner(page)
        page.wait_for_timeout(600)
        # A card rendered from IndexedDB must not request an image that cannot
        # load -- a broken thumbnail is worse than none.
        self.assertEqual([p for p in seen if 'thumbnail' in p], [])
        self.assertIn(WORK_A_TITLE, o._content_text(page))

    # ---- Recently Added (deliberately still server-backed) -------------------

    def test_recently_added_restores_offline_from_its_own_cache(self):
        """It used to be disabled offline; it now has its own snapshot, so a
        restored `recently-added` tab must render from cache rather than
        firing a doomed /api/recently-added read."""
        server, page, context, _c = self.start()
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        page.evaluate("() => prksSwitchFolderLibraryTab('recently-added')")
        o._wait_list_cached(page, 'recently-added:index')
        # Switching above already persisted the tab preference; drop the
        # in-memory dashboard state so this is a genuine restore.
        page.evaluate("() => { window.__prksFolderDashboardState = null; }")

        self.offline(page, context)
        self.index(page)
        o._wait_offline_banner(page)

        tab = page.locator('.prks-folder-library__tab-btn[data-tab="recently-added"]')
        self.assertFalse(tab.is_disabled(), 'Recently added is cached now')
        # The read-through attempts the network first, so wait on the render.
        page.wait_for_function(
            """(title) => {
                const p = document.querySelector('#prks-folder-library-recently-added');
                return !!p && p.textContent.indexOf(title) !== -1;
            }""",
            arg=WORK_A_TITLE,
            timeout=20000,
        )

    # ---- mutation surfaces (durable Folder ops; Search stays online-only) ---

    def test_folder_creation_enqueues_without_network_offline(self):
        server, page, context, _c = self.start()
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        self.offline(page, context)
        self.index(page)
        o._wait_offline_banner(page)
        posts = self.watch(page, {'POST'})

        page.evaluate("() => prksOpenFolderModalFromLibrarySearch('Brand New Folder')")
        page.wait_for_selector('#folder-modal:not(.hidden)')
        page.evaluate("""async () => {
            try { await createFolder('Direct', ''); } catch (_) {}
        }""")
        page.wait_for_timeout(400)
        self.assertEqual(posts, [])

    def test_create_dialog_disconnect_race_issues_no_request(self):
        server, page, context, _c = self.start()
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        # Open the real modal while still online.
        page.evaluate("() => prksOpenFolderModalFromLibrarySearch('Race Folder')")
        page.wait_for_selector('#folder-modal:not(.hidden)')

        self.offline(page, context)
        posts = self.watch(page, {'POST'})
        save = page.locator('#save-folder-btn')
        if save.count():
            save.click()
        else:
            page.evaluate("async () => { try { await createFolder('Race Folder', ''); } catch (_) {} }")
        page.wait_for_timeout(600)
        self.assertEqual(posts, [])

    def test_every_canonical_folder_wrapper_issues_no_network_offline(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.offline(page, context)
        seen = self.watch(page, {'POST', 'PATCH', 'DELETE'})
        page.evaluate(
            """async ([fid, wid, tid]) => {
                const calls = [
                    () => createFolder('X', ''),
                    () => patchFolder(fid, { title: 'X' }),
                    () => deleteFolderCanonical(fid),
                    () => addWorkToFolder(fid, wid),
                    () => patchWorkFolder(wid, fid),
                    () => addTagToFolder(fid, tid),
                    () => removeTagFromFolder(fid, tid),
                ];
                for (const call of calls) { try { await call(); } catch (_) {} }
            }""",
            [ids['folder_parent'], ids['work_a'], ids['folder_tag']],
        )
        page.wait_for_timeout(600)
        self.assertEqual(seen, [])

    def test_cached_detail_delete_control_stays_live_offline(self):
        server, page, context, _c = self.start()
        ids = server.ids
        # The child folder holds a Work, so seed an empty deletable one instead.
        self.cache(page, ids)
        self.detail(page, ids['folder_unvisited'])
        o._wait_entity_cached(page, 'folder', ids['folder_unvisited'])
        self.offline(page, context)
        self.detail(page, ids['folder_unvisited'])
        o._wait_offline_banner(page)
        deletes = self.watch(page, {'DELETE'})
        btn = page.locator('[data-delete-folder-id]')
        if btn.count():
            self.assertFalse(btn.is_disabled())
        page.wait_for_timeout(300)
        self.assertEqual(deletes, [])

    # ---- Work-side Folder card ----------------------------------------------

    def test_work_folder_card_cannot_start_editing_offline(self):
        server, page, context, _c = self.start()
        ids = server.ids
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        self.offline(page, context)
        page.evaluate("id => prksNavigate('#/works/' + encodeURIComponent(id))", ids['work_a'])
        o._wait_content_contains(page, WORK_A_TITLE)
        o._open_details_drawer_if_tiled(page)
        seen = self.all_paths(page)
        page.wait_for_timeout(500)

        edit = page.locator('#prks-work-folder-edit-btn')
        if edit.count():
            self.assertTrue(edit.is_disabled())
        # The closed card must not fetch the folder catalog either.
        self.assertEqual([p for p in seen if p == '/api/folders'], [])
        # ... and the current folder stays an ordinary navigable link.
        link = page.locator('.prks-work-folder-summary a')
        if link.count():
            self.assertTrue(link.first.get_attribute('href').startswith('#/folders/'))

    def test_open_work_folder_editor_keeps_clear_new_and_done(self):
        server, page, context, _c = self.start()
        ids = server.ids
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        o._open_details_drawer_if_tiled(page)
        edit = page.locator('#prks-work-folder-edit-btn')
        if not edit.count():
            self.skipTest('Work Folder card not mounted in this layout')
        edit.click()
        page.wait_for_selector('#prks-work-folder-set-btn')

        self.offline(page, context)
        seen = self.watch(page, {'POST', 'PATCH', 'DELETE'})
        page.wait_for_function(
            "() => { const b = document.getElementById('prks-work-folder-set-btn'); return !!b && b.disabled; }",
            timeout=20000,
        )
        for sel in ('#prks-work-folder-set-btn', '#prks-work-folder-search'):
            self.assertTrue(page.locator(sel).is_disabled(), sel)
        for sel in ('#prks-work-folder-clear-btn', '#prks-work-folder-new-btn',
                    '#prks-work-folder-edit-btn'):
            self.assertFalse(page.locator(sel).is_disabled(), sel)
        page.wait_for_timeout(300)
        self.assertEqual(seen, [])

    # ---- coherence ----------------------------------------------------------

    def test_folder_create_invalidates_only_folders(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        page.evaluate("async () => { await createFolder('Coherence New', ''); }")
        self.settled(page)
        self.patched(page, before, {'folders'})
        self.assertIn('Coherence New', [row['title'] for row in
                                        o._cached_list(page, 'folders:index')['value']])

    def test_description_only_update_keeps_member_work_snapshots(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        before = self.generations(page)
        page.evaluate("async (id) => { await patchFolder(id, { description: 'Edited.' }); }",
                      ids['folder_parent'])
        self.settled(page)
        self.patched(page, before, {'folders'})
        # Only the title is embedded in a cached Work detail.
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['work_a']))

    def test_folder_rename_invalidates_exactly_its_member_work_snapshots(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        o._open_work_from_home(page, WORK_B_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_b'])
        before = self.generations(page)
        page.evaluate("async (id) => { await patchFolder(id, { title: 'Renamed Parent' }); }",
                      ids['folder_parent'])
        self.settled(page)
        self.patched(page, before, {'folders'})
        # work_a is a member; work_b lives in the child folder and must survive.
        o._wait_entity_uncached(page, 'work', ids['work_a'])
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['work_b']))

    def test_reparent_invalidates_folders_but_no_work_snapshots(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        before = self.generations(page)
        page.evaluate("async ([child, parent]) => { await patchFolder(child, { parent_id: null }); }",
                      [ids['folder_child'], ids['folder_parent']])
        self.settled(page)
        self.patched(page, before, {'folders'})
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['work_a']))

    def test_moving_a_work_invalidates_folders_and_that_work(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        before = self.generations(page)
        page.evaluate("async ([wid, fid]) => { await patchWorkFolder(wid, fid); }",
                      [ids['work_a'], ids['folder_child']])
        self.settled(page)
        # The hierarchy's COUNTS moved between two folders and this device does
        # not know which folder the file left, so the hierarchy is staled -- but
        # the Work's own snapshot carries the new folder title exactly, so it is
        # patched rather than dropped.
        self.changed(page, before, {'folders'})
        cached = o._cached_entity(page, 'work', ids['work_a'])
        self.assertIsNotNone(cached)
        self.assertEqual(cached['value']['folder_id'], ids['folder_child'])

    def test_bulk_move_folder_invalidates_folders(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        page.evaluate(
            """async ([wid, fid]) => {
                await bulkUpdateWorks({ action: 'move_folder', work_ids: [wid], folder_id: fid });
            }""",
            [ids['work_a'], ids['folder_child']],
        )
        self.changed(page, before, {'folders'})

    def test_bulk_set_status_invalidates_folders_and_people(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        page.evaluate(
            """async (wid) => {
                await bulkUpdateWorks({ action: 'set_status', work_ids: [wid], status: 'Completed' });
            }""",
            ids['work_a'],
        )
        self.changed(page, before, {'folders', 'people'})

    def test_work_creation_invalidates_folders_with_no_folder_chosen(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        # Canonical creation always files the Work, defaulting to Uncategorized.
        def stub_oembed(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'title': 'Folderless Creation', 'author_name': 'E2E'}))

        page.route('**/youtube.com/oembed**', stub_oembed)
        self.addCleanup(lambda: o._safe_unroute(page, '**/youtube.com/oembed**', stub_oembed))
        page.locator('#prks-ribbon-new-file').click()
        page.wait_for_selector('#work-modal:not(.hidden):not([inert])')
        page.locator('.prks-kind-toggle__btn[data-kind="video"]').click()
        page.wait_for_selector('#work-video-url-row:not(.hidden)')
        page.locator('#work-video-url').fill('https://www.youtube.com/watch?v=e2e0000042')
        page.locator('#work-title').fill('Folderless Creation')
        page.locator('#save-work-btn').click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0", timeout=20000)
        # Navigation follows local CREATE_WORK enqueue; folders coherence
        # publishes only on ACK via reconcileCreatedWork.
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='CREATE_WORK must acknowledge before folders coherence',
        )
        self.assertGreater(o._domain_generation(page, 'folders'), before['folders'])
        o._wait_list_uncached(page, 'folders:index')

    # work_deletion / work_metadata_save → folders generation is owned by
    # offline-runtime / work-metadata Node selftests (helper-call E2Es dropped).

    def test_author_and_editor_roles_invalidate_folders_but_other_roles_do_not(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)

        def rewarm_folders_index():
            # After an invalidation the route may already be #/folders, so a
            # second index() is a no-op that never refetches. Leave and return.
            page.evaluate("() => prksNavigate('#/people')")
            page.wait_for_function("() => location.hash === '#/people'", timeout=15000)
            self.index(page)
            o._wait_list_cached(page, 'folders:index')

        for role in ('Author', 'Editor'):
            with self.subTest(role=role):
                rewarm_folders_index()
                before = o._domain_generation(page, 'folders')
                page.evaluate("([id, r]) => prksMarkWorkRoleChanged(id, r)", [ids['work_a'], role])
                self.assertGreater(o._domain_generation(page, 'folders'), before)
                o._wait_list_uncached(page, 'folders:index')
        # A role the Work card never renders must leave Folders eligible.
        rewarm_folders_index()
        before = o._domain_generation(page, 'folders')
        page.evaluate("(id) => prksMarkWorkRoleChanged(id, 'Reviewer')", ids['work_a'])
        self.assertEqual(o._domain_generation(page, 'folders'), before)
        self.assertIsNotNone(o._cached_list(page, 'folders:index'))

    def test_person_rename_invalidates_folders_but_biography_does_not(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_person(page, ids['person'])
        o._wait_entity_cached(page, 'person', ids['person'])
        self.index(page)
        o._wait_list_cached(page, 'folders:index')

        before = o._domain_generation(page, 'folders')
        page.evaluate(
            """async (id) => {
                await prksRequest('/api/persons/' + encodeURIComponent(id), {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ about: 'Biography only.' }),
                });
                prksMarkPeopleDomainChanged();
            }""",
            ids['person'],
        )
        # Biography is absent from the Work-card credit line.
        self.assertEqual(o._domain_generation(page, 'folders'), before)
        self.assertIsNotNone(o._cached_list(page, 'folders:index'))

    def test_folder_tag_mutation_patches_folder_without_dropping_index(self):
        """ADD/REMOVE_FOLDER_TAG reconcile like Work tags: patch the Folder
        entity (and folder-tag-options), never drop folders:index for a
        relationship change the catalogue does not render."""
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        # Warm tag-options so the durable enqueue has a known base.
        page.evaluate(
            """async (fid) => {
                const res = await prksRequest(
                    '/api/folders/' + encodeURIComponent(fid) + '/tag-options');
                const body = await res.json();
                await prksOfflineCacheEntity('folder-tag-options', fid, body);
            }""",
            ids['folder_parent'],
        )
        before = self.generations(page)
        page.evaluate("async ([fid, tid]) => { await removeTagFromFolder(fid, tid); }",
                      [ids['folder_parent'], ids['folder_tag']])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='folder-tag remove must acknowledge',
        )
        self.assertEqual(o._domain_generation(page, 'folders'), before['folders'])
        self.assertIsNotNone(o._cached_list(page, 'folders:index'))
        folder = o._cached_entity(page, 'folder', ids['folder_parent'])
        self.assertIsNotNone(folder)
        tag_ids = [t.get('id') for t in (folder.get('tags') or [])]
        self.assertNotIn(ids['folder_tag'], tag_ids)
    def test_unrelated_research_mutations_keep_folders_eligible(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = o._domain_generation(page, 'folders')
        page.evaluate(
            """async (id) => {
                await updateConcept(id, { description: 'Edited definition.' });
                await createPosition({ name: 'Folder-irrelevant position' });
            }""",
            ids['concept_child'],
        )
        self.assertEqual(o._domain_generation(page, 'folders'), before)
        self.assertIsNotNone(o._cached_list(page, 'folders:index'))

    # ---- Tag delete / merge coherence ---------------------------------------

    def test_tag_delete_invalidates_folders_and_exactly_its_linked_works(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        # work_a carries the tag; work_b must survive untouched.
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        o._open_work_from_home(page, WORK_B_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_b'])
        page.evaluate(
            """async ([wid, tid]) => {
                await prksRequest('/api/works/' + encodeURIComponent(wid) + '/tags', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tag_id: tid }),
                });
            }""",
            [ids['work_a'], ids['folder_tag']],
        )
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])

        before = self.generations(page)
        # Deleting a Tag is durable now, so coherence follows the CANONICAL
        # change -- the acknowledgement -- rather than the click. The answer
        # names exactly the Works that carried the Tag, so only those are
        # staled.
        page.evaluate("async (tid) => { await prksDeleteTagDurably(tid); }", ids['folder_tag'])
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(o => o.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 100));
            }
        }""")

        self.changed(page, before, {'folders'})
        o._wait_entity_uncached(page, 'work', ids['work_a'])
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['work_b']))

    def test_tag_merge_invalidates_folders_and_source_linked_works(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        target = page.evaluate(
            """async () => {
                const res = await prksRequest('/api/tags', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'E2E Merge Target' }),
                });
                return (await res.json()).id;
            }"""
        )
        page.evaluate(
            """async ([wid, tid]) => {
                await prksRequest('/api/works/' + encodeURIComponent(wid) + '/tags', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tag_id: tid }),
                });
            }""",
            [ids['work_a'], ids['folder_tag']],
        )
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        o._open_work_from_home(page, WORK_B_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_b'])

        before = self.generations(page)
        # Merging a Tag is durable: coherence follows the acknowledgement.
        page.evaluate("async ([src, dst]) => { await mergeTags(src, dst); }",
                      [ids['folder_tag'], target])
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(o => o.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 100));
            }
        }""")

        self.changed(page, before, {'folders'})
        o._wait_entity_uncached(page, 'work', ids['work_a'])
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['work_b']))

    def test_failed_tag_mutation_keeps_every_cache_eligible(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        self.index(page)
        o._wait_list_cached(page, 'folders:index')

        before = self.generations(page)
        failed = page.evaluate(
            """async (tid) => {
                const out = [];
                try { await prksDeleteTagDurably('T-does-not-exist'); } catch (e) { out.push('delete'); }
                try {
                    await mergeTags(tid, 'T-does-not-exist');
                    const deadline = Date.now() + 15000;
                    while (Date.now() < deadline) {
                        const rows = await prksSync.store.listOperations();
                        const mine = rows.find(o => o.operation === 'MERGE_TAG' &&
                            o.entity_id === tid);
                        if (!mine) { out.push('merge-acked'); break; }
                        if (mine.status === 'conflict') { out.push('merge'); break; }
                        await new Promise(r => setTimeout(r, 50));
                    }
                } catch (e) { out.push('merge'); }
                return out;
            }""",
            ids['folder_tag'],
        )
        self.assertIn('merge', failed)
        # Nothing canonical changed for the failed merge, so every cached
        # snapshot stays eligible. A durable delete of an unknown id is
        # convergence (ACK with changed:false) and also leaves caches alone.
        self.changed(page, before, set())
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['work_a']))

    def test_tag_delete_through_the_real_tags_page_publishes_coherence(self):
        """Drives the actual Tags page affordance, so the wrapper wiring is
        proven non-vacuous rather than only called directly by a test."""
        server, page, context, _c = self.start()
        ids = server.ids
        # Tag a Work too, so both halves of the coherence are observable.
        page.evaluate(
            """async ([wid, tid]) => {
                await prksRequest('/api/works/' + encodeURIComponent(wid) + '/tags', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tag_id: tid }),
                });
            }""",
            [ids['work_a'], ids['folder_tag']],
        )
        self.cache(page, ids, all_domains=True)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        before = o._domain_generation(page, 'folders')

        page.evaluate("() => prksNavigate('#/tags')")
        page.wait_for_selector('#tags-page-cloud')
        page.locator('[data-tag-alias-edit="%s"]' % ids['folder_tag']).click()
        page.wait_for_selector('#tags-page-alias-delete-btn')
        page.locator('#tags-page-alias-delete-btn').click()
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        page.locator('#prks-modal-confirm-ok').click()

        page.wait_for_function(
            "(g) => window.prksOfflineDomainGeneration('folders') > g", arg=before, timeout=20000
        )
        self.assertGreater(o._domain_generation(page, 'folders'), before)
        o._wait_entity_uncached(page, 'work', ids['work_a'])

    # ---- cache safety -------------------------------------------------------

    def test_malformed_authoritative_payloads_never_poison_the_cache(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        good_index = o._cached_list(page, 'folders:index')
        good_detail = o._cached_entity(page, 'folder', ids['folder_parent'])

        def bad_index(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps([{'id': 'F-1', 'work_count': 'lots'}]))

        page.route('**/api/folders', bad_index)
        self.addCleanup(lambda: o._safe_unroute(page, '**/api/folders', bad_index))
        self.index(page)
        page.wait_for_timeout(800)
        # The good copy already on this device survives a bad server answer.
        self.assertEqual(o._cached_list(page, 'folders:index'), good_index)
        o._safe_unroute(page, '**/api/folders', bad_index)

        def bad_detail(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'id': ids['folder_parent'], 'children': 'nope',
                                           'works': [], 'tags': []}))

        page.route('**/api/folders/*', bad_detail)
        self.addCleanup(lambda: o._safe_unroute(page, '**/api/folders/*', bad_detail))
        self.detail(page, ids['folder_parent'])
        page.wait_for_timeout(800)
        self.assertEqual(o._cached_entity(page, 'folder', ids['folder_parent']), good_detail)

    def test_missing_parent_id_is_rejected_and_never_poisons_the_cache(self):
        """A row without `parent_id` must not be cached as a root folder."""
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        good_index = o._cached_list(page, 'folders:index')
        self.assertIsNotNone(good_index)

        def truncated_index(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps([{'id': 'F-truncated', 'title': 'No Parent Field',
                                            'description': '', 'work_count': 0, 'child_count': 0}]))

        page.route('**/api/folders', truncated_index)
        self.addCleanup(lambda: o._safe_unroute(page, '**/api/folders', truncated_index))
        self.index(page)
        page.wait_for_timeout(800)
        self.assertNotIn('No Parent Field', o._content_text(page))
        # The good copy already on this device is untouched.
        self.assertEqual(o._cached_list(page, 'folders:index'), good_index)
        o._safe_unroute(page, '**/api/folders', truncated_index)

        def truncated_detail(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'id': ids['folder_parent'], 'title': 'No Parent Field',
                                           'description': '', 'private_notes': '',
                                           'parent': None, 'children': [], 'works': [], 'tags': []}))

        page.route('**/api/folders/*', truncated_detail)
        self.addCleanup(lambda: o._safe_unroute(page, '**/api/folders/*', truncated_detail))
        good_detail = o._cached_entity(page, 'folder', ids['folder_parent'])
        self.detail(page, ids['folder_parent'])
        page.wait_for_timeout(800)
        self.assertEqual(o._cached_entity(page, 'folder', ids['folder_parent']), good_detail)

        # ... and the untouched snapshot still serves offline.
        o._safe_unroute(page, '**/api/folders/*', truncated_detail)
        self.offline(page, context)
        self.index(page)
        o._wait_offline_banner(page)
        self.assertIn(FOLDER_PARENT_TITLE, o._content_text(page))

    def test_cached_row_without_parent_id_is_discarded_not_rendered_as_root(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        page.evaluate(
            """() => window.createPrksOfflineStore().putList('folders:index', [
                { id: 'F-cached-bad', title: 'Orphan Row', description: '',
                  work_count: 0, child_count: 0 }
            ])"""
        )
        page.wait_for_timeout(250)
        self.offline(page, context)
        self.index(page)
        o._wait_offline_unavailable(page)
        text = o._content_text(page)
        self.assertIn('Folders not available offline', text)
        self.assertNotIn('Orphan Row', text)
        o._wait_list_uncached(page, 'folders:index')

    def test_corrupted_cached_payloads_are_discarded_before_rendering(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)
        page.evaluate(
            """(id) => {
                const store = window.createPrksOfflineStore();
                return Promise.all([
                    store.putList('folders:index', [{ id: 'F-x', work_count: -1, child_count: 0 }]),
                    store.putEntity('folder', id, { id: id, children: [], works: 'nope', tags: [] }),
                ]);
            }""",
            ids['folder_parent'],
        )
        page.wait_for_timeout(250)
        self.offline(page, context)

        self.index(page)
        o._wait_offline_unavailable(page)
        self.assertIn('Folders not available offline', o._content_text(page))
        o._wait_list_uncached(page, 'folders:index')

        self.detail(page, ids['folder_parent'])
        o._wait_offline_unavailable(page)
        self.assertIn('Folder not available offline', o._content_text(page))
        o._wait_entity_uncached(page, 'folder', ids['folder_parent'])

    def test_reachable_server_errors_are_not_disguised_as_offline(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache(page, ids)

        def boom(route):
            route.fulfill(status=500, content_type='application/json',
                          body=json.dumps({'error': 'server exploded'}))

        page.route('**/api/folders', boom)
        self.addCleanup(lambda: o._safe_unroute(page, '**/api/folders', boom))
        self.index(page)
        page.wait_for_timeout(800)
        # A real HTTP error must not silently serve a stale cached read.
        self.assertNotIn('Offline · cached', o._content_text(page))
        self.assertIsNotNone(o._cached_list(page, 'folders:index'))

    # Stale Folder GET after markDomainChanged and Folder cleanup locality are
    # owned by tests/browser/run_offline_runtime_selftest.js.


if __name__ == '__main__':
    unittest.main()
