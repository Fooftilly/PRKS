"""Release-transition smoke guards, runnable without Playwright installed."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.build_classic_distribution import render_assets
from scripts.e2e_classic_sw_transitions import (
    ReleaseSource,
    expected_cache_names,
    serve,
)

ROOT = Path(__file__).resolve().parents[1]


class ClassicReleaseTransitionTests(unittest.TestCase):
    def test_cache_revisions_are_distinct_across_build_modes(self):
        source = (ROOT / "frontend/sw.js").read_text(encoding="utf-8")
        _, _, packaged = render_assets(ROOT)
        old = expected_cache_names(source)
        new = expected_cache_names(packaged)
        self.assertEqual(len(old), 2)
        self.assertEqual(len(new), 2)
        self.assertFalse(old & new)
        self.assertTrue(all(name.startswith("prks-") for name in old | new))

    def test_versioning_contract_fails_closed(self):
        sw = (ROOT / "frontend/sw.js").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "vendor cache revision missing"):
            expected_cache_names(sw.replace("DEPENDENCY_REVISION = ", "WRONG_REVISION = "))
        with self.assertRaisesRegex(ValueError, "cache contract changed"):
            expected_cache_names(sw.replace("const STATIC_CACHE", "const OTHER_CACHE"))

    def test_single_origin_can_switch_between_static_releases(self):
        source = ReleaseSource(ROOT / "frontend")
        with serve(source) as origin:
            from urllib.request import urlopen
            with urlopen(origin + "/sw.js", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn(b"DEPENDENCY_REVISION", response.read())
            self.assertEqual(source.frontend, ROOT / "frontend")


if __name__ == "__main__":
    unittest.main()
