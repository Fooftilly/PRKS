"""Authoritative Graph snapshots offline: variants, navigation and coherence."""
import copy
import json
import os
import unittest
from urllib.parse import urlsplit

from tests.e2e import test_offline as o
from tests.e2e.fixtures import seed_arguments_library
from tests.e2e.harness import AppServer, open_app_page, profile_e2e_phase, record_e2e_phase_seconds, require_chromium, wait_for_async
from tests.e2e.policy import PROFILE_ENV, env_flag_enabled

CORE = 'research-graph-core'
PEOPLE = 'research-graph-people'


# Install only in the opt-in browser profiler; the ordinary E2E contract
# and production route never gain instrumentation or alternate code paths.
# Wrap classic-script global function bindings rather than changing the
# production Person route. The wrappers preserve `this`, returned values,
# and rejections; measurements use browser monotonic time, not IPC latency.
# Include full route envelopes to identify unmeasured work outside the detail
# stages. Samples retain overlap, call counts and AbortError outcomes.
PERSON_RETURN_PROBE_INSTALL = r"""() => {
    if (window.__prksGraphPersonReturnProbe) {
        throw new Error('Graph Person return probe was not restored');
    }
    const required = [
        ['route_total', 'prksRenderTabRoute'],
        ['route_commit', 'prksCommitTabRouteRender'],
        ['durable_operations', 'prksDurableOperationsOrNone'],
        ['detail_fetch', 'prksOfflineDetailFetch'],
        ['offline_reader', 'prksOfflineReadEntity'],
        ['effective_person', 'prksEffectivePersonRecord'],
        ['pending_metadata', 'prksHydratePendingWorkMetadata'],
        ['person_render', 'renderPersonDetails'],
        ['banner_insertion', 'prksOfflinePrependBanner'],
    ];
    const optionalMetadata = [
        ['metadata_work', 'prksRefreshPendingWorkMetadata'],
        ['metadata_roles', 'prksRefreshPendingWorkRoles'],
        ['metadata_person_names', 'prksRefreshPendingPersonNames'],
        ['metadata_folders', 'prksRefreshPendingWorkFolders'],
        ['metadata_playlists', 'prksRefreshPendingWorkPlaylists'],
        ['metadata_concepts', 'prksRefreshPendingConceptNames'],
        ['metadata_positions', 'prksRefreshPendingPositionNames'],
        ['metadata_arguments', 'prksRefreshPendingArgumentNames'],
    ];
    const missing = required.filter(([, name]) => typeof window[name] !== 'function');
    if (missing.length) {
        throw new Error('Missing Person route instrumentation targets: ' +
            missing.map(([, name]) => name).join(', '));
    }
    const originals = [];
    const samples = [];
    for (const [phase, name] of [...required, ...optionalMetadata]) {
        const original = window[name];
        if (typeof original !== 'function') continue;
        const wrapper = function (...args) {
            const started = performance.now();
            // Observe only: do not initiate cache reads, state transitions,
            // or network requests from the profiler. The offline runtime
            // itself retains full authority for all eligibility decisions.
            const targetPerson = phase === 'offline_reader' && args[0] === 'person';
            const opts = targetPerson && args[3] || {};
            const readContext = targetPerson ? {
                stateAtStart: window.prksOfflineRuntimeState(),
                preferCache: opts.preferCacheWhenOffline === true,
                domainBlocked: window.prksOfflineIsDomainBlocked(opts.domain),
                signalAborted: !!(opts.signal && opts.signal.aborted),
            } : null;
            const record = (outcome, error, result) => {
                const sample = {
                    phase, startedMs: started,
                    ms: Math.max(0, performance.now() - started),
                    outcome, errorName: error && error.name || '',
                };
                if (readContext) {
                    sample.reader = {
                        ...readContext,
                        stateAtEnd: window.prksOfflineRuntimeState(),
                        outcome, errorName: error && error.name || '',
                        // These are the runtime's public, non-sensitive
                        // provenance values, never the cached record itself.
                        source: result && ['server', 'cache', 'unavailable']
                            .includes(result.source) ? result.source : null,
                    };
                }
                samples.push(sample);
            };
            const failed = error => {
                record(error && error.name === 'AbortError' ? 'abort' : 'error', error);
                throw error;
            };
            try {
                const value = original.apply(this, args);
                if (value && typeof value.then === 'function') {
                    return value.then(
                        result => { record('ok', null, result); return result; },
                        failed,
                    );
                }
                record('ok', null, value);
                return value;
            } catch (error) {
                failed(error);
            }
        };
        originals.push({ name, original, wrapper });
        window[name] = wrapper;
    }
    window.__prksGraphPersonReturnProbe = { originals, samples };
}"""

PERSON_RETURN_PROBE_COLLECT = r"""() => {
    const probe = window.__prksGraphPersonReturnProbe;
    if (!probe) throw new Error('Missing Graph Person return probe');
    let displaced = false;
    for (const { name, original, wrapper } of probe.originals) {
        if (window[name] !== wrapper) {
            displaced = true;
            continue;
        }
        window[name] = original;
    }
    delete window.__prksGraphPersonReturnProbe;
    if (displaced) throw new Error('Graph Person return probe was replaced');
    return probe.samples;
}"""


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class ResearchGraphOfflineTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_arguments_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        self.addCleanup(context.close)
        o._wait_sw_active(page)
        return server, page, context

    def graph(self, page, focus=''):
        page.evaluate("h => prksNavigate(h)", '#/graph' + ('?focus=' + focus if focus else ''))

    def mounted(self, page):
        """Wait for the Cytoscape instance to exist.

        Null-safe on purpose: `prksGetResearchGraphDebug()` returns null until
        the graph module has mounted, and a predicate that dereferences it
        THROWS rather than returning false -- which aborts the wait instead of
        retrying it. The condition asserted is unchanged; only the moment
        before it becomes observable is handled.
        """
        page.wait_for_function(
            "() => { const d = prksGetResearchGraphDebug(); return !!(d && d.cy); }")

    def cache(self, page, ids, variants=(False, True)):
        for people in variants:
            self.graph(page, 'person:' + ids['person'] if people else '')
            self.mounted(page)
            o._wait_entity_cached(page, PEOPLE if people else CORE, 'snapshot')

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate("async () => { try { await prksRequest('/api/settings'); } catch (_) {} }")
        page.wait_for_function("prksOfflineRuntimeState() === 'offline'")

    def toggle(self, page, people):
        panel = page.locator('[data-prks-role="graph-filters-panel"]')
        if not panel.is_visible():
            page.locator('[data-prks-role="graph-filters-toggle"]').click()
        page.locator('[data-graph-filter="people"]').set_checked(people)

    def selected(self, page, node):
        page.wait_for_function('id => getSelectedGraphNodeId() === id', arg=node)

    def generations(self, page):
        return [o._domain_generation(page, d) for d in (CORE, PEOPLE)]

    def changed(self, page, before, expected=(True, True)):
        for d, old, change in zip((CORE, PEOPLE), before, expected):
            if change:
                page.wait_for_function('x => prksOfflineDomainGeneration(x[0]) > x[1]', arg=[d, old])
                o._wait_entity_uncached(page, d, 'snapshot')
            else:
                self.assertEqual(o._domain_generation(page, d), old)
                self.assertIsNotNone(o._cached_entity(page, d, 'snapshot'))

    def open_people(self, page, work_id):
        """Manage relationships, after its revision base is actually readable.

        The role modal hiding, or the Unlink button existing, does not mean
        `work-people-state` has been read. Durable save refuses without that
        base, and a waiter on an empty operation queue then succeeds vacuously.
        """
        o._open_work_from_home(page, o.WORK_A_TITLE)
        o._open_details_drawer_if_tiled(page)
        page.evaluate("() => { void prksSetWorkDetailsMode('people'); }")
        page.wait_for_selector('.work-link-person-btn')
        o._wait_entity_cached(page, 'work-people-state', work_id)
        page.wait_for_function(
            "() => { const b = document.querySelector('.work-link-person-btn');"
            "        return !!b && !b.disabled; }")

    def people_graph_author(self, page, person_id, work_id):
        """Cached People Graph membership for one Author edge. Scalars only."""
        return page.evaluate("""([pid, wid]) => window.createPrksOfflineStore()
            .getEntity('research-graph-people', 'snapshot').then(row => {
                if (!row) return {cached: false};
                const id = 'work_author:person:' + pid + '>work:' + wid;
                const nodes = row.value.nodes || [];
                return {
                    cached: true,
                    hasPerson: nodes.some(n => n && n.id === 'person:' + pid),
                    hasWork: nodes.some(n => n && n.id === 'work:' + wid),
                    hasEdge: (row.value.edges || []).some(e => e && e.id === id),
                };
            })""", [person_id, work_id])

    def wait_people_author_edge(self, page, person_id, work_id, present):
        """Author ACK patches the People Graph snapshot in place; it is not deleted.

        Return a non-empty sentinel only on match: `wait_for_async` treats any
        truthy value as success, so returning the live `has` boolean would pass
        the instant the edge was in the wrong state.
        """
        wait_for_async(page, """([pid, wid, want]) => window.createPrksOfflineStore()
            .getEntity('research-graph-people', 'snapshot').then(row => {
                if (!row) return '';
                const id = 'work_author:person:' + pid + '>work:' + wid;
                const has = (row.value.edges || []).some(e => e && e.id === id);
                return has === want ? 'match' : '';
            })""", arg=[person_id, work_id, bool(present)])

    def test_core_cache_and_local_interactions(self):
        server, page, context = self.start()
        seen = []
        page.on('request', lambda r: seen.append(r.url) if '/api/research-graph' in r.url else None)
        self.cache(page, server.ids, (False,))
        self.assertFalse(any('people=1' in url for url in seen))
        good = o._cached_entity(page, CORE, 'snapshot')['value']
        self.offline(page, context)
        page.reload(wait_until='domcontentloaded')
        self.mounted(page)
        o._wait_offline_banner(page)
        self.assertEqual(page.evaluate('prksGetResearchGraphDebug().cy.nodes().length'), good['meta']['node_count'])
        self.assertEqual(page.evaluate('prksGetResearchGraphDebug().cy.edges().length'), good['meta']['edge_count'])
        self.assertEqual(set(good), {'nodes', 'edges', 'meta'})
        page.locator('[data-prks-role="graph-find"]').fill(server.ids['argument_a_name'])
        page.locator('.research-graph__find-hit').first.click()
        self.selected(page, 'argument:' + server.ids['argument_a'])
        page.locator('#prks-graph-inspector-title').wait_for()
        page.locator('.research-graph__neighbor').first.click()
        self.assertNotEqual(page.evaluate('getSelectedGraphNodeId()'), 'argument:' + server.ids['argument_a'])
        page.locator('[data-prks-role="graph-filters-toggle"]').click()
        page.locator('[data-graph-filter="concepts"]').uncheck()
        self.assertEqual(page.evaluate("prksGetResearchGraphDebug().cy.nodes('[type=\"concept\"]').filter(n => n.visible()).length"), 0)
        page.locator('[data-graph-filter="concepts"]').check()
        page.locator('[data-graph-filter="sources"]').uncheck()
        self.assertEqual(page.evaluate("prksGetResearchGraphDebug().cy.edges('[type=\"argument_source\"]').filter(e => e.visible()).length"), 0)
        page.locator('[data-graph-filter="sources"]').check()
        page.locator('[data-prks-role="graph-legend-toggle"]').click()
        self.assertTrue(page.locator('[data-prks-role="graph-legend-panel"]').is_visible())
        page.locator('[data-prks-role="graph-fit"]').click()
        page.locator('[data-prks-role="graph-reset"]').click()
        self.mounted(page)
        self.assertIsNone(o._cached_entity(page, PEOPLE, 'snapshot'))

    def test_people_focus_and_both_variants_toggle_offline(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        self.offline(page, context)
        page.reload(wait_until='domcontentloaded')
        self.mounted(page)
        self.selected(page, 'person:' + server.ids['person'])
        o._wait_offline_banner(page)
        self.assertGreater(page.evaluate("prksGetResearchGraphDebug().cy.edges('[type=\"work_author\"]').length"), 0)
        for people in (False, True, False):
            self.toggle(page, people)
            page.wait_for_function('''people => !!prksGetResearchGraphDebug().cy.nodes('[type="person"]').length === people''', arg=people)
            self.assertEqual(page.locator('[data-graph-filter="people"]').is_checked(), people)
            o._wait_offline_banner(page)

    def test_missing_toggle_variants_preserve_current_graph(self):
        server, page, context = self.start()
        for people in (False, True):
            with self.subTest(people=people):
                context.set_offline(False)
                page.evaluate('async () => { await prksRequest("/api/settings"); }')
                self.cache(page, server.ids, (people,))
                missing = CORE if people else PEOPLE
                page.evaluate('k => prksOfflineInvalidateEntity(k, "snapshot")', missing)
                self.offline(page, context)
                # A BLOCK body, so this returns undefined. `window.__x = cy`
                # is an expression whose value is the cytoscape instance, and
                # `evaluate` serializes whatever the expression evaluates to --
                # Playwright then walks that entire object graph, takes ~7-10
                # SECONDS, allocates heavily enough to crash the renderer under
                # parallel load, and hands back None anyway. Identity is
                # compared in the page below; the object must never cross the
                # protocol boundary.
                page.evaluate('() => { window.__graphBefore = prksGetResearchGraphDebug().cy; }')
                self.toggle(page, not people)
                page.get_by_text(('Core graph' if people else 'People-inclusive graph') + ' is not available offline on this device.', exact=True).wait_for()
                self.assertEqual(page.locator('[data-graph-filter="people"]').is_checked(), people)
                self.assertTrue(page.evaluate('window.__graphBefore === prksGetResearchGraphDebug().cy'))

    def test_missing_initial_variants_are_explicit(self):
        server, page, context = self.start()
        self.offline(page, context)
        for focus, label in [('', 'Research Graph not available offline'), ('person:' + server.ids['person'], 'Research Graph variant unavailable offline')]:
            self.graph(page, focus)
            o._wait_offline_unavailable(page)
            self.assertIn(label, o._content_text(page))
            self.assertNotIn('Requested node is not present', o._content_text(page))
            # A BOOLEAN, never the instance itself: `evaluate` returning a
            # live cytoscape object serializes the whole object graph, costs
            # seconds, and hands back None -- so `assertIsNone` on it passed
            # whether or not a graph was mounted, and this assertion was doing
            # nothing at all.
            self.assertFalse(page.evaluate('() => !!prksGetResearchGraphDebug().cy'),
                             'no graph may be mounted here')

    def malformed_authoritative(self, people):
        server, page, context = self.start()
        self.cache(page, server.ids, (people,))
        kind = PEOPLE if people else CORE
        good = o._cached_entity(page, kind, 'snapshot')['value']
        broken = copy.deepcopy(good)
        broken['nodes'].append(broken['nodes'][0])
        broken['meta']['node_count'] += 1
        handler = lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(broken))
        page.route('**/api/research-graph**', handler)
        self.graph(page, 'person:' + server.ids['person'] if people else '')
        page.get_by_text('Could not load Research Graph.', exact=True).wait_for()
        self.assertEqual(o._cached_entity(page, kind, 'snapshot')['value'], good)
        self.assertEqual(page.evaluate('prksOfflineRuntimeState()'), 'online')
        page.unroute('**/api/research-graph**', handler)
        self.offline(page, context)
        self.graph(page, 'person:' + server.ids['person'] if people else '')
        self.mounted(page)
        o._wait_offline_banner(page)

    def test_malformed_authoritative_core(self):
        self.malformed_authoritative(False)

    def test_malformed_authoritative_people(self):
        self.malformed_authoritative(True)

    def test_corrupted_cache_never_mounts(self):
        server, page, context = self.start()
        self.cache(page, server.ids, (False,))
        good = o._cached_entity(page, CORE, 'snapshot')['value']
        self.offline(page, context)
        mutations = [
            "s.nodes.push(s.nodes[0]); s.meta.node_count++",
            "s.nodes[0].route = 'https://example.com'",
            "s.nodes[0].type = 'unknown'",
            "s.edges[0].source = 'work:missing'; s.edges[0].id = s.edges[0].type + ':' + s.edges[0].source + '>' + s.edges[0].target",
            "s.edges[0].target = 'work:missing'; s.edges[0].id = s.edges[0].type + ':' + s.edges[0].source + '>' + s.edges[0].target",
            "s.edges[0].type = 'unknown'",
            "s.edges.push(s.edges[0]); s.meta.edge_count++",
            "s.edges[0].type = 'work_author'; s.edges[0].id = 'work_author:' + s.edges[0].source + '>' + s.edges[0].target",
        ]
        for change in mutations:
            with self.subTest(change=change):
                page.evaluate('async s => { ' + change + '; await createPrksOfflineStore().putEntity("research-graph-core", "snapshot", s); }', good)
                self.graph(page)
                o._wait_offline_unavailable(page)
                o._wait_entity_uncached(page, CORE, 'snapshot')
                self.assertFalse(page.evaluate('() => !!prksGetResearchGraphDebug().cy'),
                                 'no graph may be mounted here')

    def test_413_preserves_online_too_large_ui_without_fallback(self):
        server, page, context = self.start()
        self.cache(page, server.ids, (False,))
        page.route('**/api/research-graph**', lambda route: route.fulfill(status=413, content_type='application/json', body='{"code":"graph_too_large"}'))
        self.graph(page)
        page.get_by_text('This graph is too large to render as a single snapshot.', exact=True).wait_for()
        self.assertEqual(page.evaluate('prksOfflineRuntimeState()'), 'online')
        self.assertEqual(page.locator('[data-prks-role="offline-provenance-banner"]').count(), 0)
        self.assertFalse(page.evaluate('() => !!prksGetResearchGraphDebug().cy'),
                         'no graph may be mounted here')

    def test_degraded_derived_edges_remain_cacheable(self):
        server, page, context = self.start()
        self.cache(page, server.ids, (False,))
        s = o._cached_entity(page, CORE, 'snapshot')['value']
        s['edges'] = [e for e in s['edges'] if not e['type'].startswith('mentions_')]
        s['meta']['edge_count'] = len(s['edges'])
        s['meta']['derived_note_edges_available'] = False
        handler = lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(s))
        page.route('**/api/research-graph**', handler)
        self.graph(page)
        page.get_by_text('Note-mention edges unavailable. Canonical relationships still shown.', exact=True).wait_for()
        wait_for_async(page, 'async () => (await createPrksOfflineStore().getEntity("research-graph-core", "snapshot")).value.meta.derived_note_edges_available === false')
        page.unroute('**/api/research-graph**', handler)
        self.offline(page, context)
        self.graph(page)
        self.mounted(page)
        o._wait_offline_banner(page)
        page.get_by_text('Note-mention edges unavailable. Canonical relationships still shown.', exact=True).wait_for()

    def test_record_graph_navigation_both_directions(self):
        # These optional, non-additive phases reveal the slow section of this
        # browser-only navigation contract without weakening any assertions.
        with profile_e2e_phase('graph_nav_initial_setup'):
            server, page, context = self.start()
        ids = server.ids
        records = [('concept', 'concepts', ids['concept_child'], 'concept'),
                   ('position', 'positions', ids['position_a'], 'position'),
                   ('argument', 'arguments', ids['argument_a'], 'arg'),
                   ('argument', 'arguments', ids['stance'], 'arg'),
                   ('person', 'people', ids['person'], 'person')]
        with profile_e2e_phase('graph_nav_warm_record_cache'):
            for kind, route, rid, selector in records:
                page.evaluate('h => prksNavigate(h)', '#/' + route + '/' + rid)
                o._wait_entity_cached(page, kind, rid)
        with profile_e2e_phase('graph_nav_warm_graph_cache'):
            self.cache(page, ids)
        with profile_e2e_phase('graph_nav_go_offline'):
            self.offline(page, context)
        # Same original order and assertions; stage timers distinguish
        # record-page readiness, Graph mount/focus and the return path.
        # An opt-in CI probe proved Concept remains slower when run second.
        for index, (kind, route, rid, selector) in enumerate(records, start=1):
            with self.subTest(kind=kind, rid=rid):
                # Inclusive total plus non-overlapping internal steps. All
                # original real-browser navigation and assertions are retained.
                with profile_e2e_phase(f'graph_nav_roundtrip_{index}_{kind}'):
                    with profile_e2e_phase(f'graph_nav_stage_{index}_{kind}_enter'):
                        page.evaluate('h => prksNavigate(h)', '#/' + route + '/' + rid)
                        o._wait_offline_banner(page)
                        o._open_details_drawer_if_tiled(page)
                    with profile_e2e_phase(f'graph_nav_stage_{index}_{kind}_to_graph'):
                        page.locator('#prks-' + selector + '-view-graph').click()
                        self.mounted(page)
                        self.selected(page, kind + ':' + rid)
                        o._wait_offline_banner(page)
                    # Browser-side timestamps avoid attributing Playwright
                    # protocol latency to individual Person route awaits.
                    # Only profile-mode runs wrap these functions; normal
                    # E2E runs retain the exact old action/assertion path.
                    profile_person = kind == 'person' and env_flag_enabled(PROFILE_ENV)
                    if profile_person:
                        page.evaluate(PERSON_RETURN_PROBE_INSTALL)
                        # Count requests actually observed by the browser
                        # context, including service-worker-initiated traffic.
                        # Store only the HTTP method, not URLs/record IDs.
                        person_detail_methods = []
                        detail_path = '/api/persons/' + str(rid)

                        def observe_person_request(request):
                            if urlsplit(request.url).path == detail_path:
                                person_detail_methods.append(request.method)

                        context.on('request', observe_person_request)
                    try:
                        with profile_e2e_phase(f'graph_nav_stage_{index}_{kind}_return'):
                            with profile_e2e_phase(f'graph_nav_stage_{index}_{kind}_return_click'):
                                page.locator('#prks-graph-open').click()
                            with profile_e2e_phase(f'graph_nav_stage_{index}_{kind}_return_hash'):
                                page.wait_for_function('h => location.hash === h', arg='#/' + route + '/' + rid)
                            with profile_e2e_phase(f'graph_nav_stage_{index}_{kind}_return_banner'):
                                o._wait_offline_banner(page)
                        if profile_person:
                            # The banner may be inserted just before the
                            # enclosing async route promises settle. Preserve
                            # both envelope samples instead of collecting too
                            # early and misreporting a missing whole-route phase.
                            page.wait_for_function(
                                """() => {
                                    const p = window.__prksGraphPersonReturnProbe;
                                    return !!p && ['route_total', 'route_commit'].every(
                                        name => p.samples.some(x => x.phase === name)
                                    );
                                }""",
                            )
                    finally:
                        # Never leave a wrapped application function installed
                        # after a failed browser assertion or route transition.
                        if profile_person:
                            context.remove_listener('request', observe_person_request)
                            samples = page.evaluate(PERSON_RETURN_PROBE_COLLECT)
                    if profile_person:
                        # Samples are appended on completion, so use one
                        # start-time order for provenance and stage traces.
                        # Associate every reader outcome with its own timing;
                        # concurrent aborted reads must not be mistaken for a
                        # successful cache result with missing provenance.
                        trace = sorted(samples, key=lambda row: row['startedMs'])
                        origin_ms = trace[0]['startedMs'] if trace else 0.0
                        reader_calls = [
                            {
                                **row['reader'],
                                'start_ms': round(row['startedMs'] - origin_ms, 3),
                                'duration_ms': round(row['ms'], 3),
                            }
                            for row in trace
                            if row['phase'] == 'offline_reader' and 'reader' in row
                        ]
                        self.assertTrue(reader_calls, 'no Person offline reader samples')
                        self.assertTrue(
                            all(r['source'] in ('server', 'cache', 'unavailable')
                                for r in reader_calls if r['outcome'] == 'ok'),
                            'successful offline reader did not report provenance',
                        )
                        print('GRAPH_PERSON_READ_PATH ' + json.dumps({
                            'reader_calls': reader_calls,
                            'observed_person_detail_request_count': len(person_detail_methods),
                            'observed_person_detail_methods': person_detail_methods,
                            'request_observer': 'Playwright browser context',
                        }), flush=True)
                        required = {
                            'route_total', 'route_commit', 'durable_operations',
                            'detail_fetch', 'offline_reader', 'effective_person', 'pending_metadata',
                            'person_render', 'banner_insertion',
                        }
                        observed = {sample['phase'] for sample in samples}
                        self.assertFalse(required - observed, 'incomplete Person route profile')
                        # A superseded route is allowed to abort. The original
                        # assertions must still pass; preserve aborted samples
                        # to expose overlapping navigation rather than hiding it.
                        self.assertFalse(
                            any(sample['outcome'] == 'error' for sample in samples),
                            'Person route profile recorded a non-abort rejection',
                        )
                        # A chronological trace identifies overlapping calls,
                        # stale/superseded renders and per-stage call counts.
                        # It contains only phase names and timing, no user data.
                        print('GRAPH_PERSON_ROUTE_TRACE ' + json.dumps([
                            {
                                'phase': sample['phase'],
                                'start_ms': round(sample['startedMs'] - origin_ms, 3),
                                'duration_ms': round(sample['ms'], 3),
                                'outcome': sample['outcome'],
                            }
                            for sample in trace
                        ]), flush=True)
                        for phase in sorted(observed):
                            rows = [sample for sample in samples if sample['phase'] == phase]
                            # Inclusive nested phases and concurrent calls
                            # overlap; report each stage's sum, max and count,
                            # not a fictitious sequential total.
                            durations = [sample['ms'] / 1000.0 for sample in rows]
                            summary = {
                                'phase': phase, 'calls': len(rows),
                                'aborts': sum(sample['outcome'] == 'abort' for sample in rows),
                                'sum_ms': round(sum(sample['ms'] for sample in rows), 3),
                                'max_ms': round(max(sample['ms'] for sample in rows), 3),
                            }
                            print('GRAPH_PERSON_ROUTE_SUMMARY ' + json.dumps(summary), flush=True)
                            # finish_test_profile drops zero-valued samples;
                            # use a one-microsecond recording floor for a
                            # completed fast stage, never a missing-stage zero.
                            record_e2e_phase_seconds(
                                'graph_nav_person_route_' + phase + '_sum',
                                max(sum(durations), 0.000001),
                            )
                            record_e2e_phase_seconds(
                                'graph_nav_person_route_' + phase + '_max',
                                max(max(durations), 0.000001),
                            )
        # Uncached destination belongs to Concept detail, never a Graph guard.
        with profile_e2e_phase('graph_nav_uncached_destination'):
            self.graph(page, 'concept:' + ids['concept_unvisited'])
            self.selected(page, 'concept:' + ids['concept_unvisited'])
            page.locator('#prks-graph-open').click()
            o._wait_offline_unavailable(page)
            self.assertIn('Concept not available offline', o._content_text(page))
        # Cached Concept -> uncached Graph is also ordinary navigation.
        with profile_e2e_phase('graph_nav_uncached_graph'):
            page.evaluate('() => prksOfflineInvalidateEntity("research-graph-core", "snapshot")')
            page.evaluate('h => prksNavigate(h)', '#/concepts/' + ids['concept_child'])
            o._wait_offline_banner(page)
            page.locator('#prks-concept-view-graph').click()
            o._wait_offline_unavailable(page)
            self.assertIn('Research Graph not available offline', o._content_text(page))

    def test_core_canonical_mutations_and_alias_exclusion(self):
        server, page, context = self.start()
        ids = server.ids
        # A Position RENAME is deliberately not in this list: its only Graph
        # consequence is a label, so it is patched rather than invalidated.
        # See test_a_position_rename_patches_the_graph_node_it_already_has.
        operations = [
            ('argument targets', 'ids => putArgumentTargets(ids.argument_a, [{type:"position",id:ids.position_a,verdict_id:"supports"}])', (True, True)),
            ('argument sources', 'ids => putArgumentSources(ids.argument_a, [{work_id:ids.work_b,pages:"3"}])', (True, True)),
        ]
        for label, operation, expected in operations:
            with self.subTest(label=label):
                self.cache(page, ids)
                before = self.generations(page)
                page.evaluate(operation, ids)
                # Enqueue fences Graph immediately; ACK fences again. Drain so
                # the second sweep cannot race the next cache() republish.
                self.drained(page)
                self.changed(page, before, expected)

    def test_a_position_rename_patches_the_graph_node_it_already_has(self):
        """A Position rename follows the same projection rule as a Concept's.

        The only thing a Position's name decides in the Graph is the label on a
        node that is already there. Nothing is added, nothing is removed and no
        edge moves, so the snapshot an offline device warmed survives and is
        patched. Editing the DESCRIPTION is the counter-case: the Graph never
        showed it, so it changes nothing there either way.
        """
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        page.evaluate("id => { void Promise.resolve(prksReadPositionState(id)).catch(() => {}); }",
                      ids['position_a'])
        o._wait_entity_cached(page, 'position-state', ids['position_a'])

        before = self.generations(page)
        page.evaluate('ids => updatePosition(ids.position_a, {description:"Edited body"})', ids)
        self.drained(page)
        self.changed(page, before, (False, False))

        before = self.generations(page)
        page.evaluate('ids => updatePosition(ids.position_a, {name:"Renamed position"})', ids)
        self.drained(page)
        # The snapshot SURVIVES, carrying the new label.
        self.changed(page, before, (False, False))
        seen = 0
        for kind in ('research-graph-core', 'research-graph-people'):
            cached = o._cached_entity(page, kind, 'snapshot')
            if not cached:
                continue
            labels = [n['label'] for n in cached['value']['nodes']
                      if n.get('type') == 'position']
            with self.subTest(kind=kind):
                self.assertIn('Renamed position', labels)
            seen += 1
        self.assertTrue(seen, 'no cached Graph snapshot survived the rename')

    def test_a_concept_rename_patches_the_graph_node_it_already_has(self):
        """Graph is a PROJECTION, and a Concept rename is the one Concept change
        whose consequence there is fully determined: the node's label.

        So it is patched in place rather than the snapshot being thrown away --
        an offline device keeps the graph it warmed. This is the same rule the
        Work-Person role acknowledgement follows. A Concept the snapshot does
        not contain is never synthesized, because the server decides what a
        projection holds.

        Aliases are excluded for the reason they always were: they are not a
        Graph label and move no edge.
        """
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        page.evaluate("id => { void Promise.resolve(prksReadConceptState(id)).catch(() => {}); }",
                      ids['concept_child'])
        o._wait_entity_cached(page, 'concept-state', ids['concept_child'])

        before = self.generations(page)
        page.evaluate('ids => putConceptAliases(ids.concept_child, ["Alias only"])', ids)
        self.drained(page)
        self.changed(page, before, (False, False))

        before = self.generations(page)
        page.evaluate('ids => updateConcept(ids.concept_child, {name:"Renamed concept"})', ids)
        self.drained(page)
        # The snapshot SURVIVES, carrying the new label.
        self.changed(page, before, (False, False))
        for kind in ('research-graph-core', 'research-graph-people'):
            cached = o._cached_entity(page, kind, 'snapshot')
            if not cached:
                continue
            labels = [n['label'] for n in cached['value']['nodes']
                      if n.get('type') == 'concept']
            with self.subTest(kind=kind):
                self.assertIn('Renamed concept', labels)

    def drained(self, page, message='a durable operation never retired'):
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=40000, message=message)

    def test_person_name_only_invalidates_people_snapshot(self):
        server, page, context = self.start()
        for field, text, expected in [('#pd-about', 'Biography edit', (False, False)),
                                      ('#pd-first-name', 'Renamed', (False, True))]:
            self.cache(page, server.ids)
            before = self.generations(page)
            page.evaluate('id => prksNavigate("#/people/" + id)', server.ids['person'])
            o._open_details_drawer_if_tiled(page)
            page.locator('#panel-content button', has_text='Edit profile').click()
            page.locator(field).fill(text)
            page.locator('#pd-save-btn').click()
            page.wait_for_selector('.person-panel-edit', state='detached')
            self.changed(page, before, expected)

    def test_real_role_ui_author_and_reviewer_boundaries(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        seeded = self.people_graph_author(page, ids['person'], ids['work_a'])
        self.assertTrue(seeded.get('cached') and seeded.get('hasPerson') and
                        seeded.get('hasWork') and seeded.get('hasEdge'),
                        'seeded Author must already be in the People Graph snapshot: %r' % (seeded,))
        before = self.generations(page)
        self.open_people(page, ids['work_a'])
        page.locator('.work-link-person-btn').click()
        page.wait_for_selector('#role-modal:not(.hidden)')
        page.locator('#role-role-seg-mount .prks-segmented__btn[data-value="Reviewer"]').click()
        page.evaluate('''ids => {
            document.getElementById('role-person-id').value = ids.person;
            document.getElementById('role-work-id').value = ids.work_a;
            document.getElementById('role-person-search').value = 'E2E Author';
            document.getElementById('role-work-search').value = 'E2E Research Work';
        }''', ids)
        page.locator('#save-role-btn').click()
        page.locator('#role-modal').wait_for(state='hidden')
        page.wait_for_selector('.work-linked-persons__unlink[data-role-type="Reviewer"]')
        self.changed(page, before, (False, False))
        # Unlink the existing Author, then link again through the same modal.
        # Author ACK patches the People Graph edge in place: the snapshot stays
        # cached, unlike a Person rename which still invalidates it.
        # Do not re-fetch the Graph between the two: the server projection drops
        # a Person with no remaining Author role, and ACK will not invent a
        # node that the cached snapshot no longer holds.
        for linking in (False, True):
            before = self.generations(page)
            self.open_people(page, ids['work_a'])
            if not linking:
                page.locator('.work-linked-persons__unlink[data-role-type="Author"]').click()
                page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
                page.locator('#prks-modal-confirm-ok').click()
                page.wait_for_selector('.work-linked-persons__unlink[data-role-type="Author"]',
                                       state='detached')
            else:
                page.locator('.work-link-person-btn').click()
                page.wait_for_selector('#role-modal:not(.hidden)')
                page.locator('#role-role-seg-mount .prks-segmented__btn[data-value="Author"]').click()
                page.evaluate('''ids => {
                    document.getElementById('role-person-id').value = ids.person;
                    document.getElementById('role-person-search').value = 'E2E Author';
                    document.getElementById('role-work-id').value = ids.work_a;
                    document.getElementById('role-work-search').value = 'E2E Research Work';
                }''', ids)
                page.locator('#save-role-btn').click()
                page.locator('#role-modal').wait_for(state='hidden')
                page.wait_for_selector('.work-linked-persons__unlink[data-role-type="Author"]')
            self.assertEqual(o._domain_generation(page, CORE), before[0])
            self.assertIsNotNone(o._cached_entity(page, PEOPLE, 'snapshot'),
                                 'Author ACK must patch the People Graph, not discard it')
            self.wait_people_author_edge(page, ids['person'], ids['work_a'], linking)
            membership = self.people_graph_author(page, ids['person'], ids['work_a'])
            self.assertTrue(membership.get('hasPerson') and membership.get('hasWork'),
                            'ACK patches the edge; it does not drop the Person node: %r'
                            % (membership,))
            self.assertEqual(membership.get('hasEdge'), linking)

    def test_research_notes_success_and_failure(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        before = self.generations(page)
        o._open_work_from_home(page, o.WORK_A_TITLE)
        def fail(route):
            route.fulfill(status=500, content_type='application/json', body='{"error":"rejected"}')
        page.route('**/api/sync/operations', fail)
        page.locator('.work-notes-editor-wrap .CodeMirror').first.click()
        page.keyboard.press('Control+A')
        page.keyboard.insert_text('Failed research note')
        page.evaluate("""() => {
            const ctx = window.prksGetFocusedTabContext();
            window.prksFlushPendingWorkResearchNotes(ctx);
        }""")
        wait_for_async(page,
            """() => prksSync.store.listOperations().then(rows =>
                rows.some(r => r.operation === 'SET_WORK_RESEARCH_NOTE'))""")
        self.changed(page, before, (False, False))
        page.unroute('**/api/sync/operations', fail)
        wait_for_async(page,
            """() => prksSync.store.listOperations().then(rows =>
                !rows.some(r => r.operation === 'SET_WORK_RESEARCH_NOTE'))""")
        page.locator('[data-prks-role="editor-status"]', has_text='All changes saved').wait_for()
        self.changed(page, before)

    def test_a_renamed_work_is_reconciled_into_the_cached_graph(self):
        """A Work Title is local-first, so a rename no longer INVALIDATES the
        Graph -- it reconciles the exact new label into the cached snapshots.
        Destroying a usable offline Graph for a change whose shape is already
        known is the opposite of what the reconciler is for."""
        server, page, context = self.start()
        self.cache(page, server.ids)
        work = server.ids['work_a']
        node_label = lambda: page.evaluate("""id => window.createPrksOfflineStore()
            .getEntity('research-graph-core', 'snapshot').then(row => {
                if (!row) return null;
                const node = (row.value.nodes || []).find(
                    n => n.type === 'work' && n.record_id === id);
                return node ? node.label : null;
            })""", work)
        self.assertEqual(node_label(), o.WORK_A_TITLE)

        o._open_work_from_home(page, o.WORK_A_TITLE)
        o._open_details_drawer_if_tiled(page)
        page.locator('#panel-content button', has_text='Edit metadata').click()
        page.locator('[data-prks-work-field="title"]').fill('Graph Work renamed')
        page.locator('#save-work-identity-btn').click()
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(r => r.length === 0)")
        wait_for_async(page, """id => window.createPrksOfflineStore()
            .getEntity('research-graph-core', 'snapshot').then(row => {
                if (!row) return false;
                const node = (row.value.nodes || []).find(
                    n => n.type === 'work' && n.record_id === id);
                return !!node && node.label === 'Graph Work renamed';
            })""", arg=work)
        self.assertIsNotNone(node_label(), 'the snapshot was patched, not dropped')

    def test_work_delete_invalidates_the_graph(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        before = self.generations(page)
        page.evaluate('id => prksNavigate("#/works/" + id)', server.ids['work_a'])
        page.locator('.work-details-advanced').wait_for()
        o._open_details_drawer_if_tiled(page)
        page.locator('.work-details-advanced summary').click()

        def fail(route):
            route.fulfill(status=500, content_type='application/json', body='{"error":"rejected"}')

        page.route('**/api/sync/operations', fail)
        page.locator('.delete-work-btn').click()
        page.locator('#prks-modal-confirm:not(.hidden)', has_text='Delete file?').wait_for()
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function('location.hash === "#/folders"')
        wait_for_async(page,
            """() => prksSync.store.listOperations().then(rows =>
                rows.some(r => r.operation === 'DELETE_WORK'))""")
        # Unacknowledged destroy must not fence Graph snapshots.
        self.changed(page, before, (False, False))
        page.unroute('**/api/sync/operations', fail)
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000)
        self.changed(page, before)

    def test_work_creation_with_and_without_author_preserves_snapshots(self):
        from tests.e2e.fixtures import MINIMAL_PDF
        server, page, context = self.start()
        self.cache(page, server.ids)
        before = self.generations(page)
        for author in (False, True):
            page.locator('#prks-ribbon-new-file').click()
            page.wait_for_selector('#work-modal:not(.hidden):not([inert])')
            page.fill('#work-title', 'Graph unreferenced work ' + str(author))
            page.set_input_files('#work-file', str(MINIMAL_PDF))
            page.locator('#upload-selected-file-name').wait_for(state='visible')
            if author:
                page.evaluate('''pid => {
                    document.getElementById('upload-person-id').value = pid;
                    document.getElementById('upload-person-search').value = 'E2E Author';
                    addRoleToUploadList('Author');
                }''', server.ids['person'])
                page.locator('#upload-roles-list .prks-upload-person-row').first.wait_for()
            with page.expect_response(lambda r: '/api/works' in r.url and r.request.method == 'POST') as response:
                page.locator('#save-work-btn').click()
            self.assertTrue(response.value.ok)
            page.locator('#work-modal').wait_for(state='hidden')
            self.changed(page, before, (False, False))

    def test_pdf_playlist_group_changes_preserve_snapshots(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        before = self.generations(page)
        # Group changes are durable now, so they are driven through the store
        # and DRAINED: the canonical change a coherence rule follows is the
        # acknowledgement, not the click.
        page.evaluate('''async ids => {
            const created = await prksCreatePersonGroupDurably(
                { name: 'Graph-neutral group', description: '' }, []);
            const group = created.entity_id;
            const settle = async () => {
                const deadline = Date.now() + 30000;
                while (Date.now() < deadline) {
                    const rows = await prksSync.store.listOperations();
                    if (!rows.some(o => o.status !== 'conflict')) return;
                    await new Promise(r => setTimeout(r, 100));
                }
            };
            await settle();
            const observed = await prksAcknowledgedPersonGroupMembership(
                group, ids.person, await prksSync.store.listOperations());
            await prksSetPersonGroupMemberDurably(group, ids.person, true, observed);
            await settle();
            const base = await prksAcknowledgedPersonGroupBase(
                group, await prksSync.store.listOperations());
            await prksSavePersonGroupFieldsDurably(group, { name: 'Renamed group' }, base);
            await settle();
        }''', server.ids)
        # The existing Playlist component owns this canonical operation.
        page.evaluate('async () => { await createPlaylist("Graph-neutral playlist", ""); }')
        self.changed(page, before, (False, False))
        o._open_work_from_home(page, o.WORK_A_TITLE)
        o._wait_pdf_viewer(page)
        page.wait_for_function('() => { const pdf = %s; return !!(pdf && pdf.annotationPersistence); }' % o._FOCUSED_PDF)
        o._commit_pdf_highlight(page)
        page.wait_for_function(o._PDF_SYNC_SETTLED_JS, timeout=30000)
        self.changed(page, before, (False, False))

    def test_people_only_snapshot_reopens_without_core_prefetch(self):
        server, page, context = self.start()
        self.cache(page, server.ids, (True,))
        self.assertIsNone(o._cached_entity(page, CORE, 'snapshot'))
        self.offline(page, context)
        page.reload(wait_until='domcontentloaded')
        self.mounted(page)
        self.selected(page, 'person:' + server.ids['person'])
        o._wait_offline_banner(page)
        self.assertIsNone(o._cached_entity(page, CORE, 'snapshot'))
        self.toggle(page, False)
        page.get_by_text('Core graph is not available offline on this device.', exact=True).wait_for()
        self.assertTrue(page.locator('[data-graph-filter="people"]').is_checked())
