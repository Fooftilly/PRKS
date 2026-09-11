import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class WorkTagSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        result = subprocess.run(['node', str(ROOT / 'tests/browser/run_work_tag_sync_selftest.js')],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_second_tag_catalog_cache(self):
        for path in (ROOT / 'frontend/js').rglob('*.js'):
            self.assertNotIn('__prksAllTagsCache', path.read_text(), str(path))

    def test_semantic_transport_owned_by_coordinator(self):
        for path in (ROOT / 'frontend/js').rglob('*.js'):
            if path.name != 'sync-runtime.js':
                self.assertNotIn('/api/sync/operations', path.read_text(), str(path))
