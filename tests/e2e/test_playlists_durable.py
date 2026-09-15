"""Playlists, created and changed with no server.

The interesting part of this milestone is the ORDER. Every other family so far
has been a field or an element; an order is an aggregate, and the test that
matters is that a drag made offline survives and is applied as one decision
rather than as a race between positions.

Every durable-queue gate here allows a generous timeout. A save offline first
reads the base it measures the edit against, and offline that read has to let a
doomed request fail before the cache answers.
"""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import seed_library

VIDEO_A_TITLE = 'E2E Lecture One'
VIDEO_B_TITLE = 'E2E Lecture Two'
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


def seed(root):
    """Two VIDEOS, because a Playlist holds videos.

    The Work detail page mounts its Playlist card only for a video, so a seeded
    PDF would make every assertion about that card vacuous.
    """
    ids = seed_library(root)
    db = PRKSDatabase(storage=StorageConfig.for_testing(root))
    ids['video_a'] = db.add_work(
        title=VIDEO_A_TITLE, doc_type='video', source_kind='video',
        source_url='https://www.youtube.com/watch?v=E2EONELECT')
    ids['video_b'] = db.add_work(
        title=VIDEO_B_TITLE, doc_type='video', source_kind='video',
        source_url='https://www.youtube.com/watch?v=E2ETWOLECT')
    ids['playlist'] = db.add_playlist('Durable Playlist', 'Seeded description')
    db.add_work_to_playlist(ids['playlist'], ids['video_a'])
    db.add_work_to_playlist(ids['playlist'], ids['video_b'])
    return ids


class DurablePlaylistTests(unittest.TestCase):

    # ---- harness ------------------------------------------------------------

    def start(self):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        self.index(page)
        o._wait_list_cached(page, 'playlists:index')
        return server, page, context

    def index(self, page):
        page.evaluate("() => prksNavigate('#/playlists')")

    def detail(self, page, playlist_id):
        page.evaluate("id => prksNavigate('#/playlists/' + id)", playlist_id)

    def prepare_playlist(self, page, playlist_id):
        """Open the playlist once while connected, so its revisions are cached."""
        self.detail(page, playlist_id)
        o._wait_entity_cached(page, 'playlist', playlist_id)
        page.evaluate(
            "id => { void Promise.resolve(prksReadPlaylistState(id)).catch(() => {}); }",
            playlist_id)
        o._wait_entity_cached(page, 'playlist-state', playlist_id)

    def prepare_work(self, page, work_id):
        o._open_work_from_home(page, VIDEO_A_TITLE)
        o._wait_entity_cached(page, 'work', work_id)
        page.evaluate(
            "id => { void Promise.resolve(prksReadWorkPlaylistState(id)).catch(() => {}); }",
            work_id)
        o._wait_entity_cached(page, 'work-playlist-state', work_id)
        # And the browse catalogue: a playlist page renders a video CARD, and
        # the row for a video added here has to come from somewhere this device
        # holds. Nothing is invented for a video whose row it does not have.
        page.evaluate("() => prksNavigate('#/types')")
        o._wait_list_cached(page, 'works-browse:index')

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def settled(self, page):
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(op => op.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 120));
            }
        }""")

    def wait_for_family(self, page, operation):
        wait_for_async(
            page,
            "op => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === op))",
            arg=operation, timeout=60000,
            message='%s was never enqueued' % operation)

    def db_for(self, server):
        existing = getattr(self, '_db', None)
        if existing is None:
            existing = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
            self._db = existing
        return existing

    def playlists_in_db(self, server):
        return {row['title']: row for row in self.db_for(server).get_all_playlists()}

    def order_in_db(self, server, playlist_id):
        rows = self.db_for(server).execute_query(
            'SELECT work_id FROM playlist_items WHERE playlist_id = ? ORDER BY position ASC',
            (playlist_id,))
        return [row['work_id'] for row in rows]

    # ---- creation -----------------------------------------------------------

    def test_a_playlist_created_offline_is_real_and_survives_reload(self):
        server, page, context = self.start()
        self.offline(page, context)

        playlist = page.evaluate(
            "() => createPlaylist('Offline Playlist', 'Made with no server')")
        self.assertRegex(playlist, r'^PL-[0-9A-F]{32}$',
                         'a permanent id minted here, never remapped later')
        self.index(page)
        o._wait_content_contains(page, 'Offline Playlist')

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.index(page)
        o._wait_content_contains(page, 'Offline Playlist')
        self.detail(page, playlist)
        o._wait_content_contains(page, 'Made with no server')

        self.reconnect(page, context)
        self.settled(page)
        rows = self.playlists_in_db(server)
        self.assertIn('Offline Playlist', rows)
        self.assertEqual(rows['Offline Playlist']['id'], playlist,
                         'the id the client minted is the id SQLite stores')

    def test_a_repeated_title_is_accepted_because_playlists_are_not_unique(self):
        """Playlists have never been unique by title, so inventing that rule
        offline would refuse something the ordinary endpoint accepts."""
        server, page, context = self.start()
        self.offline(page, context)
        page.evaluate("() => createPlaylist('Durable Playlist', '')")
        self.wait_for_family(page, 'CREATE_PLAYLIST')

        self.reconnect(page, context)
        self.settled(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='a repeated title is not a refusal, so nothing should be stranded')
        self.assertEqual(
            len([r for r in self.db_for(server).get_all_playlists()
                 if r['title'] == 'Durable Playlist']), 2)

    # ---- fields -------------------------------------------------------------

    def test_renaming_a_playlist_offline_shows_and_lands(self):
        server, page, context = self.start()
        playlist = server.ids['playlist']
        self.prepare_playlist(page, playlist)
        self.offline(page, context)

        page.evaluate("id => updatePlaylist(id, { title: 'Renamed Offline' })", playlist)
        self.wait_for_family(page, 'SET_PLAYLIST_FIELD')
        self.index(page)
        o._wait_content_contains(page, 'Renamed Offline')

        self.reconnect(page, context)
        self.settled(page)
        self.assertIn('Renamed Offline', self.playlists_in_db(server))

    def test_a_rename_taken_back_before_it_is_sent_leaves_no_intent(self):
        server, page, context = self.start()
        playlist = server.ids['playlist']
        self.prepare_playlist(page, playlist)
        self.offline(page, context)

        page.evaluate("id => updatePlaylist(id, { title: 'Temporarily Renamed' })", playlist)
        self.wait_for_family(page, 'SET_PLAYLIST_FIELD')
        page.evaluate("id => updatePlaylist(id, { title: 'Durable Playlist' })", playlist)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='reverting an unsent rename must leave no operation behind')

    # ---- membership ---------------------------------------------------------

    def test_adding_a_video_offline_shows_on_both_the_video_and_the_playlist(self):
        server, page, context = self.start()
        destination = page.evaluate("() => createPlaylist('Destination', '')")
        self.settled(page)
        self.prepare_work(page, server.ids['video_a'])
        self.prepare_playlist(page, destination)
        self.offline(page, context)

        page.evaluate("([wid, pid]) => addWorkToPlaylist(pid, wid)",
                      [server.ids['video_a'], destination])
        self.wait_for_family(page, 'SET_WORK_PLAYLIST')

        # The playlist's own page lists it ...
        self.detail(page, destination)
        o._wait_content_contains(page, VIDEO_A_TITLE, timeout=30000)
        # ... and the video's own page names the playlist. The playlist card
        # lives in the right panel rather than the route's own root.
        o._open_work_from_home(page, VIDEO_A_TITLE)
        o._open_details_drawer_if_tiled(page)
        page.wait_for_function(
            "() => { const p = document.getElementById('panel-content');"
            "        return !!p && p.innerText.indexOf('Destination') !== -1; }",
            timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        self.assertEqual(self.order_in_db(server, destination), [server.ids['video_a']])

    def test_putting_a_video_back_where_it_started_leaves_no_intent(self):
        server, page, context = self.start()
        playlist = server.ids['playlist']
        self.prepare_work(page, server.ids['video_a'])
        destination = page.evaluate("() => createPlaylist('Destination', '')")
        self.settled(page)
        self.offline(page, context)

        page.evaluate("([wid, pid]) => addWorkToPlaylist(pid, wid)",
                      [server.ids['video_a'], destination])
        self.wait_for_family(page, 'SET_WORK_PLAYLIST')
        page.evaluate("([wid, pid]) => addWorkToPlaylist(pid, wid)",
                      [server.ids['video_a'], playlist])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='put back where it started is not two changes, it is none')

    def test_removing_a_video_offline_is_the_same_scalar(self):
        server, page, context = self.start()
        playlist = server.ids['playlist']
        self.prepare_work(page, server.ids['video_a'])
        self.prepare_playlist(page, playlist)
        self.offline(page, context)

        page.evaluate("([wid, pid]) => removeWorkFromPlaylist(pid, wid)",
                      [server.ids['video_a'], playlist])
        self.wait_for_family(page, 'SET_WORK_PLAYLIST')
        payload = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'SET_WORK_PLAYLIST') || {}).payload)")
        self.assertEqual(payload, {'playlist_id': ''},
                         'removing is adding with an empty value, not its own family')

        self.reconnect(page, context)
        self.settled(page)
        self.assertEqual(self.order_in_db(server, playlist), [server.ids['video_b']])

    # ---- order --------------------------------------------------------------

    def test_a_drag_offline_is_one_aggregate_that_survives_reload(self):
        server, page, context = self.start()
        playlist = server.ids['playlist']
        self.prepare_playlist(page, playlist)
        before = self.order_in_db(server, playlist)
        self.assertEqual(before, [server.ids['video_a'], server.ids['video_b']])
        self.offline(page, context)

        page.evaluate("([pid, ids]) => reorderPlaylist(pid, ids)",
                      [playlist, [server.ids['video_b'], server.ids['video_a']]])
        self.wait_for_family(page, 'REORDER_PLAYLIST_ITEMS')
        payload = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'REORDER_PLAYLIST_ITEMS') || {}).payload)")
        self.assertEqual(payload['work_ids'], [server.ids['video_b'], server.ids['video_a']],
                         'the whole order travels as one payload, not one moved index')

        # The page shows the new order immediately, and still does after a
        # reload with no server.
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.detail(page, playlist)
        wait_for_async(
            page,
            "titles => new Promise(resolve => setTimeout(() => {"
            "  const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();"
            "  const root = ctx && ctx.root;"
            "  if (!root) return resolve(false);"
            "  const text = root.innerText;"
            "  const first = text.indexOf(titles[0]);"
            "  const second = text.indexOf(titles[1]);"
            "  resolve(first !== -1 && second !== -1 && first < second);"
            "}, 0))",
            arg=[VIDEO_B_TITLE, VIDEO_A_TITLE], timeout=30000,
            message='a drag made offline must still be showing after a reload')

        self.reconnect(page, context)
        self.settled(page)
        self.assertEqual(self.order_in_db(server, playlist),
                         [server.ids['video_b'], server.ids['video_a']])

    def test_a_second_drag_replaces_the_first_rather_than_racing_it(self):
        server, page, context = self.start()
        playlist = server.ids['playlist']
        self.prepare_playlist(page, playlist)
        self.offline(page, context)

        page.evaluate("([pid, ids]) => reorderPlaylist(pid, ids)",
                      [playlist, [server.ids['video_b'], server.ids['video_a']]])
        self.wait_for_family(page, 'REORDER_PLAYLIST_ITEMS')
        page.evaluate("([pid, ids]) => reorderPlaylist(pid, ids)",
                      [playlist, [server.ids['video_a'], server.ids['video_b']]])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='dragging back to the acknowledged order leaves no intent at all')

    # ---- deletion -----------------------------------------------------------

    def test_deleting_a_playlist_offline_is_a_tombstone(self):
        server, page, context = self.start()
        empty = page.evaluate("() => createPlaylist('Empty Playlist', '')")
        self.settled(page)
        self.prepare_playlist(page, empty)
        self.offline(page, context)

        page.evaluate("id => deletePlaylistCanonical(id)", empty)
        self.wait_for_family(page, 'DELETE_PLAYLIST')
        self.index(page)
        page.wait_for_function(
            """needle => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const root = ctx && ctx.root;
                return !!root && root.innerText.indexOf(needle) === -1;
            }""",
            arg='Empty Playlist', timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn('Empty Playlist', self.playlists_in_db(server))

    def test_deleting_a_playlist_holding_videos_keeps_the_videos(self):
        """Unlike a Folder, a Playlist holding files is NOT protected: its items
        are memberships, not the videos themselves."""
        server, page, context = self.start()
        playlist = server.ids['playlist']
        self.prepare_playlist(page, playlist)
        self.offline(page, context)
        page.evaluate("id => deletePlaylistCanonical(id)", playlist)
        self.wait_for_family(page, 'DELETE_PLAYLIST')

        self.reconnect(page, context)
        self.settled(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='a playlist holding videos is deleted, not refused')
        self.assertNotIn('Durable Playlist', self.playlists_in_db(server))
        self.assertIsNotNone(self.db_for(server).get_work(server.ids['video_a']))

    def test_deleting_a_playlist_created_offline_folds_the_whole_case_away(self):
        server, page, context = self.start()
        self.offline(page, context)
        playlist = page.evaluate(
            "() => createPlaylist('Offline Mistake', 'Typed by accident')")
        self.wait_for_family(page, 'CREATE_PLAYLIST')
        page.evaluate("id => deletePlaylistCanonical(id)", playlist)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='nothing about this playlist should ever reach the server')

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn('Offline Mistake', self.playlists_in_db(server))


if __name__ == '__main__':
    unittest.main()
