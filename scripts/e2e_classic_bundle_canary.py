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
# Do not pass user-supplied shard strings to subprocess: this fixed map
# contains only known external partition identifiers.
SHARD_ARGUMENTS = {
    1: "1/6",
    2: "2/6",
    3: "3/6",
    4: "4/6",
    5: "5/6",
    6: "6/6",
}
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


def _temporary_service_worker(source: str) -> str:
    """Make only the two temporary root-level bundles eligible and precached.

    The production worker is unchanged. Fail closed if its known manifest
    structure changes, rather than silently testing a broken offline shell.
    """
    precache_anchor = "        '/vue/prks-vue.js',\n    ];"
    extra_anchor = (
        "const STATIC_EXTRA_PATHS = "
        "['/manifest.webmanifest', '/favicon.svg', '/logo.svg'];"
    )
    if any(url in source for url in BUNDLE_URLS):
        raise ValueError("service worker already lists a temporary bundle")
    if source.count(precache_anchor) != 1 or source.count(extra_anchor) != 1:
        raise ValueError("service-worker manifest structure changed; cannot stage canary")
    paths = "".join(f"        '{url}',\n" for url in BUNDLE_URLS)
    source = source.replace(
        precache_anchor, paths + precache_anchor, 1,
    )
    expanded = (
        "const STATIC_EXTRA_PATHS = "
        "['/manifest.webmanifest', '/favicon.svg', '/logo.svg', "
        + ", ".join(repr(url) for url in BUNDLE_URLS) + "];"
    )
    return source.replace(extra_anchor, expanded, 1)


def _browser_preflight() -> None:
    """Prove cached shell and both classic bundles work after an offline reload."""
    from tests.e2e.install_browser import apply_playwright_browser_env

    apply_playwright_browser_env()
    from playwright.sync_api import sync_playwright
    from tests.e2e.harness import AppServer

    server = AppServer().start()
    errors: list[str] = []
    # Collected afresh on offline reload, so the successful online load cannot
    # hide a missing offline script.
    responses: dict[str, int] = {}
    sw_responses: set[str] = set()
    available: dict[str, str] = {}
    controlled = False
    offline_reloaded = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(service_workers="allow")
                try:
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)[:600]))

                    def capture_response(response):
                        for url in BUNDLE_URLS:
                            if response.url.split("?", 1)[0].endswith(url):
                                responses[url] = response.status
                                if response.from_service_worker:
                                    sw_responses.add(url)

                    def capture_failed_request(request):
                        for url in BUNDLE_URLS:
                            if request.url.split("?", 1)[0].endswith(url):
                                errors.append(f"bundle request failed: {url}")

                    page.on("response", capture_response)
                    page.on("requestfailed", capture_failed_request)
                    try:
                        page.goto(
                            server.origin + "/#/folders",
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                        # Registration happens on the load event in app.js.
                        # Do NOT await navigator.serviceWorker.ready here: if
                        # registration never happens or install/precache fails,
                        # it remains pending indefinitely. A controlling worker
                        # only appears after successful activation (clients.claim).
                        # A bounded Playwright poll reports failures promptly.
                        page.wait_for_function(
                            "() => !!(navigator.serviceWorker && "
                            "navigator.serviceWorker.controller)",
                            timeout=30000,
                        )
                        controlled = True
                        responses.clear()
                        sw_responses.clear()
                        context.set_offline(True)
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        offline_reloaded = True
                        available = page.evaluate(
                            """() => {
                                const keys = [
                                    'prksNavigate', 'createPrksOfflineStore',
                                    'prksOfflineNoteRequestFailure',
                                    'prksGetResearchGraphDebug'
                                ];
                                return Object.fromEntries(
                                    keys.map(k => [k, typeof window[k]])
                                );
                            }"""
                        )
                    except Exception as exc:
                        errors.append("offline boot: " + str(exc)[:600])
                finally:
                    context.close()
            finally:
                browser.close()
    finally:
        server.stop()

    print(
        "Bundled offline reload probe: "
        f"service-worker-controlled={controlled}, reloaded={offline_reloaded}, "
        f"exports={available}, bundle statuses={responses}, "
        f"from-service-worker={sorted(sw_responses)}, errors={errors[:8]}",
        flush=True,
    )
    if not controlled or not offline_reloaded:
        raise RuntimeError("temporary bundle preflight did not complete offline reload")
    _require_boot_exports(available, responses, errors)
    if sw_responses != set(BUNDLE_URLS):
        raise RuntimeError(
            "offline bundle responses were not supplied by service worker: "
            f"{sorted(sw_responses)}"
        )
    print("Temporary classic bundle offline browser preflight passed.", flush=True)


def run_canary(root: Path, shard_index: int | None = None, *, preflight: bool = False) -> None:
    """Run one *complete-suite* external shard, restoring all files on exit."""
    if preflight:
        if shard_index is not None:
            raise ValueError("preflight may not also select a shard")
    elif type(shard_index) is not int or shard_index not in SHARD_ARGUMENTS:
        raise ValueError("expected one shard index from 1 through 6")

    index = root / "frontend/index.html"
    worker = root / "frontend/sw.js"
    original = index.read_bytes()
    original_worker = worker.read_bytes()
    worker_candidate = _temporary_service_worker(original_worker.decode("utf-8"))
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
        "--shard", SHARD_ARGUMENTS[shard_index],
        "--no-pointer-capture",
    ] if not preflight else []
    try:
        for url, text in bundles.items():
            path = bundle_paths[url]
            path.write_text(text, encoding="utf-8")
            subprocess.run([node, "--check", str(path)], cwd=root, check=True)
        index.write_text(candidate, encoding="utf-8")
        worker.write_text(worker_candidate, encoding="utf-8")
        subprocess.run([node, "--check", str(worker)], cwd=root, check=True)
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
        worker.write_bytes(original_worker)
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
