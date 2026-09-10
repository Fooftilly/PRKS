#!/usr/bin/env python3
"""Stress the cache-observed -> offline-reload transition. Not part of any gate.

`_wait_entity_cached()` / `_wait_list_cached()` append a fixed 250 ms settle
delay after the IndexedDB row becomes readable. This script exists to decide
whether that delay is load-bearing, by repeating the exact risky sequence --
authoritative online read, wait until the row is observable, make PRKS
unreachable, reload, require the offline fallback -- over a Work entity, a
domain entity and a domain list.

Its answer, on the Concept-detail case: the delay IS load-bearing. Without it,
6-9 of 20 iterations lose the cached row entirely (`getEntity` returns null
after the reload) and the route renders "not available offline"; with it, 20/20
pass. `offline-store.js` resolving `readwrite` from `tx.oncomplete` is not
enough -- a row a separate read transaction can observe is still not guaranteed
to survive an immediate page teardown. Re-run this before ever trusting the
opposite claim:

    python tests/e2e/stress_cache_offline.py --iterations 50
    python tests/e2e/stress_cache_offline.py --case entity --iterations 20 --settle-ms 250
    python tests/e2e/stress_cache_offline.py --case entity --iterations 20
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ["PRKS_E2E"] = "1"

from tests.e2e.fixtures import CONCEPT_CHILD_NAME, WORK_A_TITLE, seed_concepts_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium

CASES = ("work", "entity", "list")


def _wait_sw_active(page):
    page.evaluate(
        """() => navigator.serviceWorker && navigator.serviceWorker.ready
            ? navigator.serviceWorker.ready.then(() => true)
            : Promise.resolve(false)"""
    )
    page.wait_for_function(
        "() => !!(navigator.serviceWorker && navigator.serviceWorker.controller)"
    )


def _wait_entity_cached(page, kind, entity_id, settle_ms=0, timeout=15000):
    """The helper under test: observable row, then an optional settle delay.

    `settle_ms` exists only so the removed 250 ms delay can be A/B'd against
    the same failure: if a failure rate is identical with and without it, the
    delay was never what made the transition safe.
    """
    page.wait_for_function(
        """([kind, id]) => {
            if (typeof window.createPrksOfflineStore !== 'function') return false;
            const store = window.createPrksOfflineStore();
            return store.getEntity(kind, id).then(v => !!v);
        }""",
        arg=[kind, entity_id],
        timeout=timeout,
        polling=100,
    )
    if settle_ms:
        page.wait_for_timeout(settle_ms)


def _wait_list_cached(page, list_key, settle_ms=0, timeout=15000):
    page.wait_for_function(
        """(key) => {
            if (typeof window.createPrksOfflineStore !== 'function') return false;
            return window.createPrksOfflineStore().getList(key).then(v => !!v);
        }""",
        arg=list_key,
        timeout=timeout,
        polling=100,
    )
    if settle_ms:
        page.wait_for_timeout(settle_ms)


def _open_work(page, title):
    page.locator('#sidebar a.nav-link[href="#/folders"]').click()
    page.wait_for_function("() => location.hash === '#/folders'")
    page.locator('.prks-folder-library__tab-btn[data-tab="recently-added"]').click()
    page.locator(".card-title", has_text=title).wait_for()
    page.locator(".card-title", has_text=title).click()
    page.wait_for_function(
        "t => decodeURIComponent(location.hash).indexOf('/works/') !== -1"
        " && document.body.innerText.indexOf(t) !== -1",
        arg=title,
    )


def _navigate(page, hash_target):
    page.evaluate("h => { void window.prksNavigate(h); }", hash_target)
    page.wait_for_function("h => location.hash === h", arg=hash_target, polling=100)


def _require_offline_render(page, needle, timeout=30000):
    """Same shape the offline suite asserts: content plus the provenance banner.

    Checks the focused route's own container rather than a visibility-based
    locator, exactly as `_wait_focused_role()` in tests.e2e.test_offline does --
    these routes can render into a pane whose layout makes a CSS-visibility wait
    meaningless.
    """
    page.wait_for_selector("#sidebar")
    # Poll on a timer, not on requestAnimationFrame (Playwright's default).
    # This script drives many short-lived contexts through one browser, and a
    # rAF-driven predicate can stall in a page Chromium considers backgrounded --
    # which would otherwise show up as a fake "cache lost" failure.
    # The focused route's own container, never document.body: the needle also
    # appears in the shell (title, breadcrumb), so a body-wide check reports
    # success even while the route renders "not available offline".
    page.wait_for_function(
        """(n) => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const root = ctx && ctx.root;
            return !!root && root.innerText.indexOf(n) !== -1;
        }""",
        arg=needle,
        timeout=timeout,
        polling=100,
    )
    page.wait_for_function(
        """() => {
            const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
            const root = ctx && ctx.root;
            return !!root && !!root.querySelector('[data-prks-role="offline-provenance-banner"]');
        }""",
        timeout=timeout,
        polling=100,
    )


def _iteration(browser, server, case, settle_ms=0):
    page, context, _collector = open_app_page(
        browser, server.origin, service_workers="allow"
    )
    try:
        _wait_sw_active(page)
        if case == "work":
            _open_work(page, WORK_A_TITLE)
            _wait_entity_cached(page, "work", server.ids["work_a"], settle_ms)
            needle = WORK_A_TITLE
        elif case == "entity":
            _navigate(page, "#/concepts/" + server.ids["concept_child"])
            _wait_entity_cached(page, "concept", server.ids["concept_child"], settle_ms)
            needle = CONCEPT_CHILD_NAME
        else:
            _navigate(page, "#/concepts")
            _wait_list_cached(page, "concepts:index", settle_ms)
            needle = CONCEPT_CHILD_NAME
        context.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        _require_offline_render(page, needle)
    finally:
        try:
            context.close()
        except Exception:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tests/e2e/stress_cache_offline.py",
        description="Repeat the cache-observed -> offline-reload transition.",
    )
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=0,
        help="Re-add the removed post-cache settle delay, to A/B whether it mattered.",
    )
    parser.add_argument(
        "--case", choices=CASES + ("all",), default="all", help="Which shape to stress."
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    cases = CASES if args.case == "all" else (args.case,)

    pw, browser = require_chromium()
    server = AppServer(seed_fn=seed_concepts_library)
    failures = []
    try:
        server.start()
        for case in cases:
            started = time.perf_counter()
            for i in range(args.iterations):
                try:
                    _iteration(browser, server, case, args.settle_ms)
                except Exception as exc:
                    failures.append("%s #%d: %s: %s" % (case, i + 1, type(exc).__name__, exc))
                    print("FAIL %s iteration %d: %s" % (case, i + 1, exc), file=sys.stderr)
                if (i + 1) % 10 == 0:
                    print(
                        "  %s %d/%d (%.1fs)"
                        % (case, i + 1, args.iterations, time.perf_counter() - started),
                        flush=True,
                    )
            print(
                "%s: %d iterations in %.1fs"
                % (case, args.iterations, time.perf_counter() - started),
                flush=True,
            )
    finally:
        server.stop()
        try:
            browser.close()
        finally:
            pw.stop()

    if failures:
        print("")
        print("STRESS FAIL — %d failing iteration(s)" % len(failures), file=sys.stderr)
        for line in failures[:20]:
            print("  " + line, file=sys.stderr)
        return 1
    print("")
    print("STRESS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
