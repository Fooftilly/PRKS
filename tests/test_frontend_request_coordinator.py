"""Structural + Node regressions for the client request coordinator."""
import os
import re
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_COORD = os.path.join(_FRONTEND, "js", "request-coordinator.js")
_API = os.path.join(_FRONTEND, "js", "api.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_WORKS = os.path.join(_FRONTEND, "js", "components", "works.js")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_request_coordinator_selftest.js")

_FETCH_RE = re.compile(r"\bfetch\s*\(")

# Deliberate raw-fetch bypass contract. New first-party /api fetch() must be added here on purpose.
_ALLOWED_FETCH = (
    (_COORD, "return fetch(url, init);"),
    (_API, "fetch('/api/client-errors',"),
    (_API, "fetch('/api/backups/progress',"),
    (_API, "fetch('/api/backups/stage',"),
    (_API, "fetch('/api/backups/restore',"),
    (os.path.join(_FRONTEND, "js", "ui.js"), "fetch(oembed, { method: 'GET' })"),
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _frontend_js_files():
    root = os.path.join(_FRONTEND, "js")
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".js"):
                out.append(os.path.join(dirpath, name))
    out.sort()
    return out


def _line_at(src: str, index: int) -> str:
    start = src.rfind("\n", 0, index) + 1
    end = src.find("\n", index)
    if end < 0:
        end = len(src)
    return src[start:end]


class FrontendRequestCoordinatorTests(unittest.TestCase):
    def test_coordinator_loads_before_api_before_app(self):
        html = _read(_INDEX)
        coord_at = html.find('src="/js/request-coordinator.js"')
        api_at = html.find('src="/js/api.js"')
        app_at = html.find('src="/js/app.js"')
        self.assertNotEqual(coord_at, -1)
        self.assertNotEqual(api_at, -1)
        self.assertNotEqual(app_at, -1)
        self.assertLess(coord_at, api_at)
        self.assertLess(api_at, app_at)
        self.assertTrue(os.path.isfile(_COORD))
        self.assertTrue(os.path.isfile(_RUNNER))

    def test_coordinator_does_not_monkeypatch_fetch(self):
        src = _read(_COORD)
        self.assertIn("function createPrksRequestCoordinator", src)
        self.assertIn("function defaultFetch", src)
        self.assertIn("return fetch(url, init);", src)
        self.assertNotRegex(src, r"\b(?:window|globalThis|self)\.fetch\s*=")
        self.assertNotIn("window.fetch =", src)
        self.assertNotIn("root.fetch =", src)

    def test_old_works_inflight_dedupe_is_absent(self):
        self.assertNotIn("_prksFetchWorksInFlight", _read(_API))
        self.assertNotIn("_prksFetchWorksInFlight", _read(_APP))
        self.assertNotIn("_prksFetchWorksInFlight", _read(_COORD))

    def test_route_generation_and_abort_controller_remain(self):
        app = _read(_APP)
        self.assertIn("window.__prksRouteGen", app)
        self.assertIn("window.__prksRouteAbortController", app)
        self.assertIn("new AbortController()", app)
        self.assertIn("previousRouteAbort.abort()", app)
        self.assertIn("prksRouteStale", _read(_WORKS))

    def test_work_hint_publish_checks_stale_after_await(self):
        works = _read(_WORKS)
        concept_await = works.find("const concepts = await fetchConcepts({ signal: routeSignal });")
        self.assertNotEqual(concept_await, -1)
        concept_stale = works.find("if (prksRouteStale(routeGen)) return;", concept_await)
        concept_pub = works.find("window.__prksConceptHintList = concepts", concept_await)
        self.assertNotEqual(concept_stale, -1)
        self.assertNotEqual(concept_pub, -1)
        self.assertLess(concept_await, concept_stale)
        self.assertLess(concept_stale, concept_pub)

        arg_await = works.find("const argumentsList = await fetchArguments(undefined, { signal: routeSignal });")
        self.assertNotEqual(arg_await, -1)
        arg_stale = works.find("if (prksRouteStale(routeGen)) return;", arg_await)
        arg_pub = works.find("window.__prksArgumentHintList = argumentsList", arg_await)
        self.assertNotEqual(arg_stale, -1)
        self.assertNotEqual(arg_pub, -1)
        self.assertLess(arg_await, arg_stale)
        self.assertLess(arg_stale, arg_pub)

    def test_cache_cap_requires_known_content_length(self):
        src = _read(_COORD)
        self.assertIn("PRKS_REQUEST_CACHE_MAX_BYTES", src)
        self.assertIn("size == null || size < 0 || size > PRKS_REQUEST_CACHE_MAX_BYTES", src)

    def test_raw_fetch_bypass_contract(self):
        leftover = []
        for path in _frontend_js_files():
            src = _read(path)
            for match in _FETCH_RE.finditer(src):
                line = _line_at(src, match.start())
                allowed = False
                for allowed_path, snippet in _ALLOWED_FETCH:
                    if path == allowed_path and snippet in line:
                        allowed = True
                        break
                if not allowed:
                    rel = os.path.relpath(path, _PROJECT_DIR)
                    leftover.append("%s: %s" % (rel, line.strip()))
        self.assertEqual(
            leftover,
            [],
            "raw fetch() outside the reviewed bypass contract:\n" + "\n".join(leftover),
        )

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for request coordinator tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
