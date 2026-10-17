#!/usr/bin/env python3
"""Manually exercise all six PRKS E2E shards with temporary classic bundles.

The candidate and assets exist only in the checkout of the selected Actions
job. This is *not* a production packaging or service-worker cache change.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .benchmark_classic_script_bundle import build_candidate, BODY_URL, HEAD_URL

SHARD_COUNT = 6
BUNDLE_URLS = (HEAD_URL, BODY_URL)
ROOT = Path(__file__).resolve().parents[1]


# These classic-script exports must be visible before any full-suite test.
# Missing globals were the common symptom of all six failures in run 39023728966.
BOOT_EXPORTS = (
    "prksNavigate",
    "createPrksOfflineStore",
    "prksOfflineNoteRequestFailure",
    "prksGetResearchGraphDebug",
)


def _require_boot_exports(available: dict, responses: dict, errors: list[str]) -> None:
    missing = [key for key in BOOT_EXPORTS if available.get(key) != "function"]
    bad_responses = {
        url: status for url, status in responses.items() if status != 200
    }
    if missing or bad_responses or len(responses) != len(BUNDLE_URLS) or errors:
        raise RuntimeError(
            "Temporary bundle browser preflight failed: "
            f"missing exports={missing}, bundle responses={responses}, "
            f"browser errors={errors[:8]}"
        )


def _browser_preflight() -> None:
    """Load the real PRKS shell in Chromium and report its *first* errors.

    This runs only after the candidate index/bundles have been staged and
    before the expensive six-shard matrix is allowed to start.
    """
    from playwright.sync_api import sync_playwright
    from tests.e2e.harness import AppServer
    from tests.e2e.install_browser import apply_playwright_browser_env

    apply_playwright_browser_env()
    server = AppServer().start()
    errors: list[str] = []
    responses: dict[str, int] = {}
    available: dict[str, str] = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(service_workers="block")
                try:
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)[:600]))
                    page.on(
                        "response",
                        lambda response: responses.update({
                            url: response.status
                            for url in BUNDLE_URLS
                            if response.url.split("?", 1)[0].endswith(url)
                        }),
                    )
                    page.on(
                        "requestfailed",
                        lambda request: errors.append(
                            "request failed: " + request.url.split("?", 1)[0].rsplit("/", 1)[-1]
                        ) if any(
                            request.url.split("?", 1)[0].endswith(url)
                            for url in BUNDLE_URLS
                        ) else None,
                    )
                    try:
                        page.goto(server.origin + "/#/folders", wait_until="domcontentloaded", timeout=30000)
                    except Exception as exc:
                        errors.append("navigation: " + str(exc)[:600])
                    try:
                        available = page.evaluate("""() => {
                            const keys = [
                                'prksNavigate', 'createPrksOfflineStore',
                                'prksOfflineNoteRequestFailure', 'prksGetResearchGraphDebug'
                            ];
                            return Object.fromEntries(keys.map(k => [k, typeof window[k]]));
                        }""")
                    except Exception as exc:
                        errors.append("global probe: " + str(exc)[:600])
                finally:
                    context.close()
            finally:
                browser.close()
    finally:
        server.stop()

    print(
        f"Bundled browser boot probe: exports={available}, "
        f"script statuses={responses}, errors={errors[:8]}",
        flush=True,
    )
    _require_boot_exports(available, responses, errors)
    print("Temporary classic bundle browser preflight passed.", flush=True)


def run_canary(root: Path, shard_index: int | None = None, *, preflight: bool = False) -> None:
    """Run one *complete-suite* external shard, restoring all files on exit."""
    if preflight:
        if shard_index is not None:
            raise ValueError("preflight may not also select a shard")
    elif type(shard_index) is not int or not 1 <= shard_index <= SHARD_COUNT:
        raise ValueError("expected one shard index from 1 through 6")

    index = root / "frontend/index.html"
    original = index.read_bytes()
    candidate, bundles, counts = build_candidate(root)
    if set(bundles) != set(BUNDLE_URLS):
        raise ValueError("unexpected classic bundle asset set")
    if counts["head"] + counts["body"] < 2:
        raise ValueError("unexpectedly small classic script inventory")
    bundle_paths = {url: root / "frontend" / url.lstrip("/") for url in BUNDLE_URLS}
    if any(path.exists() for path in bundle_paths.values()):
        raise ValueError("temporary bundle already exists; refusing overwrite")
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js is required to validate the candidate")

    env = os.environ.copy()
    env["PRKS_E2E_ROUTE_FASTPATH"] = "0"
    env["PRKS_E2E_PROFILE"] = "0"
    env["PRKS_E2E_BENCHMARK_RESULT"] = ""
    env["PRKS_E2E_MODE"] = "full"
    env["PRKS_E2E"] = "1"
    runner = [
        sys.executable,
        "tests/e2e/run.py",
        "--jobs", "1",
        "--shard", f"{shard_index}/{SHARD_COUNT}",
        "--no-pointer-capture",
    ] if not preflight else []
    try:
        for url, text in bundles.items():
            path = bundle_paths[url]
            path.write_text(text, encoding="utf-8")
            subprocess.run([node, "--check", str(path)], cwd=root, check=True)
        index.write_text(candidate, encoding="utf-8")
        if preflight:
            print("Temporary classic bundles staged for browser preflight.", flush=True)
            _browser_preflight()
        else:
            print(
                f"Temporary classic bundles staged: head={counts['head']}, "
                f"body={counts['body']}. Running complete-suite shard "
                f"{shard_index}/{SHARD_COUNT}.",
                flush=True,
            )
            subprocess.run(runner, cwd=root, env=env, check=True)
    finally:
        index.write_bytes(original)
        for path in bundle_paths.values():
            path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--shard", type=int, help="1-based shard index (1 through 6)")
    group.add_argument("--preflight", action="store_true", help="check real bundled browser boot")
    args = parser.parse_args()
    run_canary(ROOT, args.shard, preflight=args.preflight)


if __name__ == "__main__":
    main()
