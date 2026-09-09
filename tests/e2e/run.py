#!/usr/bin/env python3
"""Run real Chromium E2E against isolated temporary PRKS storage.

Never targets data/ or a live PRKS_STORAGE tree. Installs Chromium into
.playwright-browsers/ when this Playwright revision is missing. Fails if the
Playwright package is missing instead of reporting SKIP.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ["PRKS_E2E"] = "1"

from tests.e2e.harness import apply_e2e_playwright_env, python_for_subprocess
from tests.e2e.install_browser import ensure_chromium_installed


def _run_pointer_capture() -> int:
    env = os.environ.copy()
    env["PRKS_E2E"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(apply_e2e_playwright_env())
    env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    proc = subprocess.run(
        [python_for_subprocess(), str(REPO / "tests" / "browser" / "pointer_capture.py")],
        cwd=str(REPO),
        env=env,
    )
    return proc.returncode


def main() -> int:
    apply_e2e_playwright_env()
    try:
        ensure_chromium_installed()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    apply_e2e_playwright_env()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        [
            loader.loadTestsFromName("tests.e2e.test_app"),
            loader.loadTestsFromName("tests.e2e.test_offline"),
            loader.loadTestsFromName("tests.e2e.test_person_groups_offline"),
        ]
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    pointer = _run_pointer_capture()
    ok = result.wasSuccessful() and pointer == 0
    if pointer != 0:
        print("pointer_capture.py failed", file=sys.stderr)
    if ok:
        print("E2E PASS")
        return 0
    print("E2E FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
