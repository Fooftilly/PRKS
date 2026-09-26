"""Local-first Work video source: one aggregate, one identity, one conflict.

The source is deliberately NOT a set of scalar fields. Replacing a video
rewrites `source_kind`, `provider`, `provider_id` and `source_url` together,
and two URLs naming the same video are the same source however they are
spelled -- so identity, not URL text, decides what a conflict is.

Chromium coverage here is intentionally thin: real UI wiring, offline edit
across reload, user-visible conflict resolution, cache-visible thumbnail, and
browser projections Node selftests cannot prove -- including editor
`state.observed` coalescing, Apply-button base updates, and `acceptAck`
spelling. Deterministic parser, store coalescing, protocol convergent writes,
conflict arithmetic, and aggregate atomics live in
`tests/test_work_source_sync.py` and
`tests/browser/run_work_source_sync_selftest.js`. Store-layer Node coverage
does not replace those editor paths.
"""
import os
import unittest

from backend import work_source_sync
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import PLAYLIST_VIDEO_ONE_TITLE, WORK_A_TITLE, seed_playlists_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium

# Three videos, each in both spellings PRKS accepts. The pairs exist so a
# re-spelling can be told apart from a different video: SHORT_TWO and WATCH_TWO
# are ONE video, and only the URL text differs.
WATCH_ONE = "https://www.youtube.com/watch?v=e2e0000001"
SHORT_ONE = "https://youtu.be/e2e0000001"
WATCH_TWO = "https://www.youtube.com/watch?v=e2e0000099"
SHORT_TWO = "https://youtu.be/e2e0000099"
OTHER_VIDEO = "https://www.youtube.com/watch?v=e2e0000055"
SHORT_OTHER = "https://youtu.be/e2e0000055"
THIRD_VIDEO = "https://www.youtube.com/watch?v=e2e0000077"


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class OfflineWorkSourceTests(unittest.TestCase):
    SOURCE_OPS = "r.operation === 'SET_WORK_SOURCE'"

    # ---- harness ------------------------------------------------------------

    def start(self, title=PLAYLIST_VIDEO_ONE_TITLE, key='playlist_video_one'):
        server = AppServer(seed_fn=seed_playlists_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        o._open_work_from_home(page, title)
        o._wait_entity_cached(page, 'work', server.ids[key])
        return server, page, context

    def edit(self, page):
        """Open Edit metadata and wait for the source group to be usable."""
        page.locator('#panel-content button', has_text='Edit metadata').click()
        page.wait_for_function(
            "() => { const b = document.getElementById('save-work-source-btn');"
            "        return !!b && !b.disabled; }")

    def url(self, page, value):
        page.locator('#meta-video-url').fill(value)

    def save(self, page):
        page.locator('#save-work-source-btn').click()

    def pending(self, page, count):
        """Exactly `count` source operations, none mid-flight."""
        page.evaluate("""async n => {
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = (await prksSync.store.listOperations())
                    .filter(r => r.operation === 'SET_WORK_SOURCE');
                if (rows.length === n && !rows.some(r => r.status === 'syncing')) return;
                if (Date.now() > deadline) throw new Error('Sync did not settle: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", count)

    def settled_conflicts(self, page, count):
        page.evaluate("""async n => {
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = (await prksSync.store.listOperations())
                    .filter(r => r.operation === 'SET_WORK_SOURCE');
                if (rows.length === n && rows.every(r => r.status === 'conflict' && !!r.server_result)) return;
                if (Date.now() > deadline) throw new Error('No conflict settled: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", count)

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def db_for(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))

    def other_device_sets_source(self, server, work_id, url):
        """What a second device's acknowledged operation leaves behind.

        Written through the aggregate's own writer rather than by hand, so the
        four columns and the revision move exactly as the server would move
        them -- a hand-written UPDATE would be testing a state the product can
        never actually be in.
        """
        db = self.db_for(server)
        with db.connection() as conn:
            work_source_sync.set_source_on_conn(
                conn, work_id, work_source_sync.canonical_source({'kind': 'video', 'url': url}))

    def columns(self, server, work_id):
        row = self.db_for(server).get_work(work_id)
        return {k: row[k] for k in ('source_kind', 'provider', 'provider_id', 'source_url')}

    def conflicts(self, page, count):
        page.evaluate("""async n => {
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = (await prksSync.store.listOperations())
                    .filter(r => r.operation === 'SET_WORK_SOURCE');
                if (rows.length === n && rows.every(r => r.status === 'conflict' && !!r.server_result)) return;
                if (Date.now() > deadline) throw new Error('No conflict settled: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", count)

    def source_operations(self, page):
        """Every unacknowledged source operation as (url, base_revision)."""
        return [tuple(row) for row in page.evaluate(
            "() => prksSync.store.listOperations().then(r => r"
            "  .filter(o => o.operation === 'SET_WORK_SOURCE')"
            "  .map(o => [o.payload.source.url, o.base_revision]))")]

    def cached_source_revision(self, page, work_id):
        return page.evaluate(
            "id => window.createPrksOfflineStore().getEntity('work-source-state', id)"
            "  .then(row => row && row.value && row.value.revision)", work_id)

    def cached_work(self, page, work_id):
        return page.evaluate(
            "id => window.createPrksOfflineStore().getEntity('work', id)"
            "  .then(row => row && row.value)", work_id)

    def effective(self, page, work_id):
        return page.evaluate(
            "id => prksRefreshPendingWorkSources().then(() => "
            "  prksEffectiveWorkSource({ id, source_kind: 'video' }))", work_id)

    # ---- KEEP: browser / UI / reload / conflict / cache-visible ------------

    def test_an_offline_source_change_survives_a_reload_and_synchronizes(self):
        """One decision, four columns -- and nothing partially applied at any
        point the user could observe."""
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)
        self.assertEqual(self.columns(server, work), {
            'source_kind': 'video', 'provider': 'youtube',
            'provider_id': 'e2e0000001', 'source_url': WATCH_ONE,
        }, 'the server has not been told anything yet')

        page.reload()
        page.wait_for_selector('#sidebar')
        self.assertEqual(self.effective(page, work)['provider_id'], 'e2e0000099',
                         'the pending identity survives a reload')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.columns(server, work), {
            'source_kind': 'video', 'provider': 'youtube',
            'provider_id': 'e2e0000099', 'source_url': WATCH_TWO,
        })

    def test_a_different_video_is_a_conflict_the_user_decides(self):
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)

        self.other_device_sets_source(server, work, OTHER_VIDEO)

        self.reconnect(page, context)
        self.settled_conflicts(page, 1)
        result = page.evaluate(
            "() => prksSync.store.listOperations().then(r => "
            "  r.filter(o => o.operation === 'SET_WORK_SOURCE')[0].server_result)")
        self.assertEqual(result['code'], 'SOURCE_REVISION_CONFLICT')
        self.assertIn('e2e0000055', result['current_preview'])
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000055',
                         'nothing was overwritten while the user decides')

    def test_a_non_video_work_has_no_source_editor_at_all(self):
        """The refusal is structural: a PDF never offers the control, so the
        unsupported transition cannot be reached from the UI."""
        server, page, _context = self.start(title=WORK_A_TITLE, key='work_a')
        page.locator('#panel-content button', has_text='Edit metadata').click()
        page.wait_for_selector('#meta-title')
        self.assertEqual(page.locator('[data-prks-role="work-source-editor"]').count(), 0)
        self.assertEqual(page.locator('#meta-video-url').count(), 0)

    def test_an_unusable_url_is_refused_before_anything_is_stored(self):
        """A link PRKS cannot resolve to a video is not saved locally either --
        an operation that can never be applied is not durable intent."""
        server, page, context = self.start()
        self.edit(page)
        self.offline(page, context)
        self.url(page, 'https://example.com/not-a-video')
        self.save(page)
        page.wait_for_function(
            "() => { const el = document.getElementById('meta-video-url-error');"
            "        return !!el && el.textContent.trim().length > 0; }")
        self.pending(page, 0)

    def test_two_consecutive_source_changes_do_not_conflict_with_each_other(self):
        """The regression 2O.1 exists for.

        An acknowledgement advances the source revision, and the base the next
        edit is measured against has to move with it -- in the cached
        projection AND in the editor that is still open. Left behind, the
        second change is created against a revision the server has already
        passed, and the user collides with their own previous edit on a Work
        nobody else has touched.
        """
        server, page, _context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)

        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 0)
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000099')
        self.assertEqual(self.cached_source_revision(page, work), 1,
                         'the cached base moved with the acknowledgement')

        # ... and immediately again, without reloading or reopening.
        self.url(page, THIRD_VIDEO)
        self.save(page)
        self.pending(page, 0)
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000077',
                         'the second change lands rather than conflicting')
        self.assertEqual(self.cached_source_revision(page, work), 2)

    def test_the_old_videos_thumbnail_does_not_survive_in_any_cache(self):
        """The server clears `thumb_url` when the identity changes. A cached
        row that keeps it shows video A's picture on a card that says video B
        -- a lie the user can see, in the one place they are looking."""
        server, page, _context = self.start()
        work = server.ids['playlist_video_one']
        self.db_for(server).execute_query(
            "UPDATE works SET thumb_url = ? WHERE id = ?",
            ('https://img.example/e2e0000001.jpg', work))
        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_work_from_home(page, PLAYLIST_VIDEO_ONE_TITLE)
        o._wait_entity_cached(page, 'work', work)
        self.assertEqual(self.cached_work(page, work)['thumb_url'],
                         'https://img.example/e2e0000001.jpg')

        self.edit(page)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 0)
        self.assertIsNone(self.db_for(server).get_work(work)['thumb_url'])
        cached = self.cached_work(page, work)
        self.assertIsNone(cached['thumb_url'],
                          'the previous video\'s image is gone from the cache too')
        self.assertEqual(cached['provider_id'], 'e2e0000099')

    def test_changing_the_video_twice_before_it_sends_is_one_operation(self):
        """A source is an aggregate, so there is at most one unsynchronized
        intent for a Work. Two rows sharing one base revision means the
        coordinator sends the first, the revision advances, and the user's own
        second change then arrives stale.

        Kept Chromium: the editor's `state.observed` must feed
        `saveWorkSource`. Store-only Node coalescing cannot catch an editor
        that stops measuring against the acknowledged base.
        """
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)

        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)
        self.url(page, THIRD_VIDEO)
        self.save(page)
        self.pending(page, 1)
        operations = page.evaluate(
            "() => prksSync.store.listOperations().then(r => r"
            "  .filter(o => o.operation === 'SET_WORK_SOURCE')"
            "  .map(o => o.payload.source.url))")
        self.assertEqual(operations, [THIRD_VIDEO],
                         'one intent, naming the video the user actually chose')
        self.assertEqual(self.effective(page, work)['provider_id'], 'e2e0000077')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000077')
        self.assertEqual(self.cached_source_revision(page, work), 1,
                         'ONE revision: the intermediate choice was never sent')

    def test_returning_to_the_acknowledged_video_leaves_no_intent(self):
        """Return-to-base through the real editor, not a hand-built store call."""
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)
        # The share-link spelling of the video it already has: a different URL,
        # the same video.
        self.url(page, SHORT_ONE)
        self.save(page)
        self.pending(page, 0)
        self.assertEqual(self.cached_work(page, work)['provider_id'], 'e2e0000001',
                         'back to the acknowledged video, with nothing to send')
        self.assertEqual(self.cached_work(page, work)['source_url'], WATCH_ONE,
                         'and the acknowledged spelling is untouched')

    def test_apply_my_source_resends_the_same_intent_against_the_server(self):
        """The conflict reached the user and the button threw.

        `resolveConflict` only permitted reapply for the field-scoped
        `REVISION_CONFLICT`, so pressing "Apply my source" on an aggregate
        conflict failed with `invalid_resolution` -- a decision the UI offered
        and the store refused.
        """
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)
        self.other_device_sets_source(server, work, OTHER_VIDEO)

        self.reconnect(page, context)
        self.conflicts(page, 1)
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000055')

        page.locator('[data-prks-role="work-source-sync"] button',
                     has_text='Apply my source').click()
        self.pending(page, 0)
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000099',
                         'the user\'s own video, applied against the server\'s revision')
        self.assertEqual(self.columns(server, work)['source_url'], WATCH_TWO)

    def test_after_apply_the_editor_measures_against_the_servers_video(self):
        """The replacement operation is created against the server's revision
        and is still NEVER SENT -- so it is still coalescible, and the base it
        coalesces against has to be the server's too.

        With the editor left on its pre-conflict base, changing one's mind
        again rewrote the replacement against a revision the server had already
        passed, so it conflicted with the same edit a second time."""
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)                 # local B
        self.save(page)
        self.pending(page, 1)
        self.other_device_sets_source(server, work, OTHER_VIDEO)   # server C

        self.reconnect(page, context)
        self.conflicts(page, 1)
        self.offline(page, context)               # hold the replacement unsent
        page.locator('[data-prks-role="work-source-sync"] button',
                     has_text='Apply my source').click()
        self.pending(page, 1)
        self.assertEqual(self.source_operations(page), [(WATCH_TWO, 1)],
                         'B, against the revision the server reported')

        # B -> D before it sends: ONE operation, still on the server's base.
        self.url(page, THIRD_VIDEO)
        self.save(page)
        self.pending(page, 1)
        self.assertEqual(self.source_operations(page), [(THIRD_VIDEO, 1)],
                         'one intent, still measured against the server')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000077',
                         'and it lands rather than conflicting a second time')

    def test_after_apply_choosing_the_servers_own_video_cancels(self):
        """Returning to the identity the server reported is a cancellation.
        On the pre-conflict base the editor read it as a change and sent it,
        colliding with the very video it was converging on.

        Kept Chromium: Apply must update the editor's observed base. A Node
        test that constructs `serverBase` by hand still passes if the Apply
        button stops doing that.
        """
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)
        self.other_device_sets_source(server, work, OTHER_VIDEO)

        self.reconnect(page, context)
        self.conflicts(page, 1)
        self.offline(page, context)
        page.locator('[data-prks-role="work-source-sync"] button',
                     has_text='Apply my source').click()
        self.pending(page, 1)

        self.url(page, SHORT_OTHER)               # the server's video, respelled
        self.save(page)
        self.pending(page, 0)
        self.assertEqual(self.source_operations(page), [],
                         'nothing left to send: this IS the server\'s video')

    def test_use_server_moves_this_device_to_the_servers_video(self):
        """Discarding the local intent is only half of "Use server".

        The tab still held the source from BEFORE the conflict, and the
        terminal result carries only a bounded URL preview -- deliberately
        shortenable, so nothing authoritative can be built from it. Dropping
        the overlay without re-reading left the user looking at video A while
        the server held video C, with nothing left to correct it.
        """
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)
        self.other_device_sets_source(server, work, OTHER_VIDEO)

        self.reconnect(page, context)
        self.conflicts(page, 1)
        page.locator('[data-prks-role="work-source-sync"] button',
                     has_text='Use server').click()
        self.pending(page, 0)

        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000055',
                         'the server keeps what it had')
        page.wait_for_function(
            "() => { const el = document.getElementById('meta-video-url');"
            "        return !!el && el.value === '%s'; }" % OTHER_VIDEO)
        self.assertEqual(self.cached_work(page, work)['provider_id'], 'e2e0000055',
                         'and the cached Work is the server\'s video')
        self.assertEqual(
            page.evaluate("() => { const ctx = prksGetFocusedTabContext();"
                          "        const w = ctx && ctx.getEntity('work');"
                          "        return w && w.provider_id; }"),
            'e2e0000055',
            'the open tab holds it too -- not the source it had before')
        self.assertEqual(self.cached_source_revision(page, work), 1,
                         'and the base is the server\'s revision, not the stale one')

        # The editor is immediately usable again against that new base.
        self.url(page, THIRD_VIDEO)
        self.save(page)
        self.pending(page, 0)
        self.assertEqual(self.columns(server, work)['provider_id'], 'e2e0000077',
                         'a further edit lands rather than conflicting')

    def test_use_server_stays_unavailable_rather_than_showing_the_stale_source(self):
        """If the server cannot be reached between the conflict arriving and
        the decision being made, the discard still stands -- but the editor
        must not fall back to the pre-conflict Work, which is precisely the
        source the user just decided against."""
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, WATCH_TWO)
        self.save(page)
        self.pending(page, 1)
        self.other_device_sets_source(server, work, OTHER_VIDEO)

        self.reconnect(page, context)
        self.conflicts(page, 1)
        self.offline(page, context)
        page.locator('[data-prks-role="work-source-sync"] button',
                     has_text='Use server').click()
        self.pending(page, 0)
        page.wait_for_function(
            "() => { const b = document.getElementById('save-work-source-btn');"
            "        return !!b && b.disabled; }")
        self.assertIn('not available offline',
                      page.locator('[data-prks-role="work-source-sync"]').inner_text())

        # And it recovers by itself once the server is reachable again -- the
        # editor is still open, so waiting for the user to navigate away and
        # back would leave a dead control on screen.
        self.reconnect(page, context)
        page.wait_for_function(
            "() => { const b = document.getElementById('save-work-source-btn');"
            "        return !!b && !b.disabled; }", timeout=20000)
        self.assertEqual(page.input_value('#meta-video-url'), OTHER_VIDEO)
        self.assertEqual(self.cached_source_revision(page, work), 1)

    def test_a_convergent_acknowledgement_never_publishes_an_unstored_url(self):
        """Two devices choosing the same video in different spellings.

        The server stores nothing -- a spelling is not a change -- so a client
        that wrote back "what I asked for" would hold an acknowledged
        `source_url` the server does not have. Worse, it would keep the
        identity it last cached while the server has moved on.

        Kept Chromium: `acceptAck` must write the stored spelling into the
        open editor. Handler/cache reconciliation alone cannot prove that.
        """
        server, page, context = self.start()
        work = server.ids['playlist_video_one']
        self.edit(page)
        self.offline(page, context)
        self.url(page, SHORT_TWO)          # youtu.be/...99
        self.save(page)
        self.pending(page, 1)
        self.other_device_sets_source(server, work, WATCH_TWO)   # watch?v=...99

        self.reconnect(page, context)
        self.pending(page, 0)
        stored = self.columns(server, work)
        self.assertEqual(stored['source_url'], WATCH_TWO, 'the server kept its own spelling')
        cached = self.cached_work(page, work)
        self.assertEqual(cached['source_url'], stored['source_url'],
                         'and the client holds exactly that, not the URL it sent')
        self.assertEqual(cached['provider_id'], stored['provider_id'])
        self.assertEqual(self.cached_source_revision(page, work), 1,
                         'the base is the server\'s revision, not the one we started from')
        self.assertEqual(page.input_value('#meta-video-url'), stored['source_url'],
                         'the open editor shows the stored spelling too')
