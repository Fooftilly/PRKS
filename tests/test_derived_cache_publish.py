"""Tests for derived cache publication containment."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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

    def test_concurrent_publish_uses_unique_sibling_temps(self):
        """Shared ``<filename>.tmp`` is unsafe under concurrent writers.

        Each publication must mint its own exclusive sibling temp so one
        writer's replace cannot orphan or truncate another's open file.
        """
        with tempfile.TemporaryDirectory() as tmp:
            temps: list[str] = []
            barrier = threading.Barrier(2)
            lock = threading.Lock()
            real_open = os.open

            def spy_open(path, flags, mode=0o600, *args, **kwargs):
                if flags & os.O_EXCL:
                    with lock:
                        temps.append(path)
                    barrier.wait(timeout=5)
                return real_open(path, flags, mode, *args, **kwargs)

            errors: list[BaseException] = []

            def worker(payload: bytes) -> None:
                try:
                    publish_derived_cache_bytes(tmp, "shared.webp", payload)
                except BaseException as exc:  # noqa: BLE001 — collect for assert
                    errors.append(exc)

            with mock.patch("backend.derived_cache_publish.os.open", spy_open):
                threads = [
                    threading.Thread(target=worker, args=(b"aaaaaaaaaa",)),
                    threading.Thread(target=worker, args=(b"bbbbbbbbbb",)),
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(len(temps), 2)
            self.assertNotEqual(temps[0], temps[1])
            final = Path(tmp, "shared.webp").read_bytes()
            self.assertIn(final, {b"aaaaaaaaaa", b"bbbbbbbbbb"})
            leftovers = [
                p for p in Path(tmp).iterdir() if p.name.startswith(".prks-cache-")
            ]
            self.assertEqual(leftovers, [])

    def test_failed_write_removes_only_this_writers_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            peer = Path(tmp) / ".prks-cache-peer.tmp"
            peer.write_bytes(b"peer")

            real_fdopen = os.fdopen

            def boom_fdopen(fd, mode="r", *args, **kwargs):
                fp = real_fdopen(fd, mode, *args, **kwargs)
                if "b" in mode:
                    original_write = fp.write

                    def fail_write(data):
                        original_write(data)
                        raise OSError("simulated write failure")

                    fp.write = fail_write  # type: ignore[method-assign]
                return fp

            with mock.patch("backend.derived_cache_publish.os.fdopen", boom_fdopen):
                with self.assertRaises(OSError):
                    publish_derived_cache_bytes(tmp, "card.webp", b"payload")

            self.assertTrue(peer.is_file())
            self.assertFalse(Path(tmp, "card.webp").exists())
            leftovers = [
                p
                for p in Path(tmp).iterdir()
                if p.name.startswith(".prks-cache-") and p.name != peer.name
            ]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
