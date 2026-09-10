"""Playlist read models, cache safety, live controls and domain boundaries."""
import json
import os
import unittest
from urllib.parse import urlparse

from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    PLAYLIST_A_DESCRIPTION,
    PLAYLIST_A_TITLE,
    PLAYLIST_B_TITLE,
    PLAYLIST_CHANNEL,
    PLAYLIST_VIDEO_ONE_TITLE,
    PLAYLIST_VIDEO_TWO_TITLE,
    PRKSDatabase,
    SCHEMA,
    StorageConfig,
    seed_playlists_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class PlaylistsOfflineTests(unittest.TestCase):
    def start(self, seed=seed_playlists_library):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        o._wait_sw_active(page)
        return server, page, context

    # ---- navigation helpers -------------------------------------------------

    def index(self, page):
        page.evaluate("() => prksNavigate('#/playlists')")

    def detail(self, page, pid):
        page.evaluate("id => prksNavigate('#/playlists/' + encodeURIComponent(id))", pid)

    def cache(self, page, ids, all_domains=False):
        """Warms the Playlist index and Playlist A's detail; `all_domains` adds
        the other five so domain independence is observable at once."""
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
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        self.detail(page, ids['playlist_a'])
        o._wait_entity_cached(page, 'playlist', ids['playlist_a'])

    def cache_member_work(self, page, ids):
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        o._wait_entity_cached(page, 'work', ids['playlist_video_one'])

    def generations(self, page):
        return {d: o._domain_generation(page, d) for d in
                ('concepts', 'positions', 'arguments', 'people', 'person-groups', 'playlists')}

    def changed(self, page, before, expected):
        for domain, generation in before.items():
            with self.subTest(domain=domain):
                if domain in expected:
                    self.assertGreater(o._domain_generation(page, domain), generation)
                else:
                    self.assertEqual(o._domain_generation(page, domain), generation)
        if 'playlists' in expected:
            o._wait_list_uncached(page, 'playlists:index')
        else:
            self.assertIsNotNone(o._cached_list(page, 'playlists:index'))

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate("async () => { try { await prksRequest('/api/settings'); } catch (_) {} }")
        page.wait_for_function("prksOfflineRuntimeState() === 'offline'")

    def online(self, page, context):
        context.set_offline(False)
        page.evaluate("async () => { await prksRequest('/api/settings'); }")
        page.wait_for_function("prksOfflineRuntimeState() === 'online'")

    def watch(self, page, methods, fragment='/api/playlists'):
        """Records every canonical Playlist request of the given methods."""
        seen = []
        page.on('request', lambda req: seen.append(req.method + ' ' + urlparse(req.url).path)
                if req.method in methods and fragment in urlparse(req.url).path else None)
        return seen

    def open_details_panel(self, page):
        o._open_details_drawer_if_tiled(page)

    # ---- index --------------------------------------------------------------

    def test_cached_index_renders_offline_without_prefetching_details(self):
        server, page, context = self.start()
        seen = []
        page.on('request', lambda req: seen.append(urlparse(req.url).path))
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        # Opening the index must never download every Playlist behind it.
        self.assertFalse(any(p.startswith('/api/playlists/') for p in seen))

        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        body = o._content_text(page)
        self.assertIn(PLAYLIST_A_TITLE, body)
        self.assertIn(PLAYLIST_B_TITLE, body)
        self.assertIn('2 items', body)
        # The one creation control on this route is unavailable offline.
        self.open_details_panel(page)
        self.assertTrue(page.locator('#prks-create-playlist-btn').is_disabled())

    def test_missing_index_cache_is_not_the_empty_state(self):
        server, page, context = self.start()
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        o._clear_cached_list(page, 'playlists:index')
        o._wait_list_uncached(page, 'playlists:index')

        context.set_offline(True)
        self.index(page)
        o._wait_offline_unavailable(page)
        body = o._content_text(page)
        self.assertIn('Playlists not available offline', body)
        self.assertNotIn('No playlists yet.', body)

    def test_legitimately_empty_index_renders_its_empty_state(self):
        def empty(root):
            ids = seed_playlists_library(root)
            db = PRKSDatabase(storage=StorageConfig.for_testing(root), schema_path=str(SCHEMA))
            db.delete_playlist(ids['playlist_a'])
            db.delete_playlist(ids['playlist_b'])
            return ids

        server, page, context = self.start(empty)
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        self.assertEqual(o._cached_list(page, 'playlists:index')['value'], [])

        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        body = o._content_text(page)
        self.assertIn('No playlists yet.', body)
        self.assertNotIn('not available offline', body)
        # A cached-empty list is still not a licence to create one offline.
        self.open_details_panel(page)
        self.assertTrue(page.locator('#prks-create-playlist-btn').is_disabled())

    # ---- detail -------------------------------------------------------------

    def test_cached_detail_renders_order_and_item_fields_offline(self):
        server, page, context = self.start()
        self.cache(page, server.ids)

        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        body = o._content_text(page)
        self.assertIn(PLAYLIST_A_TITLE, body)
        self.assertIn(PLAYLIST_A_DESCRIPTION, body)
        self.assertIn(PLAYLIST_CHANNEL, body)
        self.assertIn('04/03/2021', body)
        titles = page.evaluate(
            """() => [...document.querySelectorAll('.prks-playlist-item__title')].map(e => e.textContent.trim())"""
        )
        self.assertEqual(titles, [PLAYLIST_VIDEO_ONE_TITLE, PLAYLIST_VIDEO_TWO_TITLE])

    def test_cached_index_with_uncached_detail_is_explicit(self):
        server, page, context = self.start()
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        # Playlist B is deliberately never opened online.
        self.assertIsNone(o._cached_entity(page, 'playlist', server.ids['playlist_b']))

        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        page.locator('[data-prks-route="#/playlists/%s"]' % server.ids['playlist_b']).click()
        o._wait_offline_unavailable(page)
        body = o._content_text(page)
        self.assertIn('not available offline', body)
        self.assertNotIn('Playlist not found', body)

    def test_detail_http_errors_keep_their_normal_meaning(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        good = o._cached_entity(page, 'playlist', server.ids['playlist_a'])

        self.detail(page, 'PL-DOES-NOT-EXIST')
        o._wait_content_contains(page, 'Playlist not found')
        self.assertNotIn('not available offline', o._content_text(page))

        # A reachable server error is never disguised as an offline condition,
        # and never falls back to the good cached copy.
        def fail(route):
            route.fulfill(status=500, content_type='application/json', body='{}')

        pattern = '**/api/playlists/%s' % server.ids['playlist_a']
        page.route(pattern, fail)
        try:
            self.detail(page, server.ids['playlist_a'])
            page.wait_for_timeout(600)
            self.assertNotIn('not available offline', o._content_text(page))
            self.assertEqual(o._cached_entity(page, 'playlist', server.ids['playlist_a']), good)
        finally:
            o._safe_unroute(page, pattern, fail)

    # ---- navigation ---------------------------------------------------------

    def test_playlist_work_links_use_ordinary_work_offline_support(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        self.detail(page, ids['playlist_a'])

        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        # A cached Work opens exactly as it would online.
        page.locator('[data-pl-nav="%s"]' % ids['playlist_video_one']).click()
        page.wait_for_function('id => location.hash.indexOf(id) !== -1', arg=ids['playlist_video_one'])
        o._wait_content_contains(page, PLAYLIST_VIDEO_ONE_TITLE)

        # An uncached one reports the Work route's own unavailable state --
        # never a Playlist-side refusal.
        self.detail(page, ids['playlist_a'])
        o._wait_offline_banner(page)
        link = page.locator('[data-pl-nav="%s"]' % ids['playlist_video_two'])
        self.assertIsNone(link.get_attribute('aria-disabled'))
        link.click()
        page.wait_for_function('id => location.hash.indexOf(id) !== -1', arg=ids['playlist_video_two'])
        o._wait_offline_unavailable(page)
        self.assertIn('not available offline', o._content_text(page))

        # "All playlists" is an ordinary route back to the cached index.
        self.detail(page, ids['playlist_a'])
        o._wait_offline_banner(page)
        page.locator('a[href="#/playlists"]').first.click()
        page.wait_for_function("() => location.hash === '#/playlists'")
        o._wait_offline_banner(page)
        self.assertIn(PLAYLIST_A_TITLE, o._content_text(page))

    # ---- validators ---------------------------------------------------------

    def test_predicates_protect_every_rendered_field(self):
        server, page, context = self.start()
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        cases = page.evaluate(
            """() => ({
                indexEmpty: prksIsPlaylistsIndexShape([]),
                indexOk: prksIsPlaylistsIndexShape([{ id: 'PL-1', title: 'A', item_count: 0 }]),
                indexSparse: prksIsPlaylistsIndexShape([{ id: 'PL-1', item_count: 3 }]),
                indexNotArray: prksIsPlaylistsIndexShape({ error: 'boom' }),
                indexNull: prksIsPlaylistsIndexShape(null),
                indexMissingId: prksIsPlaylistsIndexShape([{ title: 'A', item_count: 0 }]),
                indexBlankId: prksIsPlaylistsIndexShape([{ id: '  ', title: 'A', item_count: 0 }]),
                indexNestedArrayRow: prksIsPlaylistsIndexShape([['PL-1']]),
                indexCountMissing: prksIsPlaylistsIndexShape([{ id: 'PL-1', title: 'A' }]),
                indexCountString: prksIsPlaylistsIndexShape([{ id: 'PL-1', item_count: '3' }]),
                indexCountNegative: prksIsPlaylistsIndexShape([{ id: 'PL-1', item_count: -1 }]),
                indexTitleNumber: prksIsPlaylistsIndexShape([{ id: 'PL-1', title: 7, item_count: 0 }]),
                indexUrlObject: prksIsPlaylistsIndexShape([{ id: 'PL-1', original_url: {}, item_count: 0 }]),

                detailOk: prksIsPlaylistShape({ id: 'PL-1', title: 'A', items: [] }, 'PL-1'),
                detailWithItem: prksIsPlaylistShape({ id: 'PL-1', items: [
                    { id: 'W-1', title: 'V', author_text: 'C', published_date: '2020-01-01', position: 0 }] }, 'PL-1'),
                detailSparseItem: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', position: 0 }] }, 'PL-1'),
                detailWrongId: prksIsPlaylistShape({ id: 'PL-2', items: [] }, 'PL-1'),
                detailNull: prksIsPlaylistShape(null, 'PL-1'),
                detailArray: prksIsPlaylistShape([], 'PL-1'),
                detailItemsMissing: prksIsPlaylistShape({ id: 'PL-1' }, 'PL-1'),
                detailItemsNotArray: prksIsPlaylistShape({ id: 'PL-1', items: {} }, 'PL-1'),
                detailItemNull: prksIsPlaylistShape({ id: 'PL-1', items: [null] }, 'PL-1'),
                detailItemBlankId: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: '', position: 0 }] }, 'PL-1'),
                detailItemNestedArray: prksIsPlaylistShape({ id: 'PL-1', items: [['W-1']] }, 'PL-1'),
                detailItemBadTitle: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', title: 3, position: 0 }] }, 'PL-1'),
                detailItemBadChannel: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', author_text: [], position: 0 }] }, 'PL-1'),
                detailItemBadDate: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', published_date: 2020, position: 0 }] }, 'PL-1'),
                detailItemNoPosition: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1' }] }, 'PL-1'),
                detailItemFractionalPosition: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', position: 1.5 }] }, 'PL-1'),
                detailItemNegativePosition: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', position: -1 }] }, 'PL-1'),
                detailOneBadItem: prksIsPlaylistShape({ id: 'PL-1', items: [
                    { id: 'W-1', position: 0 }, { id: 'W-2' }] }, 'PL-1'),
            })"""
        )
        self.assertEqual(cases, {
            'indexEmpty': True, 'indexOk': True, 'indexSparse': True,
            'indexNotArray': False, 'indexNull': False, 'indexMissingId': False,
            'indexBlankId': False, 'indexNestedArrayRow': False,
            'indexCountMissing': False, 'indexCountString': False,
            'indexCountNegative': False, 'indexTitleNumber': False, 'indexUrlObject': False,
            'detailOk': True, 'detailWithItem': True, 'detailSparseItem': True,
            'detailWrongId': False, 'detailNull': False, 'detailArray': False,
            'detailItemsMissing': False, 'detailItemsNotArray': False,
            'detailItemNull': False, 'detailItemBlankId': False,
            'detailItemNestedArray': False, 'detailItemBadTitle': False,
            'detailItemBadChannel': False, 'detailItemBadDate': False,
            'detailItemNoPosition': False, 'detailItemFractionalPosition': False,
            'detailItemNegativePosition': False, 'detailOneBadItem': False,
        })

    def test_malformed_authoritative_index_preserves_the_cache(self):
        server, page, context = self.start()
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        good = o._cached_list(page, 'playlists:index')

        def malformed(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps([{'title': 'No id here', 'item_count': '3'}]))

        page.route('**/api/playlists', malformed)
        try:
            self.detail(page, server.ids['playlist_a'])
            self.index(page)
            page.wait_for_timeout(700)
            self.assertNotIn('not available offline', o._content_text(page))
            self.assertEqual(o._cached_list(page, 'playlists:index'), good)
        finally:
            o._safe_unroute(page, '**/api/playlists', malformed)

        # The old good cache is still what serves offline.
        context.set_offline(True)
        self.index(page)
        o._wait_offline_banner(page)
        self.assertIn(PLAYLIST_A_TITLE, o._content_text(page))

    def test_malformed_authoritative_detail_preserves_the_cache(self):
        server, page, context = self.start()
        pid = server.ids['playlist_a']
        self.cache(page, server.ids)
        good = o._cached_entity(page, 'playlist', pid)

        for body in (
            {'id': pid, 'title': 'Broken', 'items': {}},
            {'id': pid, 'title': 'Broken', 'items': [{'title': 'no id', 'position': 0}]},
            {'id': pid, 'title': 'Broken', 'items': [{'id': 'W-1', 'author_text': 7, 'position': 0}]},
            {'id': pid, 'title': 'Broken', 'items': [{'id': 'W-1', 'published_date': 2020, 'position': 0}]},
        ):
            with self.subTest(body=body):
                # A closure, not a default arg: Playwright calls route handlers
                # with (route, request), which would bind over a second param.
                def make_handler(payload):
                    def malformed(route):
                        route.fulfill(status=200, content_type='application/json',
                                      body=json.dumps(payload))
                    return malformed

                malformed = make_handler(body)
                pattern = '**/api/playlists/%s' % pid
                page.route(pattern, malformed)
                try:
                    self.index(page)
                    self.detail(page, pid)
                    page.wait_for_timeout(600)
                    self.assertNotIn('not available offline', o._content_text(page))
                    self.assertEqual(o._cached_entity(page, 'playlist', pid), good)
                finally:
                    o._safe_unroute(page, pattern, malformed)

        context.set_offline(True)
        self.detail(page, pid)
        o._wait_offline_banner(page)
        self.assertIn(PLAYLIST_VIDEO_ONE_TITLE, o._content_text(page))

    def test_corrupted_cached_detail_is_discarded_before_rendering(self):
        server, page, context = self.start()
        pid = server.ids['playlist_a']
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        for corrupt in ('items: [null]', "items: [{ id: '', title: 'x', position: 0 }]",
                        'items: [{ id: 42 }]'):
            with self.subTest(corrupt=corrupt):
                self.cache(page, server.ids)
                page.evaluate(
                    """([id, corrupt]) => {
                        const store = window.createPrksOfflineStore();
                        return store.getEntity('playlist', id).then(row => {
                            const bad = Object.assign({}, row.value);
                            Object.assign(bad, eval('({' + corrupt + '})'));
                            return store.putEntity('playlist', id, bad, row.cachedAt);
                        });
                    }""",
                    [pid, corrupt],
                )
                context.set_offline(True)
                self.detail(page, pid)
                o._wait_offline_unavailable(page)
                self.assertIn('not available offline', o._content_text(page))
                # The unusable entity is dropped rather than left to poison the
                # next read.
                page.wait_for_function(
                    "id => window.createPrksOfflineStore().getEntity('playlist', id).then(r => r === null)",
                    arg=pid, timeout=15000,
                )
                self.online(page, context)
        self.assertEqual(errors, [])

    # ---- mutation blocking --------------------------------------------------

    def test_creation_modal_guard_and_disconnect_before_create(self):
        server, page, context = self.start()
        self.index(page)
        page.wait_for_selector('.playlists-page')
        page.evaluate("openModal('playlist-modal')")
        page.locator('#playlist-title').fill('Disconnected playlist')
        self.offline(page, context)
        posts = self.watch(page, ('POST',))
        page.locator('#save-playlist-btn').click()
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        self.assertEqual(posts, [])
        # The draft the user was building is untouched.
        self.assertEqual(page.locator('#playlist-title').input_value(), 'Disconnected playlist')
        # And a *fresh* creation modal cannot be opened at all while offline.
        page.evaluate('closeModals()')
        page.evaluate("openModal('playlist-modal')")
        self.assertEqual(page.locator('#playlist-modal:not(.hidden)').count(), 0)
        self.assertEqual(posts, [])

    def test_cached_detail_cannot_enter_edit_mode_offline(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        self.open_details_panel(page)
        self.assertTrue(page.locator('#prks-playlist-edit-btn').is_disabled())
        self.assertEqual(page.locator('#prks-playlist-edit-save').count(), 0)

    def test_open_editor_survives_disconnect_and_issues_no_requests(self):
        server, page, context = self.start()
        pid = server.ids['playlist_a']
        self.detail(page, pid)
        o._wait_content_contains(page, PLAYLIST_A_DESCRIPTION)
        self.open_details_panel(page)
        page.locator('#prks-playlist-edit-btn').click()
        page.wait_for_selector('#prks-playlist-edit-save')
        page.locator('#prks-playlist-edit-title').fill('Unsaved playlist title')
        page.locator('#prks-playlist-edit-desc').fill('Unsaved description')

        self.offline(page, context)
        seen = self.watch(page, ('POST', 'PATCH', 'DELETE'))
        work_writes = self.watch(page, ('PATCH',), fragment='/api/works')
        for selector in ('#prks-playlist-edit-title', '#prks-playlist-edit-desc',
                         '#prks-playlist-edit-original-url', '#prks-playlist-edit-save',
                         '#prks-playlist-add-search'):
            self.assertTrue(page.locator(selector).is_disabled(), selector)
        # Item mutation controls in the main list freeze too...
        for selector in ('[data-pl-up]', '[data-pl-down]', '[data-pl-remove]', '[data-pl-rename]'):
            self.assertTrue(page.locator(selector).first.is_disabled(), selector)
        # ... while the draft itself and Cancel/Close stay usable.
        self.assertEqual(page.locator('#prks-playlist-edit-title').input_value(), 'Unsaved playlist title')
        self.assertEqual(page.locator('#prks-playlist-edit-desc').input_value(), 'Unsaved description')
        self.assertFalse(page.locator('#prks-playlist-edit-cancel').is_disabled())
        self.assertFalse(page.locator('#prks-playlist-edit-close').is_disabled())

        # Invoking the handlers directly proves the guards, not the attributes.
        page.evaluate("() => { void document.getElementById('prks-playlist-edit-save').onclick(); }")
        page.wait_for_timeout(500)
        self.assertEqual(seen, [])
        self.assertEqual(work_writes, [])
        self.assertEqual(page.locator('#prks-playlist-edit-title').input_value(), 'Unsaved playlist title')

        # Reconnecting restores the controls with the draft intact.
        self.online(page, context)
        self.assertFalse(page.locator('#prks-playlist-edit-save').is_disabled())
        self.assertEqual(page.locator('#prks-playlist-edit-desc').input_value(), 'Unsaved description')

    def test_inline_rename_state_survives_disconnect_without_a_work_patch(self):
        server, page, context = self.start()
        ids = server.ids
        self.detail(page, ids['playlist_a'])
        o._wait_content_contains(page, PLAYLIST_A_DESCRIPTION)
        self.open_details_panel(page)
        page.locator('#prks-playlist-edit-btn').click()
        page.wait_for_selector('[data-pl-rename]')
        page.locator('[data-pl-rename="%s"]' % ids['playlist_video_one']).click()
        rename_input = '#prks-pl-rename-input-%s' % ids['playlist_video_one']
        page.wait_for_selector(rename_input)
        page.locator(rename_input).fill('Renamed while connected')

        self.offline(page, context)
        work_writes = self.watch(page, ('PATCH',), fragment='/api/works')
        self.assertTrue(page.locator(rename_input).is_disabled())
        self.assertTrue(page.locator('[data-pl-rename-save="%s"]' % ids['playlist_video_one']).is_disabled())
        # Rename Cancel stays live so the user can always leave.
        self.assertFalse(page.locator('[data-pl-rename-cancel="%s"]' % ids['playlist_video_one']).is_disabled())
        self.assertEqual(page.locator(rename_input).input_value(), 'Renamed while connected')
        page.locator('[data-pl-rename-save="%s"]' % ids['playlist_video_one']).click(force=True)
        page.wait_for_timeout(400)
        self.assertEqual(work_writes, [])
        titles = page.evaluate("() => fetchWorks().then(ws => ws.map(w => w.title))")
        self.assertNotIn('Renamed while connected', titles)

    # ---- direct Playlist coherence -----------------------------------------

    def test_direct_playlist_operations_invalidate_exact_domains(self):
        server, page, context = self.start()
        ids = server.ids
        actions = [
            ("createPlaylist('Unassigned playlist', '')", {'playlists'}),
            ("reorderPlaylist(ids.playlist_a, [ids.playlist_video_two, ids.playlist_video_one])", {'playlists'}),
            ("addWorkToPlaylist(ids.playlist_a, ids.work_b)", {'playlists'}),
            ("removeWorkFromPlaylist(ids.playlist_a, ids.work_b)", {'playlists'}),
        ]
        for expression, expected in actions:
            with self.subTest(expression=expression):
                self.cache(page, ids, all_domains=True)
                before = self.generations(page)
                page.evaluate('async ids => { await ' + expression + '; }', ids)
                self.changed(page, before, expected)

    def test_description_only_edit_keeps_member_work_caches(self):
        """Only `title` is embedded in Work detail as playlist_title, so a
        description edit must not cost the user their cached member Works."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        before = self.generations(page)
        page.evaluate(
            """async ids => { await updatePlaylist(ids.playlist_a,
                { title: %s, description: 'Changed description' },
                { previousTitle: %s, memberWorkIds: [ids.playlist_video_one] }); }"""
            % (json.dumps(PLAYLIST_A_TITLE), json.dumps(PLAYLIST_A_TITLE)),
            ids,
        )
        self.changed(page, before, {'playlists'})
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['playlist_video_one']))

    def test_title_edit_evicts_member_work_snapshots_only(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        # work_a is deliberately in no Playlist.
        o._open_work_from_home(page, o.WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        before = self.generations(page)
        page.evaluate(
            """async ids => { await updatePlaylist(ids.playlist_a,
                { title: 'Renamed playlist', description: '' },
                { previousTitle: %s, memberWorkIds: [ids.playlist_video_one] }); }"""
            % json.dumps(PLAYLIST_A_TITLE),
            ids,
        )
        self.changed(page, before, {'playlists'})
        page.wait_for_function(
            "id => window.createPrksOfflineStore().getEntity('work', id).then(r => r === null)",
            arg=ids['playlist_video_one'], timeout=15000,
        )
        self.assertIsNotNone(
            o._cached_entity(page, 'work', ids['work_a']),
            'a Work outside the Playlist keeps its cached snapshot',
        )

    def test_membership_changes_evict_the_affected_work(self):
        server, page, context = self.start()
        ids = server.ids
        for expression in ('addWorkToPlaylist(ids.playlist_a, ids.work_b)',
                           'removeWorkFromPlaylist(ids.playlist_a, ids.work_b)'):
            with self.subTest(expression=expression):
                self.cache(page, ids)
                o._open_work_from_home(page, o.WORK_B_TITLE)
                o._wait_entity_cached(page, 'work', ids['work_b'])
                before = self.generations(page)
                page.evaluate('async ids => { await ' + expression + '; }', ids)
                self.changed(page, before, {'playlists'})
                o._wait_entity_uncached(page, 'work', ids['work_b'])

    def test_moving_a_work_between_playlists_invalidates_the_whole_domain(self):
        """One Playlist per Work: an add is also a remove from the old one, and
        whole-domain invalidation covers both without per-Playlist bookkeeping."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.detail(page, ids['playlist_b'])
        o._wait_entity_cached(page, 'playlist', ids['playlist_b'])
        self.cache_member_work(page, ids)
        before = self.generations(page)
        page.evaluate(
            'async ids => { await addWorkToPlaylist(ids.playlist_b, ids.playlist_video_one); }', ids
        )
        self.changed(page, before, {'playlists'})
        o._wait_entity_uncached(page, 'playlist', ids['playlist_a'])
        o._wait_entity_uncached(page, 'playlist', ids['playlist_b'])
        o._wait_entity_uncached(page, 'work', ids['playlist_video_one'])

    def test_reorder_keeps_member_work_caches(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        before = self.generations(page)
        page.evaluate(
            """async ids => { await reorderPlaylist(ids.playlist_a,
                [ids.playlist_video_two, ids.playlist_video_one]); }""",
            ids,
        )
        self.changed(page, before, {'playlists'})
        self.assertIsNotNone(
            o._cached_entity(page, 'work', ids['playlist_video_one']),
            'Work detail carries no Playlist position',
        )

    def test_failed_playlist_operations_retain_good_caches(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        before = self.generations(page)

        def fail(route):
            if route.request.method in ('POST', 'PATCH', 'DELETE'):
                route.fulfill(status=500, content_type='application/json', body='{}')
            else:
                route.fallback()

        page.route('**/api/playlists**', fail)
        try:
            for expression in (
                "createPlaylist('Never created', '')",
                "removeWorkFromPlaylist(ids.playlist_a, ids.playlist_video_one)",
                "reorderPlaylist(ids.playlist_a, [ids.playlist_video_two, ids.playlist_video_one])",
            ):
                with self.subTest(expression=expression):
                    failed = page.evaluate(
                        'async ids => { try { await ' + expression + '; return false; }'
                        ' catch (_e) { return true; } }', ids)
                    self.assertTrue(failed)
                    self.changed(page, before, set())
                    self.assertIsNotNone(o._cached_entity(page, 'work', ids['playlist_video_one']))
        finally:
            o._safe_unroute(page, '**/api/playlists**', fail)

    # ---- Work -> Playlist coherence ----------------------------------------

    def test_work_metadata_save_invalidates_playlists(self):
        """A Playlist row renders the Work's title, channel and date, so the
        shared Work-title helper owns this dependency."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        self.open_details_panel(page)
        page.locator('#panel-content button', has_text='Edit metadata').click()
        page.locator('#meta-title').fill('Playlist Video Renamed')
        page.locator('#inline-save-metadata-btn').click()
        page.locator('#panel-content .card-title', has_text='Playlist Video Renamed').wait_for(timeout=15000)
        self.changed(page, before, {'concepts', 'arguments', 'people', 'playlists'})

    def test_playlist_inline_rename_inherits_the_shared_title_helper(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        self.detail(page, ids['playlist_a'])
        o._wait_content_contains(page, PLAYLIST_A_DESCRIPTION)
        self.open_details_panel(page)
        page.locator('#prks-playlist-edit-btn').click()
        page.wait_for_selector('[data-pl-rename]')
        before = self.generations(page)
        page.locator('[data-pl-rename="%s"]' % ids['playlist_video_one']).click()
        rename_input = '#prks-pl-rename-input-%s' % ids['playlist_video_one']
        page.wait_for_selector(rename_input)
        page.locator(rename_input).fill('Inline Renamed Video')
        page.locator('[data-pl-rename-save="%s"]' % ids['playlist_video_one']).click()
        page.wait_for_function("t => document.body.innerText.indexOf(t) !== -1",
                               arg='Inline Renamed Video', timeout=15000)
        self.changed(page, before, {'concepts', 'arguments', 'people', 'playlists'})

    def test_work_deletion_invalidates_playlists(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        self.open_details_panel(page)
        advanced = page.locator('.work-details-advanced')
        if advanced.get_attribute('open') is None:
            advanced.locator('summary').click()
        page.locator('.delete-work-btn').click()
        page.locator('#prks-modal-confirm:not(.hidden)', has_text='Delete file?').wait_for()
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        self.changed(page, before,
                     {'concepts', 'arguments', 'people', 'person-groups', 'playlists'})

    def test_failed_work_deletion_retains_the_playlist_cache(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        before = self.generations(page)

        def fail(route):
            if route.request.method == 'DELETE':
                route.fulfill(status=500, content_type='application/json', body='{}')
            else:
                route.fallback()

        pattern = '**/api/works/%s' % ids['playlist_video_one']
        page.route(pattern, fail)
        try:
            # Driven through the real UI: deleteWork() awaits its own
            # confirmation dialog, so calling it from evaluate() would simply
            # block forever on a prompt nothing answers.
            o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
            self.open_details_panel(page)
            advanced = page.locator('.work-details-advanced')
            if advanced.get_attribute('open') is None:
                advanced.locator('summary').click()
            page.locator('.delete-work-btn').click()
            page.locator('#prks-modal-confirm:not(.hidden)', has_text='Delete file?').wait_for()
            page.locator('#prks-modal-confirm-ok').click()
            page.locator('#prks-modal-confirm:not(.hidden)', has_text='Error deleting file!').wait_for(
                timeout=15000
            )
            page.locator('#prks-modal-confirm-ok').click()
            self.changed(page, before, set())
        finally:
            o._safe_unroute(page, pattern, fail)

    def test_work_creation_invalidates_playlists_only_when_it_names_one(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        before = self.generations(page)
        # No playlist_id: nothing about the Playlist read model changed.
        page.evaluate(
            """async () => {
                await prksRequest('/api/works', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: 'Playlistless Work', doc_type: 'book' }) });
            }"""
        )
        page.wait_for_timeout(500)
        self.changed(page, before, set())

        # The create endpoint can attach in the same canonical request.
        before = self.generations(page)
        page.evaluate(
            """async ids => {
                const payload = { title: 'Attached Video Work', doc_type: 'online',
                    source_kind: 'video', source_url: 'https://www.youtube.com/watch?v=e2e0000003',
                    playlist_id: ids.playlist_a };
                const res = await prksRequest('/api/works', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload) });
                if (res.ok && String(payload.playlist_id || '').trim()) {
                    prksMarkPlaylistsDomainChanged();
                }
            }""",
            ids,
        )
        self.changed(page, before, {'playlists'})

    # ---- exclusions ---------------------------------------------------------

    def test_unrelated_mutations_preserve_the_playlist_cache(self):
        """Playlist rendering consumes a Work's title, channel and date -- and
        nothing else the detail endpoint happens to join in."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = o._domain_generation(page, 'playlists')

        # A Work role: the Playlist UI renders no linked Person data.
        page.evaluate(
            """async ids => {
                const res = await prksRequest('/api/roles', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ person_id: ids.person_a,
                        work_id: ids.playlist_video_one, role_type: 'Author' }) });
                if (!res.ok) throw Error('role create failed');
                prksMarkWorkRoleChanged(ids.playlist_video_one, 'Author');
            }""",
            ids,
        )
        # A Person rename, a Group membership change, and bulk status.
        page.evaluate(
            """async ids => {
                await prksRequest('/api/persons/' + ids.person_a, { method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ first_name: 'Renamed', last_name: 'Author' }) });
                prksMarkPeopleDomainChanged();
                await addPersonGroupMember(ids.person_group, ids.person_b);
                await bulkUpdateWorks({ action: 'set_status',
                    work_ids: [ids.playlist_video_one], status: 'Completed' });
            }""",
            ids,
        )
        # Research-domain mutations.
        page.evaluate(
            """async ids => {
                await updateConcept(ids.concept_child, { description: 'Changed' });
                await updatePosition(ids.position_a, { description: 'Changed' });
                await putArgumentSources(ids.argument_a, []);
            }""",
            ids,
        )
        page.wait_for_timeout(600)
        self.assertEqual(o._domain_generation(page, 'playlists'), before)
        self.assertIsNotNone(o._cached_list(page, 'playlists:index'))
        self.assertIsNotNone(o._cached_entity(page, 'playlist', ids['playlist_a']))

    def test_managed_pdf_save_preserves_the_playlist_cache(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        before = o._domain_generation(page, 'playlists')
        o._open_work_from_home(page, o.WORK_A_TITLE)
        o._wait_pdf_viewer(page)
        page.wait_for_function(
            "() => { const pdf = %s; return !!(pdf && pdf.annotationPersistence); }" % o._FOCUSED_PDF,
            timeout=20000,
        )
        o._commit_pdf_highlight(page)
        page.wait_for_function(o._PDF_SYNC_SETTLED_JS, timeout=30000)
        self.assertEqual(o._domain_generation(page, 'playlists'), before)
        self.assertIsNotNone(o._cached_entity(page, 'playlist', ids['playlist_a']))

    def test_research_notes_save_preserves_the_playlist_cache(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        before = o._domain_generation(page, 'playlists')
        o._open_work_from_home(page, o.WORK_A_TITLE)
        page.locator('.work-notes-editor-wrap .CodeMirror').first.click()
        page.keyboard.press('Control+A')
        page.keyboard.insert_text('Changed research notes')
        page.locator('[data-prks-role="editor-status"]', has_text='All changes saved').wait_for()
        self.assertEqual(o._domain_generation(page, 'playlists'), before)
        self.assertIsNotNone(o._cached_list(page, 'playlists:index'))

    # ---- stale reads --------------------------------------------------------

    def test_stale_index_and_detail_reads_cannot_repopulate(self):
        server, page, context = self.start()
        pid = server.ids['playlist_a']
        held = []

        def hold(route):
            if route.request.method == 'GET':
                held.append(route)
            else:
                route.fallback()

        page.route('**/api/playlists**', hold)
        page.evaluate(
            """id => {
                window.pendingPlaylists = prksOfflineReadList('playlists:index', '/api/playlists',
                    { domain: 'playlists', validate: prksIsPlaylistsIndexShape });
                window.pendingPlaylist = prksOfflineReadEntity('playlist', id, '/api/playlists/' + id,
                    { domain: 'playlists', validate: v => prksIsPlaylistShape(v, id) });
            }""",
            pid,
        )
        for _ in range(100):
            if len(held) >= 2:
                break
            page.wait_for_timeout(50)
        self.assertEqual(len(held), 2)
        page.evaluate(
            "ids => reorderPlaylist(ids.playlist_a, [ids.playlist_video_two, ids.playlist_video_one])",
            server.ids,
        )
        page.wait_for_function("!prksOfflineIsDomainBlocked('playlists')")
        for route in held:
            route.fallback()
        page.evaluate('() => Promise.all([pendingPlaylists, pendingPlaylist])')
        self.assertIsNone(o._cached_list(page, 'playlists:index'))
        self.assertIsNone(o._cached_entity(page, 'playlist', pid))
        o._safe_unroute(page, '**/api/playlists**', hold)
        self.detail(page, pid)
        o._wait_entity_cached(page, 'playlist', pid)
