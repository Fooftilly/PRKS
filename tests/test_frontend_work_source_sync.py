"""Work source identity on the client: the aggregate family's selftests."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


class WorkSourceSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        result = subprocess.run(
            ['node', str(ROOT / 'tests/browser/run_work_source_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_one_parser_on_both_sides_of_the_boundary(self):
        """The client and the server decide separately what a YouTube URL
        means. If they disagree, one accepts an identity the other refuses --
        and a Work's stored URL and stored id could name different videos."""
        import json
        from backend import work_source_sync
        urls = [
            'https://www.youtube.com/watch?v=ABC', 'https://youtu.be/ABC',
            'https://www.youtube.com/embed/ABC', 'https://m.youtube.com/watch?v=ABC',
            'https://youtube.com/watch?v=ABC&t=30',
            'https://notyoutube.com/watch?v=ABC', 'https://youtube.com.evil.org/watch?v=ABC',
            'https://example.com/v', 'ftp://youtube.com/watch?v=ABC', '', '   ',
            'https://www.youtube.com/watch', 'https://youtu.be/', 'nonsense',
        ]
        js = """
        require(process.argv[1] + '/frontend/js/work-source-state.js');
        const out = {};
        for (const url of JSON.parse(process.argv[2])) {
            const source = globalThis.prksCanonicalWorkSource(url);
            out[url] = source ? source.provider_id : null;
        }
        process.stdout.write(JSON.stringify(out));
        """
        proc = subprocess.run(['node', '-e', js, str(ROOT), json.dumps(urls)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        client = json.loads(proc.stdout)
        for url in urls:
            with self.subTest(url=url):
                source = work_source_sync.canonical_source({'kind': 'video', 'url': url})
                server = source['provider_id'] if source else None
                self.assertEqual(client[url], server, url)

    def test_the_source_family_is_registered_everywhere_it_must_be(self):
        from backend import sync_protocol
        self.assertIn('SET_WORK_SOURCE', sync_protocol.supported_operations())
        store = (FRONTEND / 'local-store.js').read_text()
        self.assertIn("'SET_WORK_SOURCE',", store, 'the durable store must accept it')
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        self.assertIn('SET_WORK_SOURCE: root.prksWorkSourceSyncHandler', runtime)
        # The coordinator stays family-agnostic.
        coordinator = runtime[: runtime.index('root.createPrksSyncRuntime = createRuntime;')]
        for leaked in ('SET_WORK_SOURCE', 'provider_id', 'youtube'):
            self.assertNotIn(leaked, coordinator, leaked)

    def test_diagnostics_describes_and_invalidates_the_source_family(self):
        """Diagnostics is the only place a conflict on a Work whose page is no
        longer cached can be reached, so an unknown family there is a change
        the user cannot decide about.

        `describe()` ended in an unconditional Tag branch, so a source
        operation was labelled "Remove Tag"; `invalidate()` ended in an
        unconditional `else`, so discarding one invalidated `work-tag-options`
        and left the stale source projection in place.
        """
        source = (FRONTEND / 'sync-diagnostics.js').read_text()
        self.assertIn("'Video source = \"'", source, 'a source operation names itself')
        self.assertIn('SET_WORK_SOURCE: \'work-source-state\'', source,
                      'and discarding it invalidates its own projection')

        # No family may be described or invalidated by falling off the end.
        for function in ('function describe(', 'function invalidate('):
            at = source.index(function)
            body = source[at: source.index('\n    }', at)]
            self.assertNotIn(' else {', body,
                             'an unknown family must not inherit another family\'s answer')

        # Every durable family the store accepts is handled explicitly.
        store = (FRONTEND / 'local-store.js').read_text()
        listed = store[store.index('const OPERATION_TYPES = Object.freeze(['):]
        families = re.findall(r"'([A-Z_]+)'", listed[: listed.index(']')])
        self.assertIn('SET_WORK_SOURCE', families)
        for family in families:
            with self.subTest(family=family):
                self.assertIn(family, source,
                              'sync-diagnostics.js must describe every durable family')

    def test_the_envelope_never_asserts_derived_values(self):
        """A payload able to carry `provider_id` could assert an identity its
        own URL contradicts -- the defect the aggregate exists to prevent.

        The envelope is built in the STORE now, because saving a source
        coalesces rather than blindly enqueueing. So the shape is pinned where
        it is written, and the editor is checked for what it hands over: a URL
        and an identity used only to decide whether to coalesce.
        """
        store = (FRONTEND / 'local-store.js').read_text()
        at = store.index('function saveWorkSource(')
        body = store[at: store.index('function reappliable(', at)]
        self.assertIn(
            "payload: { source: { kind: 'video', url: source.url } },", body,
            'the envelope carries intent and nothing derived')
        for derived in ('provider_id', 'provider:', 'thumb_url', 'youtube'):
            self.assertNotIn(derived, body, derived)
        self.assertNotIn('identity', body[body.index('insertEnvelopeIn'):],
                         'the coalescing hint never reaches the envelope')

        editor = (FRONTEND / 'work-source-editor.js').read_text()
        at = editor.index('await root.prksSync.store.saveWorkSource(')
        call = editor[at: at + 500]
        for derived in ('provider_id:', 'provider:', 'thumb_url'):
            self.assertNotIn(derived, call, derived)
        self.assertNotIn('enqueueOperation', editor,
                         'a bare enqueue would leave two intents for one aggregate')

    def test_components_do_not_interpret_source_operations(self):
        for name in ('components/works.js', 'components/works-video.js',
                     'components/work-cards.js', 'components/playlists.js'):
            source = (FRONTEND / name).read_text()
            with self.subTest(module=name):
                for forbidden in ('SET_WORK_SOURCE', 'listOperations', 'prksSync.store'):
                    self.assertNotIn(forbidden, source, forbidden)
