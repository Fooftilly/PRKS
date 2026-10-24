"""Manual-only, source-Chromium retry-policy A/B for cached Person reads.

Excluded from DEFAULT_E2E_MODULES. The normal app/request/runtime paths are
unmodified; each mode invokes the existing runtime readThroughEntity API with
the existing per-read requestPolicy contract. This is not a production fix.
"""
import json
import statistics
import unittest
from urllib.parse import urlsplit

from tests.e2e import test_offline as offline
from tests.e2e.fixtures import seed_arguments_library
from tests.e2e.harness import (
    AppServer, open_app_page, record_e2e_phase_seconds, require_chromium,
)

_PW = None
_BROWSER = None


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class PersonReconnectRetryExperiment(unittest.TestCase):
    """Compare two real Chromium read policies within one isolated fixture."""

    def test_cached_person_reader_retry_policy_ab(self):
        server = AppServer(seed_fn=seed_arguments_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers='allow')
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        self.addCleanup(context.close)
        offline._wait_sw_active(page)
        person_id = server.ids['person']

        # The real route must have published a coherent Person detail snapshot.
        page.evaluate("id => { void prksNavigate('#/people/' + id); }", person_id)
        offline._wait_entity_cached(page, 'person', person_id)

        # Establish actual browser offline transport, not a fake fetch stub;
        # the real request coordinator, retry/backoff, IndexedDB and SW run.
        context.set_offline(True)
        page.evaluate("""async () => {
            try { await prksRequest('/api/settings'); } catch (_) {}
        }""")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'offline'")

        samples = {'default': [], 'single': []}
        # Symmetric A-B-B-A order limits a warm-up/order advantage.
        for mode in ('default', 'single', 'single', 'default'):
            person_requests = []
            route_path = '/api/persons/' + str(person_id)

            def observe(request):
                if urlsplit(request.url).path == route_path:
                    person_requests.append(request.method)

            context.on('request', observe)
            try:
                result = page.evaluate("""([id, single]) => {
                    if (prksOfflineRuntimeState() !== 'offline') {
                        throw new Error('Reader comparison must begin offline');
                    }
                    // Exercise a real runtime offline hint, which starts the
                    // normal background reachability probe and synchronously
                    // changes offline -> reconnecting. Do NOT fake runtime state.
                    window.dispatchEvent(new Event('offline'));
                    const startedState = prksOfflineRuntimeState();
                    const started = performance.now();
                    return prksOfflineReadEntity(
                        'person', id, '/api/persons/' + encodeURIComponent(id), {
                            domain: 'people',
                            preferCacheWhenOffline: true,
                            // Both arms attempt the server first in reconnecting.
                            // Single-attempt uses a supported EXISTING policy.
                            ...(single ? { requestPolicy: { retry: false } } : {}),
                            validate: value => !!value && String(value.id) === String(id),
                        }
                    ).then(response => ({
                        duration_ms: performance.now() - started,
                        startedState,
                        endedState: prksOfflineRuntimeState(),
                        source: response.source,
                        matchesCachedPerson: !!response.value &&
                            String(response.value.id) === String(id),
                    }));
                }""", [person_id, mode == 'single'])
            finally:
                context.remove_listener('request', observe)

            self.assertEqual(result['startedState'], 'reconnecting')
            self.assertEqual(result['source'], 'cache')
            self.assertTrue(result['matchesCachedPerson'])
            expected_attempts = 1 if mode == 'single' else 3
            self.assertEqual(
                person_requests, ['GET'] * expected_attempts,
                'unexpected Person request attempts during ' + mode,
            )
            samples[mode].append({
                'duration_ms': round(result['duration_ms'], 3),
                'attempts': len(person_requests),
                'started_state': result['startedState'],
                'ended_state': result['endedState'],
                'source': result['source'],
            })
            # Wait for the parallel real settings probe to settle before
            # starting the next pair; otherwise an in-flight probe may refuse
            # the next hint and confound the A/B state entry.
            page.wait_for_function("() => prksOfflineRuntimeState() === 'offline'")

        # Do not claim statistical significance or assert a speedup from just
        # two samples per arm. Record the actual wall time and attempt counts.
        for mode in ('default', 'single'):
            recorded = [s['duration_ms'] / 1000.0 for s in samples[mode]]
            record_e2e_phase_seconds(
                'person_reconnect_retry_' + mode,
                statistics.median(recorded),
            )
        print('GRAPH_PERSON_RETRY_AB ' + json.dumps(samples), flush=True)
