"""Unit regressions for fixture-only local visual baselines (no browser required)."""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

from tests.visual.compare import (
    FORMAT,
    SCENES,
    candidate,
    compare,
    fingerprint,
    metadata,
    verify,
    write_json,
)


def png_sample(*, invert: bool = False) -> bytes:
    picture = Image.new("RGB", (480, 300), "white" if not invert else "black")
    canvas = ImageDraw.Draw(picture)
    canvas.rectangle((0, 0, 150, 300), fill="black" if not invert else "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


class LocalVisualBaselineTests(unittest.TestCase):
    def test_equal_pixels_pass_and_major_layout_deviation_fails(self):
        original = fingerprint(png_sample())
        self.assertTrue(compare(original, original)["passed"])
        inverse = fingerprint(png_sample(invert=True))
        changed = compare(inverse, original)
        self.assertFalse(changed["passed"])
        self.assertGreater(changed["mean_delta"], 9)
        self.assertGreater(changed["hot_fraction"], 0.06)

    def test_subtle_global_shift_fails_on_mean_without_hot_cells(self):
        base = fingerprint(png_sample())
        # All sampled pixels shift by 16/256, below the 35-channel hot limit
        # but above the 9-channel mean limit.
        from tests.visual.compare import SAMPLE_SIZE
        raw = bytes((80, 80, 80)) * (SAMPLE_SIZE[0] * SAMPLE_SIZE[1])
        shifted = bytes((96, 96, 96)) * (SAMPLE_SIZE[0] * SAMPLE_SIZE[1])
        before = {**base, "rgb48x30_zlib": base64.b64encode(zlib.compress(raw)).decode()}
        after = {**base, "rgb48x30_zlib": base64.b64encode(zlib.compress(shifted)).decode()}
        result = compare(after, before)
        self.assertFalse(result["passed"])
        self.assertEqual(result["hot_fraction"], 0)
        self.assertGreater(result["mean_delta"], 9)

    def test_small_high_contrast_region_fails_on_hot_without_mean(self):
        from tests.visual.compare import SAMPLE_SIZE
        base = fingerprint(png_sample())
        count = SAMPLE_SIZE[0] * SAMPLE_SIZE[1]
        original = bytes((80, 80, 80)) * count
        altered = bytes((120, 120, 120)) * 150 + bytes((80, 80, 80)) * (count - 150)
        before = {**base, "rgb48x30_zlib": base64.b64encode(zlib.compress(original)).decode()}
        after = {**base, "rgb48x30_zlib": base64.b64encode(zlib.compress(altered)).decode()}
        result = compare(after, before)
        self.assertFalse(result["passed"])
        self.assertLess(result["mean_delta"], 9)
        self.assertGreater(result["hot_fraction"], 0.06)

    def test_dimensions_are_exact_even_when_mean_sample_matches(self):
        identical = fingerprint(png_sample())
        altered = {**identical, "width": 481}
        result = compare(altered, identical)
        self.assertFalse(result["passed"])
        self.assertTrue(result["size_changed"])

    def test_bounded_noise_tolerated_by_quantized_samples(self):
        sample = fingerprint(png_sample())
        png = Image.open(io.BytesIO(png_sample())).convert("RGB")
        # One isolated pixel can't be mistaken for a large UI regression.
        png.putpixel((300, 150), (250, 250, 250))
        out = io.BytesIO()
        png.save(out, format="PNG")
        self.assertTrue(compare(fingerprint(out.getvalue()), sample)["passed"])

    def test_missing_metadata_or_unreviewed_scene_fails_closed(self):
        base = fingerprint(png_sample())
        all_scenes = {scene: base for scene in SCENES}
        approved = candidate(all_scenes)
        self.assertEqual(approved["format"], FORMAT)
        self.assertTrue(all(row["passed"] for row in verify(all_scenes, approved).values()))
        damaged = {**approved, "locale": "other"}
        with self.assertRaisesRegex(ValueError, "metadata"):
            verify(all_scenes, damaged)
        with self.assertRaisesRegex(ValueError, "include every"):
            candidate({"folders": base})
        with self.assertRaisesRegex(ValueError, "incomplete"):
            verify(all_scenes, {**approved, "scenes": {"folders": base}})

    def test_invalid_png_and_corrupt_sample_are_rejected(self):
        with self.assertRaises(Exception):
            fingerprint(b"not a PNG")
        sample = fingerprint(png_sample())
        damaged = {**sample, "rgb48x30_zlib": base64.b64encode(b"bad").decode()}
        with self.assertRaises(Exception):
            compare(damaged, sample)

    def test_stored_json_is_small_without_user_html_or_screenshot_bytes(self):
        first = fingerprint(png_sample())
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.json"
            write_json(path, candidate({scene: first for scene in SCENES}))
            body = path.read_text(encoding="utf-8")
            self.assertLess(len(body), 22000)
            self.assertNotIn("data:image/png", body)
            self.assertEqual(json.loads(body)["viewport"], metadata()["viewport"])


if __name__ == "__main__":
    unittest.main()
