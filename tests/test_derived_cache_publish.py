"""Tests for derived cache publication containment."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from backend.derived_cache_publish import (
    _contained_cache_path,
    publish_derived_cache_bytes,
)


class DerivedCachePublishTests(unittest.TestCase):
    def test_publishes_under_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = publish_derived_cache_bytes(tmp, "card.webp", b"bytes")
            self.assertEqual(Path(path).read_bytes(), b"bytes")
            self.assertEqual(Path(path).parent.resolve(), Path(tmp).resolve())
            self.assertEqual(Path(path).name, "card.webp")

    def test_basename_strips_parent_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            # CodeQL-style basename rebuild: traversal prefixes are dropped.
            path = _contained_cache_path(tmp, "../escape.webp")
            self.assertEqual(Path(path).name, "escape.webp")
            self.assertEqual(Path(path).parent.resolve(), Path(tmp).resolve())

    def test_refuses_empty_and_dot_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("", ".", ".."):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        _contained_cache_path(tmp, name)


if __name__ == "__main__":
    unittest.main()
