"""Work source identity on the client: the aggregate family's selftests."""
import pathlib
import re
import subprocess
import unittest

from backend import work_source_sync

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


def _padded_url(total_bytes: int) -> str:
    """A valid YouTube URL padded to exactly `total_bytes` UTF-8 bytes."""
    base = 'https://www.youtube.com/watch?v=ABC&pad='
    return base + 'x' * (total_bytes - len(base.encode('utf-8')))


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
        from backend.work_source_sync import (
            MAX_PROVIDER_ID_CHARS, MAX_SOURCE_URL_UTF8_BYTES)
        urls = [
            'https://www.youtube.com/watch?v=ABC', 'https://youtu.be/ABC',
            'https://www.youtube.com/embed/ABC', 'https://m.youtube.com/watch?v=ABC',
            'https://youtube.com/watch?v=ABC&t=30',
            'https://notyoutube.com/watch?v=ABC', 'https://youtube.com.evil.org/watch?v=ABC',
            'https://example.com/v', 'ftp://youtube.com/watch?v=ABC', '', '   ',
            'https://www.youtube.com/watch', 'https://youtu.be/', 'nonsense',
            # The provider-id contract. `provider_id` is an IDENTIFIER, and a
            # conflict promises to report it EXACTLY while the whole terminal
            # result still fits the client's durable 2 KiB bound. Those two
            # promises are only simultaneously keepable if the identifier is
            # bounded, so the parsers -- which is where an id comes into
            # existence -- are what bound it. Both sides must draw the line in
            # exactly the same place, or one stores an identity the other
            # cannot even name.
            'https://www.youtube.com/watch?v=' + 'B' * 300,
            'https://www.youtube.com/watch?v=' + 'B' * MAX_PROVIDER_ID_CHARS,
            'https://www.youtube.com/watch?v=' + 'B' * (MAX_PROVIDER_ID_CHARS + 1),
            'https://www.youtube.com/watch?v=' + 'B' * 3000,
            'https://youtu.be/' + 'B' * (MAX_PROVIDER_ID_CHARS + 1),
            'https://www.youtube.com/embed/' + 'B' * (MAX_PROVIDER_ID_CHARS + 1),
            # Percent-escapes are where the two parsers could most easily
            # diverge: a query value is decoded on both sides and a path
            # segment on neither. `%` is not a legal identifier character, so
            # every spelling of this is refused by both.
            'https://www.youtube.com/watch?v=' + '%01' * 400,
            'https://www.youtube.com/watch?v=%01%02',
            'https://youtu.be/%01%02',
            'https://www.youtube.com/watch?v=a%20b',
            # Characters that inflate under JSON escaping, or end a token.
            'https://www.youtube.com/watch?v=ab%22cd',
            'https://www.youtube.com/watch?v=ab%5Ccd',
            'https://www.youtube.com/watch?v=ab+cd',
            'https://www.youtube.com/watch?v=ab.cd',
            'https://www.youtube.com/watch?v=ab/cd',
            # ... and the safe alphabet itself, which must stay accepted.
            'https://www.youtube.com/watch?v=dQw4-_9WgXcQ',
            # The URL's own byte bound. The client did not enforce it, so it
            # accepted a URL the server refuses -- not an acknowledged-state
            # corruption, because the durable store rejects it eventually, but
            # the user got the store's generic "could not save locally" instead
            # of being told the URL was too long. Parity that holds for
            # identity and not for size is not parity.
            _padded_url(MAX_SOURCE_URL_UTF8_BYTES),
            _padded_url(MAX_SOURCE_URL_UTF8_BYTES + 1),
            _padded_url(MAX_SOURCE_URL_UTF8_BYTES + 6000),
            # Trim, then measure what would be STORED, then parse -- surrounding
            # whitespace must not cost a caller a URL whose canonical form fits.
            '  ' + _padded_url(MAX_SOURCE_URL_UTF8_BYTES) + '  ',
            # A multi-byte URL: the bound is in BYTES, and `.length` counts
            # UTF-16 code units, so measuring characters would make the limit
            # silently not exist for the URLs most likely to reach it.
            'https://www.youtube.com/watch?v=ABC&q=' + '\u00e9' * 40000,
        ]
        # Over stdin, not argv: a 64 KiB URL is larger than a single argument
        # may be, and the cases that matter most here are the largest ones.
        js = """
        require(process.argv[1] + '/frontend/js/work-source-state.js');
        let raw = '';
        process.stdin.on('data', chunk => { raw += chunk; });
        process.stdin.on('end', () => {
            const out = JSON.parse(raw).map(url => {
                const source = globalThis.prksCanonicalWorkSource(url);
                return source ? source.provider_id : null;
            });
            process.stdout.write(JSON.stringify(out));
        });
        """
        proc = subprocess.run(['node', '-e', js, str(ROOT)], input=json.dumps(urls),
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout[:400] + proc.stderr[:400])
        client = json.loads(proc.stdout)
        self.assertEqual(len(client), len(urls))
        for url, seen in zip(urls, client):
            with self.subTest(url=url[:80], bytes=len(url.encode('utf-8'))):
                source = work_source_sync.canonical_source({'kind': 'video', 'url': url})
                server = source['provider_id'] if source else None
                self.assertEqual(seen, server, url[:120])

    def test_both_sides_bound_the_identifier_at_the_same_place(self):
        """The constant itself, not only its effects.

        Two parsers that agreed on every URL in the list above but disagreed on
        the limit would pass that test and still let one side store an identity
        the other cannot name.
        """
        import json
        from backend.work_source_sync import (
            MAX_PROVIDER_ID_CHARS, MAX_SOURCE_URL_UTF8_BYTES)

        js = """
        require(process.argv[1] + '/frontend/js/work-source-state.js');
        process.stdout.write(JSON.stringify({
            max: globalThis.PRKS_MAX_PROVIDER_ID_CHARS,
            maxUrl: globalThis.PRKS_MAX_SOURCE_URL_UTF8_BYTES,
            accepts: ['dQw4w9WgXcQ', 'a-b_C9', 'B'.repeat(512), '', 'a b', 'a"b',
                      'a.b', 'a/b', 'a%01b', 'B'.repeat(513)]
                .map(v => globalThis.prksIsProviderId(v)),
        }));
        """
        proc = subprocess.run(['node', '-e', js, str(ROOT)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        client = json.loads(proc.stdout)
        self.assertEqual(client['max'], MAX_PROVIDER_ID_CHARS)
        self.assertEqual(client['maxUrl'], MAX_SOURCE_URL_UTF8_BYTES)
        self.assertEqual(
            client['accepts'],
            [work_source_sync.is_provider_id(v) for v in
             ['dQw4w9WgXcQ', 'a-b_C9', 'B' * 512, '', 'a b', 'a"b',
              'a.b', 'a/b', 'a%01b', 'B' * 513]])
        self.assertEqual(client['accepts'][:3], [True, True, True])
        self.assertEqual(client['accepts'][3:], [False] * 7)

    def test_creation_and_the_runtime_classify_a_work_the_same_way(self):
        """Two inference rules that drift are two answers about one row.

        Creation has to decide what a Work IS by the rule the product later
        reads it with -- `prksInferWorkSourceKind()`. When they differed, a
        Work with no kind, no file and a YouTube URL was created as "no kind"
        and then displayed as a Video, offered the Video source editor, and
        refused by the aggregate.
        """
        import json
        from backend.db_manager import effective_source_kind

        rows = [
            {'source_kind': 'video', 'file_path': '', 'source_url': ''},
            {'source_kind': 'pdf', 'file_path': '', 'source_url': ''},
            {'source_kind': 'pdf', 'file_path': '/api/pdfs/a.pdf',
             'source_url': 'https://www.youtube.com/watch?v=ABC'},
            {'source_kind': 'video', 'file_path': '/api/pdfs/a.pdf', 'source_url': ''},
            {'source_kind': '', 'file_path': '/api/pdfs/a.pdf', 'source_url': ''},
            {'source_kind': '', 'file_path': '/api/pdfs/a.pdf',
             'source_url': 'https://example.org/p.pdf'},
            {'source_kind': '', 'file_path': '', 'source_url': 'https://youtu.be/ABC'},
            {'source_kind': '', 'file_path': '', 'source_url': 'https://example.com/x'},
            {'source_kind': '', 'file_path': '', 'source_url': ''},
            {'source_kind': '  VIDEO  ', 'file_path': '', 'source_url': ''},
            {'source_kind': '', 'file_path': '   ', 'source_url': 'https://youtu.be/ABC'},
        ]
        # `api.js` is a browser script that touches `window` at load time, so
        # the real function's source is lifted out and evaluated rather than
        # the module being required. It is still the shipped text, not a copy.
        js = """
        const fs = require('fs');
        const src = fs.readFileSync(process.argv[1] + '/frontend/js/api.js', 'utf8');
        const at = src.indexOf('function prksInferWorkSourceKind(');
        if (at === -1) { throw new Error('prksInferWorkSourceKind not found'); }
        const end = src.indexOf('\\n}', at) + 2;
        const infer = (0, eval)('(' + src.slice(at, end) + ')');
        const rows = JSON.parse(process.argv[2]);
        process.stdout.write(JSON.stringify(rows.map(r => infer(r))));
        """
        proc = subprocess.run(['node', '-e', js, str(ROOT), json.dumps(rows)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        runtime = json.loads(proc.stdout)
        for row, seen in zip(rows, runtime):
            with self.subTest(row=row):
                self.assertEqual(
                    effective_source_kind(row['source_kind'], row['source_url'],
                                          row['file_path']),
                    seen,
                    'creation and the runtime must agree about this row')

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
