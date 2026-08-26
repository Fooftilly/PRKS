import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.server import (  # noqa: E402
    _prks_pixmap_to_card_webp_bytes,
    _prks_pixmap_to_lossless_webp_bytes,
)


class TestThumbnailEncode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import fitz  # noqa: F401

            cls._fitz = fitz
        except ImportError:
            cls._fitz = None

    def test_lossy_webp_valid_and_smaller_than_lossless(self):
        if self._fitz is None:
            self.skipTest("PyMuPDF not installed")
        doc = self._fitz.open()
        try:
            page = doc.new_page(width=612, height=400)
            shape = page.new_shape()
            for row in range(45):
                for col in range(68):
                    r = ((col * 4) % 256) / 255.0
                    g = ((row * 5) % 256) / 255.0
                    b = (((row + col) * 3) % 256) / 255.0
                    x0, y0 = col * 9, row * 9
                    shape.draw_rect(self._fitz.Rect(x0, y0, x0 + 8, y0 + 8))
                    shape.finish(fill=(r, g, b))
            shape.commit()
            scale = 560.0 / 612.0
            pix = page.get_pixmap(matrix=self._fitz.Matrix(scale, scale), alpha=False)
            lossy = _prks_pixmap_to_card_webp_bytes(pix)
            lossless = _prks_pixmap_to_lossless_webp_bytes(pix)
        finally:
            doc.close()
        if lossy is None or lossless is None:
            self.skipTest("Pillow/WebP encode not available")
        self.assertGreaterEqual(len(lossy), 12)
        self.assertEqual(lossy[:4], b"RIFF")
        self.assertEqual(lossy[8:12], b"WEBP")
        self.assertLess(len(lossy), len(lossless))


if __name__ == "__main__":
    unittest.main()
