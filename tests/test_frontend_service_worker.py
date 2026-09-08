"""Structural + Node regressions for the app-shell/PDF service worker (sw.js)."""
import os
import re
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_SW = os.path.join(_FRONTEND, "sw.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_INDEX_HTML = os.path.join(_FRONTEND, "index.html")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_sw_selftest.js")

# Same-origin assets the ordinary shell needs that do not appear as a direct
# <script src>/<link href>/<img src> in index.html:
#   - '/' and '/index.html' are the navigation documents themselves.
#   - the Inter variable-font file is only reachable via a CSS url().
#   - pdf-viewer-runtime.js is statically import()ed by works-pdf.js (a
#     type="module" script), so the browser's module graph fetches it eagerly
#     even though no <script> tag names it directly.
#   - icon-512.png is only referenced from manifest.webmanifest (a separate
#     JSON file, not index.html markup), for PWA install/splash icons.
_SHELL_EXTRAS_NOT_IN_HTML = {
    "/",
    "/index.html",
    "/vendor/inter/InterVariable.woff2",
    "/js/pdf-viewer-runtime.js",
    "/icons/icon-512.png",
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_manifest_list(sw_src: str, const_name: str) -> list:
    m = re.search(re.escape(const_name) + r"\s*=\s*\[(.*?)\];", sw_src, re.S)
    if not m:
        raise AssertionError("could not find %s array literal in sw.js" % const_name)
    body = m.group(1)
    return re.findall(r"'([^']+)'", body)


def _extract_index_html_local_assets() -> set:
    html = _read(_INDEX_HTML)
    paths = set()
    for m in re.finditer(r'<script[^>]*\bsrc="([^"]+)"', html):
        paths.add(m.group(1))
    for m in re.finditer(r'<link[^>]*\bhref="([^"]+)"', html):
        href = m.group(1)
        if href.startswith("/"):
            paths.add(href)
    for m in re.finditer(r'<img[^>]*\bsrc="([^"]+)"', html):
        src = m.group(1)
        if src.startswith("/"):
            paths.add(src)
    return paths


class FrontendServiceWorkerTests(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_SW))
        self.assertTrue(os.path.isfile(_RUNNER))

    def test_sw_registered_from_app(self):
        app = _read(_APP)
        self.assertIn("'serviceWorker' in navigator", app)
        self.assertIn("navigator.serviceWorker.register('/sw.js')", app)

    def test_no_generic_api_json_caching(self):
        src = _read(_SW)
        # The only two request classes handled specially are managed PDFs and
        # navigations/static assets; everything else -- notably /api/... JSON --
        # must fall through untouched rather than being cached generically.
        self.assertIn("isManagedPdfPath(pathname)", src)
        self.assertIn("passes straight through", src)
        self.assertNotIn("caches.open('api", src)
        self.assertNotIn('caches.open("api', src)

    def test_never_queues_mutations(self):
        src = _read(_SW)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertNotIn("'" + method + "'", src)
        self.assertIn("if (!isGetRequest(request)) return;", src)

    def test_no_indexeddb_or_persistent_domain_storage_in_sw(self):
        src = _read(_SW)
        self.assertNotIn("indexedDB", src)
        self.assertNotIn("localStorage", src)

    def test_eligibility_helpers_are_pure_and_exported(self):
        src = _read(_SW)
        for name in (
            "isGetRequest",
            "isSameOriginUrl",
            "isNavigationRequest",
            "isStaticEligiblePath",
            "isManagedPdfPath",
            "hasRangeHeader",
            "isCacheableStaticResponse",
            "isWholeFilePdfResponse",
            "shouldRetireCache",
        ):
            self.assertIn(name + ":", src, "%s must be exported for pure-function testing" % name)

    def test_shell_precache_is_split_across_the_right_cache_buckets(self):
        # Precaching a static asset into the wrong Cache Storage bucket would
        # silently defeat offline launch, because handleStatic()'s own
        # fallback only ever looks in STATIC_CACHE.
        src = _read(_SW)
        install_start = src.index("addEventListener('install'")
        install_end = src.index("addEventListener('activate'")
        install_block = src[install_start:install_end]
        self.assertIn("performInstall(scope.caches", install_block)

        perform_start = src.index("function performInstall(cachesApi, fetchImpl)")
        perform_end = src.index("function promoteStagingCache")
        perform_body = src[perform_start:perform_end]
        # Install writes into the staging buckets, never the live
        # SHELL_CACHE/STATIC_CACHE names an already-active worker may still
        # be reading from (AGENTS.md "Make failed SW installs unable to
        # poison the active shell cache").
        self.assertIn("cachesApi.open(SHELL_STAGING_CACHE)", perform_body)
        self.assertIn("SHELL_PRECACHE_PATHS", perform_body)
        self.assertIn("cachesApi.open(STATIC_STAGING_CACHE)", perform_body)
        self.assertIn("STATIC_PRECACHE_PATHS", perform_body)
        self.assertNotIn("cachesApi.open(SHELL_CACHE)", perform_body)
        self.assertNotIn("cachesApi.open(STATIC_CACHE)", perform_body)

    def test_activate_promotes_staging_into_live_caches(self):
        """AGENTS.md 'Make failed SW installs unable to poison the active
        shell cache': the promotion from staging into the live cache names
        must only happen in performActivate(), which only ever runs once
        install has fully succeeded."""
        src = _read(_SW)
        self.assertIn("function performActivate(cachesApi)", src)
        activate_start = src.index("function performActivate(cachesApi)")
        activate_end = src.index("function attachServiceWorkerListeners")
        activate_body = src[activate_start:activate_end]
        self.assertIn("promoteStagingCache(cachesApi, SHELL_STAGING_CACHE, SHELL_CACHE)", activate_body)
        self.assertIn("promoteStagingCache(cachesApi, STATIC_STAGING_CACHE, STATIC_CACHE)", activate_body)
        self.assertIn("shouldRetireCache", activate_body)

        install_listener_start = src.index("addEventListener('install'")
        activate_listener_start = src.index("addEventListener('activate'")
        activate_listener_end = src.index("addEventListener('fetch'")
        self.assertLess(install_listener_start, activate_listener_start)
        activate_listener_body = src[activate_listener_start:activate_listener_end]
        self.assertIn("performActivate(scope.caches)", activate_listener_body)

    def test_install_precache_essential_asset_failure_fails_the_whole_install(self):
        """AGENTS.md 'Make shell precache success meaningful': one required
        (non-optional) precache fetch rejecting must fail performInstall()'s
        Promise -- the caller's event.waitUntil() -- rather than silently
        activating an incomplete shell. Only the small decorative
        STATIC_PRECACHE_OPTIONAL_PATHS subset may swallow a failure."""
        src = _read(_SW)
        self.assertIn("function performInstall(cachesApi, fetchImpl)", src)
        self.assertIn("function precacheRequiredPaths(cache, fetchImpl, paths)", src)
        self.assertIn("function precacheOptionalPaths(cache, fetchImpl, paths)", src)

        required_start = src.index("function precacheRequiredPaths(cache, fetchImpl, paths)")
        required_end = src.index("function precacheOptionalPaths")
        required_body = src[required_start:required_end]
        # The required path must throw (reject), never swallow a failure with .catch().
        self.assertNotIn(".catch(", required_body)
        self.assertIn("throw new Error(", required_body)

        optional_start = required_end
        optional_end = src.index("function performInstall")
        optional_body = src[optional_start:optional_end]
        self.assertIn(".catch(", optional_body)

        perform_start = src.index("function performInstall(cachesApi, fetchImpl)")
        perform_end = src.index("function attachServiceWorkerListeners")
        perform_body = src[perform_start:perform_end]
        self.assertIn("precacheRequiredPaths(cache, fetchImpl, requiredStaticPaths)", perform_body)
        self.assertIn("precacheOptionalPaths(cache, fetchImpl, optionalStaticPaths)", perform_body)
        self.assertIn("STATIC_PRECACHE_OPTIONAL_PATHS.indexOf(path) === -1", perform_body)

    def test_shell_manifest_covers_every_index_html_local_asset(self):
        """Anti-drift regression: a new <script src>/<link href> in index.html
        that is not added to sw.js's static-shell manifest must fail here,
        instead of silently working online-only until someone notices the
        shell can't boot offline."""
        sw_src = _read(_SW)
        shell_paths = set(_extract_manifest_list(sw_src, "SHELL_PRECACHE_PATHS"))
        static_paths = set(_extract_manifest_list(sw_src, "STATIC_PRECACHE_PATHS"))
        manifest = shell_paths | static_paths
        html_assets = _extract_index_html_local_assets()
        missing = html_assets - manifest
        self.assertEqual(
            missing,
            set(),
            "index.html references a local asset that sw.js does not precache: %s" % missing,
        )

    def test_shell_manifest_has_no_stale_entries(self):
        """The inverse of the drift check: every manifest entry is either an
        actual index.html asset or one of the small set of documented
        exceptions (nav documents, a CSS-only font, or works-pdf.js's one
        static import) -- never a leftover for a file that no longer exists
        on the shell's startup path."""
        sw_src = _read(_SW)
        shell_paths = set(_extract_manifest_list(sw_src, "SHELL_PRECACHE_PATHS"))
        static_paths = set(_extract_manifest_list(sw_src, "STATIC_PRECACHE_PATHS"))
        manifest = shell_paths | static_paths
        html_assets = _extract_index_html_local_assets()
        allowed = html_assets | _SHELL_EXTRAS_NOT_IN_HTML
        stale = manifest - allowed
        self.assertEqual(stale, set(), "sw.js precaches a path index.html no longer references: %s" % stale)

    def test_shell_manifest_excludes_lazy_pdf_viewer_bundle(self):
        # The heavy EmbedPDF/pdfium bundle must stay cache-on-first-PDF-use,
        # not part of the eager shell-boot manifest.
        sw_src = _read(_SW)
        shell_paths = set(_extract_manifest_list(sw_src, "SHELL_PRECACHE_PATHS"))
        static_paths = set(_extract_manifest_list(sw_src, "STATIC_PRECACHE_PATHS"))
        manifest = shell_paths | static_paths
        for path in manifest:
            self.assertNotIn("prks-pdf-viewer", path, path)
            self.assertNotIn("pdfium", path, path)

    def test_every_local_manifest_file_actually_exists_on_disk(self):
        sw_src = _read(_SW)
        shell_paths = _extract_manifest_list(sw_src, "SHELL_PRECACHE_PATHS")
        static_paths = _extract_manifest_list(sw_src, "STATIC_PRECACHE_PATHS")
        for path in shell_paths + static_paths:
            if path in ("/", "/index.html"):
                continue
            on_disk = os.path.join(_FRONTEND, path.lstrip("/"))
            self.assertTrue(os.path.isfile(on_disk), "manifest path missing on disk: %s" % path)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for service worker tests")
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
