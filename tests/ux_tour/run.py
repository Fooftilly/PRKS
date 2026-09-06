#!/usr/bin/env python3
"""Run the PRKS UX Interaction Tour against isolated temporary PRKS storage.

Never targets data/ or a live PRKS_STORAGE tree. Installs Chromium into
.playwright-browsers/ when this Playwright revision is missing, exactly like
tests/e2e/run.py. This is a separate, opt-in, artifact-producing suite -- see
run_tests.py --ux-tour / PRKS_UX_RECORD.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ["PRKS_UX_TOUR"] = "1"

from tests.e2e.harness import apply_e2e_playwright_env
from tests.e2e.install_browser import ensure_chromium_installed


def main() -> int:
    apply_e2e_playwright_env()
    try:
        ensure_chromium_installed()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    apply_e2e_playwright_env()

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.ux_tour.test_tours")
    # Never failfast: every tour must run and produce its own artifacts even
    # when an earlier one fails.
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
