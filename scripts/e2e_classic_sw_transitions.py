#!/usr/bin/env python3
"""Real-browser SW upgrade and rollback smoke for the classic distribution.

Serve the unbundled checkout and the *actual generated distribution* at one
loopback origin, preserving a single Chromium profile across release switches.
The raw checkout and generated distribution are never mutated.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import tempfile
from threading import Thread
from typing import Iterator
from urllib.parse import urlsplit

from scripts.build_classic_distribution import (
    BODY_BUNDLE, HEAD_BUNDLE, build_distribution,
)

REPO = Path(__file__).resolve().parents[1]
BUNDLES = (HEAD_BUNDLE, BODY_BUNDLE)
EXPORTS = (
    "prksNavigate",
    "createPrksOfflineStore",
    "prksOfflineNoteRequestFailure",
    "prksGetResearchGraphDebug",
)
CONTROL_TIMEOUT = 45000


class ReleaseSource:
    """A single origin whose static-asset release can change between requests."""

    def __init__(self, frontend: Path):
        self.frontend = frontend

    def handler(self):
        source = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(source.frontend), **kwargs)

            def log_message(self, format, *args):
                pass

            def end_headers(self):
                # A SW update must not reuse a cached copy of /sw.js.
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

        return Handler


@contextmanager
def serve(source: ReleaseSource) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), source.handler())
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def expected_cache_names(worker: str) -> set[str]:
    """Read revision expressions from real source/build worker, fail closed."""
    base = re.search(r"const DEPENDENCY_REVISION = '([0-9a-f]{12})';", worker)
    bundle = re.search(r"const CLASSIC_BUNDLE_REVISION = '([0-9a-f]{16})';", worker)
    if not base:
        raise ValueError("service-worker vendor cache revision missing")
    for kind in ("shell", "static"):
        token = f"const {kind.upper()}_CACHE = 'prks-{kind}-' + DEPENDENCY_REVISION"
        if worker.count(token) != 1:
            raise ValueError(f"service-worker {kind} cache contract changed")
    suffix = f"-{bundle.group(1)}" if bundle else ""
    revision = base.group(1) + suffix
    return {f"prks-shell-{revision}", f"prks-static-{revision}"}


def require_globals(page) -> None:
    exports = page.evaluate(
        """keys => Object.fromEntries(
            keys.map(name => [name, typeof window[name]])
        )""",
        list(EXPORTS),
    )
    missing = [key for key in EXPORTS if exports.get(key) != "function"]
    if missing:
        raise AssertionError(f"Missing globals after offline reload: {missing}")


def await_version(page, expected: set[str], obsolete: set[str]) -> None:
    # Check cache promotion AND retirement, not merely the HTTP 200 of /sw.js.
    # The latter can succeed even if a required asset failed installation.
    page.wait_for_function(
        """([expect, old]) => caches.keys().then(keys =>
           expect.every(name => keys.includes(name)) &&
           old.every(name => !keys.includes(name)) &&
           !!(navigator.serviceWorker && navigator.serviceWorker.controller)
        )""",
        arg=[sorted(expected), sorted(obsolete)],
        timeout=CONTROL_TIMEOUT,
    )


def offline_reload(context, page, script_urls: tuple[str, ...]) -> None:
    responses = {}
    errors = []

    def response_seen(response):
        path = urlsplit(response.url).path
        if path in script_urls:
            responses[path] = (response.status, response.from_service_worker)

    def page_error(error):
        errors.append(str(error)[:350])

    page.on("response", response_seen)
    page.on("pageerror", page_error)
    try:
        context.set_offline(True)
        page.reload(wait_until="domcontentloaded", timeout=CONTROL_TIMEOUT)
        page.wait_for_function(
            "() => typeof window.prksNavigate === 'function'",
            timeout=15000,
        )
        require_globals(page)
        expected = {url: (200, True) for url in script_urls}
        if responses != expected:
            raise AssertionError(
                f"Offline scripts not served from SW: got={responses} expected={expected}"
            )
        if errors:
            raise AssertionError(f"Uncaught errors on offline boot: {errors[:5]}")
    finally:
        context.set_offline(False)
        page.remove_listener("response", response_seen)
        page.remove_listener("pageerror", page_error)


def test_transitions(repo: Path = REPO) -> None:
    """Unbundled -> bundled -> unbundled, with genuine online SW updates."""
    # Keep Playwright's own repo-local Chromium cache consistent with other CI.
    from tests.e2e.install_browser import apply_playwright_browser_env

    apply_playwright_browser_env()
    from playwright.sync_api import sync_playwright

    repo = repo.resolve()
    old_frontend = repo / "frontend"
    old_worker = (old_frontend / "sw.js").read_text(encoding="utf-8")
    old_cache = expected_cache_names(old_worker)
    if not (old_frontend / "js/app.js").is_file():
        raise ValueError("unbundled bootstrap not present")

    with tempfile.TemporaryDirectory(prefix="prks-sw-transition-") as temp:
        output = Path(temp) / "frontend"
        build_distribution(repo, output)
        new_cache = expected_cache_names((output / "sw.js").read_text(encoding="utf-8"))
        if old_cache == new_cache:
            raise AssertionError("versioned bundle caches alias original caches")

        source = ReleaseSource(old_frontend)
        with serve(source) as origin, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(service_workers="allow")
                try:
                    page = context.new_page()
                    page.goto(origin + "/#/folders", wait_until="load", timeout=CONTROL_TIMEOUT)
                    await_version(page, old_cache, set())
                    offline_reload(context, page, ("/js/app.js",))
                    print("PASS unbundled worker offline boot", flush=True)

                    source.frontend = output
                    page.evaluate(
                        "() => navigator.serviceWorker.getRegistration().then(reg => reg.update())"
                    )
                    await_version(page, new_cache, old_cache)
                    page.reload(wait_until="domcontentloaded", timeout=CONTROL_TIMEOUT)
                    offline_reload(context, page, BUNDLES)
                    print("PASS upgrade to bundled worker; offline bundled boot and cache retirement", flush=True)

                    source.frontend = old_frontend
                    page.evaluate(
                        "() => navigator.serviceWorker.getRegistration().then(reg => reg.update())"
                    )
                    await_version(page, old_cache, new_cache)
                    page.reload(wait_until="domcontentloaded", timeout=CONTROL_TIMEOUT)
                    offline_reload(context, page, ("/js/app.js",))
                    print("PASS rollback to unbundled worker; offline boot and cache retirement", flush=True)
                finally:
                    context.close()
            finally:
                browser.close()


def main() -> None:
    test_transitions()


if __name__ == "__main__":
    main()
