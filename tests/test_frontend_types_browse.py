"""File types browse model: grouping stays in the classic helper Vue paints."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_types_browse_selftest.js")
_APP = os.path.join(_PROJECT_DIR, "frontend", "js", "app.js")
_TYPES_INDEX = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "types", "TypesIndexRoute.vue",
)
_TYPES_DETAIL = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "types", "TypeDetailRoute.vue",
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendTypesBrowseTests(unittest.TestCase):
    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for the types browse model")
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

    def test_route_publishes_sidebar_from_the_same_model(self):
        app = _read(_APP)
        types_at = app.index("case 'types':")
        detail_at = app.index("case 'type-detail':")
        self.assertGreater(types_at, 0)
        types_body = app[types_at:detail_at]
        self.assertIn("prksTypesIndexModel(works)", types_body)
        self.assertIn("prksPresentVueRoute(ctx, contentDiv, 'types'", types_body)
        self.assertNotIn("renderTypesIndex", types_body)
        detail_body = app[detail_at:app.index("case 'work':", detail_at)]
        self.assertIn("prksTypesDetailModel(works, route.params.docType)", detail_body)
        self.assertIn("prksPresentVueRoute(ctx, contentDiv, 'type-detail'", detail_body)
        self.assertNotIn("renderWorksByDocType", detail_body)
        self.assertIn("publishSidebar", types_body)
        self.assertIn("publishSidebar", detail_body)

    def test_types_vue_sources_use_work_html_slot(self):
        """Injected HTML slots use the shared class. Static display:contents
        must not return on the Types Vue sources."""
        for path in (_TYPES_INDEX, _TYPES_DETAIL):
            vue = _read(path)
            self.assertIn("work-html-slot", vue, path)
            self.assertNotIn('style="display: contents"', vue, path)
        index = _read(_TYPES_INDEX)
        self.assertIn("types-page__badge-host work-html-slot", index)


if __name__ == "__main__":
    unittest.main()
