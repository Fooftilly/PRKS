import os
import re
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_SANITIZE = os.path.join(_FRONTEND, "js", "markdown-sanitize.js")
_WORKS = os.path.join(_FRONTEND, "js", "components", "works.js")
_VENDOR_JS = os.path.join(_FRONTEND, "vendor", "dompurify", "purify.min.js")
_VENDOR_LICENSE = os.path.join(_FRONTEND, "vendor", "dompurify", "LICENSE")
_VENDOR_VERSION = os.path.join(_FRONTEND, "vendor", "dompurify", "VERSION")
_FIXTURE = os.path.join(_PROJECT_DIR, "tests", "browser", "markdown_security.html")
_SERVE = os.path.join(_PROJECT_DIR, "tests", "browser", "serve.py")

_BLACKLIST_SELECTOR = "querySelectorAll('script, style, link, meta, iframe, object, embed')"
_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _frontend_js_files() -> list[str]:
    out = []
    for root, _dirs, files in os.walk(os.path.join(_FRONTEND, "js")):
        for name in files:
            if name.endswith(".js"):
                out.append(os.path.join(root, name))
    return out


class MarkdownSecurityStructuralTests(unittest.TestCase):
    def test_vendor_files_exist_and_pin_3_4_14(self):
        self.assertTrue(os.path.isfile(_VENDOR_JS), "missing purify.min.js")
        self.assertTrue(os.path.isfile(_VENDOR_LICENSE), "missing DOMPurify LICENSE")
        self.assertTrue(os.path.isfile(_VENDOR_VERSION), "missing VERSION")
        purify = _read(_VENDOR_JS)
        version = _read(_VENDOR_VERSION)
        self.assertIn("DOMPurify 3.4.14", purify)
        self.assertIn("3.4.14", version)
        self.assertIn("github.com/cure53/DOMPurify", version)
        self.assertIn("MPL-2.0 OR Apache-2.0", version)
        self.assertGreater(len(purify), 1000)

    def test_index_load_order(self):
        html = _read(_INDEX)
        purify_at = html.find('src="/vendor/dompurify/purify.min.js"')
        sanitize_at = html.find('src="/js/markdown-sanitize.js"')
        works_at = html.find('src="/js/components/works.js"')
        self.assertNotEqual(purify_at, -1)
        self.assertNotEqual(sanitize_at, -1)
        self.assertNotEqual(works_at, -1)
        self.assertLess(purify_at, sanitize_at)
        self.assertLess(sanitize_at, works_at)

    def test_sanitizer_lives_in_dedicated_module_not_works_js(self):
        sanitize = _read(_SANITIZE)
        works = _read(_WORKS)
        self.assertIn("window.prksSanitizeMarkdownPreviewHtml = function", sanitize)
        self.assertNotIn("function prksSanitizeMarkdownPreviewHtml", works)
        self.assertIn("return prksSanitizeMarkdownPreviewHtml(easyMDE.markdown(t));", works)
        self.assertNotIn(_BLACKLIST_SELECTOR, works)
        self.assertNotIn(_BLACKLIST_SELECTOR, sanitize)

    def test_old_blacklist_gone_from_frontend_js(self):
        for path in _frontend_js_files():
            src = _read(path)
            self.assertNotIn(_BLACKLIST_SELECTOR, src, path)

    def test_config_shape(self):
        src = _read(_SANITIZE)
        self.assertIn("ALLOWED_TAGS", src)
        self.assertIn("'span'", src)
        self.assertIn(_XHTML_NS, src)
        self.assertIn("ALLOWED_NAMESPACES", src)
        self.assertNotIn("USE_PROFILES", src)
        self.assertNotIn("ALLOWED_URI_REGEXP", src)
        self.assertRegex(src, r"ALLOW_UNKNOWN_PROTOCOLS:\s*false")
        self.assertIn("Object.freeze(", src)

    def test_purifier_readiness_guard(self):
        src = _read(_SANITIZE)
        self.assertIn("window.DOMPurify.isSupported === true", src)
        self.assertIn("typeof window.DOMPurify.sanitize === 'function'", src)
        self.assertIn("typeof window.DOMPurify.addHook === 'function'", src)
        self.assertIn("var purifier = null;", src)
        self.assertIn("if (!purifier) return PRKS_PREVIEW_UNAVAILABLE_HTML;", src)
        self.assertIn("return purifier.sanitize(html, PRKS_PREVIEW_SANITIZE_CONFIG);", src)
        self.assertNotIn("prksSanitizerReady", src)
        addhook_at = src.find("addHook('afterSanitizeAttributes'")
        supported_at = src.find("window.DOMPurify.isSupported === true")
        self.assertNotEqual(addhook_at, -1)
        self.assertLess(supported_at, addhook_at)
        self.assertEqual(src.count("addHook("), 1)

    def test_no_fail_open_return_of_input(self):
        src = _read(_SANITIZE)
        self.assertNotRegex(src, r"return\s+html\s*;")
        self.assertIn("PRKS_PREVIEW_UNAVAILABLE_HTML", src)
        self.assertIn("Markdown preview unavailable.", src)

    def test_authority_form_rejection_in_hook(self):
        src = _read(_SANITIZE)
        self.assertIn("trimmed.indexOf('//') === 0", src)
        self.assertIn(r"trimmed.indexOf('\\\\') === 0", src)
        self.assertIn(r"trimmed.indexOf('/\\') === 0", src)
        self.assertIn(r"trimmed.indexOf('\\/') === 0", src)

    def test_fixture_loads_production_sanitizer(self):
        html = _read(_FIXTURE)
        self.assertTrue(os.path.isfile(_FIXTURE))
        self.assertIn("/frontend/vendor/dompurify/purify.min.js", html)
        self.assertIn("/frontend/js/markdown-sanitize.js", html)
        self.assertNotIn("ALLOWED_TAGS", html)
        self.assertIn("?dompurify=absent", html)
        self.assertIn("isSupported: false", html)
        self.assertIn("__prksUnsupportedSanitizeCalls", html)
        self.assertIn("__prksUnsupportedAddHookCalls", html)
        self.assertIn("wiki-link-unresolved", html)
        self.assertIn("data-pdf-ann-id", html)
        self.assertIn("//evil.example", html)

    def test_serve_py_binds_loopback_ephemeral(self):
        src = _read(_SERVE)
        self.assertIn('HOST = "127.0.0.1"', src)
        self.assertIn("HTTPServer((HOST, 0)", src)
        self.assertNotIn("0.0.0.0", src)
        imports = [
            line.split()[1]
            for line in src.splitlines()
            if line.startswith("import ")
        ]
        self.assertEqual(imports, ["http.server", "os", "sys"])


if __name__ == "__main__":
    unittest.main()
