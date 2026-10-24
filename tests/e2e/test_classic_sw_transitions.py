"""Real Chromium service-worker release transitions for packaged classics."""
from __future__ import annotations

import os
import unittest

from scripts.e2e_classic_sw_transitions import test_transitions


def load_tests(loader, standard_tests, pattern):
    if (
        os.environ.get("PRKS_E2E") != "1"
        or os.environ.get("PRKS_E2E_CLASSIC_SW_TRANSITIONS") != "1"
    ):
        return unittest.TestSuite()
    return standard_tests


class ClassicSWTransitionTests(unittest.TestCase):
    def test_unbundled_bundled_unbundled(self):
        test_transitions()
