"""Console classification without launching Chromium."""
from types import SimpleNamespace
import unittest

from tests.e2e.harness import PageCollector


class CollectorConsoleTests(unittest.TestCase):
    def test_only_revoked_blob_resource_error_is_suppressed(self):
        for url, message, ignored in (
            ('blob:http://localhost/id', 'Failed to load resource: net::ERR_FILE_NOT_FOUND', True),
            ('blob:http://localhost/id', 'Failed to load resource: net::ERR_FAILED', False),
            ('blob:http://localhost/id', 'ERR_FILE_NOT_FOUND', False),
            ('http://localhost/file', 'Failed to load resource: net::ERR_FILE_NOT_FOUND', False),
        ):
            with self.subTest(url=url, message=message):
                collector = PageCollector.__new__(PageCollector)
                collector.console_errors = []
                collector._on_console(SimpleNamespace(type='error', text=message, location={'url': url}))
                self.assertEqual(len(collector.console_errors), 0 if ignored else 1)
