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
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


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

    def reconciled(self, page, before, fenced, ids, title):
        """A Title acknowledgement PATCHES the caches that named the Work.

        Two different things are easy to confuse here. The coherence
        GENERATION of a domain that holds the Work does advance -- that is the
        fence which stops a GET issued before the acknowledgement from
        publishing its pre-rename body afterwards. It is not an invalidation:
        the snapshots stay, and gain the exact new title in place. A domain
        that holds no reference to this Work is not fenced at all.
        """
        for domain, generation in before.items():
            with self.subTest(domain=domain):
                now = o._domain_generation(page, domain)
                if domain in fenced:
                    self.assertGreater(now, generation,
                                       'a domain holding this Work is fenced')
                else:
                    self.assertEqual(now, generation,
                                     'a domain that never named this Work is untouched')
        # Nothing was thrown away: every cached representation survives and
        # carries the new title.
        self.assertIsNotNone(o._cached_list(page, 'playlists:index'))
        playlist = o._cached_entity(page, 'playlist', ids['playlist_a'])
        self.assertIsNotNone(playlist)
        self.assertIn(title, [w.get('title') for w in playlist['value']['items']])

    def db_for(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root),
                            schema_path=str(SCHEMA))

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

    def create_video_work_through_the_modal(self, page, title, playlist_id=None):
        """Drives the real New File modal, so the create handler's own hooks are
        what is under test -- not a helper called by the test itself."""
        # YouTube oEmbed is a real outbound request the handler makes for title/
        # channel prefill; stub it so this stays offline-clean and deterministic.
        def stub_oembed(route):
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'title': title, 'author_name': PLAYLIST_CHANNEL}))

        page.route('**/youtube.com/oembed**', stub_oembed)
        self.addCleanup(lambda: o._safe_unroute(page, '**/youtube.com/oembed**', stub_oembed))
        page.locator('#prks-ribbon-new-file').click()
        page.wait_for_selector('#work-modal:not(.hidden)')
        page.locator('.prks-kind-toggle__btn[data-kind="video"]').click()
        page.wait_for_selector('#work-video-url-row:not(.hidden)')
        page.locator('#work-video-url').fill('https://www.youtube.com/watch?v=e2e0000009')
        page.locator('#work-title').fill(title)
        if playlist_id:
            page.evaluate(
                """id => { document.getElementById('work-video-playlist-id').value = id; }""",
                playlist_id,
            )
        page.locator('#save-work-btn').click()
        page.wait_for_function("() => location.hash.indexOf('#/works/') === 0", timeout=20000)
        # Navigation follows local CREATE_WORK enqueue; coherence publishes on ACK.
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='CREATE_WORK must acknowledge',
        )

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
        # The one creation control on this route stays LIVE: a playlist is
        # created under an id this device mints, so it is real with no server.
        self.open_details_panel(page)
        self.assertFalse(page.locator('#prks-create-playlist-btn').is_disabled())

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
        # A cached-empty list is an ANSWER, and creating into it works offline.
        self.open_details_panel(page)
        self.assertFalse(page.locator('#prks-create-playlist-btn').is_disabled())

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
                wait_for_async(page,
                    "id => window.createPrksOfflineStore().getEntity('playlist', id).then(r => r === null)",
                    arg=pid, timeout=15000,
                )
                self.online(page, context)
        self.assertEqual(errors, [])

    # ---- creating offline ---------------------------------------------------

    def test_the_creation_modal_creates_offline_from_every_surface(self):
        """Creating a playlist mints its id here, so the modal opens and saves
        with no server -- and the playlist is real immediately."""
        server, page, context = self.start()
        self.index(page)
        page.wait_for_selector('.playlists-page')
        self.offline(page, context)
        posts = self.watch(page, ('POST',))
        page.evaluate("openModal('playlist-modal')")
        self.assertEqual(page.locator('#playlist-modal:not(.hidden)').count(), 1,
                         'the modal is no longer refused offline')
        page.locator('#playlist-title').fill('Disconnected playlist')
        page.locator('#save-playlist-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'CREATE_PLAYLIST'))",
            timeout=30000, message='the creation was never enqueued')
        self.assertEqual(posts, [], 'no canonical Playlist request left the browser')
        # It is in the list straight away, and still there after a reload.
        self.index(page)
        o._wait_content_contains(page, 'Disconnected playlist')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.index(page)
        o._wait_content_contains(page, 'Disconnected playlist')

        self.online(page, context)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='the creation never retired')
        self.assertIn('Disconnected playlist',
                      [row['title'] for row in self.db_for(server).get_all_playlists()])

    # ---- what still needs a server -----------------------------------------

    def test_cached_detail_can_be_edited_offline(self):
        """Editing a Playlist is a durable decision, so the editor opens and
        saves with no server. The one control that needs one -- the search over
        the whole Works catalogue -- disables itself."""
        server, page, context = self.start()
        self.cache(page, server.ids)
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        self.open_details_panel(page)
        self.assertFalse(page.locator('#prks-playlist-edit-btn').is_disabled())
        page.locator('#prks-playlist-edit-btn').click()
        page.wait_for_selector('#prks-playlist-edit-save')
        self.assertFalse(page.locator('#prks-playlist-edit-save').is_disabled())
        self.assertTrue(page.locator('#prks-playlist-add-search').is_disabled(),
                        'the Add video search reads a catalogue no cache answers')

    def test_the_editor_saves_durably_offline_and_issues_no_request(self):
        server, page, context = self.start()
        pid = server.ids['playlist_a']
        self.detail(page, pid)
        o._wait_content_contains(page, PLAYLIST_A_DESCRIPTION)
        # Editing offline needs the base it measures the edit against.
        page.evaluate("id => { void Promise.resolve(prksReadPlaylistState(id)).catch(() => {}); }",
                      pid)
        o._wait_entity_cached(page, 'playlist-state', pid)
        self.open_details_panel(page)
        page.locator('#prks-playlist-edit-btn').click()
        page.wait_for_selector('#prks-playlist-edit-save')
        page.locator('#prks-playlist-edit-title').fill('Renamed while disconnected')
        page.locator('#prks-playlist-edit-desc').fill('Changed offline')

        self.offline(page, context)
        seen = self.watch(page, ('POST', 'PATCH', 'DELETE'))
        for selector in ('#prks-playlist-edit-title', '#prks-playlist-edit-desc',
                         '#prks-playlist-edit-original-url', '#prks-playlist-edit-save'):
            self.assertFalse(page.locator(selector).is_disabled(), selector)
        # Order and membership controls are durable decisions too.
        for selector in ('[data-pl-up]', '[data-pl-down]', '[data-pl-remove]',
                         '[data-pl-rename]'):
            self.assertFalse(page.locator(selector).first.is_disabled(), selector)
        # Only the catalogue search is frozen.
        self.assertTrue(page.locator('#prks-playlist-add-search').is_disabled())

        page.locator('#prks-playlist-edit-save').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_PLAYLIST_FIELD' && o.payload.field === 'title'))",
            timeout=30000,
            message='the save never became a durable field operation')
        # Two fields changed, so two independent conflict units were enqueued --
        # a syncing description must never be able to refuse a rename.
        fields = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows"
            "  .filter(o => o.operation === 'SET_PLAYLIST_FIELD')"
            "  .map(o => o.payload.field).sort())")
        self.assertEqual(fields, ['description', 'title'])
        self.assertEqual(seen, [], 'nothing canonical left the browser')
        self.assertEqual(
            self.db_for(server).get_playlist(pid)['title'], PLAYLIST_A_TITLE,
            'the server has not been told anything yet')

        self.online(page, context)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='the field operations never retired')
        self.assertEqual(self.db_for(server).get_playlist(pid)['title'],
                         'Renamed while disconnected')

    def test_an_inline_rename_is_durable_offline_and_never_patches_the_work(self):
        """A video's Title is a Work field, not Playlist state.

        So renaming one from inside a Playlist takes the same durable Title
        operation the metadata editor takes -- which means it keeps working
        offline, while every genuinely Playlist-scoped control here stays
        online-only. It must never reach `PATCH /api/works`: a second,
        non-revision-aware mutation path for one field silently overwrites the
        conflicts the durable one detects.
        """
        server, page, context = self.start()
        ids = server.ids
        work = ids['playlist_video_one']
        # Renaming offline needs a base to measure the edit against, and that
        # base is prepared by opening the video's own metadata editor once
        # while connected -- which is exactly what the refusal message tells
        # the user to do. Do it the way a user would.
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        o._open_details_drawer_if_tiled(page)
        page.locator('#panel-content button', has_text='Edit metadata').click()
        o._wait_entity_cached(page, 'work-metadata-state', work)

        self.detail(page, ids['playlist_a'])
        o._wait_content_contains(page, PLAYLIST_A_DESCRIPTION)
        self.open_details_panel(page)
        page.locator('#prks-playlist-edit-btn').click()
        page.wait_for_selector('[data-pl-rename]')
        page.locator('[data-pl-rename="%s"]' % work).click()
        rename_input = '#prks-pl-rename-input-%s' % work
        page.wait_for_selector(rename_input)
        page.locator(rename_input).fill('Renamed while disconnected')

        self.offline(page, context)
        work_writes = self.watch(page, ('PATCH', 'POST', 'PUT'), fragment='/api/works')
        # The rename controls stay live: a durable save needs no server.
        self.assertFalse(page.locator(rename_input).is_disabled())
        self.assertFalse(page.locator('[data-pl-rename-save="%s"]' % work).is_disabled())
        # The distinction the UI still draws: the Add video SEARCH beside it
        # reads the whole Works catalogue, which no cache can answer.
        self.assertTrue(page.locator('#prks-playlist-add-search').is_disabled())

        page.locator('[data-pl-rename-save="%s"]' % work).click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_WORK_METADATA_FIELD' && o.payload.field === 'title'))",
            message='the rename never became a durable Title operation',
        )
        self.assertEqual(work_writes, [], 'no Work write of any kind left the browser')
        o._wait_content_contains(page, 'Renamed while disconnected')
        self.assertEqual(
            self.db_for(server).get_work(work)['title'], PLAYLIST_VIDEO_ONE_TITLE,
            'the server has not been told anything yet')

        self.online(page, context)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=25000,
            message='the Title operation never retired',
        )
        self.assertEqual(work_writes, [], 'and synchronizing still used no Work PATCH')
        self.assertEqual(self.db_for(server).get_work(work)['title'], 'Renamed while disconnected')
        titles = page.evaluate("() => fetchWorks().then(ws => ws.map(w => w.title))")
        self.assertIn('Renamed while disconnected', titles)

    def test_cached_work_playlist_card_does_not_reach_for_a_catalogue(self):
        """A cached video Work still shows its Playlist card. Starting a NEW
        editing session offline is refused, because mounting it reads the
        Playlist catalogue -- and nothing here may reach the network."""
        server, page, context = self.start()
        ids = server.ids
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        o._wait_entity_cached(page, 'work', ids['playlist_video_one'])

        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_content_contains(page, PLAYLIST_VIDEO_ONE_TITLE)
        self.open_details_panel(page)
        page.wait_for_selector('#prks-work-playlist-edit-btn')

        # Every Playlist request from here on -- including the Prev/Next nav
        # prefetch, which is a raw read, not an offline read-through.
        seen = self.watch(page, ('GET', 'POST', 'PATCH', 'DELETE'))
        page.wait_for_timeout(600)
        self.assertTrue(page.locator('#prks-work-playlist-edit-btn').is_disabled())
        # The relationship itself is real cached data and stays on screen.
        self.assertIn(PLAYLIST_A_TITLE, page.locator('#panel-content').inner_text())

        # Even forced open, the editor reaches no catalogue: the search that
        # would is disabled, and Clear -- which names no playlist at all --
        # stays live, because removing a video is a durable decision.
        page.evaluate("() => { document.getElementById('prks-work-playlist-edit-btn').onclick(); }")
        page.wait_for_selector('#prks-work-playlist-search')
        self.assertTrue(page.locator('#prks-work-playlist-search').is_disabled())
        self.assertFalse(page.locator('#prks-work-playlist-clear-btn').is_disabled())
        page.wait_for_timeout(500)
        self.assertEqual(seen, [])
        self.assertEqual(errors, [])
        self.assertIsNone(page.evaluate("() => window.__prksPendingPlaylistAttach || null"))

    def test_open_work_playlist_editor_survives_disconnect(self):
        """An editor open when the connection drops keeps its draft, and the
        decisions it can still make are made durably. Only the catalogue search
        and the Set button that depends on it freeze."""
        server, page, context = self.start()
        ids = server.ids
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        o._wait_entity_cached(page, 'work', ids['playlist_video_one'])
        # Clearing offline needs the revision it is measured against.
        page.evaluate(
            "id => { void Promise.resolve(prksReadWorkPlaylistState(id)).catch(() => {}); }",
            ids['playlist_video_one'])
        o._wait_entity_cached(page, 'work-playlist-state', ids['playlist_video_one'])
        self.open_details_panel(page)
        page.locator('#prks-work-playlist-edit-btn').click()
        page.wait_for_selector('#prks-work-playlist-search')
        # The editor's markup renders synchronously but its current-playlist
        # pre-fill only lands after `fetchPlaylists()` resolves. Typing into the
        # field before then races that assignment, so wait for the pre-filled
        # value rather than for the element alone.
        page.wait_for_function(
            """title => {
                const input = document.getElementById('prks-work-playlist-search');
                return !!input && input.value === title;
            }""",
            arg=PLAYLIST_A_TITLE,
        )
        page.locator('#prks-work-playlist-search').fill('Unsaved playlist search')

        self.offline(page, context)
        seen = self.watch(page, ('GET', 'POST', 'PATCH', 'DELETE'))
        for selector in ('#prks-work-playlist-search', '#prks-work-playlist-set-btn'):
            self.assertTrue(page.locator(selector).is_disabled(), selector)
        # Clear and New... are durable decisions, so they stay live.
        for selector in ('#prks-work-playlist-clear-btn', '#prks-work-playlist-new-btn'):
            self.assertFalse(page.locator(selector).is_disabled(), selector)
        # Done stays live so the user can always leave the editor.
        self.assertFalse(page.locator('#prks-work-playlist-edit-btn').is_disabled())
        self.assertEqual(page.locator('#prks-work-playlist-edit-btn').inner_text().strip(), 'Done')
        self.assertEqual(page.locator('#prks-work-playlist-search').input_value(),
                         'Unsaved playlist search')

        # Clear enqueues the same scalar an add does, with an empty value ...
        page.locator('#prks-work-playlist-clear-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_WORK_PLAYLIST' && o.payload.playlist_id === ''))",
            timeout=30000,
            message='Clear never became a durable membership operation')
        # ... and reaches no canonical Playlist request at all.
        self.assertEqual(seen, [])
        self.assertNotIn('Could not', page.locator('#prks-work-playlist-status').inner_text())
        self.assertEqual(errors, [])

        self.online(page, context)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='the membership operation never retired')
        self.assertEqual(
            [w['id'] for w in self.db_for(server).get_playlist(ids['playlist_a'])['items']],
            [ids['playlist_video_two']])

    # ---- direct Playlist coherence -----------------------------------------

    def drained(self, page):
        """Every durable operation acknowledged and retired."""
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='a durable operation never retired')

    def test_durable_playlist_operations_fence_only_the_playlists_domain(self):
        """Coherence now happens on ACKNOWLEDGEMENT, not at the call.

        A durable write changes nothing cached until the server answers -- the
        overlay is what the user sees meanwhile -- so the fence belongs to the
        reconciler. Two things must stay true either way: the fence is narrow
        (no other domain names a Playlist), and it drops only what this device
        cannot state exactly.

        `keeps_index` says whether the catalogue survives. It does for a
        creation, whose answer carries the stored row. It does NOT for a
        membership: the item counts of up to two playlists changed, and this
        device does not know which playlist the video left -- the answer names
        only where it landed.
        """
        server, page, context = self.start()
        ids = server.ids
        actions = [
            ("createPlaylist('Unassigned playlist', '')", True),
            ("reorderPlaylist(ids.playlist_a, [ids.playlist_video_two, ids.playlist_video_one])",
             False),
            ("addWorkToPlaylist(ids.playlist_a, ids.work_b)", False),
            ("removeWorkFromPlaylist(ids.playlist_a, ids.work_b)", False),
        ]
        for expression, keeps_index in actions:
            with self.subTest(expression=expression):
                self.cache(page, ids, all_domains=True)
                self.prepare_bases(page, ids)
                before = self.generations(page)
                page.evaluate('async ids => { await ' + expression + '; }', ids)
                self.drained(page)
                for domain, generation in before.items():
                    with self.subTest(domain=domain):
                        now = o._domain_generation(page, domain)
                        if domain == 'playlists':
                            self.assertGreater(now, generation, domain)
                        else:
                            self.assertEqual(now, generation, domain)
                if keeps_index:
                    self.assertIsNotNone(o._cached_list(page, 'playlists:index'),
                                         'the answer carries the stored row, so the '
                                         'catalogue is patched rather than dropped')

    def prepare_bases(self, page, ids):
        """Every revision a durable Playlist write measures itself against."""
        for pid in (ids['playlist_a'], ids['playlist_b']):
            page.evaluate(
                "id => { void Promise.resolve(prksReadPlaylistState(id)).catch(() => {}); }", pid)
            o._wait_entity_cached(page, 'playlist-state', pid)
        for wid in (ids['playlist_video_one'], ids['playlist_video_two'], ids['work_b']):
            page.evaluate(
                "id => { void Promise.resolve(prksReadWorkPlaylistState(id)).catch(() => {}); }",
                wid)
            o._wait_entity_cached(page, 'work-playlist-state', wid)

    def test_a_description_edit_keeps_member_work_caches(self):
        """Only `title` is embedded in Work detail as playlist_title, so a
        description edit must not cost the user their cached member Works."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        self.prepare_bases(page, ids)
        page.evaluate(
            """async ids => { await updatePlaylist(ids.playlist_a,
                { title: %s, description: 'Changed description' }); }"""
            % json.dumps(PLAYLIST_A_TITLE),
            ids,
        )
        self.drained(page)
        self.assertIsNotNone(o._cached_entity(page, 'work', ids['playlist_video_one']))
        # And the acknowledgement PATCHED the description in rather than
        # dropping the playlist this device is looking at.
        playlist = o._cached_entity(page, 'playlist', ids['playlist_a'])
        self.assertEqual(playlist['value']['description'], 'Changed description')

    def test_a_title_edit_stales_member_work_snapshots_only(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        self.prepare_bases(page, ids)
        # work_a is deliberately in no Playlist.
        o._open_work_from_home(page, o.WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        page.evaluate(
            """async ids => { await updatePlaylist(ids.playlist_a,
                { title: 'Renamed playlist', description: '' }); }""",
            ids,
        )
        self.drained(page)
        wait_for_async(page,
            "id => window.createPrksOfflineStore().getEntity('work', id).then(r => r === null)",
            arg=ids['playlist_video_one'], timeout=15000,
        )
        self.assertIsNotNone(
            o._cached_entity(page, 'work', ids['work_a']),
            'a Work outside the Playlist keeps its cached snapshot',
        )
        # The playlist itself is patched, not discarded.
        playlist = o._cached_entity(page, 'playlist', ids['playlist_a'])
        self.assertEqual(playlist['value']['title'], 'Renamed playlist')

    def test_membership_changes_patch_the_affected_work(self):
        """The acknowledgement states the playlist and its title exactly, so
        the video's own snapshot is corrected in place rather than thrown
        away -- an offline device keeps the page it is looking at."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.prepare_bases(page, ids)
        o._open_work_from_home(page, o.WORK_B_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_b'])
        page.evaluate(
            'async ids => { await addWorkToPlaylist(ids.playlist_a, ids.work_b); }', ids)
        self.drained(page)
        cached = o._cached_entity(page, 'work', ids['work_b'])
        self.assertIsNotNone(cached, 'the snapshot survives')
        self.assertEqual(cached['value']['playlist_id'], ids['playlist_a'])
        self.assertEqual(cached['value']['playlist_title'], PLAYLIST_A_TITLE)

        page.evaluate(
            'async ids => { await removeWorkFromPlaylist(ids.playlist_a, ids.work_b); }', ids)
        self.drained(page)
        cached = o._cached_entity(page, 'work', ids['work_b'])
        self.assertIsNotNone(cached)
        self.assertIsNone(cached['value']['playlist_id'])

    def test_moving_a_work_between_playlists_fences_the_whole_domain(self):
        """One Playlist per Work: an add is also a remove from the old one, and
        this device does not know which playlist the video left -- the answer
        names only where it landed -- so both playlists' contents go stale
        together."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.detail(page, ids['playlist_b'])
        o._wait_entity_cached(page, 'playlist', ids['playlist_b'])
        self.cache_member_work(page, ids)
        self.prepare_bases(page, ids)
        before = self.generations(page)
        page.evaluate(
            'async ids => { await addWorkToPlaylist(ids.playlist_b, ids.playlist_video_one); }',
            ids)
        self.drained(page)
        self.assertGreater(o._domain_generation(page, 'playlists'), before['playlists'])
        o._wait_entity_uncached(page, 'playlist', ids['playlist_a'])
        o._wait_entity_uncached(page, 'playlist', ids['playlist_b'])
        # The video itself is patched rather than dropped.
        cached = o._cached_entity(page, 'work', ids['playlist_video_one'])
        self.assertIsNotNone(cached)
        self.assertEqual(cached['value']['playlist_id'], ids['playlist_b'])

    def test_a_reorder_keeps_member_work_caches_and_reorders_the_detail(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        self.prepare_bases(page, ids)
        page.evaluate(
            """async ids => { await reorderPlaylist(ids.playlist_a,
                [ids.playlist_video_two, ids.playlist_video_one]); }""",
            ids,
        )
        self.drained(page)
        self.assertIsNotNone(
            o._cached_entity(page, 'work', ids['playlist_video_one']),
            'Work detail carries no Playlist position',
        )
        # The cached detail is reordered IN PLACE: the answer names the order
        # the server ended with, so there is nothing to re-download.
        playlist = o._cached_entity(page, 'playlist', ids['playlist_a'])
        self.assertEqual([w['id'] for w in playlist['value']['items']],
                         [ids['playlist_video_two'], ids['playlist_video_one']])

    def test_a_refused_playlist_operation_retains_good_caches(self):
        """A durable operation that the server rejects is retried, not lost --
        and until it is acknowledged, nothing cached is touched."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        self.cache_member_work(page, ids)
        self.prepare_bases(page, ids)
        before = self.generations(page)

        def fail(route):
            if route.request.method == 'POST':
                route.fulfill(status=500, content_type='application/json', body='{}')
            else:
                route.fallback()

        page.route('**/api/sync/operations**', fail)
        try:
            page.evaluate("async ids => { await createPlaylist('Never created', ''); }", ids)
            wait_for_async(
                page,
                "() => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'CREATE_PLAYLIST'))",
                timeout=30000, message='the creation was never enqueued')
            page.wait_for_timeout(1000)
            # Still queued, and every cached representation intact.
            rows = page.evaluate(
                "() => prksSync.store.listOperations().then(rows => rows.length)")
            self.assertGreater(rows, 0, 'a refused operation is retried, never dropped')
            for domain, generation in before.items():
                with self.subTest(domain=domain):
                    self.assertEqual(o._domain_generation(page, domain), generation)
            self.assertIsNotNone(o._cached_list(page, 'playlists:index'))
            self.assertIsNotNone(o._cached_entity(page, 'work', ids['playlist_video_one']))
        finally:
            o._safe_unroute(page, '**/api/sync/operations**', fail)

    # ---- Work -> Playlist coherence ----------------------------------------

    def test_work_metadata_save_reconciles_playlists_rather_than_dropping_them(self):
        """A Playlist row renders the Work's title, so a rename has to reach
        it -- but by RECONCILIATION, not invalidation: the exact new title is
        patched into the cached Playlist rather than the snapshot being
        thrown away."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        self.open_details_panel(page)
        page.locator('#panel-content button', has_text='Edit metadata').click()
        page.locator('[data-prks-work-field="title"]').fill('Playlist Video Renamed')
        page.locator('#save-work-identity-btn').click()
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(r => r.length === 0)")
        # A Work Title is local-first now, so the rename RECONCILES the exact
        # new title into every cached representation instead of invalidating
        # four domains. The cached Playlist keeps its snapshot and gains the
        # new title in place.
        wait_for_async(page, '''id => window.createPrksOfflineStore()
            .getEntity('playlist', id).then(row => {
                if (!row) return false;
                return (row.value.items || []).some(
                    w => w.title === 'Playlist Video Renamed');
            })''', arg=ids['playlist_a'])
        self.reconciled(page, before, {'concepts', 'arguments', 'people', 'playlists'},
                        ids, 'Playlist Video Renamed')

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
        # The overlay shows the new title at once; reconciliation is what the
        # ACKNOWLEDGEMENT does, so wait for the operation to retire before
        # asking what happened to the caches.
        page.wait_for_function("t => document.body.innerText.indexOf(t) !== -1",
                               arg='Inline Renamed Video', timeout=15000)
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(r => r.length === 0)",
            timeout=25000, message='the inline rename never retired')
        self.reconciled(page, before, {'concepts', 'arguments', 'people', 'playlists'},
                        ids, 'Inline Renamed Video')

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
            route.fulfill(status=500, content_type='application/json', body='{}')

        page.route('**/api/sync/operations**', fail)
        try:
            o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
            self.open_details_panel(page)
            advanced = page.locator('.work-details-advanced')
            if advanced.get_attribute('open') is None:
                advanced.locator('summary').click()
            page.locator('.delete-work-btn').click()
            page.locator('#prks-modal-confirm:not(.hidden)', has_text='Delete file?').wait_for()
            page.locator('#prks-modal-confirm-ok').click()
            page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
            wait_for_async(
                page,
                """() => prksSync.store.listOperations().then(rows =>
                    rows.some(r => r.operation === 'DELETE_WORK'))""",
                timeout=15000,
            )
            self.changed(page, before, set())
        finally:
            o._safe_unroute(page, '**/api/sync/operations**', fail)

    def test_work_creation_invalidates_playlists_only_when_it_names_one(self):
        """Driven through the real New File modal: the create endpoint can
        attach a video to a Playlist in the same canonical request, bypassing
        addWorkToPlaylist(), so that handler owes Playlists its own
        invalidation -- and owes it nothing when no Playlist was named."""
        server, page, context = self.start()
        ids = server.ids

        # A video created with no Playlist selected leaves playlists untouched.
        self.cache(page, ids)
        before = self.generations(page)
        self.create_video_work_through_the_modal(page, 'Playlistless Video Work')
        self.changed(page, before, set())

        # ... and one created straight into a Playlist stales the domain.
        self.cache(page, ids)
        before = self.generations(page)
        self.create_video_work_through_the_modal(
            page, 'Attached Video Work', playlist_id=ids['playlist_a']
        )
        self.changed(page, before, {'playlists'})
        # The attach really happened, so the invalidation was not vacuous.
        titles = page.evaluate(
            "id => fetchPlaylistDetails(id).then(pl => pl.items.map(i => i.title))",
            ids['playlist_a'],
        )
        self.assertIn('Attached Video Work', titles)

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
                const ops = await prksSync.store.listOperations();
                const observed = await prksAcknowledgedPersonGroupMembership(
                    ids.person_group, ids.person_b, ops);
                await prksSetPersonGroupMemberDurably(
                    ids.person_group, ids.person_b, true, observed);
                const deadline = Date.now() + 30000;
                while (Date.now() < deadline) {
                    const rows = await prksSync.store.listOperations();
                    if (!rows.some(o => o.status !== 'conflict')) break;
                    await new Promise(r => setTimeout(r, 100));
                }
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

    def test_a_stale_detail_read_cannot_repopulate_the_playlist(self):
        """A GET issued BEFORE a change must not publish its pre-change body
        afterwards.

        Only the DETAIL is held here. A durable write does its own bookkeeping
        against the playlist CATALOGUE -- the overlay that makes a pending
        membership visible needs the destination's title -- so holding the index
        as well would stall the very write this test is observing, which is an
        artifact of the route interception rather than anything a server does.
        """
        server, page, context = self.start()
        ids = server.ids
        pid = ids['playlist_a']
        self.cache(page, ids)
        self.prepare_bases(page, ids)
        held = []
        detail_path = '/api/playlists/' + pid

        def hold(route):
            if (route.request.method == 'GET'
                    and urlparse(route.request.url).path == detail_path):
                held.append(route)
            else:
                route.fallback()

        page.route('**/api/playlists/**', hold)
        page.evaluate(
            """id => {
                window.pendingPlaylist = prksOfflineReadEntity('playlist', id,
                    '/api/playlists/' + id,
                    { domain: 'playlists', validate: v => prksIsPlaylistShape(v, id) });
            }""",
            pid,
        )
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        self.assertEqual(len(held), 1)

        page.evaluate(
            'async ids => { await addWorkToPlaylist(ids.playlist_a, ids.work_b); }', ids)
        self.drained(page)
        for route in held:
            route.fallback()
        held.clear()
        page.evaluate('() => pendingPlaylist')

        self.assertIsNone(o._cached_entity(page, 'playlist', pid),
                          'a read issued before the change cannot refill the playlist')
        o._safe_unroute(page, '**/api/playlists/**', hold)
        self.detail(page, pid)
        o._wait_entity_cached(page, 'playlist', pid)

    def test_a_stale_index_read_cannot_resurrect_a_deleted_playlist(self):
        """The catalogue is PATCHED by a deletion rather than dropped, so the
        question is sharper than "is it still cached": a body fetched before
        the deletion must not put the playlist back into it.

        Deleting is the change used here because it needs no base read at all,
        and -- with nothing pending that names a playlist -- no catalogue read
        either, so holding the index stalls nothing.
        """
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        held = []

        def hold(route):
            if (route.request.method == 'GET'
                    and urlparse(route.request.url).path == '/api/playlists'):
                held.append(route)
            else:
                route.fallback()

        page.route('**/api/playlists**', hold)
        page.evaluate(
            """() => {
                window.pendingPlaylists = prksOfflineReadList('playlists:index',
                    '/api/playlists',
                    { domain: 'playlists', validate: prksIsPlaylistsIndexShape });
            }""")
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        self.assertEqual(len(held), 1)

        page.evaluate('async ids => { await deletePlaylistCanonical(ids.playlist_b); }', ids)
        self.drained(page)
        for route in held:
            route.fallback()
        held.clear()
        page.evaluate('() => pendingPlaylists')

        cached = o._cached_list(page, 'playlists:index')
        titles = [row['title'] for row in cached['value']] if cached else []
        self.assertNotIn(PLAYLIST_B_TITLE, titles,
                         'a body fetched before the deletion cannot put it back')
        o._safe_unroute(page, '**/api/playlists**', hold)


if __name__ == "__main__":
    unittest.main()
