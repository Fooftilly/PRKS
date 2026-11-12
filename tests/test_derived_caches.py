"""The derived thumbnail cache helpers (``backend/derived_caches.py``).

Driver-free: the helpers touch only files under the thumbnails directory.
Moved out of ``tests/test_work_deletion.py`` when PG-C2 (#641) retired its
SQLite deletion tests; the PostgreSQL delete route uses the same helper.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from backend.derived_caches import prks_delete_pdf_thumbnails_for_work_id, prks_thumb_cache_stem


class ThumbnailCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thumbs_dir = tempfile.mkdtemp(prefix="prks-thumbs-")
        self.addCleanup(shutil.rmtree, self.thumbs_dir, True)

    def _thumb(self, work_id: str, page: int, ext: str = "webp") -> str:
        path = os.path.join(self.thumbs_dir, f"{prks_thumb_cache_stem(work_id, page)}.{ext}")
        with open(path, "wb") as handle:
            handle.write(b"t")
        return path

    def test_thumbnail_helper_listdir_failure_is_reported(self) -> None:
        thumb = self._thumb("W-LISTFAIL", 1)
        with patch("backend.derived_caches.os.listdir", side_effect=OSError("cannot list")):
            failed = prks_delete_pdf_thumbnails_for_work_id("W-LISTFAIL", self.thumbs_dir)
        self.assertEqual(failed, (self.thumbs_dir,))
        self.assertTrue(os.path.isfile(thumb))

    def test_only_the_works_thumbnails_are_removed(self) -> None:
        mine = [self._thumb("W-MINE", 1), self._thumb("W-MINE", 2, "png")]
        other = self._thumb("W-OTHER", 1)
        self.assertEqual(prks_delete_pdf_thumbnails_for_work_id("W-MINE", self.thumbs_dir), ())
        self.assertEqual([os.path.isfile(path) for path in mine], [False, False])
        self.assertTrue(os.path.isfile(other))

    def test_a_missing_thumbnails_directory_is_nothing_to_do(self) -> None:
        missing = os.path.join(self.thumbs_dir, "absent")
        self.assertEqual(prks_delete_pdf_thumbnails_for_work_id("W-ANY", missing), ())


class ThumbnailStemTests(unittest.TestCase):
    """Thumbnail cache names (moved from ``tests/test_db_manager.py``)."""

    def test_thumb_cache_stem_includes_primary_asset(self):
        self.assertNotEqual(
            prks_thumb_cache_stem("W-1", 1, "AS-A"),
            prks_thumb_cache_stem("W-1", 1, "AS-B"),
        )
        self.assertIn("_aAS-A_", prks_thumb_cache_stem("W-1", 1, "AS-A"))


if __name__ == "__main__":
    unittest.main()
