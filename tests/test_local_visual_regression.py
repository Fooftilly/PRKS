"""Unit regressions for fixture-only local visual baselines (no browser required)."""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from scripts.ci.visual_regression import (
    VisualCaptureFailure, assert_split_pdf_ownership, collect, main,
)

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
    def test_replay_discards_old_outputs_before_capture_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stale = ["prks-visual-candidate.json", "prks-visual-results.json",
                     "prks-visual-failure.json"]
            stale += [f"prks-visual-{name}.png" for name in SCENES]
            for name in stale:
                (root / name).write_text("stale result", encoding="utf-8")
            unrelated = root / "keep.txt"
            unrelated.write_text("unchanged", encoding="utf-8")
            with (patch("scripts.ci.visual_regression.Path.cwd", return_value=root),
                  patch("scripts.ci.visual_regression.collect",
                        side_effect=VisualCaptureFailure("capture_pdf", "TimeoutError"))):
                with self.assertRaises(VisualCaptureFailure):
                    main(["--mode", "check"])
            for name in stale:
                with self.subTest(name=name):
                    self.assertFalse((root / name).exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "unchanged")

    def test_replay_writes_only_this_runs_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "prks-visual-results.json").write_text("stale", encoding="utf-8")
            (root / "prks-visual-failure.json").write_text("stale", encoding="utf-8")
            sample = fingerprint(png_sample())
            with (patch("scripts.ci.visual_regression.Path.cwd", return_value=root),
                  patch("scripts.ci.visual_regression.collect",
                        return_value={scene: sample for scene in SCENES})):
                self.assertEqual(main(["--mode", "propose"]), 0)
            self.assertTrue((root / "prks-visual-candidate.json").exists())
            self.assertFalse((root / "prks-visual-results.json").exists())
            self.assertFalse((root / "prks-visual-failure.json").exists())

    def test_split_capture_requires_distinct_work_and_pdf_ownership(self):
        page = Mock()
        assert_split_pdf_ownership(
            page, main_tab="main-tab", secondary_tab="secondary-tab",
            main_work="work-a", secondary_work="work-b",
        )
        page.wait_for_function.assert_called_once()
        args, kwargs = page.wait_for_function.call_args
        predicate = args[0]
        self.assertIn("mainTab === secondaryTab", predicate)
        self.assertIn("String(mainWork) === String(secondaryWork)", predicate)
        self.assertIn("a.getEntity('work')", predicate)
        self.assertIn("b.getEntity('work')", predicate)
        self.assertIn("String(wa.id) === String(mainWork)", predicate)
        self.assertIn("String(wb.id) === String(secondaryWork)", predicate)
        self.assertIn("a.getResource('pdf')", predicate)
        self.assertIn("b.getResource('pdf')", predicate)
        self.assertIn("String(pa.workId) === String(mainWork)", predicate)
        self.assertIn("String(pb.workId) === String(secondaryWork)", predicate)
        self.assertEqual(kwargs["arg"], {
            "mainTab": "main-tab", "secondaryTab": "secondary-tab",
            "mainWork": "work-a", "secondaryWork": "work-b",
        })
        self.assertEqual(kwargs["timeout"], 30000)

    def test_visual_capture_errors_label_scene_without_private_browser_text(self):
        from tempfile import TemporaryDirectory

        with (
            patch("scripts.ci.visual_regression.AppServer") as server_type,
            patch("scripts.ci.visual_regression.require_chromium") as browsers,
            patch("scripts.ci.visual_regression.PageCollector"),
            patch("scripts.ci.visual_regression.capture") as capture_scene,
            TemporaryDirectory() as td,
        ):
            server = server_type.return_value
            server.origin = "http://127.0.0.1:1234"
            browser_api, browser = Mock(), Mock()
            browsers.return_value = (browser_api, browser)
            ctx = browser.new_context.return_value

            capture_scene.side_effect = ValueError("private-document-title")
            server.stop.side_effect = RuntimeError("secondary-private-error")
            with self.assertRaises(VisualCaptureFailure) as caught:
                collect(Path(td))
            self.assertEqual(caught.exception.stage, "capture_folders")
            self.assertEqual(caught.exception.cause_type, "ValueError")
            self.assertEqual(
                str(caught.exception), "visual/capture_folders (ValueError)",
            )
            self.assertNotIn("private", str(caught.exception))
            failure = json.loads((Path(td) / "prks-visual-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure, {
                "format": "prks-local-visual-failure-v1",
                "status": "capture_failed",
                "stage": "capture_folders",
                "exception_type": "ValueError",
                "completed_scenes": [],
            })
            self.assertNotIn("private", json.dumps(failure))
            ctx.close.assert_called_once()
            browser.close.assert_called_once()
            browser_api.stop.assert_called_once()
            server.stop.assert_called_once()

    def test_visual_cleanup_error_fails_only_when_no_primary_failure(self):
        from tempfile import TemporaryDirectory

        with (
            patch("scripts.ci.visual_regression.AppServer") as server_type,
            patch("scripts.ci.visual_regression.require_chromium") as browsers,
            patch("scripts.ci.visual_regression.PageCollector") as collector_type,
            patch("scripts.ci.visual_regression.capture", return_value={
                "width": 1400, "height": 900, "rgb48x30_zlib": "",
            }),
            patch("scripts.ci.visual_regression._wait_pdf_viewer"),
            patch("scripts.ci.visual_regression._wait_pdf_tab"),
            TemporaryDirectory() as td,
        ):
            server = server_type.return_value
            server.origin = "http://127.0.0.1:1234"
            server.ids = {"work_a": "work-a", "work_b": "work-b"}
            browser_api, browser = Mock(), Mock()
            browsers.return_value = (browser_api, browser)
            ctx = browser.new_context.return_value
            page = ctx.new_page.return_value
            page.evaluate.return_value = {
                "mainTabId": "main-tab",
                "secondaryTree": {"tabId": "secondary-tab"},
            }
            # unittest.mock rejects attributes starting with "assert_" unless
            # explicitly configured. This fixture must model a *clean* real
            # PageCollector or it masks the cleanup error we're verifying.
            collector_type.return_value.assert_clean = Mock()
            ctx.close.side_effect = ValueError("private-context-info")
            with self.assertRaises(VisualCaptureFailure) as caught:
                collect(Path(td))
            self.assertEqual(caught.exception.stage, "close_context")
            self.assertEqual(caught.exception.cause_type, "ValueError")
            self.assertNotIn("private", str(caught.exception))
            failure = json.loads((Path(td) / "prks-visual-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["stage"], "close_context")
            self.assertEqual(failure["exception_type"], "ValueError")
            self.assertEqual(failure["completed_scenes"], list(SCENES))
            self.assertNotIn("private", json.dumps(failure))
            collector_type.return_value.assert_clean.assert_called_once_with()
            browser.close.assert_called_once()
            browser_api.stop.assert_called_once()
            server.stop.assert_called_once()

    def test_equal_pixels_pass_and_major_layout_deviation_fails(self):
        original = fingerprint(png_sample())
        self.assertTrue(compare(original, original)["passed"])
        inverse = fingerprint(png_sample(invert=True))
        changed = compare(inverse, original)
        self.assertFalse(changed["passed"])
        self.assertEqual(changed["reason"], "mean_and_hot")
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
        self.assertEqual(result["reason"], "mean_delta")
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
        self.assertEqual(result["reason"], "hot_fraction")
        self.assertGreater(result["hot_fraction"], 0.06)

    def test_dimensions_are_exact_even_when_mean_sample_matches(self):
        identical = fingerprint(png_sample())
        altered = {**identical, "width": 481}
        result = compare(altered, identical)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "dimensions")
        self.assertTrue(result["size_changed"])

    def test_bounded_noise_tolerated_by_quantized_samples(self):
        sample = fingerprint(png_sample())
        png = Image.open(io.BytesIO(png_sample())).convert("RGB")
        # One isolated pixel can't be mistaken for a large UI regression.
        png.putpixel((300, 150), (250, 250, 250))
        out = io.BytesIO()
        png.save(out, format="PNG")
        result = compare(fingerprint(out.getvalue()), sample)
        self.assertTrue(result["passed"])
        self.assertEqual(result["reason"], "within_tolerance")

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
