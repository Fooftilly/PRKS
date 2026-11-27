import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class WorkTagSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        result = subprocess.run(['node', str(ROOT / 'tests/browser/run_work_tag_sync_selftest.js')],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ack_ordering_per_family(self):
        # #1425: an older ACK never replaces newer cached data, in any ported
        # family (Tag links #1088, Work source/notes #1106/#1105, Folders and
        # Person Groups #1081/#1079, ...). One self-test file per port.
        scripts = sorted((ROOT / 'tests/browser').glob('run_*ack_order_selftest.js'))
        self.assertGreaterEqual(len(scripts), 3)
        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run(['node', str(script)], cwd=ROOT, capture_output=True,
                                        text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('ACK ordering checks passed', result.stdout)

    def test_no_second_tag_catalog_cache(self):
        for path in (ROOT / 'frontend/js').rglob('*.js'):
            self.assertNotIn('__prksAllTagsCache', path.read_text(), str(path))

    def test_semantic_transport_owned_by_coordinator(self):
        for path in (ROOT / 'frontend/js').rglob('*.js'):
            if path.name != 'sync-runtime.js':
                self.assertNotIn('/api/sync/operations', path.read_text(), str(path))
