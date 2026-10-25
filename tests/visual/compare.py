"""Local, fixture-only Playwright visual comparison using compact pixel baselines.

Baselines intentionally store coarse, deterministic sampled pixels (not screenshots);
review the full synthetic candidate PNGs before committing an updated baseline.
This pilot catches layout/theme regressions, not one-pixel or WCAG defects.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image

FORMAT = "prks-local-visual-v1"
SAMPLE_SIZE = (48, 30)
SCENES = ("folders", "work", "pdf", "split", "settings")
# Tolerate tiny font-antialias changes but fail substantial movement/contrast.
MEAN_DELTA_LIMIT = 9.0
HOT_FRACTION_LIMIT = 0.06
HOT_CHANNEL_DELTA = 35
MAX_PNG_BYTES = 6 * 1024 * 1024


def fingerprint(png: bytes) -> dict:
    if len(png) > MAX_PNG_BYTES:
        raise ValueError("synthetic screenshot exceeds bounded size")
    with Image.open(io.BytesIO(png)) as source:
        if source.format != "PNG":
            raise ValueError("expected a synthetic PNG screenshot")
        source.load()
        if source.width < 200 or source.height < 100:
            raise ValueError("unexpected tiny screenshot region")
        # BOX downsampling smooths 1-pixel raster/font noise; quantize after.
        reduced = source.convert("RGB").resize(SAMPLE_SIZE, Image.Resampling.BOX)
        rgb = bytes((channel // 8) * 8 for channel in reduced.tobytes())
        return {
            "width": source.width,
            "height": source.height,
            "rgb48x30": base64.b64encode(rgb).decode("ascii"),
        }


def compare(actual: dict, expected: dict) -> dict:
    if (actual["width"], actual["height"]) != (
        expected["width"], expected["height"]
    ):
        return {"passed": False, "size_changed": True, "mean_delta": None, "hot_fraction": None}

    def pixels(entry: dict) -> bytes:
        decoded = base64.b64decode(entry["rgb48x30"], validate=True)
        if len(decoded) != SAMPLE_SIZE[0] * SAMPLE_SIZE[1] * 3:
            raise ValueError("invalid visual sample dimensions")
        return decoded

    left, right = pixels(actual), pixels(expected)
    mean = sum(abs(a - b) for a, b in zip(left, right)) / len(left)
    hot = sum(
        max(abs(left[i + c] - right[i + c]) for c in range(3)) > HOT_CHANNEL_DELTA
        for i in range(0, len(left), 3)
    ) / (len(left) // 3)
    return {
        "passed": not (mean > MEAN_DELTA_LIMIT and hot > HOT_FRACTION_LIMIT),
        "size_changed": False,
        "mean_delta": round(mean, 3),
        "hot_fraction": round(hot, 4),
    }


def metadata() -> dict:
    return {
        "format": FORMAT,
        "viewport": {"width": 1400, "height": 900},
        "device_scale_factor": 1,
        "locale": "en-US",
        "timezone": "UTC",
        "color_scheme": "light",
        "reduced_motion": "reduce",
        "sampling": list(SAMPLE_SIZE),
    }


def candidate(samples: dict[str, dict]) -> dict:
    if set(samples) != set(SCENES):
        raise ValueError("visual capture must include every named scene")
    return {**metadata(), "scenes": {name: samples[name] for name in SCENES}}


def verify(samples: dict[str, dict], baseline: dict) -> dict[str, dict]:
    if not isinstance(baseline, dict):
        raise ValueError("visual baseline must be an object")
    expected_metadata = metadata()
    for key, value in expected_metadata.items():
        if baseline.get(key) != value:
            raise ValueError(f"visual baseline metadata mismatch: {key}")
    scenes = baseline.get("scenes")
    if not isinstance(scenes, dict) or set(scenes) != set(SCENES):
        raise ValueError("visual baseline has incomplete or extra scenes")
    if set(samples) != set(SCENES):
        raise ValueError("visual comparison did not capture all scenes")
    return {name: compare(samples[name], scenes[name]) for name in SCENES}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
