"""Browser store scoping and the sync auth hold (#629 L-F-1, PR 2).

The Node selftest covers behavior; these checks pin the structure that keeps
every store scoped: no unscoped database or cache name is left in page code,
the scope module loads before the stores, and the worker reads the same
scope record the page writes.
"""

import os
import re
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_JS = os.path.join(_FRONTEND, "js")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_browser_scope_selftest.js")


def _read(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def _code(src: str) -> str:
    """Source without comments."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)//[^\n]*$", "", src)


class BrowserScopeSelftest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_node_selftest(self) -> None:
        result = subprocess.run(
            ["node", _RUNNER], cwd=_PROJECT_DIR, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failed", result.stdout)


class BrowserScopeStructure(unittest.TestCase):
    def test_no_unscoped_store_name_in_page_code(self) -> None:
        for name in os.listdir(_JS):
            if not name.endswith(".js") or name == "editor-recovery.js":
                continue  # editor recovery is scoped in its own slice
            code = _code(_read(_JS, name))
            for legacy in ("prks-local-v1", "prks-offline-v1", "prks-pdf-v1"):
                with self.subTest(file=name, name=legacy):
                    self.assertNotIn(legacy, code)
        for component in ("works-pdf.js",):
            code = _code(_read(_JS, "components", component))
            self.assertNotIn("prks-pdf-v1", code)
            self.assertNotIn("PRKS_OFFLINE_PDF_CACHE_NAME", code)

    def test_stores_have_no_unscoped_default(self) -> None:
        for name in ("local-store.js", "offline-store.js"):
            code = _code(_read(_JS, name))
            with self.subTest(file=name):
                self.assertNotIn("DB_NAME", code)
                self.assertIn("root.prksBrowserScope", code)

    def test_scope_loads_before_the_stores(self) -> None:
        html = _read(_FRONTEND, "index.html")
        scope_at = html.find('src="/js/browser-scope.js"')
        self.assertGreater(scope_at, 0)
        for later in ("offline-store.js", "offline-runtime.js", "local-store.js", "sync-runtime.js"):
            with self.subTest(script=later):
                self.assertGreater(html.find('src="/js/%s"' % later), scope_at)

    def test_worker_reads_the_record_the_page_writes(self) -> None:
        scope = _read(_JS, "browser-scope.js")
        sw = _read(_FRONTEND, "sw.js")
        for page_const, sw_const in (
            ("POINTER_DB_NAME", "SCOPE_POINTER_DB"),
            ("POINTER_STORE", "SCOPE_POINTER_STORE"),
            ("POINTER_KEY", "SCOPE_POINTER_KEY"),
        ):
            page_value = re.search(r"const %s = '([^']+)';" % page_const, scope).group(1)
            sw_value = re.search(r"const %s = '([^']+)';" % sw_const, sw).group(1)
            with self.subTest(constant=page_const):
                self.assertEqual(page_value, sw_value)
        self.assertEqual(
            re.search(r"pdf: '([^']+)'", scope).group(1),
            re.search(r"const PDF_CACHE_BASE = '([^']+)';", sw).group(1),
        )
        self.assertEqual(
            re.search(r"ID_PATTERN = (/[^;]+/);", scope).group(1),
            re.search(r"SCOPE_ID_PATTERN = (/[^;]+/);", sw).group(1),
        )

    def test_sync_runtime_checks_the_session_before_each_claim(self) -> None:
        code = _code(_read(_JS, "sync-runtime.js"))
        check = code.find("await sessionAllowsSend()")
        claim = code.find("await store.claimOperation(candidate.op_id)")
        self.assertGreater(check, 0)
        self.assertLess(check, claim)
        self.assertIn("session: root.prksBrowserScope", code)


if __name__ == "__main__":
    unittest.main()
