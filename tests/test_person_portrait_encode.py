import os
import sys
import unittest
from io import BytesIO

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.server import _prks_portrait_cache_bytes  # noqa: E402


def _large_test_png_bytes(width: int = 1200, height: int = 900) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (40, 80, 160))
    for y in range(0, height, 40):
        for x in range(0, width, 40):
            img.putpixel((x, y), ((x + y) % 256, 100, 180))
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


class TestPersonPortraitEncode(unittest.TestCase):
    def test_portrait_webp_smaller_than_source_and_valid_header(self):
        raw = _large_test_png_bytes()
        encoded = _prks_portrait_cache_bytes(raw, max_edge=512)
        if encoded is None:
            self.skipTest("Pillow/WebP encode not available")
        out, subtype = encoded
        self.assertEqual(subtype, "webp")
        self.assertGreaterEqual(len(out), 12)
        self.assertEqual(out[:4], b"RIFF")
        self.assertEqual(out[8:12], b"WEBP")
        self.assertLess(len(out), len(raw))

    def test_portrait_respects_max_edge(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        raw = _large_test_png_bytes(800, 600)
        encoded = _prks_portrait_cache_bytes(raw, max_edge=256)
        if encoded is None:
            self.skipTest("Pillow/WebP encode not available")
        out, _ = encoded
        img = Image.open(BytesIO(out))
        self.assertLessEqual(max(img.size), 256)


if __name__ == "__main__":
    unittest.main()
