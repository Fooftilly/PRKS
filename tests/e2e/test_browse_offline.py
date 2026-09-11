"""Offline browse catalogs: #/progress, #/types, #/types/:type, #/recent and
Home -> Recently added.

Three INDEPENDENT projections back these routes. The central thing under test
is that independence: merely *opening* a Work is a canonical mutation (it
stamps `last_opened_at`) and must cost the Recent cache alone, never the
Progress/Types/Recently-added caches. A single `works:index` carrying
`last_opened_at` would have made reading a file drop four offline surfaces.
"""
import json
import os
import unittest
from urllib.parse import urlparse

from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    WORK_A_TITLE,
    WORK_B_TITLE,
    seed_folders_library,
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

BROWSE_KEYS = ('works-browse:index', 'recent:index', 'recently-added:index')
BROWSE_DOMAINS = ('works-browse', 'recent', 'recently-added')


class BrowseOfflineTests(unittest.TestCase):
    def start(self, seed=seed_folders_library):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        return server, page, context, collector

    # ---- helpers ------------------------------------------------------------

    def progress(self, page, status='In Progress'):
        page.evaluate("s => prksNavigate('#/progress?status=' + encodeURIComponent(s))", status)

    def types(self, page):
        page.evaluate("() => prksNavigate('#/types')")

    def type_detail(self, page, doc_type='book'):
        page.evaluate("t => prksNavigate('#/types/' + encodeURIComponent(t))", doc_type)

    def recent(self, page):
        page.evaluate("() => prksNavigate('#/recent')")

    def recently_added(self, page):
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector('.prks-folder-library__tab-btn[data-tab="recently-added"]')
        page.evaluate("() => prksSwitchFolderLibraryTab('recently-added')")

    def cache_all(self, page, ids):
        """Warms all three browse projections plus the Folder hierarchy."""
        self.progress(page)
        o._wait_list_cached(page, 'works-browse:index')
        # Recent is empty until something has been opened.
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        self.recent(page)
        o._wait_list_cached(page, 'recent:index')
        page.evaluate("() => prksNavigate('#/folders')")
        o._wait_list_cached(page, 'folders:index')
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')

    def generations(self, page):
        return {d: o._domain_generation(page, d) for d in BROWSE_DOMAINS}

    def changed(self, page, before, expected):
        for domain, generation in before.items():
            with self.subTest(domain=domain):
                if domain in expected:
                    self.assertGreater(o._domain_generation(page, domain), generation)
                else:
                    self.assertEqual(o._domain_generation(page, domain), generation)

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate("async () => { try { await prksRequest('/api/settings'); } catch (_) {} }")
        page.wait_for_function("prksOfflineRuntimeState() === 'offline'")

    def all_paths(self, page):
        seen = []
        page.on('request', lambda req: seen.append(urlparse(req.url).path))
        return seen

    # ---- cached rendering ---------------------------------------------------

    def test_progress_renders_offline_from_the_cached_catalog(self):
        server, page, context, _c = self.start()
        self.progress(page)
        o._wait_list_cached(page, 'works-browse:index')
        self.offline(page, context)
        seen = self.all_paths(page)
        self.progress(page)
        o._wait_offline_banner(page)
        text = o._content_text(page)
        self.assertIn(WORK_A_TITLE, text)
        # work_b is 'Not Started', so it must not appear under 'In Progress'.
        self.assertNotIn(WORK_B_TITLE, text)
        # Filtering is a pure local projection of the cached catalog.
        self.assertEqual([p for p in seen if p.startswith('/api/works/')], [])

    def test_types_and_type_detail_render_offline_from_one_cache(self):
        server, page, context, _c = self.start()
        # Visiting Types alone must warm the same catalog Type detail uses.
        self.types(page)
        o._wait_list_cached(page, 'works-browse:index')
        self.offline(page, context)
        self.types(page)
        o._wait_offline_banner(page)
        self.assertIn('File types', o._content_text(page))
        self.type_detail(page, 'book')
        o._wait_offline_banner(page)
        detail = o._content_text(page)
        self.assertIn(WORK_A_TITLE, detail)
        self.assertNotIn(WORK_B_TITLE, detail)

    def test_recent_renders_offline_from_its_own_snapshot(self):
        server, page, context, _c = self.start()
        ids = server.ids
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_a'])
        self.recent(page)
        o._wait_list_cached(page, 'recent:index')
        self.offline(page, context)
        self.recent(page)
        o._wait_offline_banner(page)
        text = o._content_text(page)
        self.assertIn(WORK_A_TITLE, text)
        self.assertIn('Last opened', text)

    def test_recently_added_renders_offline_and_is_no_longer_disabled(self):
        server, page, context, _c = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        o._wait_list_cached(page, 'folders:index')
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')
        self.offline(page, context)
        page.evaluate("() => prksNavigate('#/folders')")
        o._wait_offline_banner(page)
        tab = page.locator('.prks-folder-library__tab-btn[data-tab="recently-added"]')
        self.assertFalse(tab.is_disabled(), 'Recently added is cached now')
        page.evaluate("() => prksSwitchFolderLibraryTab('recently-added')")
        page.wait_for_timeout(700)
        text = o._content_text(page)
        self.assertIn(WORK_A_TITLE, text)
        self.assertIn('Added', text)

    def test_cached_browse_routes_request_no_thumbnails(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        self.offline(page, context)
        seen = self.all_paths(page)
        for go in (self.progress, self.type_detail, self.recent):
            go(page)
            o._wait_offline_banner(page)
        self.recently_added(page)
        page.wait_for_timeout(700)
        self.assertEqual([p for p in seen if 'thumbnail' in p], [])

    # ---- unavailable vs empty ----------------------------------------------

    def test_missing_snapshots_report_unavailable_not_an_empty_library(self):
        server, page, context, _c = self.start()
        self.offline(page, context)
        for go, label in (
            (self.progress, 'Progress not available offline'),
            (self.types, 'Types not available offline'),
            (self.recent, 'Recently opened not available offline'),
        ):
            with self.subTest(label=label):
                page.evaluate("() => prksNavigate('#/concepts')")
                page.wait_for_timeout(200)
                go(page)
                o._wait_offline_unavailable(page)
                self.assertIn(label, o._content_text(page))

    def test_cached_empty_catalog_is_a_legitimate_empty_state(self):
        server, page, context, _c = self.start()
        self.progress(page)
        o._wait_list_cached(page, 'works-browse:index')
        page.evaluate("() => window.createPrksOfflineStore().putList('works-browse:index', [])")
        page.wait_for_timeout(250)
        self.offline(page, context)
        self.progress(page)
        o._wait_offline_banner(page)
        text = o._content_text(page)
        self.assertNotIn('Progress not available offline', text)
        self.assertIn('No files with this progress status yet.', text)

    def test_missing_recently_added_snapshot_reports_its_own_state(self):
        server, page, context, _c = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        o._wait_list_cached(page, 'folders:index')
        self.offline(page, context)
        page.evaluate("() => prksNavigate('#/folders')")
        o._wait_offline_banner(page)
        page.evaluate("() => prksSwitchFolderLibraryTab('recently-added')")
        # The read-through must first fail at the transport layer, so wait on
        # the rendered outcome rather than a fixed delay.
        page.wait_for_function(
            """() => {
                const p = document.querySelector('#prks-folder-library-recently-added');
                return !!p && p.textContent.indexOf('not available offline') !== -1;
            }""",
            timeout=20000,
        )

    # ---- independence: the reason there are three caches --------------------

    def test_opening_a_work_costs_recent_alone(self):
        """The defining property of this milestone. A single catalog carrying
        last_opened_at would drop Progress, Types and Recently added here."""
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        before = self.generations(page)

        o._open_work_from_home(page, WORK_B_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_b'])

        self.changed(page, before, {'recent'})
        # The other two snapshots are still on the device and still serve.
        self.assertIsNotNone(o._cached_list(page, 'works-browse:index'))
        self.assertIsNotNone(o._cached_list(page, 'recently-added:index'))
        self.offline(page, context)
        self.progress(page)
        o._wait_offline_banner(page)
        self.assertIn(WORK_A_TITLE, o._content_text(page))

    def test_internal_refreshes_never_record_an_open(self):
        """The regression this milestone exists for.

        `GET /api/works/:id` used to stamp `last_opened_at`, so every internal
        refresh after a save silently reordered Recent -- while the UI left
        `recent:index` eligible, because it correctly believed a tag or folder
        edit had nothing to do with Recent. Each case below drives the REAL UI
        path, refresh read included.
        """
        server, page, context, _c = self.start()
        ids = server.ids
        tag_id = page.evaluate(
            """async () => {
                const res = await prksRequest('/api/tags', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'Browse Open Probe Tag' }),
                });
                return (await res.json()).id;
            }"""
        )
        cases = (
            ('folder move', """async ([wid, fid]) => {
                    await patchWorkFolder(wid, fid);
                    await fetchWorkDetails(wid);
                }""", ['work_a', 'folder_child']),
            ('tag add', """async ([wid, tid]) => {
                    await prksRequest('/api/works/' + encodeURIComponent(wid) + '/tags', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tag_id: tid }),
                    });
                    await fetchWorkDetails(wid);
                }""", ['work_a', None]),
            # Patch `status`, not `title`: renaming would break the
            # title-based card lookup the next iteration uses.
            ('metadata save', """async ([wid]) => {
                    await prksRequest('/api/works/' + encodeURIComponent(wid), {
                        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: 'Paused' }),
                    });
                    await fetchWorkDetails(wid);
                }""", ['work_a', None]),
            ('playlist set', """async ([wid, plid]) => {
                    try { await addWorkToPlaylist(plid, wid); } catch (_) {}
                    await fetchWorkDetails(wid);
                }""", ['work_b', 'playlist_b']),
        )
        for label, script, keys in cases:
            with self.subTest(case=label):
                self.cache_all(page, ids)
                before = self.generations(page)
                arg = [ids.get(keys[0]), tag_id if keys[1] is None else ids.get(keys[1])]
                page.evaluate(script, arg)
                page.wait_for_timeout(400)

                # Asserting only on the client generation cannot detect this
                # bug: the defect IS that the client stays unaware while the
                # server representation moves. The real invariant is that the
                # snapshot the device still considers eligible matches what
                # the server would serve right now.
                envelope = o._cached_list(page, 'recent:index')
                self.assertIsNotNone(envelope, label)
                cached = envelope['value']
                fresh = page.evaluate(
                    """async () => {
                        const res = await prksRequest('/api/recent');
                        return await res.json();
                    }"""
                )
                self.assertEqual(
                    [(r['id'], r['last_opened_at']) for r in cached],
                    [(r['id'], r['last_opened_at']) for r in fresh],
                    "%s changed the server's Recent while recent:index stayed eligible" % label,
                )
                self.assertEqual(
                    o._domain_generation(page, 'recent'), before['recent'],
                    "%s recorded an open" % label,
                )

    def test_a_genuine_foreground_open_does_record_one(self):
        """The other half: the explicit event must still fire for real opens."""
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        before = self.generations(page)
        seen = []
        page.on('request', lambda req: seen.append(urlparse(req.url).path)
                if req.method == 'POST' else None)

        o._open_work_from_home(page, WORK_B_TITLE)
        o._wait_entity_cached(page, 'work', ids['work_b'])
        page.wait_for_function(
            "(g) => window.prksOfflineDomainGeneration('recent') > g",
            arg=before['recent'], timeout=20000,
        )
        self.assertIn('/api/works/%s/opened' % ids['work_b'], seen)
        # ... and still only Recent.
        self.changed(page, before, {'recent'})

    def test_work_creation_stales_the_catalog_and_recently_added_but_not_recent(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        before = self.generations(page)
        page.evaluate(
            """async () => {
                await prksRequest('/api/works', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: 'Browse Created Work' }),
                });
                prksMarkWorksBrowseChanged();
                prksMarkRecentlyAddedChanged();
            }"""
        )
        # A new Work has last_opened_at NULL, so Recent cannot contain it.
        self.changed(page, before, {'works-browse', 'recently-added'})

    def test_folder_move_stales_recently_added_alone(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        before = self.generations(page)
        page.evaluate("async ([wid, fid]) => { await patchWorkFolder(wid, fid); }",
                      [ids['work_a'], ids['folder_child']])
        # Only Recently added carries folder_id; Progress/Types never render it.
        self.changed(page, before, {'recently-added'})

    def test_display_changes_stale_all_three_projections(self):
        server, page, context, _c = self.start()
        ids = server.ids
        for hook, label in (
            ("prksMarkWorkTitleChanged(id)", 'metadata'),
            ("prksMarkWorkRoleChanged(id, 'Author')", 'author role'),
            ("prksMarkWorkRoleChanged(id, 'Editor')", 'editor role'),
        ):
            with self.subTest(hook=label):
                self.cache_all(page, ids)
                before = self.generations(page)
                page.evaluate("id => %s" % hook, ids['work_a'])
                self.changed(page, before, set(BROWSE_DOMAINS))

    def test_non_display_role_and_unrelated_mutations_keep_browse_caches(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        before = self.generations(page)
        page.evaluate(
            """async ([wid, cid]) => {
                prksMarkWorkRoleChanged(wid, 'Reviewer');
                await updateConcept(cid, { description: 'Browse-irrelevant edit.' });
                await createPosition({ name: 'Browse-irrelevant position' });
            }""",
            [ids['work_a'], ids['concept_child']],
        )
        self.changed(page, before, set())
        for key in BROWSE_KEYS:
            self.assertIsNotNone(o._cached_list(page, key), key)

    def test_bulk_status_stales_all_three_and_bulk_move_only_recently_added(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        before = self.generations(page)
        page.evaluate(
            """async (wid) => {
                await bulkUpdateWorks({ action: 'set_status', work_ids: [wid], status: 'Completed' });
            }""",
            ids['work_a'],
        )
        self.changed(page, before, set(BROWSE_DOMAINS))

        self.cache_all(page, ids)
        before = self.generations(page)
        page.evaluate(
            """async ([wid, fid]) => {
                await bulkUpdateWorks({ action: 'move_folder', work_ids: [wid], folder_id: fid });
            }""",
            [ids['work_a'], ids['folder_parent']],
        )
        self.changed(page, before, {'recently-added'})

    # ---- cache safety -------------------------------------------------------

    def test_malformed_authoritative_payloads_never_poison_the_cache(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        good = {k: o._cached_list(page, k) for k in BROWSE_KEYS}
        for key in BROWSE_KEYS:
            self.assertIsNotNone(good[key], key)

        # Each row is invalid for its own projection: no abstract_excerpt, no
        # last_opened_at, no folder_id respectively.
        cases = (
            ('**/api/works**', [{'id': 'W-1', 'title': 'x'}], 'works-browse:index', self.progress),
            ('**/api/recent', [{'id': 'W-1', 'title': 'x'}], 'recent:index', self.recent),
            ('**/api/recently-added', [{'id': 'W-1', 'title': 'x', 'created_at': '2026-01-01'}],
             'recently-added:index', self.recently_added),
        )
        for pattern, body, key, go in cases:
            with self.subTest(key=key):
                payload = json.dumps(body)

                # Playwright calls handlers as (route, request); binding the
                # body through a default arg would be clobbered by `request`.
                def bad(route, _request=None, _payload=payload):
                    route.fulfill(status=200, content_type='application/json', body=_payload)

                page.route(pattern, bad)
                try:
                    go(page)
                    page.wait_for_timeout(900)
                    self.assertEqual(o._cached_list(page, key), good[key], key)
                finally:
                    o._safe_unroute(page, pattern, bad)

    def test_corrupted_cached_snapshots_are_discarded_before_rendering(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)
        page.evaluate(
            """() => {
                const store = window.createPrksOfflineStore();
                return Promise.all([
                    // no abstract_excerpt
                    store.putList('works-browse:index', [{ id: 'W-x', title: 'Bad' }]),
                    // last_opened_at missing
                    store.putList('recent:index', [{ id: 'W-x', title: 'Bad' }]),
                    // folder_id absent must not read as "unfiled"
                    store.putList('recently-added:index',
                        [{ id: 'W-x', title: 'Bad', created_at: '2026-01-01' }]),
                ]);
            }"""
        )
        page.wait_for_timeout(250)
        # Corrupting the store directly does not bump any coherence
        # generation, so the tab's in-memory Recently-added copy would still
        # (correctly) win. Drop it to model the real case: a fresh mount that
        # finds a corrupted snapshot.
        page.evaluate("() => { window.__prksFolderDashboardState = null; }")
        self.offline(page, context)

        self.progress(page)
        o._wait_offline_unavailable(page)
        o._wait_list_uncached(page, 'works-browse:index')
        self.recent(page)
        o._wait_offline_unavailable(page)
        o._wait_list_uncached(page, 'recent:index')
        self.recently_added(page)
        page.wait_for_function(
            """() => {
                const p = document.querySelector('#prks-folder-library-recently-added');
                return !!p && p.textContent.indexOf('not available offline') !== -1;
            }""",
            timeout=20000,
        )

    def test_reachable_server_errors_are_not_disguised_as_offline(self):
        server, page, context, _c = self.start()
        ids = server.ids
        self.cache_all(page, ids)

        def boom(route):
            route.fulfill(status=500, content_type='application/json',
                          body=json.dumps({'error': 'server exploded'}))

        page.route('**/api/works?projection=browse', boom)
        self.addCleanup(lambda: o._safe_unroute(page, '**/api/works?projection=browse', boom))
        self.progress(page)
        page.wait_for_timeout(800)
        self.assertNotIn('Offline · cached', o._content_text(page))
        self.assertIsNotNone(o._cached_list(page, 'works-browse:index'))

    def test_browse_index_does_not_prefetch_work_details(self):
        server, page, context, _c = self.start()
        seen = self.all_paths(page)
        self.progress(page)
        o._wait_list_cached(page, 'works-browse:index')
        self.types(page)
        # Thumbnails are an ordinary online render; what must not happen is
        # pulling each Work's DETAIL behind a catalog render.
        details = [p for p in seen
                   if p.startswith('/api/works/') and not p.endswith('/thumbnail')]
        self.assertFalse(details, details)


if __name__ == '__main__':
    unittest.main()
